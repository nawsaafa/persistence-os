# persistence.coder CHANGELOG

## Phase 2.3b — 2026-05-07 (MCTS branch escalation — `_escalate_branch` wired to `_searcher._escalate_branch_body`)

Phase 2.3b fills the `_escalate_branch` stub that 2.3a left with
`CoderStubNotImplemented`. When the LLM emits `kind="branch"` with a
`payload["seed_plan_edn"]` field, the new bridge validates the seed plan
through a four-stage ingestion pipeline (byte budget →
`parse(strict=False)` → 2.3a's strict semantic validator
re-used verbatim → optional `mcts_config` resolution with bool-numeric
rejection + LD6 bounds), constructs LLM-driven expander + evaluator
provider closures whose audit datoms route through
`substrate.effect.perform(":llm/call", ...)`, runs `s.plan.mcts_search`,
and executes the WINNER once via 2.3a's `_escalate_plan_body`. Losing
branches are explored as Plan-AST structures only — they NEVER dispatch
their leaves through the substrate handler stack.

Branch escalation is a TERMINAL mode-switch — `Coder.run()` returns
immediately after `_escalate_branch_body` via the same early-return
shape that 2.3a's `_escalate_plan` uses.

### Locked decisions (LD0–LD7) — design ARIS R1.1 PASS (mean 8.16 / min 8.0)

- **LD0 — SCOPE**: MCTS-search drives over Plan-AST structures; the
  winner executes ONCE via 2.3a's `_escalate_plan_body`. NO execution-
  time handler dispatch happens during the search itself. Falsifiability
  pin: the disk-marker test in `tests/coder/test_searcher_search.py::
  test_only_winner_writes_marker_to_disk` constructs a winner whose
  plan writes one file and a seed whose plan would write a different
  file; after `_escalate_branch_body` returns, the file-system contains
  exactly the winner's file and zero loser files.
- **LD1 — TRIGGER**: `_should_escalate_branch` returns
  `decision.kind == "branch"` only. The previous confidence-based half
  (`or decision.confidence < self.confidence_threshold`) was removed
  per the LD1 R0 codex finding: a confidence-below-threshold trigger
  could route a `kind="act"` payload (which carries `{op, args}`) into
  the branch escalator that expects branch-specific payload contract,
  creating a type/shape mismatch. Confidence-based escalation is
  deferred to Phase 2.4a once dogfood calibration data exists.
- **LD2 — PAYLOAD**: `payload["seed_plan_edn"]` is the canonical EDN
  string. Stage 3 semantic validation re-uses 2.3a's strict
  `validate_plan_for_2_3a` verbatim — no looser sibling validator was
  introduced (R0-fold N1: a looser validator would waste search budget
  on plans the winner-execution path can't accept). Stage 1 byte budget
  is `MAX_BRANCH_EDN_BYTES = MAX_PLAN_EDN_BYTES = 8192`. The expander's
  dry-run wrapper rejects proposals whose `apply_action` result would
  fail the same validator.
- **LD3 — EXPANDER + EVALUATOR**: `LLMExpander.provider` and
  `LLMJudgeEvaluator.provider` closures route through
  `substrate.effect.perform(":llm/call", ...)` with the actual
  `_session.py:134-141` request shape `{model, messages, tools}`. NO
  `system` or `response_format` keys (they are not part of the
  registered `:llm/call` dispatch). The system message is embedded as
  `messages[0]` with `role="system"`. JSON-mode is enforced via the
  `tools` arg. Two new tool-use schemas live in `_prompt.py`:
  `EMIT_BRANCH_PROPOSAL_TOOL_SCHEMA` (expander) and
  `EMIT_BRANCH_SCORE_TOOL_SCHEMA` (evaluator). Response parsing +
  softmax normalization is bridge-side post-processing in
  `_parse_expander_tool_response` and `_parse_evaluator_tool_response`.
  `LLMJudgeEvaluator.provider` signature is `Callable[[Node], float]`.
- **LD4 — FAILURE**: two-path `BranchSearchFailed` funnel.
  - Path-1 (bubble-out): `mcts_search` raises (`ExpanderContractError`
    or any raw expander provider exception; per R1-fold I1
    `PlanDepthExceeded` is engine-caught at `_populate_children`
    `phase="reject"` and does NOT bubble). The bridge wraps with
    try/except, emits one `:act/result(op=":mcts/search", error=...)`
    via `_emit_search_failure_act_result`, and raises
    `BranchSearchFailed` with `search_id=None`, `iter_count=None`,
    `terminated_by=None` (search never completed enough to have an id).
  - Path-2 (post-search detection): `mcts_search` returns cleanly but
    `MCTSResult.terminated_by == "all_evaluations_failed"`. The bridge
    inspects post-search and raises `BranchSearchFailed` with all four
    fields populated from the returned `MCTSResult`.
  - 2.3a's `PlanExecutionFailed` propagates UNCHANGED for winner-
    execution failures (LD4 invariant — `BranchSearchFailed` is
    reserved for SEARCH-layer failures only, not post-search execution).
- **LD5 — AUDIT**: the substrate engine emits `:mcts/iteration` (Merkle-
  chained, content-addressed) + `:mcts/search` start/end + `:llm/call`
  per dispatch (canonical chain via `:llm/call` with request_hash +
  response_hash + model + tokens, wrapped under `:mcts/iteration`
  provenance) + `:plan/done` provenance on the winner via 2.3a's
  emission path. NO new audit shapes were introduced. ZERO `:fork/*`
  datoms — `mcts_search` uses in-memory dict transposition at
  `_mcts.py:881-882`, NOT `s.txn.fork` (R0-fold B1).
- **LD6 — MCTS-CONFIG**: branch-bridge default
  `_BRANCH_BRIDGE_DEFAULT_CONFIG = MCTSConfig(max_iter=50, expander_k=4)`.
  Distinct from the engine default `MCTSConfig()` of `max_iter=200`.
  ALWAYS used unless `payload["mcts_config"]` overrides; bounds-check
  on overrides via `MAX_BRANCH_MAX_ITER=50` / `MAX_BRANCH_EXPANDER_K=4`.
  Closed-set narrowing: only `max_iter` and `expander_k` are
  payload-overridable; other `MCTSConfig` fields require subclassing
  (queued for 2.4a).
- **LD7 — SUBSTRATE-PREREQS**: NONE. All four SDK touchpoints
  (`s.plan.mcts_search`, `s.plan.judge`, `s.fact.transact`,
  `s.effect.perform`) ship today.

### Forced spec deviations

1. **FD1** — `mcts_search.started_at_ms` must be a positive int (NOT
   bool, NOT float; enforced at `_mcts.py:870-877`). The bridge uses
   `time.time_ns() // 1_000_000` at the call site; routing through
   `:sys/now` is queued for Phase 2.4a.
2. **FD2** — `MCTSConfig.__post_init__` rejects bool numerics via
   `isinstance(v, bool)` check FIRST. The bridge's `_resolve_mcts_config`
   coerces JSON `true`/`false` to `BranchPayloadValidation` BEFORE
   `MCTSConfig(...)` construction so the bridge's error contract is
   uniform (`BranchPayloadValidation` not `ValueError`).
3. **FD3** — `LLMExpander.provider` signature is `Callable[[Node, int],
   Sequence[tuple[Action, float]]]` — `(node, k)` returns up to k
   `(action, prior)` pairs. `propose(plan, *, k)` is keyword-only k
   on the engine side; the bridge passes positional.
4. **FD4** — `LLMJudgeEvaluator.provider` signature is
   `Callable[[Node], float]`; `evaluator.evaluate(plan)` is the public
   method. `0.0` is a finite valid score per `_is_finite_score` —
   accepting it for malformed-response cases does NOT trigger the
   engine's `phase="reject"` absorption path.
5. **FD5** (R0-fold B1) — `mcts_search` uses in-memory dict
   transposition at `_mcts.py:881-882`, NOT `s.txn.fork`. The G4 audit-
   shape assertion in `tests/coder/test_searcher_failure.py` explicitly
   requires zero `:fork/*` datoms. The phase-table description in the
   active Phase 2 design doc was updated to reflect this.
6. **FD6** — `_derive_search_id(initial_plan.id, config, started_at_ms)`
   is deterministic given `started_at_ms` but NOT cross-run byte-
   identical (wall-clock advances). Replay byte-identity (record →
   replay → same audit chain) holds; cross-run byte-identity is not a
   2.3b acceptance criterion.
7. **FD7** — `Action` ADT is closed-isinstance-strict at `_mcts.py:80-81`.
   `ComposeWithSkillAction` is rejected at the wrapper layer via BOTH
   a kind-string check AND a post-decode `isinstance` check (belt-and-
   braces — the LLM may mislabel the `kind` field; isinstance catches
   the case after decode). `ComposeWithSkillAction` is deferred to
   Phase 2.3c when the skill library lands.
8. **FD-T2.1** — EDN canonical form is bare-keyword + attrs-map:
   `[:seq {} [:fs/read {:path "x.txt"}]]`. The plan-author drafted
   tests with the quoted-keyword form `'[":seq" [":fs/read" ":path" "x.txt"]]'`
   which the parser rejects (parses `":seq"` as a string scalar, not
   a node-tag keyword). Caught at T2; canonical form used throughout.
9. **FD-T6.1** — `:fs/write` argument key is `bytes_or_text` per
   `src/persistence/effect/handlers/fs.py:60`, NOT `content`. Caught at
   T6; corrected throughout the test fixtures.
10. **FD-T7.1** — `uuid.uuid4().hex` and `dt.datetime.now(...)` in
    `_emit_search_failure_act_result` require `# noqa: wall-clock` per
    the ARIS R2 F5 wall-clock ban. Mirrors `_session.py:213` _act._record
    precedent for the same datom-emission pattern. Latency tracking
    (`latency_ms=0` placeholder) deferred to Phase 2.4a.
11. **FD-T8.1** — `Coder._escalate_branch` body uses a lazy
    `from persistence.coder._searcher import _escalate_branch_body`
    inside the function to avoid module-load circular import. Mirrors
    the 2.3a `_escalate_plan_body` lazy-import pattern.

### Test surface

- `tests/coder/test_searcher_errors.py` — 5 unit tests for
  `BranchPayloadValidation` + `BranchSearchFailed` shapes (T1, G1
  partial).
- `tests/coder/test_searcher_build.py` — 6 G1 tests for `_build_seed_plan`
  Stages 1+2 (T2).
- `tests/coder/test_searcher_validate.py` — 5 G2 tests for
  `_validate_seed_plan_for_2_3b` re-use of 2.3a strict validator (T2).
- `tests/coder/test_searcher_config.py` — 9 G7 tests for
  `_resolve_mcts_config` (5 from T3 + 4 from T3-fix closing falsifiability
  gaps: closed-set narrowing on real MCTSConfig field, non-Mapping
  reject, float reject, positivity).
- `tests/coder/test_searcher_expander.py` — 11 G3 tests for
  `_make_branch_expander` + `_softmax_normalize` (5 from T4 + 6 from
  T4-fix: empty `tool_calls`, `proposals=None` non-iterable, dry-run
  rejection of `:branch` leaf, `AddStepAction` happy path, parametrized
  malformed proposals).
- `tests/coder/test_searcher_evaluator.py` — 12 G3 tests for
  `_make_branch_evaluator` (3 from T5 + 9 from T5-fix: empty
  `tool_calls`, NaN/Inf guard, malformed-score parametrize, missing
  score field, non-Mapping `tool_calls[0]`).
- `tests/coder/test_searcher_search.py` — 10 tests covering BOTH the
  bridge-isolated layer (7: disk-marker invariant via mock, mcts_search
  kwargs, default vs override config, winner→`_escalate_plan_body`
  delegation, byte-budget + semantic short-circuit) AND end-to-end
  real-MCTS engine control (3: LD0 disk-marker under engine, ZERO
  `:fork/*` datoms, exactly-one `:plan/done`).
- `tests/coder/test_searcher_winner_failure.py` — 3 G5b tests for
  winner-execution failure inheriting 2.3a's `PlanExecutionFailed`
  unchanged.
- `tests/coder/test_searcher_failure.py` — 7 G5a tests for the two-path
  `BranchSearchFailed` funnel (Path-1 bubble-out + raw provider
  exception coverage; Path-2 post-search detection; both paths emit
  one `:act/result` with `op=':mcts/search'`; both paths emit zero
  `:plan/done`).
- `tests/coder/test_searcher_rejection.py` — 5 G6/G7 tests with
  `side_effect=AssertionError + assert_not_called()` spy double-
  protection (2.3a precedent).

Suite delta: 2454 → 2528 (+74 net; baseline at 2.3a merge was 2454).

### Test-fixture pattern

Most of the `_escalate_branch_body` tests mock
`coder.substrate.plan.mcts_search` to a `MagicMock` returning a hand-
crafted `MCTSResult`, which isolates BRIDGE behavior from ENGINE
behavior (engine correctness is `tests/plan/test_mcts.py`'s job —
substrate-side, separately tested).

**End-to-end real-MCTS G4 acceptance signal** (added at T9.1 to close
codex Impl R1 I1+I2): three tests in `test_searcher_search.py` run
`s.plan.mcts_search` end-to-end with a smart `make_callable_llm_handler`
call_fn that distinguishes expander vs evaluator by tool name. With
`max_iter=2, expander_k=2`, the search produces winner + loser child
plans; the engine selects the highest-q winner via PUCT; the bridge
executes ONLY the winner via `_escalate_plan_body`.

These tests pin three load-bearing invariants under real engine
control:
- **LD0** disk-marker single-execution invariant — only `winner.txt`
  exists post-search (zero `loser.txt`, zero `seed.txt`).
- **LD5** zero `:fork/*` datoms (R0-fold B1 — `mcts_search` uses
  in-memory dict transposition at `_mcts.py:881-882`, NOT
  `s.txn.fork`).
- **LD5** exactly one `:plan/done` datom (winner execution emits via
  2.3a's `_escalate_plan_body`; losers never reach it).

Total real-MCTS test runtime: ~50ms across the three tests.

The earlier "fixed-proposal LLM mock deadlocks on transposition dedup"
concern was specific to a fixture pattern that used `s.effect.perform =
scripted_fn` direct method assignment. The working pattern (above)
uses `make_callable_llm_handler` with the proper handler-stack
installation, which routes correctly through `Substrate._runtime`.

### Module layout

NEW:
- `src/persistence/coder/_searcher.py` (~660 LOC)
- `src/persistence/coder/_searcher_errors.py` (~50 LOC)
- 9 test files (~2000 LOC total)

MODIFIED:
- `src/persistence/coder/_session.py` (+24 / -7 — `_escalate_branch`
  body delegation + LD1 gate simplification)
- `src/persistence/coder/_prompt.py` (+~140 — 2 new tool-use schemas +
  2 new system prompts + `_BRANCH_EDN_GUIDANCE`)
- `src/persistence/coder/__init__.py` (+9 — `BranchPayloadValidation` +
  `BranchSearchFailed` re-exports)
- `tests/coder/test_session_stubs.py` (-1 stub-premise test, -1
  parametrize entry, +1 LD1-invariant test)
- `tests/coder/test_loop_e2e.py` (-1 stub-raise test)

## Phase 2.3a — 2026-05-06 (Plan AST escalation — `_escalate_plan` wired to `_planner._escalate_plan_body`)

Phase 2.3a fills the `_escalate_plan` stub that 2.2a left with
`CoderStubNotImplemented`. When the LLM emits `kind="plan"` with a
`payload["plan_edn"]` field, the new bridge validates the plan through a
three-stage ingestion pipeline (byte budget → `parse(strict=False)` →
semantic validator), registers all 10 coder substrate ops as plan-leaf
handlers on a fresh `Dispatcher`, and runs `s.plan.execute`. On success
every leaf's handler result is recorded as a `:act/result` datom in walk
order and one `:plan/done` provenance datom is emitted before returning.
On failure a partial `:act/result` trace is emitted for every completed
leaf, a failure-shaped `:act/result` is appended for the failing leaf
(recovered via a pre-walked id→Node map), and `PlanExecutionFailed` is
raised so the caller's contract mirrors `_act`'s error surface. Plan
execution is a TERMINAL mode-switch: `Coder.run()` returns immediately
after `_escalate_plan_body` via the early-return at `_session.py:79`.

### Locked decisions (LD1–LD8) — design ARIS R1 PASS (mean 8.14 / min 7.8)

- **LD0 — terminal mode-switch contract**: `_escalate_plan` is a one-call
  bridge; `_session.py` returns immediately after on either path.
- **LD1 — gate predicate closure**: `_should_escalate_plan` returns
  `decision.kind == "plan"` (keyword `"plan"`, NOT `:strategy/plan`
  attribute). See § 211 prose update below and design doc § 3.4.
- **LD2 — `:plan/done` provenance datom**: Written via `s.fact.transact`
  after all leaf `:act/result`s. NOT in `CANONICAL_AUDIT_WRAPPED_OPS`.
  Shape: `{plan_id, leaf_count, walk_order_node_ids}`. Presence implies
  status=ok (failure path raises before this datom is written).
- **LD3 — extended `:act/result` shape**: Plan-leaf results extend the 2.2a
  `{op, args_hash, result_summary, error, latency_ms}` envelope. The
  `result_summary` field packs plan-context keys `{plan_id, node_id, tag,
  handler_id}` alongside the handler's own result. Single datom shape so
  2.2b LD3 `_render_latest_action` works without branching. Handler output
  passes through `_summarize_result`'s 512-char cap before merging;
  plan-context keys WIN over same-named handler-returned keys
  (`{**base, **plan_context}` merge order).
- **LD4 — id→Node pre-walk**: `_collect_all_nodes` called BEFORE
  `s.plan.execute` to build the `id_to_node` map. Failure-path leaf
  attribution relies on this map; it cannot be built post-failure.
- **LD5 — 10-op handler registration**: All 10 coder substrate ops
  (`:fs/read`, `:fs/write`, `:fs/glob`, `:fs/grep`, `:shell/exec`,
  `:code/run`, `:git/diff`, `:git/status`, `:git/log`, `:git/commit`)
  registered as plan-leaf handlers on a fresh `Dispatcher` per invocation.
  One `_make_adapter(substrate, tag)` factory closes over the substrate;
  all adapters call `substrate.effect.perform(tag, dict(node.attrs))`.
- **LD6 — three-stage ingestion**: Stage 1 byte budget (`≤ 8192` bytes
  UTF-8); Stage 2 `parse(strict=False)` (FD2 — strict would reject all
  coder tags); Stage 3 semantic validator is the SOLE safety layer.
  Stage-1/2/3 violations surface as `PlanPayloadValidation`.
- **LD7 — `_planner_errors.py` exception module**: `PlanPayloadValidation`
  (invalid plan structure, `field=` context) and `PlanExecutionFailed`
  (runtime leaf failure, `plan_id=` + `failed_node_id=` + `cause=`).
- **LD8 — few-shot EDN prompt guidance**: `_PLAN_EDN_GUIDANCE` string
  added to `_prompt.py` and injected into `build_messages` for
  `kind="plan"` cycles. Disambiguates bare `:branch`/`:code` (banned
  plan-spec primitives) from `:code/run` (coder substrate op, allowed).

### Test gates

- **G1** — `tests/coder/test_planner_errors.py` + `test_planner_build.py`
  (6 tests). `PlanPayloadValidation` field/reason shape, missing `plan_edn`,
  wrong type, byte-budget exceeded, `ParseError` wrapping.
- **G2** — `tests/coder/test_planner_validate.py` (6 tests).
  `validate_plan_for_2_3a`: root-must-be-`:seq`, empty-root rejection,
  node-count budget, depth budget, banned-leaf rejection, unregistered-
  leaf rejection.
- **G3** — `tests/coder/test_planner_dispatcher.py` (4 + 4 = 8 tests).
  T0 factory (4): `s.plan.new_dispatcher()` curated factory contract.
  T3 expansion (4): `_register_substrate_handlers` registers all 10 tags,
  adapter calls `substrate.effect.perform` with correct tag + dict-coerced
  attrs, `_make_adapter` name derived from tag.
- **G4** — `tests/coder/test_planner_execute.py` (8 tests; 6 spec'd + 2
  T4-fix2 regression). Happy-path: `_escalate_plan_body` returns None,
  emits per-leaf `:act/result` + `:plan/done` in walk order, `plan_id` in
  both datoms, `walk_order_node_ids` in `:plan/done`. T4-fix2 regressions:
  `walk_order_node_ids` present (initial draft omitted field), correct
  `{**base, **plan_context}` key-precedence.
- **G5** — `tests/coder/test_planner_failure.py` (7 tests; 6 spec'd + 1
  T3-residual adapter property). Failure path: partial `:act/result` trace
  emitted for completed leaves, failure-shaped `:act/result` for failing
  leaf, `PlanExecutionFailed` raised, `_DEFAULT_HANDLER_ID` imported from
  `_execute.py` (not duplicated literal), `_summarize_result` 512-char cap
  applied on failure-leaf result.
- **G6** — `tests/coder/test_planner_rejection.py` (10 tests; 7 spec'd +
  2 T2-IMPORTANT closures + 1 T6-fix nested-empty-`:seq`). Interior `:par`
  rejected (`field="interior_tag"`) closing silent-skip hole. Nested empty
  `:seq` raises `field="plan_body"` not `field="leaf_tag"` (correct defect
  class). All 6 original validator edge cases pass.
- **G7 (collapsed at R0-fold I1)**: Drift-pin assertions for `:plan/done`
  outside `CANONICAL_AUDIT_WRAPPED_OPS` were absorbed into G4's `:plan/done`
  emission shape tests; no standalone gate needed.

Total: gross 48 new tests (G1-G6 = 6+6+8+8+7+10+3 T0/T1 standalone = 48),
T7 deleted 3 stub-assertion tests → **net +45**. Suite delta: 2408 → 2453.

### Forced spec deviations and implementation-time discoveries

1. **T2 FD1 — `Node.tag` is KEYWORD-FORM**: `_ast.py:__post_init__` requires
   `tag.startswith(":")`. `REGISTERED_LEAF_TAGS` and `_BANNED_LEAF_TAGS`
   store keyword-form (`:fs/read`, `:seq`). `leaf.tag` used DIRECTLY
   throughout — no `f":{tag}"` prepending. Impl plan incorrectly claimed bare
   form.
2. **T2 FD2 — `parse(strict=False)`**: `strict=True` rejects all coder ops
   (`:fs/read`, `:code/run`, `:git/*`, `:shell/exec`) because the plan-spec
   enum at `_parse.py:211` covers only the closed canonical set. Stage 3
   semantic validator (`validate_plan_for_2_3a`) is the SOLE safety layer for
   the 2.3a coder subset — it is strictly tighter than strict-mode for this
   surface (explicit 10-op allowlist + banned-leaf list + interior-tag `:seq`-
   only restriction).
3. **T2 FD3 — `walk()` returns `list[str]`**: Walk returns a list of node ID
   strings, not `list[Node]`. The `visitor` callback is used to accumulate
   `Node` objects; `_depth()` is a separate recursive helper.
4. **T2 FD4 — banned-tag pre-screen before `walk()`**: `_check_nodes_recursive`
   validates every node BEFORE `_collect_all_nodes` calls `walk()`. `walk()`
   raises `UnimplementedNodeKindError` for `:branch`/`:code` leaves
   (`_walk.py:49`); banned checks MUST precede the walk call.
5. **T3 FD — `dict(node.attrs)` coercion**: `node.attrs` is a frozen
   `Mapping`; `substrate.effect.perform` expects a plain `dict`. Each adapter
   wraps `node.attrs` with `dict()` at call time.
6. **T4 FD — `latency_ms=0` for plan leaves**: Per-leaf wall-clock timing
   deferred to 2.4a (`:sys/now` substrate op). All plan-leaf `:act/result`
   datoms emit `latency_ms=0` at 2.3a.
7. **T4-fix — `:plan/done` payload corrected**: Initial draft omitted
   `walk_order_node_ids` from the `:plan/done` payload. Corrected to
   `{plan_id, leaf_count, walk_order_node_ids}` per LD2; regression tests
   added (2 of G4's 8 tests).
8. **T4-fix — `_escalate_plan_body` signature aligned**: Initial impl had
   `(plan, dispatcher, *, substrate)` parameter drift vs spec `(coder,
   decision)`. Corrected; `coder.substrate` accessed via duck-typed attribute.
9. **T4-fix2 — `_summarize_leaf_result` pipes through `_summarize_result`**:
   LD3 contract from 2.2b requires handler output capped at 512 chars BEFORE
   merging plan-context. Without this, a `:fs/read` returning 60KB would land
   verbatim in `:act/result.v`. Merge order `{**base, **plan_context}` ensures
   plan-context keys win over handler-returned keys with the same name.
10. **T5-fix — `_DEFAULT_HANDLER_ID` imported from `_execute.py`**: Not
    duplicated as a literal. Coupling consistency with success-path
    `LeafResult.handler_id` — both paths use the same source of truth.
11. **T6 IMPORTANT closures — validator tightened (two coderabbit residuals)**:
    (a) Interior nodes restricted to `:seq` only: `[:seq {} [:par {} [:fs/read {}]]]`
    was previously accepted; `:par` is interior so leaf-only checks skipped it.
    Now raises `field="interior_tag"`, closing the silent-skip hole. (b) Nested
    empty `:seq` raises `field="plan_body"` (correct defect class) not
    `field="leaf_tag"` (wrong class). T6-fix added 1 regression test (total G6 = 10).
12. **T7 — EDN prompt disambiguation**: `_PLAN_EDN_GUIDANCE` clarifies bare
    `:branch`/`:code` (plan-spec primitives, banned in 2.3a) vs `:code/run`
    (coder substrate op, allowed). Without this, an LLM can confuse the two
    similarly named tags.

### New datoms

- **`:plan/done`** — one entity per `_escalate_plan_body` success. Written via
  `s.fact.transact`; NOT in `CANONICAL_AUDIT_WRAPPED_OPS` (same supporting-
  provenance shape as `:act/result` and `:llm/decision`). Shape:
  `{plan_id, leaf_count, walk_order_node_ids}`. Absence means failure (failure
  path raises before this write).
- **Extended `:act/result`** — plan-leaf shape has plan-context keys
  (`plan_id`, `node_id`, `tag`, `handler_id`) packed INSIDE `result_summary`
  alongside the handler's own result. Same outer envelope as 2.2a `_act` so
  `_render_latest_action` renders both paths without branching.

### W3 honest-rescope deferrals

- **Native traceback passthrough (v0.9.x)**: `PlanExecutionFailed.cause` carries
  the original exception but traceback is not surfaced verbatim to the prompt.
  The 512-char `_summarize_result` cap applies on the failure-leaf result.
  Full native traceback passthrough queued as v0.9.x.
- **Per-leaf wall-clock timing (2.4a)**: `latency_ms=0` for all plan leaves.
  `:sys/now` substrate op (2.4a) unlocks real per-leaf timing.
- **LLM EDN syntax-error rate (2.4a)**: How often the LLM generates malformed
  EDN in practice is unknown at 2.3a. Calibration data from 2.4a dogfood will
  inform whether `_PLAN_EDN_GUIDANCE` or parser feedback needs strengthening.
- **Multi-iteration plans (2.5+)**: Single plan execution per `run()` call at
  2.3a; multi-plan chaining and loop re-entry after plan completion deferred.
- **`:branch` interior tag (2.3b)**: MCTS branch escalation. Banned at 2.3a.
- **Skill library (2.3c)**: Named versioned `PlanAST`-backed skills. Deferred.

### Refs

- Design: `docs/plans/2026-04-30-phase-2-persistence-coder-design.md`
- New modules: `src/persistence/coder/_planner.py`,
  `src/persistence/coder/_planner_errors.py`
- New tests: `tests/coder/test_planner_build.py`,
  `tests/coder/test_planner_dispatcher.py`,
  `tests/coder/test_planner_errors.py`,
  `tests/coder/test_planner_execute.py`,
  `tests/coder/test_planner_failure.py`,
  `tests/coder/test_planner_rejection.py`,
  `tests/coder/test_planner_validate.py`
- Suite delta: 2408 → 2453 (+45 net; +48 gross − 3 T7 stub-test cleanup).

---

## Phase 2.2b — 2026-05-06 (LD3 latest-action prompt widening + `_act` coverage of `:code/run` + `:git/*`)

Phase 2.2b extends the coder's prompting and dispatch surface to the new
`:code/run` and `:git/{diff,status,log,commit}` ops shipped in
`persistence.effect` this phase. The `build_messages` body grows an LD3
"latest action" block so the LLM is no longer blind on its own outputs,
and the `_act` body from 2.2a now has falsifiable test coverage of every
new op via isolated substrate fixtures.

### Added

- **LD3 `build_messages` widening** (`_prompt.py`). New helper
  `_render_latest_action(action: Mapping[str, Any]) -> list[str]` renders
  `obs.recent_actions[-1]` with explicit field formatting (op, error,
  result_summary keys laid out individually). The rendered block is
  inserted into `build_messages` BEFORE the existing "Recent loop
  history" block. This closes the "blind on its own outputs" failure
  mode where the existing `[:200]` truncation in the older history
  block would eat stdout sentinels and tracebacks. The OLDER history
  block (trailing 3 entries with the `[:200]` cap) is unchanged so
  rolling context still has a hard length budget. Forced spec
  deviation: action dicts in `recent_actions` do NOT carry an
  `iter_count` field (only the top-level `Observation` does), so the
  latest-action header is plain `"Latest action output:"` without
  iter-count context. Iter context continues to be announced once via
  the existing `"Recent loop history (iter N):"` line.

- **Three falsifiable LD3 acceptance signals (G5a)** in
  `tests/coder/test_observe_latest_action.py`:
  - stdout sentinel reaches the prompt verbatim past 200-char depth
    (would have been truncated under 2.2a's `[:200]` cap).
  - stderr sentinel reaches the prompt verbatim past 200-char depth.
  - `result_summary=None` exception path renders the placeholder and
    leaves the `error` field non-null, so the LLM sees both that the
    last action failed and what failed about it.

- **`_act` dispatch coverage of the new ops** via isolated `Substrate.open`
  fixtures. The `_act` body itself (`_session.py:225` from 2.2a) is
  unchanged — it still routes via `s.effect.perform(op, args)` and emits
  a `:act/result` provenance datom on both success and failure paths.
  T4 added:
  - `tests/coder/test_act_git.py` (4 G3 tests). `:git/diff` and
    `:git/commit` via `_act` write a `:act/result` datom AND advance the
    canonical audit chain by EXACTLY 1 entry per op (the LD2 mask
    property holds end-to-end, not just at the handler call site).
    `:git/commit` paths-unchanged returns `exit=1` passthrough; the
    coder records a non-error `:act/result` because the op succeeded
    even if git's exit code is 1. cwd-outside-root surfaces
    `FsCapabilityDenied` via `:act/result.error` (provenance-survives-
    failure invariant from 2.2a).
  - `tests/coder/test_act_code_run.py` (4 G4 tests). `:code/run`
    traceback is captured in `result_summary` (truncated by
    `_summarize_result`'s 512-char cap, same shape as every other
    op's failure-path `:act/result`).

### Notes

- LD3 fixes a regression-class introduced incidentally by 2.2a's
  `[:200]` cap — the cap was applied to the JSON-dump of EVERY recent
  action including the most recent one, which meant any action whose
  result string exceeded 200 bytes would get truncated before the LLM
  saw it. After 2.2b only OLDER history entries (the trailing 3 in
  `recent_actions[:-1]`) get the cap; the most recent action passes
  through verbatim, subject only to `_summarize_result`'s 512-char
  ceiling on string fields. That 512-char ceiling is the new
  effective limit for "what the LLM sees about its last action".
- The G5b 5-iter scripted scenario in `test_loop_e2e.py` asserts
  EXACTLY 9 audit entries across the run (5 `:llm/call` + 1 each of
  `:fs/read`, `:code/run`, `:git/diff`, `:git/commit`) AND that the
  `:shell/exec` count is 0. The `:shell/exec` zero-count IS the LD2
  single-audit-entry-via-mask property proved end-to-end through the
  coder loop, not just at a single handler call site.
- The G6 deterministic-invariants tests in `test_loop_replay.py`
  assert `output_hash` byte-identity for `:code/run` across runs and
  argv-determinism for `:git/*` across handler instances. They do
  NOT assert full `AuditEntry.id` byte-identity — that would fail
  because `wall_clock_ms` is included in both ops' result dicts and
  `result_hash` is non-deterministic by design. The deterministic
  invariants (`output_hash`, argv) are the right G6 surface; the
  full-replay byte-identity property is a separate concern owned by
  the replay module's own tests.
- CLI wiring of `:fs/*`, `:shell/exec`, `:code/run`, and `:git/*` in
  `__main__.py` is DEFERRED. This is a pre-existing gap from 2.2a:
  `__main__.py` only installs the LLM provider handler, and the
  test surface uses isolated `Substrate.open("memory")` fixtures
  that install the new handlers per-test. Production CLI wiring of
  the full effect surface belongs to 2.4a hardening or a separate
  follow-up; carrying that work into 2.2b would have widened scope
  beyond LD2/LD3.

### Tests

- `tests/coder/test_observe_latest_action.py` — 6 G5a tests
  (latest-action verbatim passthrough, stdout/stderr sentinel
  reach, exception-path placeholder + non-null error, header
  ordering, empty-history no-op, `_render_latest_action` field
  formatting).
- `tests/coder/test_act_git.py` — 4 G3 tests (`:git/diff` audit
  entry count, `:git/commit` paths-unchanged exit=1 passthrough,
  cwd-outside-root provenance-survives-failure, single-audit-entry
  per `:git/*` call via `_act`).
- `tests/coder/test_act_code_run.py` — 4 G4 tests (`:code/run`
  success path, traceback captured in `result_summary`,
  forbidden-import sentinel reaches `:act/result.error`,
  `args_hash` distinct from `:code/exec` for the same logical
  input).
- `tests/coder/test_loop_e2e.py` — 3 G5b tests appended (existing 5
  unchanged): 5-iter 9-audit-entry scenario asserting
  `:shell/exec` count == 0 (LD2 e2e proof), `:code/run`
  traceback reaches the next-iter prompt verbatim, cwd-denied
  `:git/diff` surfaces error in `:act/result`.
- `tests/coder/test_loop_replay.py` — 3 G6 tests appended
  (existing 3 unchanged): `:code/run` `output_hash` byte-identity
  across runs, `:git/diff` argv-determinism via spy fixture,
  `:git/log` argv-determinism across handler instances.

### Refs

- Design: `docs/plans/2026-05-06-phase-2.2b-git-code-exec-design.md`
- Suite delta at T7: 2354 → 2404 (+50 across 2.2b T1-T7; +20 in
  the coder module).

---

## v0.9.0a1 (unreleased) — Phase 2.2a `_observe` + `_act` + `run()` loop widening

Phase 2.2a fulfils the LD5 deferral from 2.1b: "loop widening lands in 2.2a
when `_observe`/`_act` have substance to iterate over." All three methods
(`_observe`, `_act`, `run`) now have real bodies. The coder can execute
scripted multi-step runs end-to-end: observe substrate state, decide, act on
the decision, observe updated state, repeat up to `max_iters`.

### Added

- **`Coder.max_iters: int = 20`** (`_session.py`; LD2). Upper bound on the
  `run()` for-loop. `--max-iters N` CLI flag exposed via `_cli.py` and wired
  in `__main__.py`.

- **`Coder.observe_depth: int = 5`** (`_session.py`; LD3). Controls how many
  recent datoms per attribute are kept in each `Observation`. Tighter windows
  keep `build_messages` prompts from growing unbounded across iterations.

- **`Coder._session_start_dt`** (`_session.py`; `init=False`). Set
  unconditionally by `run()` before the loop so `_observe` can filter datoms
  emitted before the current session. Type: `datetime | None` (default None).

- **`Coder._observe()` body** (`_session.py`). Reads via
  `s.fact.since(self._session_start_dt)`, sorts by `d.tx`, filters
  `d.op == "assert"`, partitions datoms into decisions (attribute
  `"llm/decision"`) vs actions (attribute `"act/result"`) by bare `d.a`
  (Datom strips leading colon per `datom.py:175` — see forced deviations),
  slices last `observe_depth` entries per attribute. Returns a frozen
  `Observation`.

- **`Coder._act(decision)` body** (`_session.py`). Validates
  `decision.kind == "act"` and that `decision.payload["op"]` is a
  `:`-prefixed string. Dispatches via `s.effect.perform(op, args)`. Emits a
  `:act/result` provenance datom via the `_record` helper in BOTH the success
  and failure paths (provenance-survives-failure guarantee). `args_hash`
  reuses `canonical_hash` (same helper as the audit middleware, so the hash
  is byte-identical to what the audit chain would record for a wrapped op).
  `_summarize_result` truncates dict-string values longer than 512 chars.
  Inside the `except` clause the bare `raise` re-raises the original
  exception (R0 B1 fix; preserves stack trace).

- **`run()` loop widening** (`_session.py`). Body is now a
  `for self._iter_count in range(self.max_iters)` loop (see forced deviations
  for the assignment-form used). Four exit paths in order:
  1. `_should_escalate_branch` → `_escalate_branch` (stub; `CoderStubNotImplemented`).
  2. `_should_escalate_plan` → `_escalate_plan` (stub; `CoderStubNotImplemented`).
  3. `decision.payload.get("done") is True` → return before `_act`.
  4. Loop exhausted → silent return (max_iters cap).
  `_check_pause` removed from the loop body (LD8 deferral to 2.3d — pausing
  requires hormone-event integration that doesn't exist yet).

- **`_should_escalate_plan` and `_should_escalate_branch` one-liners**
  (`_session.py`). Each returns `decision.kind == "plan"` or
  `decision.kind == "branch"` respectively. No stub raises — these are
  one-liner predicate gates, not stub bodies.

- **`_escalate_plan`, `_escalate_branch`, `_check_pause` stub bodies**
  (`_session.py`). All keep `CoderStubNotImplemented` raises per Phase 2.2a
  scope. `decision` parameter renamed `_decision` per project convention
  (unused-parameter signals intent clearly; prevents accidental use before
  the body is filled in 2.3a/2.3b/2.3d).

- **`Observation` dataclass body** (`_types.py`). Three fields:
  `iter_count: int = 0`, `recent_decisions: tuple = ()`,
  `recent_actions: tuple = ()`. Empty defaults preserve unparametrized
  constructor calls in all 2.1b unit tests — no test breakage.

- **`build_messages` history hook** (`_prompt.py`). Adds an optional
  "Recent loop history" section rendered only when `obs` has non-empty
  `recent_decisions` or `recent_actions`. Per-entry text is truncated to 200
  chars; the list is further trimmed to the last 3 entries of each attribute
  before rendering. Absent history → no section added (prompt identical to
  2.1b for the zero-history case).

### New datom emitted

- **`:act/result`** — one entity per `_act` call (success or failure).
  Value is a canonical-JSON serialized dict:
  `{op, args_hash, summary, latency_ms, error}` where `error` is `None` on
  success and a `str` on failure. Written via `s.fact.transact`; bypasses the
  canonical audit chain (same supporting-provenance shape as `:llm/messages`
  and `:llm/decision` from 2.1b — not a billable wrapped op, just a
  substrate-side fact written for observability).

### Forced spec deviations

1. **`Datom.a` strips leading colons** (`datom.py:175`). `_observe` filters
   by `d.a == "llm/decision"` and `d.a == "act/result"` (bare, no colon)
   throughout. The design doc used `:llm/decision` notation; the runtime
   strips the colon on storage so bare-string comparison is correct.
2. **`make_callable_llm_handler` call_fn signature is `**kwargs`**
   (`model, messages, tools, temperature, max_tokens`), not a single-arg
   callable. `_scripted_decisions` test helper adapted to accept and ignore
   the extra kwargs.
3. **`for self._iter_count in range(...)` assignment form.** Python does not
   support `for self.attr in range(...)` directly in all toolchain versions.
   Loop body uses the equivalent `for i in range(...): self._iter_count = i`
   form to keep `_iter_count` current for any observer that inspects it.
4. **`__main__.py` is the Coder construction site** for
   `max_iters=args.max_iters`. The `_cli.py` module only defines argparse
   args; the wiring between `args.max_iters` and the `Coder` constructor
   happens in `__main__.py` (consistent with `--model` wiring precedent
   from 2.1b).

### Architectural decisions at impl time

1. `_safe_resolve(*allowed_roots)` variadic helper in the FS handler — allows
   `:fs/read` to accept both `project_root` and `scratch_dir` while
   `:fs/write` is restricted to `scratch_dir` only, without duplicating the
   path-escape check.
2. Empty `argv` guard placed before `basename` call in the shell handler →
   `ShellAllowlistDenied("argv is empty")` (T3 contract fix; `basename("")`
   would pass through silently otherwise).
3. TimeoutExpired bytes-branch in the shell handler documented as empirically
   live on macOS (`_check_timeout` joins raw bytes before decode); not dead
   code despite appearances.
4. `ALLOWLIST_VERSION` auto-derived from `frozenset` at module load time via
   `sha256(canonical_dumps(sorted(ALLOWLIST_V1)))[:16]`. Any allowlist edit
   propagates the version automatically — no manual constant to update.

### Test surface

- **G1** — `tests/effect/handlers/test_fs_handler.py` (13 tests). Capability
  denial, binary base64 round-trip, glob/grep canonical sort, symlink escape,
  scratch_dir glob symmetry.
- **G2** — `tests/effect/handlers/test_shell_handler.py` (12 tests). Allowlist
  pass/denial, version pin, timeout, version-mismatch replay, env passthrough,
  full-path basename matching, empty-argv denial.
- **G3** — `tests/coder/test_observe.py` (6 tests). Empty substrate,
  3-datom window, 7-datom window-trim, partition by attribute, iter-count
  propagation, pre-session datom exclusion.
- **G4** — `tests/coder/test_act.py` (8 tests). Dispatch, provenance-survives-
  failure, `kind != "act"` rejection, missing op, non-string op, missing colon
  prefix, args_hash agreement, `latency_ms >= 0`.
- **G5** — `tests/coder/test_loop_e2e.py` (5 tests). 3-iter scripted run,
  max_iters cap, done-flag short-circuit, plan halt, branch halt.
- **G6** — `tests/coder/test_loop_replay.py` (3 tests). Byte-identity positive,
  handler-swap mismatch, clock-skew mismatch.

Suite delta: 2300 → 2354 (+54). Pyright 0 / 0 / 0 on touched files.

### Notes

- The LLM is still not exposed to `:fs/` or `:shell/exec` tool schemas
  directly. In 2.2a the routing is substrate-side: `_act` reads
  `decision.payload["op"]` and dispatches. The LLM emits a structured
  `payload` via `EMIT_DECISION_TOOL_SCHEMA`; the substrate validates and
  routes. No change to the LLM-visible tool surface in 2.2a.
- `_escalate_plan`, `_escalate_branch`, and `_check_pause` remain stubs.
  2.3a fills `_escalate_plan`; 2.3b fills `_escalate_branch`; 2.3d wires
  `_check_pause` to hormone events.

---

## v0.9.0a1 (unreleased) — Phase 2.1b `_decide` body + first `:llm/*` datoms

Phase 2.1b fills the `_decide` method — the first behavioral method
on the persistence-coder skeleton. Three substrate-side handler
factories ship under `persistence.effect.handlers.{anthropic,
claude_code, callable}`. The CLI gains `--provider` + `--model` flags;
provider auto-detection prefers `claude-code` (Max subscription) over
`anthropic` (paid API) over an `echo` floor.

### Added

- **`Coder._decide` body** (`_session.py`). Calls
  `s.effect.perform(":llm/call", ...)` with a single tool exposed —
  `EMIT_DECISION_TOOL_SCHEMA` (LD4 decision/action split). Two-tier
  parsing: tool-use → text-fenced fallback → missing-confidence
  default last (LD3). Transacts `:llm/messages` BEFORE the call (so
  provenance survives even if the call raises) and `:llm/decision`
  AFTER parsing. The decision datom carries an FK `source_call`
  back to the matching `:llm/messages` entity-id; full provenance
  materializable via `s.fact.q` joins.
- **`LLMDecision` dataclass body** (`_types.py`). Three frozen
  fields — `kind: Literal["act","plan","branch"]`, `confidence:
  float`, `payload: Mapping[str, Any]`. Payload shape is loose in
  2.1b; tightens 2.2a/2.3a/2.3b.
- **`Coder.model` field** (`_session.py`). Default `"claude-opus-4-7"`;
  CLI `--model` overrides.
- **`_prompt.py`** — `EMIT_DECISION_TOOL_SCHEMA` (the only tool
  exposed to the LLM in 2.1b), `build_messages(task, obs)`,
  `parse_text_decision(text)` (tier-2 envelope parser, total
  function — never raises on malformed input).
- **`_provider.py`** — `detect_or_explicit(provider)` for CLI
  auto-detection (LD6: `claude-code` → `anthropic` → `echo` ordering)
  and explicit-provider validation. `_claude_code_available()` is
  importability-only; signed-out state surfaces lazily on first call
  per R1 F8.
- **CLI flags `--provider {auto,anthropic,claude-code}` and
  `--model <id>`** (`_cli.py`). `--confidence-threshold` still
  deferred to 2.3b/2.4a per CP2.
- **`__main__.py` provider install**. After `Substrate.open(...)`,
  installs the chosen handler at `position="bottom"` via
  `s.effect.install_handler(...)`. Zero `s.escape.*` callsites in
  `src/persistence/coder/` (LD7 — Q2 preserved literally).

### First datoms emitted

- `:llm/messages` — entity per call. Value is a canonical-JSON
  serialized dict `{messages, tools, model}`. Honors the 2.1a
  CHANGELOG promise verbatim.
- `:llm/decision` — entity per parsed decision. Value is a
  canonical-JSON serialized dict `{kind, confidence, payload,
  parsed_via, source_call}`. The `parsed_via` field is one of
  `"tool_use"` / `"text_fallback"` / `"missing_default"` —
  substrate-side observable signal for "did the LLM behave?"
  without re-parsing logs.

### LD5 — `run()` body deferred to 2.2a

The 2.1a CHANGELOG-coder hint said "2.1b widens one-iter → while-loop".
**Amended in 2.1b.** Loop widening lands in 2.2a when `_observe`/`_act`
have substance to iterate over. Widening in 2.1b would either bypass
`_observe` (architectural drift) or wrap its raise in try/except
(masks the 2.2a stub — defeats the skeleton's audit-friendliness).
`_decide` is exercised via direct unit tests in 2.1b, NOT via
`run()` (which still raises `CoderStubNotImplemented("Phase 2.2a —
substrate read via s.fact.q")` on the first call).

### Test surface

- `tests/coder/test_decide.py` (~13 tests) — comprehensive `_decide`
  coverage: tier 1/2/3 paths, datom emission ordering, FK linkage,
  malformed tool-call fallthrough, provenance-on-failure, AST G6
  decision/action split assertion, Hypothesis G2.1b-a property
  (`_parse_decision` total function over 200 generated catalog
  responses), R5 invariant (`missing_confidence_default <
  confidence_threshold`).
- `tests/coder/test_provider_detection.py` (~7 tests) — G2.1b-c
  auto-detection matrix + explicit-provider error paths.
- `tests/coder/test_prompt_schema.py` (~17 tests) — schema shape,
  text-parser parametric (3 valid + 9 invalid envelope shapes).
- `tests/coder/test_decide_replay.py` (1 test) — G3 byte-identity.
- `tests/coder/test_main_provider_install.py` (4 tests) —
  subprocess-based stderr UX + exit-code coverage.
- `tests/coder/test_types.py` (6 tests) — LLMDecision shape.
- `tests/coder/test_cli_args.py` (6 tests) — `--provider` / `--model`
  argparse coverage.
- `tests/effect/handlers/test_callable_handler.py` (4 tests).
- `tests/effect/handlers/test_anthropic_handler.py` (3-4 tests).
- `tests/effect/handlers/test_claude_code_handler.py` (3 tests).
- `tests/effect/handlers/test_provider_translation_contract.py`
  (6 parametric tests, G5).
- `tests/effect/test_audit_stack_llm_call.py` (6 tests, G4).
- `tests/sdk/test_effect_namespace.py` (5 tests, install_handler).

Suite delta `+57 / 35 skipped / 8 xfailed` (2,108 → ~2,165). Pyright
`0 errors / 0 warnings / 0 info` on touched files.

### Notes

- Substrate prereqs land in this same sub-phase per LD7 (R1 fix-pass):
  curated `s.effect.install_handler` (replaces non-existent
  `s.escape.effect.push`); `CANONICAL_AUDIT_OPS` split into
  `_WRAPPED_OPS` (audit middleware wraps — includes `:llm/call`)
  and `_RAW_OPS` (raw terminator covers — excludes `:llm/call`).
  The split prevents the raw terminator from masking the LLM
  provider handler. See `CHANGELOG-sdk.md` and `CHANGELOG-effect.md`.
- The LLM never sees `:fs/`, `:shell/`, `:code/`, `:git/` tool
  surfaces in 2.1b — only `emit_decision`. Real effect tools land
  in 2.2a; the substrate routes intents in `_act` (2.2a) /
  `_escalate_plan` (2.3a) / `_escalate_branch` (2.3b).
- Mode 3 callable handler (`make_callable_llm_handler`) ensures
  persistence-coder is NOT Claude-specific. Any host (Codex,
  Cursor, juba, Ollama / vLLM / local LLMs) can wire its LLM access
  by passing a `call_fn` that translates its vendor's response into
  the catalog wire shape. ~30 LOC, zero new deps.

---

## v0.9.0a1 (unreleased) — Phase 2.1a `persistence.coder` skeleton

Phase 2.1a lands the persistence-coder skeleton — the FIRST agent
built ON the v0.8.5a1 substrate. Consumer-side module: imports from
`persistence.sdk` only, never from raw substrate modules.

### Added

- **`Coder` class** (`_session.py`). `@dataclass` with substrate
  dependency-injected per design LD2 (callers own substrate
  lifecycle; `repl/_session.py` precedent). Six method ReAct loop
  shape from base design § 3.4: `_observe` → `_decide` →
  `_should_escalate_branch` / `_escalate_branch` /
  `_should_escalate_plan` / `_escalate_plan` → `_act` →
  `_check_pause`. Every method body is a `raise CoderStubNotImplemented(...)`
  tagged with the downstream sub-phase that fills it. Class
  attributes `confidence_threshold = 0.65` and
  `missing_confidence_default = 0.5` from base § 3.4 (CLI flag
  deferred to 2.3b/2.4a per design CP2).
- **`CoderStubNotImplemented`** (`_session.py`). `NotImplementedError`
  subclass — the Phase 2.1a skeleton sentinel. `__main__.py` catches
  this subtype only, so real `NotImplementedError` raised by 2.1b+
  implementation code (e.g. an LLM-provider abstract method that
  isn't overridden) propagates as a genuine failure rather than
  being banner-masked. ARIS R1 fix-1 (codex hard-mode review of
  design doc 2026-05-03; mean 8.0 / min 7.6).
- **`Observation` / `LLMDecision` value-shape dataclasses**
  (`_types.py`). Empty frozen dataclasses in 2.1a so type hints in
  `_session.py` resolve; fields land in 2.1b (LLMDecision) and 2.2a
  (Observation) when wire shapes stabilize.
- **CLI entry** (`__main__.py` + `_cli.py`). `python -m persistence.coder
  --task "..." [--db-path <uri>]`. argparse-based per `repl/_cli.py`
  precedent (no click/typer dep — yagni). `--db-path` defaults to
  `None` → bare-string `"memory"` URI to `Substrate.open()` (per
  design CP1, verified against `_facade.py:1354-1442`) plus a stderr
  warning. On `CoderStubNotImplemented`, prints
  `persistence-coder skeleton: <phase-tag> — <purpose>` to stderr
  and exits 1.

### Notes

- **Zero datom emissions in 2.1a.** Substrate at exit is byte-identical
  to a fresh substrate. 2.1b lands the first datoms (`:llm/messages`,
  `:llm/decision`).
- **Zero `s.escape.*` callsites.** Three AST-guard smoke greps from
  design § 6.1 (G1.A no-raw-substrate-imports / G1.B no-`.escape`
  regardless of alias / G1.C no allowed-set callsites) return zero
  matches. The 2.1c lockfile contract test (Wed 2026-05-06) replaces
  the smoke greps with a load-bearing AST walk.

### Test surface

`tests/coder/test_session_stubs.py` (5 functions / 12 invocations
including 8 parametrized stub-tag checks) + `tests/coder/test_cli_smoke.py`
(3 subprocess-driven CLI invocations). Suite delta `+15 / 33 skipped /
8 xfailed` (2,093 → 2,108). Pyright `0 errors / 0 warnings / 0 info`
on touched files.
