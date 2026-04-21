# W-rigor — ARIS Round 1 Fix Pass

**Branch:** `W-rigor`
**Worktree:** `/Users/nawfalsaadi/Projects/persistence-os/.claude/worktrees/W-rigor`
**Scope:** R2 F2, R2 F3, R2 F4, R2 F5, R1 F3 (test-rigor + invariant gaps).
**Out of scope (owned by siblings):**
- R2 F1 (HAMT / Prop 1) — deferred to W-paper (paper softening) / Phase 2.
- Datom/audit/trajectory/plan-node shape unification — W-boundary.
- replay ↔ effect wiring, `Mem0Interceptor.add` kwargs, `_tx_counter` global — W-integration.
- Paper corrections — W-paper.

## Test count

| Phase | Count |
|---|---:|
| Baseline (main) | **356 passed** |
| After W-rigor | **380 passed** |
| Delta | **+24 tests** |

Final pytest output:

```
380 passed in 1.21s
```

## Commits (one per finding)

```
007a603 fix(fact): guard DB.transact against retroactive negative intervals (R1 F3)
dbc4e70 fix(src): eliminate wall-clock/rng calls; add lint + injection seams (R2 F5)
c09588e fix(effect): scope Runtime._masks via per-instance ContextVar (R2 F4)
6eebcde fix(replay): fail fast on out-of-range step + empty trajectory (R2 F3)
b3a1b2a test(effect/audit): cover deletion/reorder/truncation of Merkle chain (R2 F2)
```

## Findings addressed

### R2 F2 — Audit chain tamper tests (MAJOR, INVARIANT)
**Commit:** `b3a1b2a` — test-only (`verify_chain` already correct; added adversarial coverage).

Tests added in `tests/effect/test_audit.py`:
- `test_deleting_an_audit_entry_breaks_the_chain`
- `test_reordering_audit_entries_breaks_the_chain`
- `test_truncating_audit_entries_from_tail_preserves_chain` (design pin — a truncated-but-intact prefix still verifies; length checks live above this layer)

### R2 F3 — Replay multi-step / out-of-range / empty-trajectory (MAJOR, EDGE)
**Commit:** `6eebcde` — prod fix + tests.

Production: `src/persistence/replay/engine.py::replay()` now:
- Raises `ValueError("empty trajectory")` when `traj.facts == []`.
- Raises `ValueError("... out of range ...")` when any intervention's `step` is `< 0` or `> max_step`.

Tests added in `tests/replay/test_replay.py`:
- `test_multi_step_simultaneous_interventions_produce_consistent_hash` — two interventions at different steps both land AND hash is deterministic across two calls.
- `test_replay_with_step_greater_than_trajectory_length_raises`
- `test_replay_with_negative_step_raises`
- `test_empty_trajectory_replay_raises`

### R2 F4 — ContextVar isolation on shared Runtime (MAJOR, CONCURRENCY — REAL BUG)
**Commit:** `c09588e` — prod fix + tests.

Production: `src/persistence/effect/runtime.py::Runtime`:
- Converted from `@dataclass` to explicit `__init__` so each instance owns a unique `ContextVar[tuple[frozenset[str], ...]]` (key `persistence_effect_runtime_masks_<id>`).
- `_masks: list[set[str]]` attribute removed; replaced by `_mask_var.get()` on read and `_push_mask`/`_pop_mask` (ContextVar `set`/`reset` token dance).
- `mask()` context manager now uses the push/pop API, making it Task-local under asyncio.

Tests added in new file `tests/effect/test_runtime_concurrency.py`:
- `test_contextvar_isolates_runtime_state_across_asyncio_tasks` — 10 concurrent tasks sharing one Runtime (5 masked + 5 unmasked); both asserts the unmasked group all hit audit exactly once AND no masked task triggers audit. **RED before fix, GREEN after.**
- `test_mask_scope_does_not_bleed_after_task_completes` — a task that awaits mid-mask does not perturb a second task's dispatch.

### R2 F5 — Wall-clock / rng ban: fixes + lint (MAJOR, INVARIANT)
**Commit:** `dbc4e70` — prod fix + lint test + injection tests.

Per the task brief, W-integration is aligning op namespaces to leading-colon (`:sys/now`, `:sys/random`). Until that wiring lands, the modules that need a clock/rng accept an injectable parameter with a sensible default. Violations fixed:

| Before | After | Seam |
|---|---|---|
| `src/persistence/fact/db.py:38` — `_now_utc()` used `datetime.now` | Removed; `DB(store, clock=ClockFn)` injected. Sole authorised wall-clock call is `_system_clock()` annotated `# noqa: wall-clock`. `branch` / `transact` return inherit the parent's clock. | ClockFn callable |
| `src/persistence/fact/interceptors/mem0_adapter.py:65,97` — `datetime.now` default for `valid_from` | `Mem0Interceptor(..., clock=ClockFn)` with precedence `arg > db._clock > _system_clock` | ClockFn callable |
| `src/persistence/spec/_canonical.py` (11 lines of `random.{random,randint,choice,choices}`) | Module-local `_rng = random.Random()`; new `set_generator_seed(int \| None)` helper. | Seedable rng |
| `src/persistence/replay/trajectory.py:58,113` — `uuid.uuid4()` on `Trajectory.id` | `# noqa: wall-clock` with justification: trajectory id is a fresh primary key, excluded from `trajectory_hash` by `_HASH_IGNORE_FIELDS`, never required to be replayable. | N/A |

Lint added: `tests/test_wall_clock_ban.py` — AST scan across `src/persistence/` for `time.time`, `datetime.now/utcnow`, `random.*`, `uuid.uuid4`. Allows only `effect/handlers/{clock,raw,retry}.py` + `demo.py` files. Recognises `# noqa: wall-clock`. Fails with a sorted violation list so new drift is loud.

Tests added in `tests/fact/test_db.py`:
- `TestClockInjection::test_injected_clock_is_used_for_tx_time` (memory + sqlite)
- `TestClockInjection::test_injected_clock_survives_transact_return`
- `TestClockInjection::test_injected_clock_survives_branch`

### R1 F3 — DB.transact retroactive valid-to guard (MAJOR)
**Commit:** `007a603` — prod fix + tests.

Production: `src/persistence/fact/db.py`:
- New `RetroactiveCorrectionError(ValueError)` exported from `persistence.fact.db`.
- `transact(...)` now accepts `force_retroactive: bool = False` (kw-only).
- When `new.valid_from < prior.valid_from`:
  - Without opt-in: raises `RetroactiveCorrectionError` with a descriptive message (shows both dates + mentions opt-in).
  - With `force_retroactive=True`: the companion retract uses `valid_from=new.valid_from, valid_to=prior.valid_from` so the interval is non-negative and semantically invalidates the prior from the corrected effective date onward (agent1-fact-spec §0).
- Equal `valid_from` is explicitly allowed (zero-length interval is not negative).

Tests added in `tests/fact/test_db.py::TestTransact`:
- `test_retroactive_correction_without_opt_in_raises` (memory + sqlite)
- `test_retroactive_correction_with_opt_in_produces_bounded_valid_to`
- `test_normal_future_correction_still_works` (regression pin)
- `test_retroactive_correction_at_same_valid_from_is_allowed`

## Files touched

Production (src):
- `src/persistence/effect/runtime.py` — ContextVar-scoped mask stack.
- `src/persistence/fact/db.py` — injectable clock, `RetroactiveCorrectionError`, retroactive guard.
- `src/persistence/fact/interceptors/mem0_adapter.py` — injectable clock.
- `src/persistence/replay/engine.py` — empty-trajectory + out-of-range step guards.
- `src/persistence/replay/trajectory.py` — `# noqa: wall-clock` annotations on uuid calls.
- `src/persistence/spec/_canonical.py` — module-local `_rng`, `set_generator_seed`.

Tests:
- `tests/effect/test_audit.py` (+3 tests)
- `tests/effect/test_runtime_concurrency.py` (NEW, +2 tests)
- `tests/replay/test_replay.py` (+4 tests)
- `tests/fact/test_db.py` (+7 tests: 3 clock-injection x 2 backends + 4 retroactive x 2 backends, 2 pure + 2 regression)
- `tests/test_wall_clock_ban.py` (NEW, +1 lint test)

## Coordination notes for merge

1. **W-integration** will rename effect ops to leading-colon (`:clock/now`, `:sys/random`, …). Once that lands, the `ClockFn` seam in `DB` and `Mem0Interceptor` is the intended substitution point for a `:sys/now`-driven clock handler — the plumbing is already in place.
2. **W-boundary** will align `audit_entry_to_datom`'s emission (currently `tx-time` is a float from `recorded_at`). The Merkle tamper tests added in this pass do not depend on that shape; they exercise `verify_chain` directly against in-memory `AuditEntry`s.
3. **W-paper** will soften Prop 1's HAMT claim. R2 F1 is explicitly deferred per the task brief — no work done here.
4. The new `RetroactiveCorrectionError` is a subclass of `ValueError`, so existing `except ValueError` call sites continue to work. The `force_retroactive` kwarg is kw-only and default `False`, so all existing callers retain their behavior.

## Deferred items

- **R2 F1 (CRITICAL — HAMT / Prop 1 untestable).** Owned by W-paper (paper softening) per the dispatch table. Not touched.
- **R2 F6–F16, R1 F4–F13 and so on** — not in this worker's scope.

## How to verify

```bash
cd /Users/nawfalsaadi/Projects/persistence-os/.claude/worktrees/W-rigor
.venv/bin/python -m pytest --tb=short
# → 380 passed
```

Specifically target this worker's additions:

```bash
.venv/bin/python -m pytest tests/effect/test_audit.py \
                           tests/effect/test_runtime_concurrency.py \
                           tests/replay/test_replay.py \
                           tests/fact/test_db.py \
                           tests/test_wall_clock_ban.py -v
```
