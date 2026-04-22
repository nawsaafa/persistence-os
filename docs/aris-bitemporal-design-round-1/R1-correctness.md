# R1 Correctness — Memory Palace Bitemporal Design Round 1

**Date:** 2026-04-22
**Grade:** 6.8 / 10
**Grade rationale:** The design's *architecture* is sound — bitemporal retrofit on top of the frozen `persistence.fact` log, Qdrant/Kuzu as deterministic projections, taxonomy-v2 enforcement at query time, counterfactual branches via `persistence.replay` — all composes cleanly with the existing modules. But the design routinely misstates the actual API surface of the frozen layer it claims to build on. `DB.transact` is described as `transact([datoms]) -> tx`; the real signature takes a list of fact dicts and returns a new DB. The query primitives the design names (`q_latest`, `q_entity`, `q_as_of`, `q_all`, `q_by_tx`, `latest_tx`) do not exist — the real names are `as_of`, `as_of_valid`, `history`, `since`, plus `DBView.entity()`. The audit handler's hook for `vault_snapshot_tx` is not a clean parameter — `make_audit_handler` currently accepts only `run_id/principal/policy_id`, and per-call context injection requires new plumbing. The canonicalisation claim about `provenance["source"]` is verified, but the design doesn't flag the adjacent reality that `provenance["tier"]`/`["bucket"]` are NOT canonicalised (which is actually fine, but the design reads as if it's been checked and it hasn't). These are all surface-level mismatches that force Phase 1 to rediscover them at implementation time. Architecture strong; contract hygiene weak. Fix findings then ship.

## Findings

### N1 MAJOR — `DB.transact` signature is wrong throughout the doc
**Location:** design §4.1 ("`persistence.fact.DB.transact([datoms])` ... returns tx number (int64, monotonic, BEGIN IMMEDIATE)"), §4.2 ("appends 4–7 datoms in one `allocate_and_append` transaction"), §8 ("16-thread stress test from ARIS R2 P-concurrency applies directly")
**What:** The real signature (`src/persistence/fact/db.py:99-124`) is:
```python
def transact(self, facts: list[dict], provenance: Optional[dict] = None,
             *, force_retroactive: bool = False) -> "DB":
```
It takes **fact dicts**, not `Datom` instances. It returns a **new DB**, not a tx id. Each fact dict must carry `e`, `a`, `v`, `valid_from`, and may carry `valid_to`/`op`. Datoms are constructed internally, with the caller's `provenance` dict merged into each one plus a per-fact `prompt_hash`.
**Why it matters:** Phase 1 workers will write code against an API that doesn't exist. Recovering the tx id after `transact` returns (so Qdrant/Kuzu projection can index on it) requires calling `db.store.next_tx() - 1` or inspecting the returned DB's store — the design presents this as a trivial return value.
**Fix recommendation:** Rewrite §4.1/§4.2 to match the real API. Either (a) accept the fact-dict interface and document how projection retrieves the allocated tx, or (b) add a convenience method `DB.transact_datoms(datoms) -> (DB, tx)` in Phase 1 scope and flag it as a new API on the frozen module (which triggers an ADR and a re-review of the Phase 1 freeze).

### N2 MAJOR — Named query primitives don't exist
**Location:** design task description referenced `q_latest`, `q_entity`, `q_as_of`, `q_all`, `q_by_tx`, `latest_tx`. Design doc §4.5 only names the endpoints (`/vault/recall`, `/vault/as-of`) not the underlying primitives, but Phase 1 implementation plans need the right names.
**What:** The real primitives on `DB` are `log()`, `as_of(t)`, `as_of_valid(vt)`, `history(e)`, `since(t)`, `branch(t, assertions)`. Entity projection is `DBView.entity(e)`. There is no `q_latest`, no `q_by_tx`, no `latest_tx`. The nearest approximations:
- `q_as_of` ≈ `DB.as_of(t)` (transaction-time) or `DB.as_of_valid(vt)` (valid-time) — the design conflates these two, which matters for §4.5(b): `/vault-as-of?t=...` needs to choose one deliberately.
- `q_entity` ≈ `DB.history(e)` + `DBView.entity(e)`.
- `latest_tx` ≈ `DB.store.next_tx() - 1` (with the caveat that `next_tx` is a read-only probe per the concurrency docstring in `store.py:115-127`).
**Why it matters:** Design §4.5(b) says `/vault-as-of?t=...` filters Qdrant with `valid_from ≤ t AND (valid_to IS NULL OR valid_to > t)` — that's **valid-time** semantics (`as_of_valid`). But §4.6 says `vault_snapshot_tx` is "the max tx visible to this query (the 'as-of' of the read)" — that's **transaction-time** (`as_of`). The design needs to commit to which axis `/vault-as-of` addresses; right now it's ambiguous and Phase 1 will implement whichever it picks up off the floor first.
**Fix recommendation:** Add a §4.5.1 "Query primitives" table mapping each endpoint to the exact `DB` method. Decide whether `/vault-as-of` is transaction-time (as the audit replay story requires) or valid-time (as §4.5b filter shows). Most likely both, with two separate endpoints.

### N3 MEDIUM — Audit handler doesn't expose `vault_snapshot_tx` as a clean hook
**Location:** design §4.6 ("`vault_snapshot_tx`: the max tx visible to this query (the 'as-of' of the read)")
**What:** `make_audit_handler` in `src/persistence/effect/handlers/audit.py:362-475` takes `entries`, `wraps`, `sink_name`, `run_id`, `principal`, `policy_id`. There is no `vault_snapshot_tx` parameter, and the `ctx` dict it builds is frozen at handler-creation time. The clause reads `ctx.get("policy_id")` etc. but has no per-call injection path. To emit `vault_snapshot_tx` on each `/vault/recall` audit entry, Phase 3 must either:
1. Put the snapshot tx into `args` before `perform(":vault/recall", ...)`, and add `vault_snapshot_tx` → `AuditEntry` extraction (requires a new `AuditEntry` field and a schema migration).
2. Add a caller-supplied context injector (`ctx_provider: Callable[[dict], dict]`) to `make_audit_handler` — this is an extension of the frozen effect module and needs an ADR.
**Why it matters:** The design assumes this is a "clean hook" — it isn't. Either path modifies the frozen module. Prop 4 extension depends on `vault_snapshot_tx` being recorded in the Merkle-chained entry, which means the canonical `AuditEntry` shape must grow a field, which means `:persistence.effect/audit-entry` spec must grow a slot, which means ARIS revisits the Phase 1 freeze.
**Fix recommendation:** Add a §4.6.1 subsection ADR-style: "Audit handler extension for vault_snapshot_tx". Name the new field. State whether it lives on `AuditEntry` or in `principal`/provenance (principal is the cheaper path — no spec change). Pin the choice in §6 as a new coordination constraint.

### N4 MEDIUM — Canonicalisation claim is partially correct but the adjacent risk isn't flagged
**Location:** design §6 pin 1 ("tier, bucket" in Qdrant payload), §4.4 ("tier/bucket values ride in `:datom/provenance`")
**What:** `Datom.__post_init__` (`src/persistence/fact/datom.py:58-104`) canonicalises `self.a` via `lstrip(":")` and canonicalises `self.provenance["source"]` via `lstrip(":")`. It does **not** canonicalise arbitrary other keys in `provenance`. So `provenance["tier"] = ":L2"` would NOT be stripped to `"L2"`. This is actually *fine* for the design's intent (tier values are strings like `"L2"`, not keywords), but the design claims to have checked and doesn't note the adjacency. The design's §7.1 says "`_canonicalise_content` (inherited from persistence.effect) applies to vault provenance without re-canonicalising stable fields" — this is misleading. `_canonicalise_content` lives in `handlers/audit.py:295-336` and only touches `policy_id`, `handler_chain`, `principal` on AuditEntry content dicts. It never runs over datom provenance.
**Why it matters:** A Phase 1 worker reading §7.1 will assume a canonicalisation helper exists for datom provenance. It doesn't. If the worker writes tests expecting ":L2" to be stripped, the test passes against a non-existent guarantee.
**Fix recommendation:** Strike the "_canonicalise_content ... applies to vault provenance" claim. Replace with: "Datom provenance is stored verbatim; only `provenance['source']` is canonicalised (colon-stripped) by `Datom.__post_init__`. Tier/bucket strings are stored as-is — tests should assert the in-memory and stored forms are identical."

### N5 MEDIUM — Spec registry namespace freedom is verified but not exercised idiomatically
**Location:** design §6 (implicit — reg constraint), task description ("`registry.register(':persistence.vault/*', ...)`")
**What:** `src/persistence/spec/_registry.py:23-33` accepts any string key — there is no namespace gate, so `:persistence.vault/memory` etc. register cleanly. Verified. But the design doesn't list which spec keys it intends to register in Phase 1. The existing registration points are `persistence.fact._canonical` (imported at module init in `spec/__init__.py:57`). Vault specs would need their own canonical module loaded at import time, or explicit registration on vault startup.
**Why it matters:** Without a Phase 1 "spec registration manifest" the design can't state which keys exist before/after Phase 1 — a reviewer can't verify Prop-4-extended or the audit boundary self-conform without that list.
**Fix recommendation:** Add a short table in §4 or §6: `:persistence.vault/memory`, `:persistence.vault/tier`, `:persistence.vault/bucket`, `:persistence.vault/audit-entry`, `:persistence.vault/qdrant-payload` — names + where they load from + who conforms against them.

### N6 MINOR — Genesis tx sentinel inconsistency with SQLiteStore
**Location:** design §6 pin 2 ("Genesis `tx = 0` — reserved sentinel. My Phase 1 `allocate_and_append` starts at `tx=1`")
**What:** Verified in `src/persistence/fact/store.py:263-266`: `SELECT COALESCE(MAX(tx), 0) + 1 FROM datom_log` — empty log allocates tx=1. `InMemoryStore.allocate_and_append` (line 153) same. The genesis=0 *sentinel* does not physically exist in the store; it's a downstream convention the design wants Qdrant to use. Fine, but the design should note that tx=0 is synthetic (never appears in the log) not reserved (implies a row).
**Why it matters:** Minor wording hygiene. A Qdrant migration script that tries to `SELECT ... WHERE tx = 0` to find the sentinel will return zero rows.
**Fix recommendation:** §6 pin 2 → "Genesis `tx = 0` — synthetic sentinel (never written to the log). Real writes start at `tx = 1`; `WHERE tx > 0` in Qdrant payload filters distinguishes real writes from genesis placeholder."

### N7 MINOR — Phase 5 depends on Phase 5B externally and this isn't tracked as a blocker
**Location:** design §5 ("Phase 5B (taxonomy-v2) bundle handles Qdrant genesis"), §6 pin 5 ("Phase 5B ships first (~2026-04-26), Phase 1 layers on top")
**What:** §5 Phase 1 is listed as 5-7 days but its implementation depends on Phase 5B's payload shape being final. If Phase 5B slips past 2026-04-26, Phase 1 can't start. The phase-breakdown table doesn't encode this cross-track dependency.
**Why it matters:** Duration estimates are reasonable *given the pin holds*. If the parallel track renegotiates the payload, Phase 1 rework is non-trivial.
**Fix recommendation:** Add a "Depends on" column to the §5 table. Phase 1 depends on "Phase 5B field schema pinned & shipped (2026-04-26)"; Phase 5 depends on Phase 1 datom log populated.

### N8 MINOR — Retroactive correction isn't named in the retraction example
**Location:** design §4.2 ("A correction at tx=67 ('actually 2021') appends two datoms")
**What:** The example shows a normal forward correction. But `DB.transact` refuses retroactive corrections (where `valid_from` is strictly earlier than the prior open assert) unless `force_retroactive=True`. See `src/persistence/fact/db.py:53-62` `RetroactiveCorrectionError`. A vault correction "actually 2021" relative to a prior "2022" assert *is* retroactive — the caller must pass `force_retroactive=True`.
**Why it matters:** Phase 1 workers copying §4.2 verbatim will write a vault-correction path that raises `RetroactiveCorrectionError` on every real correction. Silent failure in the happy path.
**Fix recommendation:** In §4.2, add a one-line note: "Vault corrections that move `valid_from` earlier (the common case for 'actually X') are retroactive — call `db.transact(..., force_retroactive=True)` or the fact layer refuses the write."

## Verified claims (positive list)

- `Datom` dataclass fields `(e, a, v, tx, tx_time, valid_from, valid_to, op, provenance, invalidated_by)` match the 10-field design claim — verified `src/persistence/fact/datom.py:47-56`.
- `SQLiteStore.allocate_and_append` runs under `BEGIN IMMEDIATE` with an in-process `threading.Lock` — verified `src/persistence/fact/store.py:259-281`. Design §8's concurrency claim is correct.
- `Datom.__post_init__` strips leading colons via `lstrip(":")` on `a` and `provenance["source"]` — verified `datom.py:88-104`. Idempotent on `"::x"`.
- `persistence.spec.register(key, spec)` accepts any string key; no namespace gate. `:persistence.vault/*` registration works — verified `src/persistence/spec/_registry.py:23-33`.
- `verify_chain` Merkle check over `AuditEntry`s is real and hash-based — verified `handlers/audit.py:339-354`.
- `AuditEntry` has `policy_id`, `handler_chain`, `principal`, `args_hash`, `result_hash` fields — verified `handlers/audit.py:49-62`. (Missing: no `vault_snapshot_tx` slot — see N3.)
- `make_audit_handler`'s `wraps` parameter is caller-configurable — verified `handlers/audit.py:365`. Wrapping `:vault/recall` + `:vault/remember` is a one-line change.
- `DB.branch(t, assertions)` for counterfactual branches exists and deep-copies provenance — verified `db.py:270-309`. Phase 4 counterfactual infra is already present in the frozen fact module.
- Phase 5B coordination pins 1, 3, 6, 8 are consistent with the design body: winner-only bitemporal fields (§4.3), omit `valid_to` at genesis (§4.3 table row), `re_embedded_at` ≠ `tx` (implicit in §4.3), duplicates have no bitemporal fields (§4.3).

## Overall verdict

Architecture ships. API claims do not. Close N1–N3 (rewrite §4.1, add a query-primitives table, ADR the audit hook for `vault_snapshot_tx`) before handing implementation plans to workers — otherwise Phase 1 burns a day re-discovering the real surface and either the freeze cracks or the retrofit takes a dependency on a non-frozen extension that re-opens ARIS. N4–N8 are wording/scope fixes; fold them in the same pass. A round 2 review after those fixes lands should clear 8.0+ without further structural change.
