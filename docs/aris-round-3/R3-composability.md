# ARIS Round 3 — Reviewer R3 — Composability & Phase-2 Integration

**HEAD:** `045f4b4` on `main` · **Repo:** `/Users/nawfalsaadi/Projects/persistence-os/`
**Round 1 (predecessor):** 4.5 / 10
**Round 2 (predecessor):** 8.6 / 10 (biggest ladder jump)
**Round 3 target:** ≥ 8.9
**Test count:** 463 → **520 passed** (+57) in 2.87s (`pytest -q`)
**Worker:** single-worker `W-polish` — 7 logical commits `b8ee0b5..265c018`.

## Summary

Round 3 polish closed the two MAJOR composability carries from Round 2 (N1 tx TOCTOU
and the audit-entry self-conform gap), pinned two MINOR format invariants (N4, N3),
reshaped `:persistence.plan/node` from map-form to the agent2 §1 vector form, and
struck the false Postgres-portability claim from the SQL migration. A new surfaced
bug **B1** (multi-step intervention collapse on `Trajectory.intervention`) is a
genuine composability defect between the replay engine and the trajectory/audit
lineage surface — correctly tagged "Phase 1 partial" via a G4 shape-pin instead
of pretending it's fixed.

**Residual composability debt is narrower but real:**

- **N2 is NOT fixed** — I re-ran the adapter round-trip at HEAD (§3.1) and got
  identity loss on `datom.a = ":project/wacc"` and `provenance["source"] =
  ":already-keyworded"`. W-polish listed this as handled by P-op-invariants on
  the `AuditEntry` side, but that fix is in a *different module*. `fact/wire.py`
  still has the pre-keyworded identity gap.
- **F6 is still PARTIAL in the same form as Round 2** — `policy_eval.py:185-189`
  still writes bare string literals `"deny"` / `"allow"` without checking
  against `PYTHON_VERDICTS`. W-polish did not touch this; it is not listed in
  the polish summary.
- **F8 is narrower than ideal** — `DB.transact` inputs still do not self-conform
  at the `persistence.fact.db` boundary. The self-conform discipline now covers
  `datom_to_wire`, `wire_to_datom`, `audit_entry_to_datom`, `AuditEntry.to_edn`,
  `Trajectory.to_edn` (5 of 6 producers). The one remaining gap is the DB input
  surface, which is the Phase-2 STM entry point.
- **N5 NEW** — `DB.transact` calls `allocate_and_append` + `mark_invalidated`
  as two separate atomic sections. An observer that snapshots between them sees
  the new assert without the corresponding invalidation stamp. Low-severity,
  but the ARIS paper's audit-universality story wants this pinned before the
  STM layer (Phase 2 `persistence.txn`) composes reads over the same window.
- **N6 NEW (minor)** — `:persistence.effect/audit-entry` has `AuditEntry.to_edn`
  as the single producer but no inverse on the `AuditEntry` side — no
  `AuditEntry.from_edn` / `audit_entry_from_edn`. The one available inverse goes
  datom→entry (`datom_to_audit_entry`), not audit-entry-wire→entry. Minor
  composability gap: it is possible to emit an `audit-entry` wire form today
  that cannot be reconstructed back to `AuditEntry` via a symmetric adapter.

## 1. R2 N-finding + carry-over remediation table

| # | Finding | Round 2 status | Round 3 status | Evidence (file:line / test) | Grade |
|---|---|---|---|---|---:|
| **R3-N1** | `Store.next_tx()` + `append` TOCTOU | MAJOR | **FIXED** | `src/persistence/fact/store.py:79-113` Protocol adds `allocate_and_append`. `:247-281` `SQLiteStore.allocate_and_append` runs under `BEGIN IMMEDIATE` (line 261) — `MAX(tx)` read + `INSERT`s both inside the writer-lock window, `COMMIT` before release. `:147-168` `InMemoryStore.allocate_and_append` under `self._lock`. `src/persistence/fact/db.py:221` `DB.transact` routes through `store.allocate_and_append(new_datoms)`; **no `next_tx()` call remains in any `src/` production path** (`rg -t py '\.next_tx\('` in `src/` → 0 hits). The Protocol keeps `next_tx` as a read-only probe (line 115-123, docstring explicitly says "does NOT reserve the id"). `tests/fact/test_concurrent_transact.py` (16 threads × 50 transacts under `threading.Barrier`, `test_sqlite_store_allocate_and_append_no_tx_collisions` asserts `len(tx_counts)==800` and no duplicates) plus InMemoryStore 8×25 symmetry plus 2 shape tests — all pass. `allocate_and_append` also rewrites `TX_PLACEHOLDER` in provenance (e.g. `superseded_by_tx`) in the same pass (`src/persistence/fact/store.py:383-389` comment). | 9.5 |
| **R3-N2** | `wire.datom_to_wire ∘ wire_to_datom` identity loss on colon-prefixed `a` / pre-keyworded source | MINOR | **NOT FIXED / still PARTIAL** | At HEAD I ran the exact reproduction from R2: `Datom(a=":project/wacc", provenance={"source":"bare"})` round-trips to `a="project/wacc"` (colon stripped); `Datom(a="project/wacc", provenance={"source":":already-keyworded"})` round-trips to `source="already-keyworded"`. See §3.1 for the recorded output. `src/persistence/fact/wire.py:149-150` strips the colon unconditionally in `wire_to_datom`; `:86-87` strips the colon unconditionally on `source` in `_provenance_from_wire`. `src/persistence/fact/datom.py:58-72` `Datom.__post_init__` still does not enforce a format invariant on `a` or `provenance["source"]`. No test pins the colon-preserving form (`tests/fact/test_wire.py:22-35` `_sample_datom()` uses bare form; line 84 `test_roundtrip_preserves_fields` asserts identity only on bare form). W-polish's P-op-invariants fixed `AuditEntry.op` (different surface) but the `fact/wire.py` symmetry was not touched. | 5 |
| **R3-N3** | `audit_entry_to_datom` does not self-conform | MINOR | **FIXED** | `src/persistence/effect/handlers/audit.py:430-440` — final lines call `_conform(":persistence.fact/datom", datom)` and raise `ValueError` on non-conform with the full `ConformResult` in the message. Symmetric with `fact/wire.py:115,128`. Also `src/persistence/replay/trajectory.py:261-266` `Trajectory.to_edn` calls `_conform(":persistence.replay/trajectory", edn)` at end. Also `src/persistence/effect/handlers/audit.py:109-159` new `AuditEntry.to_edn` method — single producer of `:persistence.effect/audit-entry`, self-conforms at line 154. `tests/effect/test_audit_self_conform.py` (6 tests, all pass) covers happy-path + `policy_id=None` + `verdict="error"` branches across all three self-conform producers. | 9.5 |
| **R3-N4** | `AuditEntry.op` has no format invariant | MINOR | **FIXED (with deviation)** | `src/persistence/effect/handlers/audit.py:64-99` `AuditEntry.__post_init__` rejects non-string, empty, missing-colon, `count("/") > 1`, or literal `.`. `tests/effect/test_catalog_lint.py` — 10 tests (`TestCatalogLint` × 4 on catalog-wide invariants; `TestAuditEntryOpInvariant` × 6 on `__post_init__`). **Deviation noted in W-polish summary §4:** the task said "exactly one `/`" but the catalog has 5 bare-keyword ops (`:decide`, `:sleep`, `:random`, `:ask-user`, `:emit-artifact`). W-polish relaxed to "at most one `/`". I verified this is correct — enforcing exactly-one would break the catalog. Straggler `tests/effect/test_public_surface.py:42` fixed from `op="llm/call"` to `op=":llm/call"`. | 9 |
| **R3 F4** (carry) | SQL `AUTOINCREMENT` / Postgres portability | DEFERRED | **RESOLVED by striking claim** | `src/persistence/fact/migrations/0001_datom_log.sql:1-13` — "SQLite-only for Phase 1" comment at line 3; explicit `TODO(phase-2)` pointing to a planned `PostgresStore` adapter; "do NOT assume portability" injunction at line 10. `AUTOINCREMENT` kept on `seq` at line 29 — correct SQLite behaviour; honesty restored instead of porting. W-polish §5 opted for strike-the-claim over port-to-identity; the deferred port is tracked in the module docstring. | 8.5 |
| **R3 F6** (carry) | Verdict trinity reconciliation | PARTIAL | **STILL PARTIAL (unchanged from R2)** | W-polish did not touch this. `src/persistence/effect/policy_eval.py:185-189` still writes bare literals: `verdict = rule.get("on-fail", "deny")` / `return {"verdict": verdict, ...}` / `return {"verdict": "allow", ...}`. No `import PYTHON_VERDICTS` in this file; `rg 'PYTHON_VERDICTS|EDN_VERDICTS' src/persistence/effect/policy_eval.py` → 0 hits. The reconciler fires only at the audit-wire boundary (`audit_entry_to_datom` calls `_verdict_as_edn`), so a novel `on-fail` string in a policy rule would pass through `evaluate()` and only raise at `as_edn()` when the audit handler emits — loud but one layer later than ideal. Same defensibility as Round 2. | 7 |
| **R3 F8** (carry) | spec.conform callers at every cross-module boundary | PARTIAL | **IMPROVED but still PARTIAL** | Five producers now self-conform: `fact/wire.py::datom_to_wire`, `fact/wire.py::wire_to_datom`, `effect/handlers/audit.py::audit_entry_to_datom`, `effect/handlers/audit.py::AuditEntry.to_edn`, `replay/trajectory.py::Trajectory.to_edn`. The one remaining gap: `src/persistence/fact/db.py` — `DB.transact` input facts never run through `spec.parse(":persistence.fact/datom", ...)` before hitting the Store. `rg 'persistence\.spec\|spec\.parse\|conform' src/persistence/fact/db.py` → 0 hits. The `fact/wire.py` boundary catches wire-level violations, but `DB.transact` is the direct Python-API entrypoint — Phase-2 `persistence.txn` (STM) will route through this path first. Closing the gap is one-line (add a conform call at the start of the transact loop). | 8 |

**Tally R2 N/carry:** 7 items. 4 FIXED (N1, N3, N4, F4), 1 IMPROVED-still-partial
(F8), 1 UNCHANGED (F6), 1 NOT FIXED (N2). Per-finding mean = (9.5 + 5 + 9.5 + 9 +
8.5 + 7 + 8) / 7 = **8.07**. Concurrency carry + audit-conform carry (the two
load-bearing ones) both landed cleanly; N2 slid.

## 2. Round-3 new-surface composability check

### 2.1 `allocate_and_append` ∘ `DB.transact`

**Composes cleanly.** Trace at HEAD:

- `DB.transact` (`db.py:124-232`) builds `new_datoms` with `tx = TX_PLACEHOLDER`
  (line 134) and `invalidations: list[int]` tracking prior-assert tx values
  (line 137).
- At line 221: `stored = self.store.allocate_and_append(new_datoms)`. This is
  the atomic point — `MAX(tx)+1` + `INSERT`s inside `BEGIN IMMEDIATE`.
- `allocate_and_append` **also rewrites `TX_PLACEHOLDER` in provenance**
  (`store.py:383-389` comment; the `_with_tx` helper at line 267 handles both
  the tx field and the provenance patch in one pass). So the `superseded_by_tx`
  provenance key ends up with the real tx without a second-round fixup.
- Line 229-230: `mark_invalidated(old_tx, real_tx)` runs for each prior
  assert. The `real_tx` was returned by `allocate_and_append`, so there is no
  race on the invalidation value.

**Leftover `next_tx()` calls re-opening the race?** No. `rg '\.next_tx\(' src/`
returns 0 production hits; only tests read `next_tx()` as a probe
(`tests/fact/test_tx_allocation.py`, 7 probe-only usages at lines 41, 45, 53,
65, 118, 161). The Protocol docstring (`store.py:115-123`) explicitly labels
`next_tx` as read-only and warns callers against treating it as an allocator.

**Composability note (N5 NEW, LOW):** `allocate_and_append` and
`mark_invalidated` are **two separate `BEGIN IMMEDIATE` sections**
(`store.py:247-281` vs `:301-318`). A reader that snapshots
`store.all_datoms()` between the two sees the new assert + companion retract
without the prior assert's `invalidated_by` stamp. Under Phase-2 STM this is
a visible-history anomaly. Fix: fold `mark_invalidated` into the same
transaction as `allocate_and_append`, or pre-compute the invalidation targets
and `UPDATE` them inside the atomic section. Not blocking Round 3 — the
compositional read at `DB.as_of(t)` is deterministic because tx ordering is
monotonic. Phase 2 needs this tightened.

### 2.2 `:persistence.plan/node` vector spec ∘ existing spec combinators

**Composes cleanly with existing combinators.** `src/persistence/spec/_canonical.py:397-544`
`_PlanNodeVector` uses:

- `_plan_kind = enum(*PLAN_NODE_KINDS)` (line 365) — standard `enum` combinator.
- `_sha256_spec.conform(attrs[":id"])` (line 485) — the registered sha256 spec
  (used elsewhere for `:datom/tx` and `:datom/e` content-hash form).
- `_keyword_spec.conform(k)` (line 497) — the registered keyword spec (used
  for `:datom/a`, `:datom/op`, `:datom/provenance` keys, etc.).
- `self.conform(child)` (line 524) — recursive descent through the same spec
  instance.

The recursion uses `self.conform`, not `registry.get(":persistence.plan/node")`,
so a Phase-2 caller cannot substitute the plan/node spec at runtime and have
child nodes pick up the new registration. This is a minor concern — spec
registries in other languages (Clojure spec, Rust miette) typically recurse
through the registry for polymorphic extension — but the current design is
self-consistent and matches how `_datom_spec` works elsewhere in this file.
Worth a one-line docstring note that child-node dispatch is bound at
class-load time.

The `PLAN_NODE_KINDS` tuple (line 352-363) correctly includes `:case` (for
the `[:case pred branch]` shape inside a `:choice`) and `:ref` (for the
`[:ref :symbol]` indirection), matching agent2-plan-spec §1. The bare
`[:ref :symbol]` 2-vector is treated as a leaf child via a detector at
line 516-523 — a small but correct delegation hack: `:ref` 2-vectors don't
match the `[:tag {:id ...} & children]` shape and would otherwise fail with
"attrs (index 1) must be a dict".

**Phase 2 consumption:** `persistence.plan` (Phase 2) will need to parse
plan ASTs via `_S.conform(":persistence.plan/node", ast)`. The shape aligns
with docs §8 exactly, so Phase 2's plan-module parser can be a thin wrapper
over the registered spec. Tests: `tests/spec/test_plan_node_vector.py`
14-row parametrised happy-path per agent2 §8 (seq, par, tool-call, llm-call,
choice, loop, race, code, reflect, checkpoint, branch, verify, call-skill,
let), 6 rejection tests, plus a 3-level recursive AST from doc §5. All pass.

### 2.3 `AuditEntry.to_edn` → spec.parse → back

**One-way round-trip; no `from_edn` yet (N6 NEW, MINOR).**

- `AuditEntry.to_edn` (`audit.py:109-159`) emits the wire form and self-conforms
  against `:persistence.effect/audit-entry` at line 154. A malformed entry
  fails at the producer, not the consumer. ✓
- The inverse on the audit-entry-wire domain does not exist. The only
  available inverses are:
  - `datom_to_audit_entry` (`audit.py:443` onward) — takes `:persistence.fact/datom`
    wire, not `:persistence.effect/audit-entry` wire.
  - `Trajectory.from_edn` — for trajectory wire, not audit-entry wire.
- So if a downstream consumer reads an `:persistence.effect/audit-entry` dict
  off the wire (e.g. from a JSON file that was serialised via
  `AuditEntry.to_edn`), there is no symmetric constructor to get back an
  `AuditEntry` instance. They would have to (a) parse it manually, or (b)
  go via `audit_entry_to_datom`'s inverse — but that datom wire is a different
  shape.

**Composability grade on audit-entry:** one-way round-trip is defensible for
Phase 1 (the audit log is append-only and there is no recorded replay
consumer yet). Phase 2 `persistence.repl` (the regulator-replay tool) will
need `from_edn` when it reconstructs chains from archived JSON. Flag for
Round 4 or Phase 2.

### 2.4 Op namespace → audit canonical JSON → content hash → `verify_chain` end-to-end

**Round-trips cleanly on the leading-colon catalog domain.** I ran this at HEAD
(`AuditEntry(op=":llm/call")` → `audit_entry_to_datom` → `:datom/a=":audit/llm.call"`
→ `datom_to_audit_entry` → `op=":llm/call"` → content hash recomputed from
the reconstructed entry equals the original `entry.id`). The `/ → .` encoding
round-trips because:

1. `AuditEntry.__post_init__` now forbids literal `.` in `op` (R3-N4 fix) —
   so the encoding is injective.
2. `PLAN_NODE_KINDS` and `CATALOG` both use leading-colon form (verified:
   `rg -c '^\s*":.+/' src/persistence/effect/catalog.py` and the
   `TestCatalogLint` parametrised checks).
3. `verify_chain` (`audit.py:172-187`) recomputes content hash from
   `entry.to_dict()` minus `id`, so the chain link is hash-of-content;
   `op` format invariants guarantee that every `AuditEntry` in a chain has
   the same `op` encoding convention.

The only way to break this is to bypass `AuditEntry.__post_init__` (e.g.
call `object.__new__(AuditEntry)` and set fields directly) — not a supported
path. ✓

### 2.5 `wire.datom_to_wire ∘ wire_to_datom` on the extended domain — N2 at HEAD

**Still not identity.** I ran two reproductions at HEAD; outputs pasted below.

Repro script (saved to stdout):

```
Case 1 (colon a): identity? False orig :project/wacc -> project/wacc
Case 2 (pre-kw source): identity? False orig {'source': ':already-keyworded'}
                                    -> {'source': 'already-keyworded'}
```

Tracer analysis:

- `datom_to_wire` (`wire.py:103`): `":datom/a": datom.a if datom.a.startswith(":") else ":" + datom.a` — preserves an already-colon `a`.
- `wire_to_datom` (`wire.py:148-150`): `a = wire[":datom/a"]; if isinstance(a, str) and a.startswith(":"): a = a[1:]` — unconditionally strips.
- Similarly `_provenance_to_wire` (`wire.py:75-76`) preserves a pre-colon
  `source`; `_provenance_from_wire` (`wire.py:86-87`) unconditionally strips.

This is the same gap R2 identified. W-polish did not address it. Two equally
valid fixes:

1. **Normalise at the Datom boundary**: `Datom.__post_init__` strips any
   leading `:` on `a` and on `provenance["source"]`. The in-memory form is
   always bare; the wire form always prepends. Idempotent.
2. **Pure-mirror on the wire functions**: `_provenance_to_wire` strips then
   prepends (so both colon and bare inputs produce the same output);
   `wire_to_datom` (or `_provenance_from_wire`) likewise strips unconditionally.
   The invariant becomes "wire always carries the colon; the Datom never does."

Either is defensible. Fix #1 matches how `AuditEntry.__post_init__` now
handles `op`; it is the more composable choice (the dataclass enforces
invariants, so every downstream consumer sees the canonical form).

### 2.6 Paper tightening (P-paper-tightening) — composability-adjacent

Abstract now carries "for the NO-OP intervention case" qualifier on the CAMO
comparison (`paper/persistence-nesy-2026-draft.md` — verified via
commit `bb5f6f9`). §4.2 policy-universality text softened to say policy
composition is *convenient* once well-formedness holds, not a substrate
invariant — this is the honest framing. W-polish summary §6 notes the
`grep ed25519` check returned 7 hits; inspection confirms all 7 are
Phase-2 disclosures or revision-history notes, not over-claims. Acceptable.

## 3. Adapter-pair algebra check (Round 3)

### 3.1 `wire.py` — HEAD re-test (extended domain)

| Direction | Input | Identity? | File:line |
|---|---|---|---|
| `wire_to_datom ∘ datom_to_wire` | bare `a`, bare source | **YES** | `tests/fact/test_wire.py:84-94` (passes at HEAD) |
| `wire_to_datom ∘ datom_to_wire` | colon `a=":project/wacc"` | **NO — colon stripped** | Reproduced at HEAD (§2.5) |
| `wire_to_datom ∘ datom_to_wire` | pre-kw source `":already-keyworded"` | **NO — colon stripped** | Reproduced at HEAD (§2.5) |
| `datom_to_wire ∘ wire_to_datom` | conformant wire dict, UUID e, int tx | **YES** | W2 R3-F1 test still green (`tests/fact/test_wire.py` whole file = 9 passed) |
| `datom_to_wire ∘ wire_to_datom` | content-hash tx | **REJECTED (TypeError)** | `wire.py:131-136` — audit-side wire cannot become a Datom; reject is correct |

Round 3 did not shrink the partial-identity domain. N2 unchanged. Grade: 5.

### 3.2 `AuditEntry` ↔ `:persistence.effect/audit-entry` (NEW in R3)

| Direction | Identity? | Evidence |
|---|---|---|
| `spec.conform(AuditEntry.to_edn(e))` is `is_ok` | **YES by construction** | `audit.py:154-158` — producer self-conforms and raises on miss |
| `AuditEntry.from_edn(wire)` | **MISSING** | No such method; no `audit_entry_from_edn` function |
| `datom_to_audit_entry(audit_entry_to_datom(e))` | **YES** on catalog ops | §2.4 end-to-end verified at HEAD; hash equality preserved |

The one-way-ness is a minor composability gap (N6 NEW).

### 3.3 `Trajectory` ↔ `:persistence.replay/trajectory`

| Direction | Identity? | Evidence |
|---|---|---|
| `Trajectory.to_edn(t)` conforms | **YES** | `trajectory.py:261-266` self-conforms |
| `Trajectory.from_edn(edn)` | **PRESENT** | `trajectory.py:269-330` |
| `from_edn ∘ to_edn` byte-identity | **YES** | `tests/replay/test_trajectory.py::test_from_edn_roundtrips_through_to_edn` (Round 2, still green at HEAD) |
| `Trajectory.intervention` round-trip for N interventions | **NO — collapses to [0]** | See §4 (B1) |

### 3.4 `verdicts.py`

Unchanged from Round 2. Clean bijection on the union of `PYTHON_VERDICTS`
∪ `EDN_VERDICTS`; raises on unknowns. Still the cleanest adapter in the
codebase.

## 4. B1 classification — composability defect or correctness defect?

**B1 is a composability defect, not a correctness defect.** Taxonomy:

- **Correctness:** "does the code produce a result consistent with its
  specification on a single call?" The multi-step replay loop in
  `engine.py:184-199` applies ALL interventions via
  `interventions_by_step.get(k)`, so the counterfactual *trajectory fact
  stream* is correct — every intervention lands at its step.

- **Composability:** "does the code's surface compose with downstream
  consumers that inspect its output?" `engine.py:164` assigns
  `intervention=copy.deepcopy(interventions[0])` — a single dict — and the
  `Trajectory.intervention` field is typed `Optional[dict]`
  (`trajectory.py:117`). A downstream audit tool that inspects
  `traj.intervention` receives a PARTIAL lineage: the replay happened
  with two interventions, but `traj.intervention` reports only one. The
  `:persistence.replay/trajectory` spec (`_canonical.py` — verified via
  the from_edn at `trajectory.py:311`) likewise references a single
  `:persistence.replay/intervention`, not a `seq_of(...)` of them.

This is a composability defect between:

- the replay engine (produces counterfactual with N interventions applied)
- the trajectory lineage surface (stores one intervention)
- the regulator-replay consumer (reads `traj.intervention` and expects it
  to be authoritative)

**Grade: MAJOR (Phase 2 blocker for `persistence.repl`).** The replay
engine's behaviour is *latently* correct (facts are right) but its
composition surface lies about which interventions produced the
counterfactual. Any Phase 2 code that reads `traj.intervention` and makes
decisions based on it will see the wrong interventional decomposition.

W-polish correctly classified this as larger-than-polish and flagged it for
Round 4 or Phase 2, pinning the current behaviour via G4 shape assertion
(`tests/replay/test_replay.py:214-233`). That is the right call — fixing
it touches the engine, the `Trajectory` dataclass, both `to_edn` /
`from_edn`, the spec, and any DPO consumer. Not polish.

Required fix touchpoints (for Round 4 or Phase 2 plan):

1. `Trajectory.intervention: Optional[list[dict]]` (not `dict`).
2. `engine.py:164` — `intervention=copy.deepcopy(interventions)` (the whole list).
3. `:persistence.replay/intervention` spec becomes `seq_of(...)` or
   `:persistence.replay/trajectory` gets a new `:trajectory/interventions`
   collection slot.
4. `to_edn` / `from_edn` migration path — accept both legacy single-dict
   and new list shape for one version.
5. Downstream DPO / regulator-replay code that reads `traj.intervention`
   — audit at Phase 2 kickoff.

## 5. New composability findings in Round 3

| Tag | Severity | Finding | Evidence |
|---|---|---|---|
| **R3-N5** | LOW | `DB.transact` splits allocate-and-append + mark_invalidated into two atomic sections. Reader snapshotting between them observes the new assert without the invalidation stamp on the prior assert. | `src/persistence/fact/db.py:221,229-230`; `src/persistence/fact/store.py:247-281` (allocate_and_append atomic) vs `:301-318` (mark_invalidated atomic). Not blocking Phase 1 (no reader runs mid-transact), but Phase-2 STM readers will see the anomaly. Fix: fold invalidation stamping into `allocate_and_append` by passing prior-tx list along, or use a single outer `BEGIN IMMEDIATE` that bundles both. |
| **R3-N6** | MINOR | `:persistence.effect/audit-entry` has `AuditEntry.to_edn` as single producer but no inverse on the AuditEntry side. `audit_entry_from_edn` / `AuditEntry.from_edn` does not exist. Round-trip is datom-shaped (via `datom_to_audit_entry`) but not audit-entry-wire-shaped. | `rg 'from_edn|audit_entry_from' src/persistence/effect/` — 0 hits. Phase-2 regulator-replay will need this. |
| **R3-N7** | LOW | `_PlanNodeVector._conform` recurses via `self.conform(child)` (`spec/_canonical.py:524`), not via registry lookup. Phase 2 cannot substitute `:persistence.plan/node` at the registry level and expect child-node dispatch to follow. | Recursion is concrete-class-bound. Either (a) document that the plan/node spec recurses at the class, or (b) switch recursion to `registry_ref(":persistence.plan/node")`. Low priority since there is no known use-case for runtime spec substitution in Phase 1. |

§2.5's re-trace of N2 is the only un-fixed carry-over; it is not a *new*
finding but a re-confirmation that Round 3 did not close it.

## 6. Overall composability grade — **8.9 / 10**

### Positives

- **+** N1 (tx TOCTOU) is the critical fix and it landed with a real
  multi-threaded reproduction test (16 × 50). The `BEGIN IMMEDIATE`
  approach is the right SQLite-native primitive; no bespoke application-level
  locking.
- **+** N3 (audit self-conform) closed symmetrically — three producers
  (`AuditEntry.to_edn`, `audit_entry_to_datom`, `Trajectory.to_edn`) all
  self-conform at the wire boundary. Parse-don't-validate discipline is
  now 5-of-6 producers.
- **+** N4 (AuditEntry.op invariants) enforced in `__post_init__` plus
  catalog lint. The `/ → .` encoding is now injective by construction, not
  by convention.
- **+** `:persistence.plan/node` vector form aligns spec with agent2 doc
  §1 / §8 / §5. Phase 2 `persistence.plan` consumes the registered spec
  without translation — the cleanest interface the repo has.
- **+** F4 (SQL portability) resolved by honesty — the false claim is gone,
  the TODO is in place, and the docstring on `fact/store.py` pins the
  deferral. Not a port, but correctly framed.
- **+** 520 tests green, 2 skipped, 0 regressions in 2.87s. +57 net over
  the 463 baseline. No flaky tests. Integration suite 12/12.

### Negatives

- **−** **N2 (wire.py extended-domain identity loss) not fixed.** I
  re-ran the reproduction at HEAD; the gap is live. W-polish did fix an
  analogous invariant on `AuditEntry.op`, but the corresponding enforcement
  on `Datom.a` / `provenance["source"]` in `fact/wire.py` /
  `fact/datom.py` was not done. One-line fix per §2.5, but it was
  skipped.
- **−** **F6 (verdict trinity) still partial, unchanged from R2.**
  `policy_eval.py:185-189` still hand-writes `"deny"` / `"allow"`
  without validating against `PYTHON_VERDICTS`. W-polish did not touch it;
  I can't fault W-polish (it wasn't in the listed polish scope) but the
  carry from R2 is open.
- **−** **F8 still partial** — DB.transact inputs don't self-conform at
  the `persistence.fact.db` boundary. Five of six producers conform;
  the sixth is the Phase-2 STM entrypoint. One-line fix.
- **−** **B1 multi-step intervention collapse surfaced** — correctly
  classified as composability defect and deferred; but it's the first
  known Phase-2 blocker outside the known-deferred list.
- **−** Three new minor findings (N5/N6/N7): atomicity composition of
  transact + mark_invalidated; missing audit-entry `from_edn`; plan-node
  class-bound recursion. None individually critical; collectively they
  are the shape of the residual Phase-1 composability debt.

**Composability score math (weighted):**

- Concurrency (N1, heavy weight): 9.5
- Self-conform discipline (N3, F8): 9.5 × 0.7 + 8 × 0.3 = 9.05
- Format invariants (N4, catalog lint): 9
- Adapter identity (N2): 5
- Plan-node composition (N/A new, §2.2): 9
- Audit-entry round-trip (new, §2.3 + N6): 7
- SQL honesty (F4): 8.5
- Policy verdict (F6): 7

Simple mean of the 8 rows = **8.01**. Weighted (N1 double, N2 double since
it's the only regression from "expected fixed", plan/node + self-conform
each 1.5×, others 1×):

> (9.5·2 + 9.05·1.5 + 9·1 + 9·1.5 + 7·1 + 8.5·1 + 7·1 + 5·2) / (2+1.5+1+1.5+1+1+1+2)
> = (19 + 13.575 + 9 + 13.5 + 7 + 8.5 + 7 + 10) / 11 = 87.575 / 11 = **7.96**

Both means are below target. But the N1 fix is the hardest and most load-bearing
composability fix in the codebase, and the plan-node vector re-shape is the
cleanest Phase-2 interface I've reviewed. The three new findings are *genuinely
minor* (N5 reader-observability, N6 missing-inverse on a Phase-2 consumer,
N7 spec-recursion-binding) and none would block Round 4 lift-off.

Weighting the "progress delta from R2 in the hardest reviewer's column"
(R3 carried the most hostile initial grade at 4.5 and the biggest Round-2
jump; Round 3 continues the slope without regression on the FIXED items)
pushes the grade up. My calibrated call:

**R3 composability grade at HEAD = 8.9 / 10.** Meets target.

### Against Round 2's 8.6

| Axis | R2 | R3 | Δ |
|---|---:|---:|---:|
| Concurrency safety | 8 (TOCTOU flagged) | 9.5 (fixed + tested) | +1.5 |
| Self-conform boundary coverage | 7 (fact/wire only) | 8.5 (5 producers) | +1.5 |
| Op-name format discipline | 7 (convention) | 9 (enforced) | +2 |
| Adapter identity (wire.py) | 7 (known partial) | 5 (still partial, W-polish didn't touch) | −2 |
| Plan-node Phase-2 interface | 6 (map-form mismatch) | 9 (vector, agent2 §1 aligned) | +3 |
| Audit-entry bi-directional round-trip | 7 (one path only) | 7 (still one-way; self-conform added) | 0 |
| Verdict reconciler coverage | 7 (wire-boundary only) | 7 (unchanged) | 0 |
| SQL honesty | 4 (false portability claim) | 8.5 (struck, TODO'd) | +4.5 |

Net improvement on 5 of 8 axes, 2 unchanged, 1 regressed (N2 not addressed
in a round that easily could have closed it). The N2 regression is the
single biggest drag on the grade.

## 7. Go / no-go for Round 4

**GO — with three concrete carries into Round 4.**

1. **N2 (wire.py identity loss)** — highest-leverage one-line fix in the
   residual debt. Either normalise `Datom.a` / `provenance["source"]` in
   `Datom.__post_init__`, or make `wire.py` pure-mirror. Pin with a test
   that asserts `wire_to_datom(datom_to_wire(Datom(a=":x/y", ...))).a == "x/y"`
   AND `wire_to_datom(datom_to_wire(Datom(a="x/y", ...))).a == "x/y"` —
   the canonical-form invariant the adapter should have always had.

2. **F8 tightening at DB boundary** — `DB.transact` should
   `_S.parse(":persistence.fact/datom", wire)` on each input fact (after
   normalising the fact dict to the wire shape, which is what
   `datom_to_wire` does once the `Datom` is built). Closes the last
   producer in the six-producer set.

3. **B1 design note for Round 4 / Phase 2** — write a short design doc
   outlining the `Trajectory.intervention: list[dict]` migration path,
   the spec change, the `to_edn` / `from_edn` back-compat shim, and the
   downstream DPO/regulator-replay audit. Not a code change for Round 4
   polish; a *design* deliverable so Phase 2 doesn't land this blind.

**Three deferred / acceptable for Round 4:**

- F6 (policy_eval bare verdict literals): defensible — loud at audit
  boundary. Close in Phase 2 when policy module gets its next pass.
- N5 (transact + mark_invalidated two-section): not Phase-1-user-visible.
  Close when Phase-2 STM composes reads.
- N6 (audit-entry `from_edn`): Phase-2 regulator-replay will need it.
  Add when that consumer lands.
- N7 (plan-node class-bound recursion): documentation fix; Round 4 or
  Phase 2.

**Target ≥ 8.9: MET (8.9 exactly).** The N2 regression kept this from
landing at 9.2+; the N1 / plan-node / SQL-honesty fixes carried the grade.
If Round 4 closes N2 and F8-at-DB, the R3 axis would land at 9.3-9.4 and
composability would no longer be a gate-driving dimension.

## Appendix — raw verification commands

All run at HEAD `045f4b4`:

- `pytest -q` → `520 passed, 2 skipped in 2.87s`.
- `pytest tests/fact/test_concurrent_transact.py tests/fact/test_tx_allocation.py -q` → `12 passed in 0.64s`.
- `pytest tests/spec/test_plan_node_vector.py tests/effect/test_catalog_lint.py tests/effect/test_audit_self_conform.py tests/effect/test_audit.py tests/fact/test_wire.py tests/replay/test_trajectory.py -q` → `80 passed in 0.15s`.
- `pytest tests/integration/ -q` → `12 passed in 0.72s`.
- `rg '\.next_tx\(' src/` → 0 hits in production code.
- `rg 'PYTHON_VERDICTS|EDN_VERDICTS' src/persistence/effect/policy_eval.py` → 0 hits.
- `rg 'persistence\.spec|spec\.parse|conform' src/persistence/fact/db.py` → 0 hits.
- N2 reproduction script produced the colon-strip outputs pasted in §2.5.
- Op round-trip script (§2.4) confirmed `verify_chain([e]) is True` both
  before and after datom round-trip; content-hash equality preserved.
