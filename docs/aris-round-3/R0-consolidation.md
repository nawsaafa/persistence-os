# ARIS Round 3 — Consolidation

**Date:** 2026-04-21
**HEAD:** `045f4b4` (main, 520 tests green)
**Round-3 gate:** min ≥ 8.5
**Verdict:** ✅ **PASS** — min 8.6. Round 4 required to reach ≥ 9.0 NeSy-submittable target.

## Scorecard

| Reviewer | R1 | R2 | R3 | R3-Target | Delta (vs R2) |
|---|---:|---:|---:|---:|---:|
| R1 Correctness | 7.4 | 8.3 | **8.6** | 8.8 | +0.3 |
| R2 Rigor | 6.4 | 8.3 | **8.9** | 8.5 | +0.6 |
| R3 Composability | **4.5** | 8.6 | **8.9** | 8.9 | +0.3 |
| R4 Research | 6.5 | 8.2 | **8.6** | 8.5 | +0.4 |
| **min** | **4.5** | **8.2** | **8.6** | **8.5 PASS** | **+0.4** |

R3 composability arc: 4.5 → 8.6 → 8.9 — biggest ladder jump of any axis.

## Polish-pass effectiveness on R2 residuals

**Cleanly FIXED:**
- R2 N3 SQLite TOCTOU → P-concurrency (`allocate_and_append` under `BEGIN IMMEDIATE`; 16-thread Barrier stress test; R2 reproduced pre-fix race: 800 writes → 97 unique tx, 88 collision groups)
- R2 F4/N4 plan/node map-vs-vector → P-plan-node (vector form `[:tag {attrs} & children]` matches agent2 §1, handles empty children / tuple-form / `:case-in-choice` / 8-level recursion)
- R2 N2 op-name invariants → P-op-invariants (`AuditEntry.__post_init__` + catalog lint; at-most-one-`/` relaxation defensible given bare-keyword ops)
- R2 F4 AUTOINCREMENT → P-sql-portability (claim struck, TODO(phase-2) added)
- R2 G1-G3 rigor polish → P-rigor-polish (plant-and-catch lint self-test; value-level equality on cache/call_log/outcome stronger than hash; 20-thread Barrier stress)
- R4 residuals → P-paper-tightening (NO-OP qualifier + §4.2 softening landed verbatim, two clean hunks, zero collateral)

**PARTIAL (intentional or surfaced):**
- R2 G4 multi-step shape pin → pins B1 buggy behavior so suite stays green; R2 flagged as honesty-marker but wants `pytest.xfail(strict=False)` upgrade
- R2 F6 verdict trinity → `policy_eval.py:185-189` still uses bare `"deny"`/`"allow"` without `PYTHON_VERDICTS` validation
- R2 F8 spec.conform external callers → 5-of-6 producers now conform; `DB.transact` inputs still bypass spec at DB boundary

## New findings in Round 3 (NOT addressed by polish)

### Two MAJOR regressions introduced BY the polish (R1 N5 + N6) — must fix

- **R1 N5 [MAJOR]** — `Trajectory.to_edn()` self-conform rejects *every* counterfactual produced by `engine.replay(...)`. Engine writes bare-string-keyed interventions (`"step"`, `"field"`, `"new_value"`) while `:persistence.replay/intervention` spec requires keyword form. Reproduced with 3-line script — fails single AND multi intervention. **No existing test exercises `cf.to_edn()` on actual replay output.** The happy-path conform test constructs synthetic trajectories with pre-keywordised keys.
- **R1 N6 [MAJOR]** — `AuditEntry.to_edn()` rejects *every* production `handler_chain`. Bare-string handler names (`"audit"`, `"llm"`) aren't keywordified at wire boundary; only `principal` is. The one self-conform happy-path test pre-keywordises the chain, hiding the regression.

### Convergent findings across reviewers

- **Multi-step intervention audit-trail collapse** (B1; R3 classification: composability, not correctness) — `engine.py:164` assigns `intervention=copy.deepcopy(interventions[0])`. Replay loop still applies every intervention correctly; only `Trajectory.intervention` lineage is partial. Touches engine + `Trajectory` dataclass + spec + `to_edn`/`from_edn` + DPO readers. R1 notes DPO has zero references to `intervention` — fix is ~3 files not 5. Should bundle with R1 N5 since both are wire-boundary intervention shape defects.
- **wire.py identity loss still broken (R3 N2)** — `Datom(a=":project/wacc")` round-trips to `a="project/wacc"`; `provenance["source"]=":already-keyworded"` strips leading colon. P-op-invariants fixed `AuditEntry.op` but skipped `Datom.a` and `provenance["source"]`. R3 calls this the single biggest drag on the composability grade.
- **Paper drift from P-sql-portability (R4)** — SQL header now says "SQLite-only" (truthful) but paper §5.1 line 219 still claims migration "runs unmodified on SQLite 3.37+ and Postgres 14+". R3 made code honest, left paper inconsistent.

### Single-reviewer MINOR findings

- **R1 N7** — `keys()` open-map semantics silently pass stray non-schema keys (by design, worth flagging in Phase-2 regulator-replay).
- **R1 N8 / R3 N5** — `DB.transact` splits `allocate_and_append` + `mark_invalidated` across two SQL transactions. Not TOCTOU, but crash-recovery gap and observability anomaly for Phase-2 STM.
- **R3 N6** — `AuditEntry.to_edn()` exists but no `AuditEntry.from_edn()`. Phase-2 regulator-replay will need the inverse.
- **R3 N7** — `_PlanNodeVector._conform` recurses via `self.conform(child)` not via registry lookup (class-bound, not registry-polymorphic).
- **R2 G4 partial** — shape-pin locks B1 buggy behavior; no `xfail` marker so a future fix won't light up as XPASS.
- **R4 date bug** — paper §6.6 line 290 says "abstract submission (2026-06-16)"; should be 2026-06-09.
- **R4 paper silent** on P-plan-node vector form (missed homoiconicity contribution angle), P-concurrency, P-audit-conform — not overclaims, undersold.
- **R2 Prop 1 still 6/10** — no scaling regression guard; `time.monotonic`/`time.perf_counter` missing from `BANNED_CALLS`.

## Round 4 scope — "wire boundary reconciliation + paper polish"

Estimated total: **~2–3 hours** for one worker, mostly surgical.

### Must-fix (required to reach ≥ 9.0 min)

1. **W4-intervention-wire** — Fix `Trajectory.intervention` to be `list[dict]` (closes B1 + R1 N5 + G4 partial). Engine writes keyword-keyed interventions. Self-conform on `Trajectory.to_edn()` passes for real replay outputs. Suite has an end-to-end test that asserts `cf.to_edn()` conforms.
2. **W4-handler-chain-wire** — Keywordify `handler_chain` entries at the wire boundary in `AuditEntry.to_edn()` (closes R1 N6). Self-conform passes on production audit handlers, not just synthetic ones.
3. **W4-wire-identity** — Fix `wire.datom_to_wire ∘ wire_to_datom` to be identity on the extended domain (closes R3 N2). `Datom.__post_init__` normalises `.a` to start with `":"` + `provenance["source"]` same; `wire.py` round-trips losslessly on already-keyworded inputs.
4. **W4-paper-patch** — Strike SQL portability claim from §5.1 line 219; sync `:audit/entry` → `:persistence.effect/audit-entry` at §5.3 line 237; fix date bug at §6.6 line 290; add 3 new-contribution-angle sentences (vector homoiconicity, self-conform, atomic allocation); 2-line `verify_chain` formal proposition.
5. **W4-g4-xfail** — Add `pytest.xfail(strict=False, reason="B1")` marker paired with the W4-intervention-wire fix so the suite lights up XPASS once B1 lands.

### Nice-to-have (boost toward 9.5 floor)

6. `BANNED_CALLS` extends to `time.monotonic`, `time.perf_counter`.
7. `DB.transact` self-conforms inputs at the boundary (closes R2 F8 residual).
8. `AuditEntry.from_edn` inverse (closes R3 N6; needed for Phase-2 anyway).
9. Multi-line `# noqa: wall-clock` scan (R2 minor).
10. `verify_chain` formal proposition statement in §4.3.

### Out of scope (R4 / Phase 2)

- Prop 1 scaling regression guard — needs HAMT or explicit benchmark, Phase-2 work.
- `Runtime.assert_universal_audit` — half-day, nice-to-have but not blocking.
- `_PlanNodeVector` registry-polymorphic recursion — design decision for Phase-2 plan module.
- §6.3 regulator-replay dataset — flagship NeSy risk, camera-ready path already documented.

## Round 4 target grades

- R1 ≥ 9.0 (was 8.6; closing N5 + N6 = +0.3 to +0.4)
- R2 ≥ 9.0 (was 8.9; xfail marker + BANNED_CALLS extension + Prop 1 hedge = +0.1 to +0.2)
- R3 ≥ 9.2 (was 8.9; N2 + B1 + handler-chain wire = +0.3)
- R4 ≥ 9.0 (was 8.6; paper patch alone = +0.4)
- **min ≥ 9.0 → NeSy-submittable**

After Round 4, if min ≥ 9.0, we freeze Phase 1 and hand to Phase 2 workstreams (Memory Palace retrofit, Trader v2 cron, Plan module, Txn module).

## Reports

- `R1-correctness.md` · `R2-rigor.md` · `R3-composability.md` · `R4-research.md`
