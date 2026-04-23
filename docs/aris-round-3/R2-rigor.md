# ARIS Round 3 — Reviewer R2 — Test Rigor & Invariants

**Commit reviewed:** `045f4b4` (main, W-polish merged)
**Suite state:** 518 passed + 2 skipped (13 hypothesis-gated tests not collected in this sandbox; 507 run cleanly under the same pyproject without hypothesis; +57 net new tests since Round 2).
**Round 1 grade:** 6.4 / 10 — gate FAILED.
**Round 2 grade:** 8.3 / 10 — gate PASSED, four new rigor gaps flagged (G1–G4) plus one honesty watch item (B1).
**Round 3 grade:** **8.9 / 10** — gate PASSED (target ≥ 8.5). Go for Round 4.

## Summary

P-rigor-polish is the real thing, not a veneer. I read every test file end-to-end, walked the production symbols they target, and reproduced the pre-fix concurrency race locally to confirm the new test would catch it. G1 is closed with a factored `_scan_source_for_violations(src)` helper and 7 plant-and-catch tests that cover each banned call pattern AND the `noqa` opt-out AND the chain variant — the lint is no longer a regex-that-tests-a-regex. G2's byte-identity trajectory test now has a non-empty-payload precondition AND value-level equality on `cache`, `call_log`, and `outcome` — the real state carriers — so the downstream `trajectory_hash` comparison is no longer structurally vacuous. G3's threading variant is genuine: the second test spawns 20 threads (10 masked + 10 unmasked) under a `threading.Barrier` and asserts per-name `Counter` cardinality in the audit trace, not merely absence of crash. G4 pins both `cf.branch_point == min(intervention.step)` and the shape of `cf.intervention`, but the intervention shape assertion pins the *buggy* Phase-1 behaviour (see §3 — this is documented honestly in the test's inline comment and cross-referenced to W-polish B1).

P-concurrency is the real headline. I ran a probe against the pre-fix `next_tx() + append()` split with the exact stress shape of the new test (16 threads × 50 transacts, `threading.Barrier` release) and observed: **800 writes → 97 unique tx ids → 88 collision groups** (worst group saw tx=2 assigned to 15 threads). The post-fix `allocate_and_append` passes the same stress shape with 800 unique tx ids. The `BEGIN IMMEDIATE` seam genuinely closes the TOCTOU. Reproduction evidence in §1-G-concurrency.

One systemic rigor gap remains for Round 4, unchanged from Round 2: no benchmark or scaling test for the softened Prop 1 claim. The paper's honest retreat to "constant-time logical branch, O(|Δ|) materialisation" reads fine, but it is asserted by inspection only — `tests/fact/test_db.py::TestBranch` proves *isolation* (branch does not mutate parent store) and that is it. A future Phase-2 HAMT port will lose the regression guard that should pin the linear-in-delta claim. This is explicitly the "Round 4 target" gap.

## Per-module grades

| Module | Round 1 | Round 2 | Round 3 | Δ (R2→R3) | Notes |
|---|---:|---:|---:|---:|---|
| fact | 6.5 | 8.3 | **9.2** | +0.9 | P-concurrency atomic `allocate_and_append`, race-shape regression test that genuinely reproduces TOCTOU on the old code path |
| effect | 7.0 | 9.0 | **9.3** | +0.3 | P-audit-conform self-conform, P-op-invariants `__post_init__` + catalog lint, threading concurrency variant |
| spec | 6.0 | 7.2 | **8.3** | +1.1 | P-plan-node vector form with 14-row parametrised happy path + 6 rejections + 3-level AST; F7/F8/F9 still open (MINOR) |
| replay | 7.0 | 8.7 | **8.8** | +0.1 | G4 shape pin, but pins Phase-1 buggy singleton intervention shape — tracked as B1 for Round 4 |

## 1. R2 G-finding remediation table

| Finding | Status | Test evidence | Quality 1-10 |
|---|---|---|---:|
| **G1** — wall-clock lint has no self-test | **FIXED** | `tests/test_wall_clock_ban.py` factors `_scan_source_for_violations(src)` as the shared detector, then calls it from **7 plant-and-catch tests**: `test_lint_detects_planted_datetime_now`, `_time_time`, `_random_random`, `_uuid_uuid4`, `_chained_dt_datetime_now` (the hard `a.b.c` chain branch), `test_lint_does_not_flag_noqa_annotated_call` (opt-out regression), and `test_lint_plant_in_tempfile_gets_flagged` (real file on disk, end-to-end). Each plants a minimal source, parses it, and asserts the detector flags (or, for noqa, does not flag). A regression that silently stubs `_is_banned_call` to return None would fail 6 of these tests immediately. The chain variant specifically covers the `dt.datetime.now` branch that R2 F1 flagged as load-bearing and unverified. | 9 |
| **G2** — e2e "byte-identical trajectory" test hashes `facts=[]` (vacuous) | **FIXED** | `tests/integration/test_effect_replay_bridge.py::test_record_then_replay_byte_identical_trajectory` now contains three new assertions before the hash compare: `assert len(traj.cache) > 0` and `assert len(traj.call_log) > 0` (non-empty-payload guards — would red on a no-effect run), plus `assert replayed.cache == traj.cache`, `assert replayed.call_log == traj.call_log`, `assert replayed.outcome == traj.outcome` (the real load-bearing state equality, since `_HASH_IGNORE_FIELDS` still excludes `cache` and `call_log` from the canonical hash — confirmed in `trajectory.py:371–372`). The value-level check is strictly stronger than the hash check for this test shape: it catches drift in the exact fields that the hash ignores. The original hash assertion stays as the §4.5 Corollary headline. This is the single cleanest fix in the polish ladder. | 9 |
| **G3** — concurrency test is asyncio-only | **FIXED** | New `tests/effect/test_runtime_concurrency_threading.py` with two tests. `test_thread_starts_with_empty_mask_stack` (spawns a child thread from inside `mask("audit")` and asserts `"child" in audited` — the child does NOT inherit the parent's mask — while `"parent" not in audited` confirms parent's own mask still fires). `test_concurrent_threads_do_not_share_mask_state` runs 10 masked + 10 unmasked threads under a `threading.Barrier` (forces interleave inside the mask block), collects audit calls under a lock, then asserts `counts[uname] == 1` for every unmasked name and `mname not in audited` for every masked one. This is genuine isolation testing — it asserts the specific cardinality of audit hits per thread, not mere absence-of-crash. Pairs correctly with the SQLiteStore P-concurrency fix: one covers thread-local ContextVar, the other covers cross-thread store-level atomicity. | 9 |
| **G4** — multi-step intervention shape pin | **PARTIALLY FIXED** (see §3 — B1 honesty check) | `tests/replay/test_replay.py::test_multi_step_simultaneous_interventions_produce_consistent_hash` now asserts `cf_a.branch_point == min(i["step"] for i in interventions)` (the min-step correctness), `cf_b.branch_point == expected_branch` (deterministic across replays), and `cf_a.intervention == cf_b.intervention` (lineage field stable across replays). The `branch_point` pin is **solid and correct** — I traced `engine.py:157` and confirmed `branch_point = min(i["step"] for i in interventions)` is the actual implementation, so the test pins the right contract. The `cf.intervention` shape pin is `assert isinstance(cf_a.intervention, dict)` with an inline comment stating "Phase 1 stores only the first intervention". This pins the **buggy** Phase-1 behaviour (see B1) rather than asserting the correct `list[dict]` shape — deliberate, documented, but a latent trap. | 7 |
| **P-concurrency** (bonus rigor artifact) | **EXCELLENT** | `tests/fact/test_concurrent_transact.py` — 4 tests. The primary one is parametrised at `(16 threads, 50 per_thread)` under `threading.Barrier(16)`, sharing one `SQLiteStore` across threads. Asserts: all 800 datoms land, every tx id is unique, `max(tx) == 800`. I ran a probe against the pre-fix `next_tx() + append()` split path with this exact stress shape and observed **800 writes → 97 unique tx → 88 collision groups** (some tx ids hit by 14-15 threads). The test is NOT theoretical — the race is reliably reproducible on the split, and reliably absent on the atomic `allocate_and_append`. Companion tests: InMemoryStore symmetry (8×25), return-shape (returned datoms carry the allocated tx), and empty-iterable no-op (no tx burned). Meets the bar of "does it fail on the old code?" with observable evidence. | 10 |

**Summary:** Three of four R2 G-findings closed at quality 9. G4's shape pin at quality 7 is a deliberate, documented regression trap — R2 accepts it for this round because W-polish flagged it honestly (W-polish §"Surfaced bug"), but it must be cleared in Round 4. The P-concurrency bonus is the strongest rigor artifact in the whole fix ladder.

### G-concurrency probe evidence

Reproduction of the pre-fix race (`src/persistence/fact/store.py` pre-`b8ee0b5` split path, simulated by calling `store.next_tx()` then `store.append([d])` from worker threads):

```
total: 800; unique: 97; duplicates: 88
top: [(2, 15), (33, 14), (38, 14), (43, 14), (45, 14)]
```

Post-fix (`store.allocate_and_append([d])`): all 800 tx ids unique, `max(tx) == 800`, zero collisions. The test reliably RED-greens across the fix boundary. The `BEGIN IMMEDIATE` seam is genuinely load-bearing; the `threading.Lock` in InMemoryStore is the symmetric single-process guard. Confidence: high.

## 2. Proposition coverage update

### Prop 1 — Branch complexity

**Round 2 grade: 6 / 10. Round 3 grade: 6 / 10.** No change — no benchmark or scaling test was added in Round 3, and none was in scope for P-rigor-polish. The paper's softened `O(|Δ|)` claim is still assertion-by-inspection. Isolation is tested (`tests/fact/test_db.py::TestBranch::test_branch_does_not_mutate_original_db`, `::test_branch_is_isolated_counterfactual`, `::test_branch_provenance_marks_source`). Scaling is not. A Phase-2 HAMT migration has no regression guard.

This is the single biggest open rigor gap for Round 4. The fix is small: parametrise `test_branch_is_isolated_counterfactual` over log sizes `[10, 100, 1_000, 10_000]` datoms, time the branch call with `time.perf_counter()` (under `# noqa: wall-clock`), and assert the ratio of branch-time to log-size is bounded. Not a benchmark, a scaling regression guard. 1-hour task.

### Prop 2 — Well-formedness machine-check

**Round 2 grade: 7.5 / 10. Round 3 grade: 7.5 / 10.** No change — Round 3 polish did not touch `Runtime.is_well_formed` interactions with `mask`. The formal statement tests (`test_runtime.py:88-119`) still cover the two-op happy/sad case cleanly; the `_bankability_stack` integration test (`test_composition.py:151-157`) covers the realistic stack happy-path. What's still missing: `is_well_formed(catalog)` called **inside** a `mask(...)` block — a stack that is statically well-formed can become operationally ill-formed when all handlers for an op are masked, and that interaction is the load-bearing case for the §4.2 policy-universality rewording. Round 4 candidate, MINOR.

### Prop 3 — Byte-identical NO-OP replay

**Round 2 grade: 9 / 10. Round 3 grade: 9.5 / 10.** Strengthened. The standalone `tests/replay/test_determinism.py::test_noop_intervention_produces_byte_identical_trajectory` remains the sharpest field-level check (per-Fact equality on state/obs/llm_in/llm_out/action/tool_calls/random_draws across a 4-step trajectory). The integration bridge test now materially reinforces Prop 3 under `effect.Runtime`: the G2 fix adds value-level equality on `cache`, `call_log`, and `outcome` — three load-bearing fields that the canonical hash ignores — so a replay that produces the right trajectory hash but the wrong cache contents would now fail at the value-level assertions. The trajectory-hash check is no longer the only signal; the cache/call_log equality is the sharper probe for this test shape. Prop 3 is now the best-tested claim in the suite across two independent code paths (standalone record/replay and effect-runtime-wired record/replay).

The remaining 0.5 deduction: the bridge-test trajectories still have `facts=[]`, so the per-Fact byte equality that the standalone test exercises is not exercised through the effect-runtime path. A trajectory assembled by routing record() through an effect.Runtime would close this — still MINOR, Round 4 candidate.

## 3. B1 honesty check — is G4's pinned-buggy-shape assertion a regression trap?

**Verdict: NOT a trap — but only because W-polish documented it exhaustively. Barely above the line.**

The facts:

- `src/persistence/replay/engine.py:164` assigns `intervention=copy.deepcopy(interventions[0])` — only the first element of the passed-in list is stored on the resulting counterfactual `Trajectory`.
- `src/persistence/replay/trajectory.py:118` types the field as `intervention: Optional[dict] = None` — not `Optional[list[dict]]`.
- The `:persistence.replay/trajectory` spec treats `:trajectory/intervention` as a single `:persistence.replay/intervention`, not a `seq_of(...)`.
- The replay loop at `engine.py:191-200` DOES apply every intervention correctly via `interventions_by_step.get(k)`. The bug is lineage-only: the counterfactual's `intervention` field records only the first.

G4's test assertion:

```python
assert isinstance(cf_a.intervention, dict), (
    "Phase 1 stores only the first intervention; multi-intervention "
    "list storage on Trajectory is a Phase 2 upgrade tracked as "
    "a surfaced-bug item in ARIS Round 3 WORKER-SUMMARY"
)
first = interventions[0]
assert cf_a.intervention["step"] == first["step"]
assert cf_a.intervention["field"] == first["field"]
assert cf_a.intervention["new_value"] == first["new_value"]
```

This explicitly pins the buggy shape so the suite stays green. **This is honest test-first failure documentation**, not an undiscussed regression trap, because:

1. The inline docstring (test lines 214-220) explicitly names the bug's location (`engine.py:164`), the type constraint that caused it (`Optional[dict]` vs `Optional[list[dict]]`), and cross-references W-polish's `WORKER-SUMMARY` B1 entry.
2. `W-polish-summary.md` §"Surfaced bug — flagged for Round 4" documents B1 in full, with a scope call that fix touches 5 files, and an explicit deferral.
3. `docs/aris-round-2/R0-consolidation.md` did NOT contain B1 at Round 2 time (G4 surfaced it). The paper trail is: G4 flag in Round 2 → P-rigor-polish test added in Round 3 → bug revealed by the test → documented → deferred → R2 honesty check in Round 3 (this review).

**Where it is borderline:** if a future engineer fixes `engine.py:164` to `intervention=copy.deepcopy(interventions)` to actually store the list, the G4 test will RED immediately on the `isinstance(cf_a.intervention, dict)` line. They will have done the right thing and broken a test that asserted the wrong thing. If they only read the test assertion and not the docstring, they will "fix" the test by reverting their engine.py change. This is exactly the regression-trap pattern.

**Mitigation in place:** W-polish documents the intended fix boundary (5 files), so a future engineer fixing the bug has a shopping list and knows the test must change in tandem. The test docstring explicitly says "ARIS Round 3 WORKER-SUMMARY" as the pointer. This is enough if the engineer reads the docstring; it is not enough if they only read the assertion.

**Recommended Round 4 action:** When B1 is fixed, the G4 test's `isinstance(cf_a.intervention, dict)` becomes `isinstance(cf_a.intervention, list)` and the first/second element pins are asserted separately. The test should also gain a new assertion `assert cf_a.intervention == interventions` to pin full list equality. A `pytest.xfail` marker on the current assertion with an explicit `reason="B1 — see engine.py:164"` would make the trap visible in the pytest output, not just in a docstring; this is a nice-to-have polish for R4, not a blocker.

**Verdict:** Honest test-first failure, adequately documented, with one suggested hardening (xfail marker) for R4. Not a trap, but close enough to the line that a future engineer who skims might fall in. Grade: acceptable for Round 3, trip-wire for Round 4.

## 4. New rigor gaps in Round 3

### New G1 (R3-originated) — Wall-clock lint still misses `time.monotonic` / `time.perf_counter` [severity: MINOR] [module: tests] [class: COMPLETENESS]

**Location:** `tests/test_wall_clock_ban.py::BANNED_CALLS`

**What's missing:** `time.monotonic()` and `time.perf_counter()` are not in `BANNED_CALLS`. They are wall-clock-ish — they affect latency measurements (§6.3 regulator-replay p95 claim) and any `elapsed_ms` field on `AuditEntry`. The Round 2 R2 F5 meta-gap flagged this; P-rigor-polish closed the plant-and-catch self-test but did NOT extend the banned set.

**Why it bites:** §6.3 of the paper proposes measured p95 latency for regulator-replay. If that measurement is implemented against `time.monotonic()` inside `src/persistence/effect/` without a `# noqa` and without being added to the banned set, it will look like a determinism violation to a casual reader of the paper's §4.2 claim but it will not be caught by the lint. The fix is a two-line addition to `BANNED_CALLS`, plus either a noqa on the legitimate latency-measurement call site or a dedicated `elapsed_ms` handler.

**Severity:** MINOR — the lint is plant-and-catch-verified for what it covers, this is a scope extension, not a correctness regression. But closing it is cheap, and it completes the R2 F5 follow-through.

### New G2 (R3-originated) — Multi-line call `noqa` detection still single-line [severity: MINOR] [module: tests] [class: EDGE]

**Location:** `tests/test_wall_clock_ban.py::_is_banned_call`

**What's missing:** `line = source_lines[node.lineno - 1]` — the noqa check reads only the line where the call starts. A call split across multiple physical lines:

```python
datetime.now(
    timezone.utc,
)  # noqa: wall-clock
```

will not match `"noqa: wall-clock" in line` because `line` is the `datetime.now(` line, not the closing paren line. The lint will flag this as a violation even though the noqa is present.

**Why it bites:** A code reviewer who refactors a long `datetime.now(...)` call onto multiple lines for readability and adds the noqa on the closing paren will see the lint fail. They will either move the noqa (awkward) or add a different comment and move on. Not catastrophic, but a quiet footgun.

**Fix:** scan `source_lines[node.lineno-1 : node.end_lineno]` (both inclusive) instead of a single line, or use `ast.get_source_segment(src, node)` for the full call string.

**Severity:** MINOR — no known call site triggers this currently, but it is a latent failure mode of the lint. R4 nice-to-have.

### New G3 (R3-originated) — B1 shape pin lacks an xfail marker [severity: MINOR] [module: replay] [class: REGRESSION-TRAP]

**Location:** `tests/replay/test_replay.py::test_multi_step_simultaneous_interventions_produce_consistent_hash`

**What's missing:** The `isinstance(cf_a.intervention, dict)` assertion is a hard pin on the current-buggy-shape, with only docstring documentation. A future engineer who fixes `engine.py:164` will break this test and may not read the docstring. See §3 for the full analysis.

**Fix:** Add `@pytest.mark.xfail(strict=False, reason="B1 — engine.py:164 stores only first intervention, see W-polish-summary.md")` to a **separate sub-test** that asserts the correct shape:

```python
@pytest.mark.xfail(strict=False, reason="B1 pending")
def test_multi_step_intervention_is_stored_as_list_on_trajectory():
    ...
    assert isinstance(cf.intervention, list)
    assert cf.intervention == interventions
```

When B1 is fixed, the xfail flips to unexpected-pass (flagged), the engineer sees pytest's XPASS output, and they know to remove the `xfail` and update the companion pin in the existing test.

**Severity:** MINOR — the current state is adequately documented, but the xfail pattern is the canonical way to mark "test-first for a deferred bug" and it is strictly safer than a docstring pointer. R4 polish.

### New G4 (R3-originated) — No scaling regression test for Prop 1 softened claim [severity: MINOR] [module: fact] [class: PROP-COVERAGE]

See §2 Prop 1. Carry-over from Round 2 R2 F1, not a Round-3-introduced gap but re-raised because Round 3's polish scope did not address it and it is the single biggest open rigor gap. Round 4 should close this.

## 5. Overall rigor grade

**8.9 / 10** — passes the ≥8.5 gate cleanly, up from 8.3 in Round 2.

The grade reflects: (a) all four Round 2 G-findings addressed, three at quality 9, one (G4) at quality 7 with honest documentation; (b) the P-concurrency bonus is the sharpest piece of rigor in the entire Phase-1 suite — a test that I reproduced locally and confirmed reliably reds on the pre-fix split path and greens on the post-fix atomic path; (c) Prop 3 is now strengthened under the effect-runtime path via G2's value-level equality additions; (d) the catalog lint (`test_catalog_lint.py`) and the op-name `__post_init__` invariants close a broad class of silent-format-drift bugs that were never regressed. Deductions: Prop 1 scaling test still missing; Prop 2 `mask` interactions still untested; G4 pins the buggy shape with only docstring mitigation; two MINOR lint completeness gaps newly surfaced (G1 time.monotonic, G2 multi-line noqa).

Not 9.5 because: the Round 4 target is ≥ 9.0, and that requires closing at least Prop 1 scaling and adding the xfail marker on the B1 shape pin. Both are small-effort fixes; neither was in scope for Round 3's single-worker polish pass, which is why they weren't done. Scope was respected; ceiling wasn't reached.

## 6. Go / no-go for Round 4

**GO for Round 4.**

All four Round 2 G-findings are either closed or closed-with-documented-honesty. The P-concurrency stress test is the single strongest rigor addition in the entire Phase-1 cycle — it is the kind of test that a reviewer should want to see on every concurrency-sensitive Phase-2 feature (STM, distributed log, Postgres write-through). The catalog lint + op-name invariants close a silent-format-drift class that Round 1 and Round 2 both flagged. The paper's honest softening (Prop 1, ed25519, seven capabilities) has held through Round 3 without re-overclaiming. 518 tests green on `045f4b4`; no flakes observed in five local reruns of the concurrency tests.

**Round 4 target: rigor ≥ 9.0.** The four new MINOR findings above are the exact backlog. All are small-effort (1-hour-each at the fix-pass level), all are orthogonal to each other, and none require a redesign. The B1 bug itself is the one larger-than-polish item — R2's recommendation is to either fix it properly in Round 4 (touching engine.py + trajectory.py + EDN round-trip + spec + any DPO code that reads `cf.intervention`) OR add the xfail marker and push the real fix to Phase 2 with a tracked issue. Both are legitimate; W-polish's "flagged for Round 4" note implies the former is preferred.

**Suggested Round 4 additions (all MINOR):**

1. **Prop 1 scaling regression guard** — parametrise `test_branch_is_isolated_counterfactual` over log sizes `[10, 100, 1_000, 10_000]`, assert `branch_time / log_size` bounded. Closes R2 F1 residual. ~1h.
2. **Prop 2 mask-interaction test** — `test_is_well_formed_under_mask_is_not_automatic` — call `rt.is_well_formed(catalog)` from outside `mask(...)` (True) then from inside (False or still True, whichever matches the design decision). Closes R2 F12 tail. ~30min.
3. **Wall-clock lint extensions** — add `time.monotonic`, `time.perf_counter` to `BANNED_CALLS`; widen noqa scan to `lineno..end_lineno`. Closes New G1 + G2. ~30min.
4. **B1 honest fix OR xfail marker** — either fix `engine.py:164` to store the full list (and update trajectory dataclass, spec, EDN round-trip) OR add the xfail companion test per §4 New G3. Closes New G3. ~4h for the real fix, ~15min for xfail.
5. **Bridge test with non-empty facts** — extend `test_effect_replay_bridge.py` with a second scenario that builds a proper 4-fact Trajectory through effect.Runtime, so Prop 3's field-level byte-identity is exercised through the runtime path. Closes R2 F2 residual. ~2h.

None of these block Round 4 from beginning; all five combined are one focused afternoon of work and would put R2 comfortably at 9.3–9.5.

**Net:** Round 3's scoped polish pass delivered what it promised. Proceed to Round 4 — the final round — with confidence.
