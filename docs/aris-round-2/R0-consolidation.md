# ARIS Round 2 — Consolidation

**Date:** 2026-04-21
**HEAD:** `a28d8f5` (main, 463 tests green)
**Round-2 gate:** min ≥ 8.0
**Verdict:** ✅ **PASS** — min 8.2. Proceeding to scoped Round 3 polish (single-worker), not another full parallel fix pass.

## Scorecard

| Reviewer | Round 1 | Round 2 | Target | Delta |
|---|---:|---:|---:|---:|
| R1 Correctness | 7.4 | **8.3** | 8.5 | +0.9 |
| R2 Rigor | 6.4 | **8.3** | 8.0 | +1.9 |
| R3 Composability | **4.5** | **8.6** | 8.5 | **+4.1** |
| R4 Research alignment | 6.5 | **8.2** | 8.0 | +1.7 |
| **Minimum** | **4.5** | **8.2** | 8.0 | **+3.7** |

R3 made the critical ladder jump. R1 missed its 8.5 target by 0.2 but passed the 8.0 gate.

## Fix-pass effectiveness — Round 1 findings remediated

| Theme | Status after Round 2 |
|---|---|
| Boundary incoherence (R1 F1–F2, F6, F13 · R3 F1, F3, F6) | **FIXED.** `fact/wire.py` + `effect/verdicts.py` provide adapters; `audit_entry_to_datom` emits conformant wire form; `Trajectory.to_edn`/`Fact.to_edn` conform; 33-name `effect.__all__`. |
| Replay ↔ Effect unpluggable (R1 F7 · R3 F2) | **FIXED.** `make_replay_handler → effect.Handler`; e2e byte-identical record→replay test passes; NON_REPLAYABLE_OPS triggers correctly; leading-colon op namespace unified everywhere. |
| Paper overclaims HAMT/ed25519/7-capabilities (R2 F1 · R4 F1, F2, F4, F7) | **FIXED.** Paper v0.2: Prop 1 honest (list-backed O(|D|) with constant-time logical op); ed25519 + 20-40ms figure deleted; 4-shipped/3-designed framing; Fig.1 split; §6 rescoped to Reproduction Plan. |
| E2E wire-up never tested (R3 F5, F7, F8, F9) | **FIXED.** `Mem0Interceptor` targets real `mem0ai 2.x` signature with strict fake; `effect.__all__` populated; `spec.conform` now has external callers in `fact/wire.py`; `pyproject.toml` has hypothesis + pytest-asyncio. |
| Rigor gaps (R2 F2, F3, F4, F5) | **FIXED.** Audit-chain deletion/reorder/truncation tests; multi-step replay + step-out-of-range + empty-trajectory; **real bug** ContextVar concurrency (asyncio interleave test); wall-clock AST lint. |
| Retroactive valid-to (R1 F3) | **FIXED** with `RetroactiveCorrectionError` + `force_retroactive` opt-in. |

## New findings introduced/surfaced in Round 2

### Convergent (≥ 2 reviewers)

- **SQLite transact is non-atomic — R1 N3 + R3 N1 [MAJOR, convergent].** `Store.next_tx()` + `append()` is TOCTOU under concurrency. R3 reproduced: 5 threads with a `Barrier` all allocate `tx=1`. GIL hides it under light load; Phase 2 STM (`persistence.txn`) will corrupt the log.
- **Plan/node spec still map-shaped — R1 F4/N4 + R3 carry-over [MAJOR].** `:persistence.plan/node` spec remains `keys(...)` form while `docs/agent2-plan-spec.md` §1 + §8 describe vector form `[:node-type {attrs} & children]`. Cost increased in Round 2: paper §4.7 now elevates spec-first plan-node as a methodology contribution, making the defect more visible to readers.
- **`audit_entry_to_datom` doesn't self-conform — R1 N1 + R3 N3 [MAJOR/MINOR].** `:persistence.effect/audit-entry` spec is an orphan: registered but no producer uses its shape. AuditEntry dataclass field names don't overlap with the spec's 14 keys. Conform lives only in `fact/wire.py`, asymmetric with `datom_to_wire`.
- **Op-name format invariants — R1 N2 + R3 N4 [MINOR].** Audit encoding hack `/ → .` is lossy if an op contains a literal dot. `AuditEntry.op` has no `__post_init__` format invariant — one test straggler at `tests/effect/test_public_surface.py:42` constructs `op="llm/call"` without leading colon.

### Single-reviewer, worth noting

- **R2 G1** — `test_wall_clock_ban.py` has no self-test that plants a violation.
- **R2 G2** — E2E "byte-identical trajectory" test sometimes hashes `facts=[]` (structurally vacuous); real check is value-level.
- **R2 G3** — Concurrency test is asyncio-only; threading isolation (WSGI/gunicorn) not regressed. Related to R1 N3 / R3 N1.
- **R2 G4** — Multi-step intervention test doesn't pin `branch_point == min(step)` or intervention list shape.
- **R3 F4 [DEFERRED]** — SQL migration header claims Postgres portability but uses `AUTOINCREMENT`. Either drop the claim or actually port.
- **R3 N2** — `wire.datom_to_wire ∘ wire_to_datom` is not identity on colon-prefixed `datom.a` or pre-keyworded `provenance["source"]`. Undocumented precondition.
- **R4 residual** — Abstract needs "for the NO-OP case" qualifier on "stronger than CAMO"; §4.2 rhetorically over-reaches on policy universality.
- **R4 benchmark risk** — §6.3 regulator-replay is the flagship novelty but has zero measured rows at submission. Camera-ready minimum: `Runtime.assert_universal_audit` (0.5d), per-step rng-state recording (3–5d), 50-traj synthetic generator + reconstruction + CC-BY-4.0 dataset (2wk), one measured p95 latency number (2h).

## Deferred from Round 2 fix pass (all reviewer-blessed deferrals)

- R1 F4 plan/node map→vector (now R1 N4, elevated)
- R1 F8 replay `_advance_rngs_to_match` assumes exactly-one LLM + one env draw per step
- R1 F10 `is_well_formed` doesn't consider mask interactions
- R1 F12 entity tie-breaker (docstring only)
- R3 F4 AUTOINCREMENT / Postgres portability

## Round 3 plan — scoped polish (NOT another full parallel fix round)

All four reviewers voted GO for Round 3 as a **single-worker polish pass**, not a four-worker parallel fix. Scope:

1. **P-concurrency** [from R1 N3 + R3 N1] — `SQLiteStore.allocate_and_append(datoms)` atomic under `BEGIN IMMEDIATE` transaction; stress test with 16 threads × 50 transacts under `threading.Barrier`; document the GIL-doesn't-save-you in module docstring.
2. **P-plan-node** [from R1 F4/N4] — `:persistence.plan/node` spec rewritten as vector form per agent2 §1. Touch only spec registry + tests; plan module itself is still Phase 2.
3. **P-audit-conform** [from R1 N1 + R3 N3] — `audit_entry_to_datom` + `Trajectory.to_edn` self-conform at the end. `:persistence.effect/audit-entry` either aligned with `AuditEntry` shape or unregistered if orphan.
4. **P-op-invariants** [from R1 N2 + R3 N4] — `AuditEntry.__post_init__` enforces leading-colon format on `op`; catalog lint forbids literal dot in op names; fix `test_public_surface.py:42` straggler.
5. **P-sql-portability** [from R3 F4] — either port the SQL to `SERIAL`/`GENERATED BY DEFAULT AS IDENTITY` (real Postgres) or strike the portability claim from the migration header comment.
6. **P-paper-tightening** [from R4 residual] — abstract gets "for the NO-OP case" qualifier on CAMO comparison; §4.2 policy-universality wording softened.
7. **P-rigor-polish** [from R2 G1–G4] — plant-and-catch self-test for wall-clock lint; non-empty-trajectory guard on the byte-identity assertion; shape pin on multi-step intervention; threading concurrency test paired with R3 N1's fix.

Estimated size: 1 worker, 1 session, ~4–6 hours of focused work.

## Round 3 target grades

- R1: ≥ 8.8
- R2: ≥ 8.5
- R3: ≥ 8.9
- R4: ≥ 8.5
- **min ≥ 8.5** for gate pass to Round 4

Round 4 (final) then targets min ≥ 9.0, which is NeSy-submittable with confidence.

## Reports

- `R1-correctness.md`
- `R2-rigor.md`
- `R3-composability.md`
- `R4-research.md`
- Fix-pass worker summaries: `W-boundary-summary.md`, `W-integration-summary.md`, `W-rigor-summary.md`, `W-paper-summary.md`
