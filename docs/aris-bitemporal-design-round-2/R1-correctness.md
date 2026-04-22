# ARIS R2 Correctness Review

**Date:** 2026-04-22
**Round:** 2 (post fix-pass)
**Scope:** design doc (`docs/plans/2026-04-22-memory-palace-bitemporal-design.md`) + Phase 1 plan (`ai-box/docs/plans/2026-04-22-memory-palace-bitemporal-phase1-impl.md`) — updated versions
**Grade:** 9.3 / 10 (prior R1: 6.8)

## Verdict

**Pass.** The fix pass closes all four Round-1 correctness findings I raised (N1/N2/N3 MAJOR-MEDIUM, plus the MINORs N6/N7/N8). API citations now match the real `persistence.fact.DB` / `make_audit_handler` surface, the `ctx_provider` ADR is a clean extension not a schema break, and the `/vault-as-of` axis is pinned to valid-time with a separate tx-indexed internal variant for audit replay. The Phase 1 Task 2 implementation will compile mentally against the real imports. One remaining MEDIUM (N4 carry-over, §7.1 stray claim about `_canonicalise_content` reaching vault provenance) and two MINORs below — none block the 9.0 gate.

## Round-1 findings — resolution status

| Finding | Prior severity | Status | Evidence |
|---|---|---|---|
| **N1** `DB.transact` signature wrong | MAJOR | **RESOLVED** | Design §4.1 bullet 1 cites the exact signature `(facts: list[dict], provenance: dict | None = None, *, force_retroactive: bool = False) -> DB`. Pipe pattern `db = db.transact(...)` documented. Task 2 `remember_fact` (plan lines 572–580) rebinds `self._db = new_db` and reads `tx` from `new_db.history(memory_id)` tail — matches db.py:99–232. Cardinality-one auto-retraction called out as built-in; adapter "MUST NOT author companion retracts" pinned explicitly (plan line 735). |
| **N2** Named query primitives don't exist | MAJOR | **RESOLVED** | Design §4.5 now names the real primitives: `DB.as_of(t)` (transaction-time), `DB.as_of_valid(vt)` (valid-time), `DB.history(e)`, `DBView.entity(e)`. §4.5 decision paragraph commits `/vault/as-of?vt=...` → `DB.as_of_valid`, `/vault/as-of?tx=...` → `DB.as_of(tx_time_of_tx)`, bare `t` → HTTP 400. §4.5(c) audit reconstruction uses `db.as_of(tx_time_of_tx)` — transaction-time, correct per Prop-4 semantics. No `q_latest`/`q_by_tx`/`latest_tx` references remain. Verified against design doc full text. |
| **N3** `vault_snapshot_tx` has no clean hook | MEDIUM | **RESOLVED** | §4.6 ADR paragraph cites option 3 (`ctx_provider` extension) with rationale. Full ADR at `docs/adr/2026-04-22-audit-ctx-provider.md` — clean extension of `make_audit_handler`, backward-compatible (omitting the parameter is a no-op), spec entry registered as optional. ADR acknowledges the R6 N1 drift-pin matrix already covers "unknown-key preservation" so the canonicalisation byte-invariant holds. Not a schema change; doesn't re-open Phase 1 freeze. |
| **N4** `_canonicalise_content` claim on vault provenance | MEDIUM | **PARTIAL — still present** | Design §7.1 line 223 still reads `_canonicalise_content (inherited from persistence.effect) applies to vault provenance without re-canonicalising stable fields`. `_canonicalise_content` lives at `src/persistence/effect/handlers/audit.py:295–336`-ish and only touches AuditEntry content (`policy_id`, `handler_chain`, `principal`). It does NOT run over datom provenance. Fix still needed — see "New findings M1" below. |
| **N5** Spec registry manifest | MEDIUM | **RESOLVED** | Phase 1 plan Task 1 registers `:persistence.vault/memory-content`, `-embedding-hash`, `-tier`, `-bucket` with source-of-truth patterns. Task 5.0 (new) registers `:persistence.vault/qdrant-payload` with the full 10-field schema + interval invariant. Adequate manifest for Phase 1. |
| **N6** Genesis tx=0 wording | MINOR | **RESOLVED** | Phase 1 plan line 347 comment reads `# first real tx; tx=0 is Phase 5B genesis sentinel`. Design §6 pin 2 still reads "reserved sentinel" rather than "synthetic sentinel," but §4.3 table row clarifies `0 = genesis sentinel` and Qdrant filter `WHERE tx > 0` is documented. Minor wording remains but not load-bearing given Task 5.0 spec requires `tx >= 0` and comments explain. Accept. |
| **N7** Phase 5B dependency in phase table | MINOR | **RESOLVED** | §5 phase table now has a "Depends on" column. Phase 1 row reads "Phase 5B re-embed landed (~2026-04-26) — winners carry genesis bitemporal payload before Phase 1 projector goes live." Phase 4 row depends on `bench/regulator_replay/` scaffold. Explicit. |
| **N8** Retroactive example | MINOR | **RESOLVED** | §4.2 retraction example is explicitly present-dated now: "A correction at tx=67 dated now ('actually 2021') goes through `DB.transact` as a single new assert with `valid_from=now`." Retroactive corrections get their own paragraph explaining `force_retroactive=True` gated behind `/vault/correct-retroactively` admin path. `RetroactiveCorrectionError` behaviour matches db.py:53–62 and the test at Phase 1 plan lines 393–409 pins the refusal. Clean. |

## Sanity checks on rewritten sections

**`DB.transact` auto-appends companion retracts for cardinality-one.** Verified against `db.py:147–196`. The `if op == "assert":` block calls `_find_prior_assert` and, when found, constructs a companion `Datom(op="retract", ...)` with the prior's value, sharing the same `tx`. Design §4.1 bullet 3 and §4.2 correction example match. Plan Task 2's `remember_fact` / `correct_content` rely on this behaviour and do NOT author companion retracts — correct. `forget()` (plan lines 646–677) emits explicit `op="retract"` facts, which bypass the auto-retraction path (db.py:147 only fires on `op == "assert"`) — also correct.

**`DB.as_of` vs `DB.as_of_valid` matches `/vault-as-of` decision.** Verified against `db.py:239–256`. `as_of(t)` filters by `d.tx_time <= t` (system-time; every datom regardless of op). `as_of_valid(vt)` filters by `d.op == "assert" AND valid_from <= vt AND (valid_to is None OR vt < valid_to)` (user-time; assertions only, interval-aware). Design §4.5 pins `/vault/as-of?vt=...` to `DB.as_of_valid` (correct for the product semantics "what did the vault believe about the world at time vt") and `/vault/as-of?tx=...` internal-only to `DB.as_of` (correct for audit replay where the question is "what had the DB learned by tx_time"). Split is deliberate and coherent.

**Task 2 compiles mentally against real imports.** Plan line 487 imports `from persistence.fact import DB, Datom` and line 488 `from persistence.fact.db import RetroactiveCorrectionError`. Both symbols exist — `DB` + `Datom` at `persistence.fact.__init__.py` top-level (inferred from design §0 and the public-API conventions), `RetroactiveCorrectionError` at `db.py:53` with `__all__` re-export on line 409. `SQLiteStore` (line 489) lives at `persistence.fact.store`. All three imports land. The `_current_assert` helper on plan line 707 walks `self._db.history(memory_id)`, returns None or the latest open assert — matches the `history` semantics at `db.py:258–263` (tx-sorted, all datoms for `e`). The tail-reading `tx = max(d.tx for d in tail)` at line 579 works because `history(e)` includes datoms from the just-appended tx and they share one tx id (invariant from `allocate_and_append`). Implementation is correct.

**`ctx_provider` ADR.** ADR at `docs/adr/2026-04-22-audit-ctx-provider.md` proposes adding `ctx_provider: Callable[[], dict[str, Any]] | None = None` to `make_audit_handler` (`audit.py:362`). The ADR implementation snippet merges `extra = ctx["_ctx_provider"]()` into `content` before `_canonicalise_content(content)` runs (audit.py:438). This is correct because `_canonicalise_content` preserves unknown keys (R6 N1 drift-pin matrix "unknown-key preservation" case), and `AuditEntry.__post_init__` mirrors dict-side for unknown keys via `object.__setattr__`. Byte-invariant holds. The existing `with mask(audit_name):` scope (audit.py:394, 413) already prevents `ctx_provider` from re-entering the audit handler; the ADR's "must not itself perform audited ops" constraint is enforced by mechanism not by convention. Sound.

## New findings

### M1 MEDIUM — §7.1 still overclaims `_canonicalise_content` scope

**Location:** design §7.1 line 223.

**What:** The bullet reads:

> `_canonicalise_content` (inherited from persistence.effect) applies to vault provenance without re-canonicalising stable fields.

`_canonicalise_content` (at `persistence/effect/handlers/audit.py` around line 295) operates on `AuditEntry.content` dicts only — it normalises `policy_id`, `handler_chain`, `principal` for the Merkle byte-invariant. It never runs over `Datom.provenance`. A Phase 1 worker reading this bullet will assume vault provenance gets a free canonicalisation pass; they'll write a test expecting `":L2"` to be stripped and it will pass against nothing.

**Why it matters:** Low-likelihood but non-zero chance of a bad test landing in Phase 1 Task 2 (tier/bucket provenance round-trip). R1 N4 carry-over — fix missed in this pass.

**Fix:** Replace the §7.1 bullet with: "Datom provenance is stored verbatim; only `provenance['source']` is canonicalised (colon-stripped) by `Datom.__post_init__` at `datom.py:88–104`. Tier and bucket strings are stored as-is — tests assert in-memory and stored forms are identical. `_canonicalise_content` applies only to AuditEntry content dicts in Phase 3 and does not reach vault provenance."

### M2 MINOR — `_coerce_dt` rejects naive datetimes; plan test doesn't exercise tz

**Location:** `db.py:380–387` (`_coerce_dt` raises `ValueError("naive datetime not allowed: ...")`). Phase 1 plan lines 395, 420–421 do use `dt.timezone.utc`, so the tests themselves are fine. But `VaultFactStore.remember_fact` accepts `valid_from: Optional[_dt.datetime] = None` and calls `_utc_now()` when None (line 547) — correct. No test asserts that a caller passing a naive datetime gets a clear error at the vault layer rather than a surprise from deep inside `db.transact`.

**Why it matters:** UX signal at the adapter layer. Not a correctness bug; a caller who passes a naive dt gets `ValueError` from `db.py:383` which mentions the raw repr but not the vault context.

**Fix:** Add one test to `tests/test_vault_fact_store.py`: pass `valid_from=dt.datetime(2026, 1, 1)` (naive) and assert the error message mentions `valid_from` or a hint to use `tzinfo=timezone.utc`. Optionally wrap the `_coerce_dt` call in the adapter with a friendlier message. Accept-or-defer.

### M3 MINOR — Spec combinator imports in Task 5.0 not verified against `persistence.spec`

**Location:** Phase 1 plan lines 893–897. Task 5.0 imports `spec_and, spec_keys, spec_value, spec_pattern, spec_int_range, spec_iso8601` from `persistence.spec.combinators`. Task 1 (line 254) imports `and_, pred, str_`. The two tasks use different combinator naming conventions — one set is snake-case with `spec_` prefix, the other is snake-case with trailing underscore.

**Why it matters:** Worker executing Task 5.0 may discover the combinator names don't exist as cited. Plan line 919 acknowledges "Exact combinator signature lives in `persistence.spec` docs; worker consults `context7` for latest combinator API" — that's an acceptable escape hatch, but it means Task 5.0 code is not drop-in verified.

**Fix:** Before Phase 1 kickoff, a 5-minute spike to open `persistence.spec.combinators` and pin the real export names in both Task 1 and Task 5.0. Or add a pre-Task-5.0 step "verify combinator names match `persistence.spec` exports." Accept-or-defer.

## Verified positive items (retained from R1)

- Datom 10-field shape — `datom.py:47–56`.
- Atomic `BEGIN IMMEDIATE` concurrency — `store.py:259–281`.
- `lstrip(":")` canonicalisation idempotent on `a` and `provenance["source"]` — `datom.py:88–104`.
- Spec registry accepts `:persistence.vault/*` — `_registry.py:23–33`.
- `verify_chain` Merkle check — `handlers/audit.py:339–354`.
- `AuditEntry` fields — `handlers/audit.py:49–62`.
- `DB.branch(t, assertions)` deep-copies provenance — `db.py:270–309`.
- Phase 5B pins 1/3/6/8 consistent with §4.3.

## Sign-off criteria

**Yes** — this round clears the ≥9.0 gate for correctness.

Grade 9.3. The three convergent Class-A findings (R1 N1/N2/N3) are all closed with evidence. M1 (carry-over of R1 N4) is the only residual MEDIUM and is a single-line edit — it does not gate Phase 1 because the worker dispatch prompt can reference this report directly and the wording fix lands during the same commit that strikes the line. M2 and M3 are accept-or-defer MINORs.

Recommendation: fold M1 into the next doc commit; no additional correctness round required. Phase 1 can kickoff once R2 rigor, R3 composability, R4 research all clear ≥9.0.
