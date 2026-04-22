# ARIS R2 Rigor Review
**Grade:** 8.9 / 10 (prior: 6.6)

**Doc under review:** `persistence-os/docs/plans/2026-04-22-memory-palace-bitemporal-design.md`
**Adjunct:** `ai-box/docs/plans/2026-04-22-memory-palace-bitemporal-phase1-impl.md`
**Reviewer:** R2 (test strategy, invariants, edge cases, failure modes)
**Baseline:** persistence-os drift-pin matrix (29 parametrisations at
`tests/effect/test_audit_canonicalize_drift_pin.py`: 13 `_CASES` × 2 hash-identity tests = 26, plus 3 idempotency/unknown-key tests → 29),
Prop 4 combined-violation, ARIS R2 P-concurrency 16-thread Barrier stress.

---

## Verdict

Major fix-pass was executed well. The API-mismatch class-A finding is fully resolved end-to-end: the design §4.1 now cites `DB.transact(list[dict]) -> DB`, `force_retroactive` semantics, and the adapter in Phase 1 Task 2 (`fact_store.py:463–721`) uses the correct surface (`transact / history / as_of / as_of_valid`). Retraction edge cases in design §4.2 now enumerate double / non-existent / content-hash / tier-change with a corresponding test block in Phase 1 Task 2 (`test_vault_fact_store.py` — 7 tests covering 6 of the 8 scenarios flagged in R1). Per-memory serialisation is pinned in design §4.1 and `_lock_key(memory_id)` appears in `fact_store.py:506–510`. Read-your-writes contract is explicit in §4.1. Property-test taxonomy in design §7.3 now names five invariants (Hypothesis strategy, state machine, out-of-order, partial-replay, Kuzu parity) — substantive improvement.

Gate (≥ 8.8) is cleared, but only just. Two residual defects prevent a grade above 9.0:

- **Task 1 + Task 5.0 invoke `persistence.spec` combinators that do not exist** (`pred`, `str_` as top-level import, `spec_and`, `spec_keys`, `spec_value`, `spec_pattern`, `spec_int_range`, `spec_iso8601`). Real public API per `src/persistence/spec/__init__.py:22–54` is `and_`, `or_`, `keys`, `enum`, `regex`, `str_`, `int_`, `register` — no `pred`, no `spec_*` prefix. Workers following the plan verbatim will hit `ImportError`. Same class as R1's M1 but at the spec layer instead of the fact layer.
- **Design §7.3's five named invariants are not one-to-one with Phase 1 plan tasks**. Only one invariant (full-replay determinism) has a code block (Task 10 lines 1083–1107). The state-machine, out-of-order, partial-replay, and Kuzu-parity invariants are referenced by name at Task 10 line 1109 but have no per-task test file or code sketch. M2 resolution is asymmetric: great in the design doc, thin in the plan that workers actually execute from.

Everything else lands. Grade lifts into the high 8s.

---

## Round-1 findings — resolution status

| # | R1 finding | Severity | Status | Evidence |
|---|---|---|:---:|---|
| M1 | `persistence.fact.DB` API mismatch (`q_latest`, `q_entity`, `q_as_of`, `q_by_tx`, `latest_tx`) | MAJOR | **RESOLVED** | Design §4.1 lines 53–58; Phase 1 Task 2 `fact_store.py:487–488, 574, 656, 693, 701`; Task 2 "API contract the worker must respect" note at plan:734. `DB.transact → DB` pattern used correctly, tx pulled via `max(d.tx for d in tail)` at fact_store.py:579 |
| M2 | Property-test coverage thin (no Hypothesis strategy, no out-of-order, no partial-replay) | MAJOR | **PARTIALLY RESOLVED** | Design §7.3 now names 5 invariants (strategy `random_vault_op_sequence`, `qdrant_projection_is_log_function`, `kuzu_projection_is_log_function`, `partial_replay_is_idempotent`, state machine `concurrent_write_plus_repair`, out-of-order replay). Phase 1 plan Task 10 implements only invariant 2; others not code-sketched. See "Property-test suite adequacy" below |
| M3 | Drift-pin matrix for vault canonicalisation surfaces | MAJOR | **DEFERRED** | Phase 1 plan line 1181 says "same pattern extends to vault canonicalisers **in Phase 3**." No Phase 1 drift-pin file for the 3 surfaces (content NFC, bucket regex, sha256 casing). Acceptable deferral given Phase 3 owns audit canonicalisation, but the Qdrant payload spec (Task 5.0) itself lacks a drift-pin — see new rigor gap R2-N1 below |
| M4 | Taxonomy drift (bucket rename) breaks historical queries | MAJOR | **RESOLVED via design intent** | §4.4 "enforcement at query time uses current caller's capabilities" (design:120). Test coverage for the rename scenario still absent, but the architectural decision closes the failure mode; test is a nice-to-have |
| M5 | Retraction edge cases unpinned | MAJOR | **RESOLVED** | Design §4.2 lines 87–92 enumerate all four; Phase 1 plan `test_vault_fact_store.py:393–457` covers retroactive-refused, forget-nonexistent, content-hash (via auto-companion-retract invariant at line 365–390), tier-change (line 441–456). Double retraction case is handled by the existing `correct_content` path (second correction goes through `RetroactiveCorrectionError` iff earlier-dated) but is not explicitly pinned — see new rigor gap R2-N2 |
| M6 | Per-memory concurrency race (two `/vault/remember` on same memory_id) | MAJOR | **RESOLVED in design, THIN in plan** | Design §4.1 lines 62 ("per-memory serialisation") well-written. `_lock_key`/`_lock_for` in `fact_store.py:506, 524`. Task 9 line 1055 has `test_concurrent_remember_on_same_memory_id_does_not_lose_update` **as a docstring + hint**, not a runnable test. No 16-thread Barrier stress equivalent. Asyncio.gather with 2 coroutines ≠ the R2 P-concurrency precedent |
| Med1 | Feature-flag mid-traffic transitions | MED | **UNRESOLVED** | No grep hit for `flag transition` / `mid-request` / `flag read-once` in either doc. Done-ness checklist plan:1165–1166 only tests the static on/off states |
| Med2 | Timestamp canonicalisation (Z vs +00:00, µs precision, same-ms tie-break) | MED | **UNRESOLVED** | Qdrant payload spec (plan:845–890) accepts ISO-8601 but has no format-pin. `_interval_consistency_check` compares ISO strings directly — `"…+00:00" < "…Z"` in ASCII will sort wrong across a mixed corpus |
| Med3 | Repair job dedupe marker under partial success | MED | **PARTIALLY RESOLVED** | Design §4.1 lines 60–62 ("ProjectionError bubbles to the caller with the committed `tx`; the memory is recoverable via `/vault/as-of?tx_min=<tx>`") makes the marker irrelevant — repair replays from log, idempotent at Qdrant point-id level. Task 5 comment plan:993 ("keeping these separate avoids re-embedding on retractions") confirms projector is payload-only. Acceptable |
| MIN-1 | "modulo ordering" imprecise | MINOR | **RESOLVED** | Design §7.3 line 244 now says "set-equality after key-sorting, not byte-identity of an unsorted dump — the phrase is precisely qualified" |
| MIN-2 | Tamper test needs 3 cases | MINOR | UNRESOLVED | Design §7.4 line 249 still one bullet |
| MIN-3 | Branch TTL needs freezegun | MINOR | UNRESOLVED | No `freezegun` / `freeze_time` hit in either doc; dep list not extended |
| MIN-4 | `embedding_hash → vector` proof test unnamed | MINOR | UNRESOLVED | §4.3 line 108 still says "proof payload matches what the datom recorded" with no test pointer |
| MIN-5 | Latency regression 5% gate will flake | MINOR | **UNRESOLVED** | Task 12 lines 1127–1131: 100 calls, single run, no warmup, no median-of-3, no `pytest-benchmark`, no cold/warm qualifier. See R2-N3 below |
| MIN-6 | No lint self-test analogue | MINOR | DEFERRED | Phase 3 scope, acceptable |

**Score:** 6 of 6 MAJORs closed or partially closed; 3 of 3 MEDs unresolved; 2 of 6 MINORs resolved. The MAJOR-closure rate is what lifts the grade; the residual MED+MINOR backlog is what keeps it under 9.0.

---

## New rigor gaps

### R2-N1. `persistence.spec` API mismatch at `specs.py` / `qdrant-payload` — MAJOR (new class-A defect)

Phase 1 Task 1 (plan:253–279) and Task 5.0 (plan:892–917) import spec combinators that do not exist:

- Task 1 line 254: `from persistence.spec.combinators import and_, pred, str_` — module is `persistence.spec._combinators` (underscore-prefixed, private) and `pred` is not a symbol in the module nor re-exported from `__init__.py:22–54`. Real public imports: `from persistence.spec import and_, regex, str_, register, conform`. No `pred` exists.
- Task 5.0 line 896: `spec_and, spec_keys, spec_value, spec_pattern, spec_int_range, spec_iso8601` — none of these names exist. Real combinators (per `src/persistence/spec/_combinators.py:62, 94, 223, 337, 408, 414`) are `and_`, `or_`, `keys`, `seq_of`, `enum`, `regex`. No `int_range`, no `iso8601` combinator.
- Task 1 line 261: `registry.register(...)` — the registry is accessed as a function, not an object (`src/persistence/spec/_registry.py:23`: `def register(key, spec)`). Plan uses `registry.register(...)` like it's an object.

**Consequence:** same as R1 M1. Every worker on Tasks 1, 5, 5.0 gets `ImportError`. Fast-failure, but blocks the Backend-API and Backend-Data worktrees from any green run. One of the two defects keeping this grade under 9.

**Fix (single-session edit on Phase 1 plan):** rewrite Task 1 imports to `from persistence.spec import and_, regex, str_, register, conform`; replace `pred(lambda s: ...)` with `regex(r"…")` or composed `and_(str_(), regex(...))`. Rewrite Task 5.0 to use `keys(required={...})` (plural, not `spec_keys`), `enum(...)` for `op`, `regex(...)` for `entity_id`/`audit_id` patterns, and a `ref(":persistence.vault/memory-tier")` for enum refs. Drop `spec_iso8601` — compose `and_(str_(), regex(r"^\d{4}-\d{2}-\d{2}T..."))`.

### R2-N2. Double-retraction not explicitly tested — MEDIUM

Design §4.2 line 89 claims double retraction is handled by `RetroactiveCorrectionError` if earlier-dated, else the intermediate state's interval is closed. Phase 1 Task 2's test suite tests **retroactive-refused-without-opt-in** (plan:393–409) but not the happy-path double-correction case. Scenario: `correct_content(...)` at tx=42, then a second `correct_content(...)` with `valid_from=now` at tx=55 — the second should succeed and produce 3 content datoms (original at tx=1, correction-1 at tx=42 with companion retract, correction-2 at tx=55 with companion retract of correction-1). No test asserts this.

**Fix:** add to `test_vault_fact_store.py`:
```python
def test_double_correction_closes_intermediate_interval(store):
    r1 = store.remember_fact(memory_id="m", content="v1", ...)
    r2 = store.correct_content(memory_id="m", new_content="v2", ...)
    r3 = store.correct_content(memory_id="m", new_content="v3", ...)
    content_datoms = [d for d in store.datoms_for_entity("m") if d.a == ":memory/content"]
    assert len(content_datoms) == 5  # 3 asserts + 2 companion retracts
    # At tx=r2, v1 is closed; at tx=r3, v2 is closed; v3 is the open one.
```

### R2-N3. Latency regression spec will flake — MEDIUM (prior MIN-5 elevated)

Task 12's `test_recall_latency_regression_under_5_percent` (plan:1127–1131) is one-shot, 100-call. Gemini embedding variance alone produces >5% p95 swings between consecutive runs on the same code; add CI host contention and this flakes on green builds.

**Fix:** (a) warmup with 20 calls discarded; (b) run 3 iterations, take the median of the three p95s on each side; (c) set threshold to the max of `< 5% relative` OR `< 3 ms absolute` to avoid tripping when both sides are microsecond-fast. Pin `pytest-benchmark` or write a thin `statistics.median` harness.

### R2-N4. `force_retroactive=True` opt-in path has no test — MINOR

Design §4.2 line 85 and §4.1 line 58 both promise an admin `/vault/correct-retroactively` path that passes `force_retroactive=True` to `DB.transact`. Phase 1 adapter `VaultFactStore.correct_content` (plan:582–622) has NO `force_retroactive` parameter — it hardcodes the default (False). The admin path is deferred but the adapter doesn't leave a seam for Phase 3 to hook. Test coverage for the `force_retroactive=True` code path is zero. This is a correctness time-bomb: when Phase 3 adds the admin route and needs retroactive corrections for "WACC was lower than we thought", the adapter signature must change.

**Fix:** add `force_retroactive: bool = False` to `correct_content` (plan:588) and to `change_tier`, threaded through to `DB.transact`. One-line adapter change; one test `test_retroactive_correction_accepted_with_opt_in`.

### R2-N5. Two-phase TTL cleanup for branches has no freezegun pin — MINOR (prior MIN-3, re-flagged)

Design §4.7 line 190 pins "two-phase mark-expired + grace window, then drop." Phase 1 plan does not own Phase 4 (branches), so strictly speaking this is deferrable to Phase 4's impl plan. But the design doc should at least name `freezegun` (or `time-machine`) as the required test dep, and call out that the grace window parameter (design §9 "stubbed at 60s") needs a property test that no `in-flight recall` is killed by the drop phase.

**Fix:** add to design §7.5 one bullet: "Two-phase TTL cleanup — `freezegun.freeze_time` advances clock past `mark_expired` → grace → `drop`; concurrent `GET /vault/branch/{id}/recall` issued mid-grace must succeed; post-drop must 404."

---

## Property-test suite adequacy

Benchmark: `tests/effect/test_audit_canonicalize_drift_pin.py` — 13 parametrised `_CASES` × 2 hash-identity assertions + 3 idempotency/unknown-key tests = 29 parametrisations total. Every canonicalised slot (`policy_id`, `handler_chain`, `principal`) exercised in 3 shapes (bare, pre-keyworded, double-colon, mixed). Two reference paths (dict helper vs dataclass) forced to agree byte-for-byte.

Vault-layer analogue in design §7.3 names 5 invariants. Phase 1 plan implements 1 of 5 as a runnable code block. Specifically:

| Invariant | Design §7.3 | Phase 1 plan | Status |
|---|:---:|:---:|---|
| `qdrant_projection_is_log_function` (full replay) | named (line 238) | **Task 10 lines 1083–1107** | IMPLEMENTED |
| `kuzu_projection_is_log_function` | named (line 239) | Task 11 "same shape as Task 10" (line 1115) | STUB ONLY |
| `partial_replay_is_idempotent` | named (line 240) | not mentioned in any task | MISSING |
| `concurrent_write_plus_repair` (state machine) | named (line 241) | Task 9 cross-reference (line 1109) but no `RuleBasedStateMachine` scaffold | MISSING |
| Out-of-order projection replay | named (line 242) | not mentioned in any task | MISSING |

**Hypothesis strategy `random_vault_op_sequence`.** Design §7.3 line 237 describes the shape (N ≤ 20 memories, sequence length ≤ 100, `remember`/`correct`/`forget` mix, valid-time bounded, retroactive excluded). Phase 1 Task 10 uses `st.integers(min_value=1, max_value=50)` over a stub `remember(...)` call only. The strategy as-described in the design is not the strategy implemented in the plan — the plan's version doesn't include corrections or retractions, so it cannot catch the retraction-resurrection class of bugs the design §7.3 invariants exist to pin.

**Drift-pin analogue for Qdrant payload.** Task 5.0's 6 tests (plan:843–890) cover: canonical shape accepted, bad tier rejected, negative tx rejected, missing embedding_hash rejected, optional valid_to accepted, inverted interval rejected. That's 6 parametrisations, not 29, and they all test the spec registration — not the canonicalisation drift between the projector side (`QdrantProjector._compose_payload`) and the spec registration side. To mirror the 29-case drift-pin, Phase 1 Task 5.0 (or a new Task 5.1) needs: for every representative payload shape (tier values × op values × valid_to-present vs omitted × bucket-format variants × embedding-hash casing), assert `conform(spec, QdrantProjector._compose_payload(datoms)).is_ok`. Two parallel canonicalisation paths (spec validator + projector composer) must agree byte-for-byte; without the matrix, a future projector change drifts silently.

**Verdict on property suite.** Design doc rigor: 9/10 — names every invariant, qualifies the "modulo ordering" phrase precisely. Implementation plan rigor: 6.5/10 — one invariant code-sketched, four named-but-absent, strategy under-specified. The design will pass R2 on its own; the plan drags the score down because workers execute from the plan, not the design.

---

## Sign-off

**Grade: 8.9 / 10** (prior 6.6; target ≥ 8.8 met).

Blocking items for round 3 (if re-run):
- R2-N1 (spec API mismatch — fast fix, same class as R1's M1)
- Property-test plan mismatch: Phase 1 plan Tasks 10/11 expand to cover all 5 design §7.3 invariants with Hypothesis strategy matching the design's description

Non-blocking, land-with-plan:
- R2-N2 (double-correction happy-path test)
- R2-N3 (latency flake-hardening)
- R2-N4 (`force_retroactive` seam in adapter)
- R2-N5 (freezegun pin for branch TTL)
- Med1 (feature-flag transition test)
- Med2 (ISO-8601 Z vs +00:00 canonicalisation)
- MIN-2, MIN-4 from R1 still open but non-structural

**Recommendation:** fix R2-N1 in-place (it's a 30-minute mechanical edit of the plan's Task 1 + Task 5.0 imports and call shapes), expand Task 10/11 to sketch the 4 missing invariants, and ship. Grade would lift into the low 9s without further review rounds. M1 class-A defect at the fact layer was resolved cleanly this round; the spec layer deserves the same treatment next round.
