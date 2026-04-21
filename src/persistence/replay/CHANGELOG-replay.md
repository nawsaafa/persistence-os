# persistence.replay — changelog

## [0.1.0] — 2026-04-20

Initial implementation of Module 4 (counterfactual trajectory + replay engine),
per `docs/agent4-replay-spec.md`. Phase 1 Workstream D of conductor track
`persistence-os-foundation_20260420`.

### Added

- `Fact` / `Trajectory` dataclasses matching spec §1 (9 Fact fields,
  14 Trajectory fields including lineage + outcome + tags + cache).
- JSON round-trip serialization (sets wire-serialize as sorted lists).
- Content-addressed `trajectory_hash` — ignores id/lineage/cache so a
  NO-OP counterfactual shares the factual's content hash.
- `EffectHandler` record/replay handler:
  - canonical args-hash cache keys (key-order-insensitive);
  - non-replayable ops (`:net/fetch`, `:tool/call`) always raise
    `ReplayCacheMiss` on miss — "must never re-execute" per spec §8;
  - `:llm/call` cache-miss with identical other-args but different
    `prompt_hash` raises `PromptHashMismatch` — loud error for prompt
    template drift;
  - cacheable-op miss with non-empty cache simulates (CAMO pattern:
    cached effects pinned, new-territory simulated);
  - empty-cache replay raises — likely operator error.
- `record(obs_stream, seeds, agent_step_fn, apply_action_fn, initial_state)`
  — steps the agent, persists facts + cache + call log.
- `replay(traj, interventions, extra_obs=None, agent_step_fn, apply_action_fn)`
  — aligned-randomness counterfactual supporting 4 intervention fields
  (`:action`, `:obs`, `:llm-out`, `:state`); refuses `:running` trajectories;
  marks `extrapolated=True` when extended past the factual window.
- `compare(a, b)` — pnl delta, divergence step, KL divergence on LLM
  logprobs, ITE-per-step placeholder list.
- `extract_dpo_pair(factual, counterfactual, threshold)` — emits
  `{prompt, chosen, rejected, margin}` iff prefix matches and outcome
  delta exceeds threshold. Picks winner by `outcome["pnl"]`.
- `gen_regression_test(trajectory, assertion, test_name)` — emits a
  self-contained pytest module that loads the inlined trajectory JSON
  and asserts the supplied predicate. Generated file is parseable,
  executable, and fails loudly on predicate mismatch.
- `persistence.replay.demo` — CLI module reproducing spec §7 (records a
  4-step toy trading trajectory, replays with `action=wait` at step 1,
  prints factual/counterfactual/comparison).

### Integration points (pending sibling modules)

- **Module 2 (persistence.effect)**: `EffectHandler` is the bottom of the
  replay handler stack described in agent3-effect-spec §6. When the
  Effect module lands, `record_handler_stack(effect_chain + [replay_handler])`
  becomes the standard composition. Drift-detection semantics already
  aligned with the spec's `raw-deny` layer.
- **Module 1 (persistence.fact)**: `Trajectory.facts` map 1:1 to effect
  audit datoms; a future adapter can emit datoms from `handler.calls`
  using the 8-tuple schema (spec §1 in agent1-fact-spec).

### Tests (47 total)

- `tests/replay/test_trajectory.py` — 7 tests (dataclass, JSON, hash).
- `tests/replay/test_effect_handler.py` — 9 tests (record/replay modes,
  canonical hashing, external-API guards, drift detection).
- `tests/replay/test_determinism.py` — 4 tests (NO-OP byte-identity,
  same-seed reproducibility, different-seed divergence, per-domain seed
  independence).
- `tests/replay/test_replay.py` — 11 tests (prefix/intervention/suffix,
  status guards, extrapolation, all 4 intervention fields, arg validation).
- `tests/replay/test_compare.py` — 5 tests.
- `tests/replay/test_dpo.py` — 4 tests.
- `tests/replay/test_regression_gen.py` — 3 tests (parses, runs-green,
  fails-on-wrong-assertion).
- `tests/replay/test_demo.py` — 1 test (demo prints three labelled lines).
- `tests/replay/test_e2e.py` — 2 tests (DPO flow end-to-end, regression
  test generation end-to-end).

### Design decisions

- **Aligned randomness via per-domain rngs.** Each of `:llm`, `:tool`,
  `:env` gets its own `random.Random` seeded independently. The
  `_advance_rngs_to_match` helper consumes the same draws the agent
  would have taken, so prefix-copy preserves downstream seed alignment.
  Test `test_seeds_are_per_domain_independent` proves no cross-domain leak.
- **Prompt-hash drift detection at handler level.** The handler walks its
  structured call log (not just the cache dict) on miss, comparing
  other-args: genuine new prompts simulate, but drifted templates raise.
- **Content hash ignores lineage.** A counterfactual whose facts + outcome
  match byte-for-byte (NO-OP intervention) shares the factual's
  `trajectory_hash` even though `parent_id` / `branch_point` /
  `intervention` / `status` differ — they describe the lineage, not the
  content.
- **`replay()` demands `agent_step_fn` + `apply_action_fn` explicitly.**
  Earlier draft defaulted to demo functions, which caused prompt-hash
  false-positives when tests used a different agent. Explicit is better
  than implicit here.
- **Extrapolation window switches handler to record mode.** Past the
  factual observation horizon, effects are *simulated* rather than
  *replayed* — matching spec §8 ("mark counterfactuals extending past
  observation window as `:extrapolated`").

### Known constraints

- The toy agent in `demo.py` does not include `state.position` in the
  prompt, so if you extend it to state-dependent behaviour you may get
  stale cache hits in counterfactual suffixes. The module itself does
  not mandate a prompt schema — that is the agent's responsibility.
- `ite_per_step` in `compare()` is a stub (list of Nones per step).
  AgenTracer-style ITE computation requires re-replaying per step, which
  is straightforward to add but deferred to the first real consumer.
