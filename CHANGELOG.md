# Changelog

All notable changes to Persistence OS are tracked here. Versions follow
`<semver>` with a `-aN` pre-release suffix until the paper lands.

## v0.7.0a1 — 2026-04-29 (Module 7: capability-gated live REPL — WS + browser console)

Stream D of the v1.0 ferrari-first roadmap. Adds a live, capability-gated,
audit-emitting REPL surface over a WebSocket transport with a vanilla-JS
browser console UI — the operator-facing surface that Streams A/B/C/E/F
all hang off for production inspection, two-step edits, view-cursor
rewind, and branch-as-cursor-marker. ARIS R2 (code quality) PASS at mean
**8.90 / min 8.4** with two MAJORs closed via design-doc-only edits
(ADR-9 code-name realignment + ADR-13). Suite **1517 passed / 7 xfailed**
(+30 over v0.6.5; +5 D-INT integration tests).

### Added

- **`persistence.repl` package** (`src/persistence/repl/`). Public surface:
  `Capability`, `CapabilitySet`, `mint_token`, `store_token`, `WSServer`,
  plus the eight application-band error constants
  (`ERR_CAPABILITY_DENIED`, `ERR_AUTH_FAILED`, `ERR_TOKEN_INVALID`,
  `ERR_VERIFY_CHAIN_FAILED`, `ERR_REQUEST_HASH_MISMATCH`,
  `ERR_SESSION_EXPIRED`, `ERR_BRANCH_DEPTH_EXCEEDED`,
  `ERR_STALE_CURSOR_EDIT`).
- **WebSocket transport** (`_ws.py`, `_protocol.py`). `aiohttp.web`-based
  single-port server (ADR-1 + ADR-10). JSON-RPC 2.0 envelope (ADR-2)
  with closed application-band code table at `-32001..-32008` (ADR-9).
  Auth handshake on first frame: `repl/auth { token }` → returns
  deterministic `session_id = sha256(token_id + ":" + auth_clock_iso)[:16]`
  and the granted capability set.
- **Capability tokens** (`_caps.py`). Opaque random 256-bit tokens stored
  fact-store-backed (ADR-3). `Capability(op, qualifier)` pairs:
  `inspect:read`, `inspect:audit-tail`, `edit:write`, `rewind:any`,
  `branch:fork`, `auth:login`. `expires_at` enforced at every op-dispatch.
  Idempotent `revoke_token`; revocation propagates on next
  `validate_token` call (mid-op semantics: in-flight ops complete).
- **Four ops** (`_ops.py`). Capability-gated handlers, all dispatching
  through one shim that flattens params per ADR-12:
  - `repl/inspect` — `kind=entity` / `audit-window` / `causal-history` /
    `plan` (read-only; cap `inspect:read` or `inspect:audit-tail`).
  - `repl/edit` — two-step propose-confirm with `request_hash`-strip
    canonical-hash matching (ADR-7); zero substrate-source extension.
    Stale-cursor edit rejected with `-32008` (§5.2 invariant).
  - `repl/rewind` — sticky per-session view-cursor (ADR-5). Cursor only
    affects view; intervening `db.transact` writes still land.
  - `repl/branch` — cursor + depth marker (ADR-13; **NOT a `db.branch`
    store fork**). Advances `view_cursor_tx_time_iso` and increments
    `parent_chain_depth`; rejected with `-32007` past `max_branch_depth`
    (default 16). Safety rests on the stale-cursor edit invariant.
- **Audit emission** (`_audit.py`). Every op writes one `:repl/op`
  AuditEntry whose `principal` rides the REPL fields (`op_kind`,
  `view_cursor_tx_time_iso`, `parent_session_id`, `parent_chain_depth`)
  so Module 2's canonical AuditEntry slot set is intact. `verify_chain`
  returns `True` over a pure-REPL OR mixed programmatic+REPL chain;
  audit-window is hot-path read and exempted from self-emission per
  ADR-11. In-memory FIFO ring (hot cache, default cap 1000) +
  fact-store persistence (durable).
- **Browser console UI** (`static/index.html` / `app.js` / `style.css`).
  Vanilla JS, zero build step (ADR-6). Two-pane layout: command output
  (left) + audit tail (right). XSS-pinned via `textContent` only —
  the unsafe HTML-string DOM setter and unsafe dynamic-code constructors
  do not appear in the source (a regression test scans for absence).
  Token loaded from URL fragment `#token=...` then immediately scrubbed
  via `history.replaceState`. Audit-tail polled at 1Hz via
  `repl/inspect kind=audit-window` (W3 ADR-11 server-side gate
  prevents the poll from logging itself into the audit log).
- **D-INT integration test** (`tests/integration/test_v0_7_repl_e2e.py`,
  5 tests, 722 LOC incl. fixtures + docstrings):
  inspect-after-edit ⋅ rewind-cursor-isolation ⋅ branch-records-cursor-and-depth
  ⋅ replay-from-datoms-alone byte-identity (the W2 chain invariant at
  the REPL boundary, mirrors Stream B's Prop 6 defense via
  `canonical_dumps` projection) ⋅ audit-chain integrity with W3
  self-loop verified end-to-end.

### Design

- **13 ADRs** in `docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md`:
  - ADR-1..10: transport, protocol, capabilities, audit shape, view-cursor
    semantics, UI bundling, propose-confirm, subscription model, error
    envelope, async runtime.
  - **ADR-11** (W3): `repl/inspect kind=audit-window` polls don't
    self-emit — closes the 1Hz audit-tail self-loop that drowned the
    audit pane during browser verification.
  - **ADR-12** (W3): flat handler params, DSL is canonical — handler
    flattens `kind`-stripped params; no nested `params.params` shape on
    the wire.
  - **ADR-13** (W4 fix-pass): `repl/branch` is a cursor + depth marker,
    NOT a `db.branch` store fork — codifies shipped semantics; safety
    rests on §5.2 stale-cursor-edit rejection (any non-null-cursor
    session is read-only because `repl/edit` rejects with `-32008`).
- **R2 code-quality** at mean 8.90 / min 8.4 with zero MAJORs after W4:
  Correctness 8.4 / Robustness 8.8 / Readability 9.2 / Coverage 9.4 /
  Performance 8.7. Eight MINORs deferred to v0.7.x post-tag (browser
  audit-poll cosmetic flat-params drift, exception-class tightening on
  `_OpError` error-string surface, `_SyntheticAuthSession` factory
  hardening, etc.).

### Migration / behavioural notes

- **No substrate-source extension.** Module 7 is a layer over the
  existing `db.transact` / `db.branch` / `verify_chain` primitives; no
  `preview_transact`, no new fact-store APIs. Two-step propose-confirm
  is implemented entirely in `_ops.py` via `canonical_hash(params with
  confirm and request_hash stripped)`.
- **`branch_op` is read-only.** A "branched" session inherits the
  capability set including `edit:write` but cannot mutate state because
  its non-null `view_cursor_tx_time_iso` triggers `-32008`. To write at
  a past coordinate, operators must coordinate out-of-band with the
  substrate's `db.branch(...)` primitive directly (Phase 3 NeSy 2027
  may add a `branch:write` capability that swaps the session's DB).
- **Audit-window polls don't audit themselves.** Operators relying on
  the audit count to detect access-pattern anomalies need to
  account for the gate: only `kind ∈ {entity, audit-window-tail-by-id,
  causal-history, plan}` emit `:repl/op` AuditEntries. (Implementation
  is the inverse — `audit-window` is the lone exempt kind; all other
  inspect kinds DO emit.)

### Compatibility

- **Pre-release.** No prior `repl` API surface existed (was a one-line
  `[stub]` marker in `persistence/__init__.py`). All public exports are
  new in v0.7.0a1. Pre-release suffix `-a1` reflects substrate-internal
  alpha; will graduate to `v0.7.0` when paper Stream H lands its v1.0
  rewrite around the eval block (2026-05-28→06-04).
- **Optional install.** `pip install persistence[repl]` pulls the
  `aiohttp>=3.9.0,<4.0.0` extra. Substrate-only consumers do NOT need
  aiohttp.

### Stream-D timeline

- D1-D8: per-task subagent dispatch (1 fresh implementer + spec-reviewer
  + code-quality-reviewer per task), 8 commits.
- W3: micro-pass after browser verification on `chrome-MCP` tab
  `2101276577` + huashu-design first-pass surfaced 2 ⚠️ critical
  defects (audit-window self-loop + DSL/handler param-shape drift).
  Single subagent, single commit `dd31f37`. ADR-11 + ADR-12.
- D-INT: integration test subagent at `1ce8ac4` (5 tests, all PASS,
  +5 to suite total → 1517).
- D-FINAL.1 R2: PASS-with-W1 at mean 8.90 / min 8.4. Two MAJORs were
  design-doc drift only.
- W4 fix-pass: design-doc-only edits at `4157c64` closing R2 MAJORs
  (ADR-9 code-name realignment + ADR-13 branch-as-cursor-marker).
- D-FINAL.2: this release. **17-day margin to NeSy 2026 abstract
  deadline 2026-06-09 preserved.**

## v0.6.5 — 2026-04-28 (Module 3.X: MCTS — PUCT search + skill-library 4-gate closed loop)

Stream B of the v1.0 ferrari-first roadmap. Adds PUCT tree search over
the content-addressed Plan AST with an LLM-evaluator port, full
`:mcts/iteration` provenance for replay-from-audit-log alone (Prop 6),
and a `mcts_promote()` orchestrator that chains `mcts_search → promote
→ SkillLibrary.register` to close the search → promotion → reuse loop.
Single-flat-file impl per ADR-11 (`_mcts.py`) with provenance helpers
lifted to `_mcts_datoms.py`. ARIS R1 (design fitness) PASS at composite
8.90 / min 8.0; ARIS R2 (code quality) PASS at mean 8.94 / min 8.7.

### Added

- **`Action` algebraic data type** (`persistence.plan._mcts`).
  `SubstituteLeafAction(target_path, new_leaf)` /
  `AddStepAction(target_path, new_child)` /
  `ComposeWithSkillAction(target_path, skill_id)`. All
  `@dataclass(frozen=True, slots=True)`. `apply_action(plan, action,
  *, skill_library=None)` dispatches by `isinstance` (strict — third-
  party action subclasses fall through to `ValueError("unknown action
  kind")`). `_action_hash` composes via `Node.id` for nested Nodes;
  reuses the canonical-JSON helper from `_ast.py`. `MAX_PLAN_DEPTH = 32`
  enforced via two-layer guard (apply-time + ComposeWithSkill skill-
  plan ≤ MAX_PLAN_DEPTH//2 = 16).
- **`MCTSConfig`** `@dataclass(frozen=True, slots=True)`. Defaults:
  `c_puct=1.4`, `max_iter=200`, `max_unique_plans=64`, `expander_k=4`,
  `simple_regret_window=5`, `simple_regret_threshold=False`,
  `selection_temperature=0.0`. `__post_init__` runs bool-isinstance-FIRST
  then positive-only validators on every numeric field (Stream A
  W1.B/G4 anti-pattern preempted: `bool(...)` coercion never accepted).
- **`MCTSNode`** + **`MCTSEdge`** `@dataclass(slots=True)` (non-frozen
  for backup mutation). Per design §4: `MCTSNode` carries
  `plan_id / visits / total_value / children / is_terminal` with
  computed `q_value` property; `MCTSEdge` carries
  `action / action_hash / prior / child_plan_id / visits_through_edge /
  total_value_through_edge`. Prior lives on the edge, not the node.
- **`Expander`** `@runtime_checkable` Protocol with `propose(plan, *,
  k) → Sequence[tuple[Action, float]]`. `_StaticExpander(proposals,
  on_unknown="empty"|"raise")` for deterministic test harnesses.
  `LLMExpander(provider: Callable[[Node, int], Iterable[tuple[Action,
  float]]])` — pure delegation (prior-sum tolerance `_PRIOR_TOL = 1e-6`
  enforced in the `mcts_search` loop, not in the protocol).
- **`Evaluator`** `@runtime_checkable` Protocol with
  `evaluate(plan) → float`. `_StaticEvaluator` + `LLMJudgeEvaluator`
  (provider-callable injection — no registry indirection).
  `_is_finite_score` rejects NaN, Inf, bool, and non-numeric — emits
  `evaluator_returned_non_finite` reject datom (no silent coercion).
- **`mcts_search(initial_plan, *, expander, evaluator, started_at_ms,
  config=None, skill_library=None, db=None) → MCTSResult`**. Single-
  player PUCT loop (SELECT → EXPAND → EVALUATE on the just-expanded
  parent → BACKUP). `MCTSResult` is `frozen=True, slots=True`:
  `winner / winner_plan_id / initial_plan_id / search_id (content-
  addressed sha256) / iter_count / unique_plans_visited / terminated_by /
  root_q / tree_dump`. `tree_dump` canonical-ordered: lex sort by
  `(parent_plan_id, child_plan_id)`, 5-tuple includes `action_hash`.
  `db is None` short-circuits provenance (unit-test escape hatch).
- **Visit-conservation 3-case invariant** (root / interior / leaf-of-
  path) verified per design §16; pinned by
  `tests/plan/test_mcts_visit_conservation.py`.
- **Synthetic time discipline**: `t = started_at_ms + iter_index`. No
  wall-clock leaks; one `db.transact(...)` per iteration so each
  iteration is one audit-chain entry.
- **`:mcts/iteration` provenance schema** (`persistence.plan._mcts_
  datoms`). Per design §13 with kebab-case attr keys throughout.
  `phase ∈ {"start", "select", "expand", "evaluate", "backup",
  "reject", "search"}`. `expand` records carry both `_id` and
  `_canonical` slots for `SubstituteLeafAction.new_leaf` /
  `AddStepAction.new_child` so replay can materialize Node bytes
  without requiring the originating in-process state (W2 M4 closure
  for production-LLMExpander Prop-6 defense).
- **`mcts/prev-hash` Merkle chain**. Each datom commits `sha256(
  canonical_json(prev datom content))`; the search trajectory thus
  forms its own Merkle chain SEPARATE from the Module 2 effect-
  handler audit chain (W1 closure of the R2 audit-chain category-
  error: Prop 1/2/4 composition does NOT lift to the search layer
  for free).
- **Cache-miss-only recording** (ADR-10). Hits do not emit datoms;
  replay re-derives on demand. `MCTSReplayCacheMiss` test-local
  exception fires loud in the replay-loud-stub harness.
- **Reject reasons** (closed `frozenset`):
  `evaluator_returned_non_finite / evaluator_raised /
  plan_too_deep / compose_creates_cycle / skill_not_registered /
  plan_construction_raised`. `_classify_apply_failure` dispatches by
  isinstance — no string-substring matching on error messages
  (W1 micro-pass closure of R2 m1).
- **Cycle detection** on `ComposeWithSkillAction`: `_PlanCycleDetected`
  raised when the candidate plan's content-hash already appears in
  the looked-up skill plan's subtree set; mapped to
  `compose_creates_cycle` reject.
- **`mcts_promote(initial_plan, *, expander, evaluator, started_at_ms,
  skill_library, replay_engine, training_set, metric, scores_before,
  scores_after, threshold, db, config=None) → MCTSPromotionResult`**.
  Composition: `mcts_search → promote() → SkillLibrary.register`. No
  chained `optimize()` per design §12 — promotion gate is the source
  of truth for skill-library admission.
- **B-INT integration test** (`tests/integration/test_v0_6_5_mcts.py`).
  7-step body: setup → `mcts_search` with full provenance → verify
  `:mcts/search` summary → verify Merkle chain → verify reject
  schema → verify expand-output Node round-trip → REPLAY-FROM-DATOMS-
  ALONE with byte-identity assertion on `tree_dump`. Step 7 is the
  load-bearing Prop 6 test: caches reconstructed from `db.log()`
  only, Nodes materialized from `new_leaf_canonical` bytes, no
  cross-state cheating.

### Suite

`1084 → 1272 passed, 7 xfailed` (+188 over `v0.6.0a1` baseline; +153
under `tests/plan/`, +35 under `tests/integration/` and shared
fixtures). pyright + ruff clean on the three Stream B source files.

### Files

- `src/persistence/plan/_mcts.py` (1129 LOC — single flat module)
- `src/persistence/plan/_mcts_datoms.py` (416 LOC — provenance
  helpers + canonical Node round-trip)
- `src/persistence/plan/_mcts_promote.py` (144 LOC — promote
  orchestrator)
- `src/persistence/plan/_errors.py` (deltas: `PlanDepthExceeded`,
  `ExpanderContractError`, `EvaluatorContractError`)
- `src/persistence/plan/__init__.py` (public surface re-exports)
- `tests/plan/test_action_*.py` + `tests/plan/test_mcts_*.py`
  (28 unit files)
- `tests/integration/test_v0_6_5_mcts.py` (1 integration file)

### Design + impl docs

- `docs/plans/2026-04-28-v0.6.5-mcts-design.md` (1535 lines, 25
  sections, 12 ADRs — ARIS R1 PASS round-3 at 8.90 / 8.0 after W1
  + W2 fix-passes)
- `docs/plans/2026-04-28-v0.6.5-mcts-impl.md` (1191 lines, 11-task
  playbook B1–B9 + B-INT + B-FINAL)

## v0.6.0a1 — 2026-04-28 (Module 3: Plan — execution + optimization + 4-gate promotion)

Stream A of the v1.0 ferrari-first roadmap. Closes the
"plan as data → plan as runnable program" boundary by shipping
`execute()`, `optimize()`, `SkillLibrary`, and the four promotion
gates (G1/G2/G3/G4) wired through a single `promote()` orchestrator.
ARIS R1 (design fitness) + R2 (code quality) PASS at mean 8.92 / min 8.4.

### Added

- **`execute(plan, db, *, dispatcher=None) → ExecutionResult`**
  (`persistence.plan._execute`). Walks a `Node` AST, calls the per-tag
  `Handler` registered on a `Dispatcher`, and returns a frozen
  `ExecutionResult(leaves: tuple[LeafResult, ...], failures: tuple[FailureInfo, ...])`.
  `LeafResult` and `FailureInfo` are `@dataclass(frozen=True, slots=True)`.
  Failures are caught per-leaf and reported in `failures`; only handler-
  thrown exceptions of an explicitly-allowed set propagate.
- **Metric registry** (`persistence.plan._metric_registry`).
  `register_metric(name, fn)` / `lookup_metric(name) → MetricRef`
  / `unregister_metric(name)`. Process-local, idempotent re-registration
  rejected. `MetricNotRegistered` raised on lookup miss (now exported
  from `persistence.plan`).
- **`TrainingExample`** + `_canonicalize_training_set(...)`. Sorts
  examples deterministically and pins the canonical EDN form so DSPy
  optimization runs are reproducible across re-imports.
- **`_plan_to_dspy_module(node)`** forward adapter
  (`persistence.plan._optimize`). Lazy-imports DSPy 2.5+; explicit
  `OptimizerNotAvailable` when DSPy missing. Inverse adapter
  rebuilds a `Node` AST from the optimized DSPy program with full
  provenance pinning back to the source plan id.
- **`optimize(plan, training_set, metric, *, db, max_demos=...) → OptimizedPlan`**.
  End-to-end MIPROv2 wrapper: forward → optimize → inverse → emit
  `:plan/optimization` datom on the source plan's provenance. Caller-
  injectable dispatcher (W1.A4) keeps the optimizer pure.
- **`SkillLibrary`** (`persistence.plan._skill_library`).
  `register(skill_id, node)` / `lookup(skill_id) → Node | None`
  / `list_skills() → list[str]`. Cross-instance idempotency via fact-
  store log scan: re-registration of the same `skill_id → Node` content
  is a no-op; conflicting content raises. Backed by a
  `_PromotionRecordLike` `@runtime_checkable` Protocol so A5 stays
  decoupled from A7's `PromotionRecord` dataclass.
- **`gate_g1_replay_byte_identity(plan, replay_engine, db, *, window=None) → bool`**.
  Pulls a deterministic replay window, calls
  `replay_engine.compare(plan, audit_window) → dict` (positional-only
  via `/`), and returns False on `divergence_step != None`. Strict-key
  contract: missing `divergence_step` raises `TypeError`. Empty replay
  corpus → `False` + `UserWarning` (vacuous truth not accepted).
- **`gate_g2_audit_chain(db, *, window=None) → bool`**. Pulls audit
  entries in the window via the bitemporal store, requires
  `provenance[":signature"]` on every entry (raises `ValueError` on
  absence), then defers to `verify_chain()` for Merkle-prev-hash
  contiguity. Empty window → `False` + `UserWarning`.
- **`gate_g3_score_delta(scores_before, scores_after, threshold) → bool`**.
  Strict IEEE-754 `>=` comparison contract on `score_after - score_before`.
  Empty-list inputs raise `ValueError` (no vacuous pass).
- **`gate_g4_stub(g4_fn, *, plan, scores_before, scores_after) → bool`**.
  Stub for human / regulator approval. Calls `g4_fn(...) → dict`,
  reads `result["approved"]`, requires strict `bool` (truthy non-bool
  values raise `TypeError`). Phase-3 NeSy 2027 will replace the stub
  with the regulator-replay corpus surface (Stream F).
- **`PromotionRecord`** + **`promote(plan, db, *, replay_engine, scores_before, scores_after, threshold, g4_fn, ...) → PromotionRecord`**.
  Frozen, `slots=True` dataclass with content-addressed `promotion_id`
  (canonical-JSON sha256 over 10 keys). `promote()` orchestrates
  G1 → G2 → G3 → G4 in sequence and raises `GateFailure(message,
  partial_record)` on the first False gate, where `partial_record`
  carries the snapshot of which gates ran (and what their outcomes
  were) before the failure.
- **`GateFailure`** typed class (`persistence.plan._errors`) with
  class-level `partial_record: Any` attribute and explicit `__init__`.
  `Any` retained to avoid an import cycle with `_promotion`; runtime
  value is always a `PromotionRecord`.
- **End-to-end integration test**
  (`tests/integration/test_v0_6_plan_execution.py`):
  `parse → optimize → promote → register → lookup` on a real DSPy-
  mocked plan, exercising every public surface added in this release.
- **18 new commits** on `feat/v0.6-plan-execution`. Suite:
  `1018 → 1084 passed, 7 xfailed` (+66 over v0.5.1 baseline, +3 W1
  pin tests on the fix-pass).

### W1 fix-pass (post-ARIS)

Closes 3 R2 MAJORs, 3 R2 MINORs, 1 NIT, and 4 R1 design-doc drifts
identified by Codex `gpt-5.2` `model_reasoning_effort=high`:

- **W1.A** G1 strict-key membership check on
  `compare()` dict (raises `TypeError` instead of fail-open on
  missing `divergence_step`).
- **W1.B** G4 `isinstance(approved_raw, bool)` check (rejects truthy
  non-bool values like `"False"` string).
- **W1.C** G2 empty audit window now warns + returns `False`
  (`_G2_EMPTY_WINDOW_WARNING`).
- **W1.D/E/G** Design doc (`docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md`)
  tightened: ExecutionResult shape, Π → derivation persistence
  semantic (full record is in-memory cache; persistent reconstruction
  is Phase 3 NeSy 2027 scope), G1/G2 spec contracts.
- **W1.F-1** `:signature` required in `_datom_to_wire_for_audit`
  (raises `ValueError` on absence — prevents hash-equivalent audit
  entries with mismatched IDs).
- **W1.F-2** Simplified `_raise_gate_failure` to direct
  `raise GateFailure(message, partial_record)`.
- Doc fixes: `_skill_library.py` docstring (`plan.id → Node` → `skill_id → Node`);
  `__init__.py` adds `MetricNotRegistered` to public exports;
  integration teardown narrowed `except Exception:` → `except MetricNotRegistered:`.

### ARIS verdict

- R1 design fitness: PASS (4 MAJORs closed via doc updates).
- R2 code quality: PASS at mean **8.92** / min **8.4**
  (correctness 9.3, robustness 9.0, readability 8.7, test coverage
  9.2, performance 8.4). Gate: mean ≥ 8.5 and min ≥ 7.0.
- R3 paper fitness: deferred to Stream G cumulative ARIS R4 at v1.0.0.

### References

- Design: `docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md`
- Implementation playbook: `docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md`
- Review log: `review-stage/v0.6.0a1-aris/AUTO_REVIEW.md`
- Plan-module CHANGELOG: `src/persistence/plan/CHANGELOG-plan.md`

---

## v0.5.1 — 2026-04-27 (Module 5: Txn — rev O narrowings closure)

Closes the 5 carry-forwards from v0.5.0a1 `CHANGELOG-txn.md` § rev O
in a single tight release. Zero proposition impact. Tag drops the
`-aN` suffix (this is a patch release that closes named TODOs, not
a new module — first non-alpha tag in the persistence-os repo).

### Added
- `:persistence.txn/read-set` (sorted eid list) and
  `:persistence.txn/intent-log` (queue-ordered `[{:op, :kwargs}]` list)
  emitted on every commit datom's provenance. Direct read; no longer
  reconstructable-only.
- `_EdnValueSpec` (recursive: scalars + lists/tuples + str-keyed dicts)
  registered under `:persistence.txn/edn-value`. Replaces the v0.5.0a1
  placeholder `seq_of(_uuid_str_spec)` registration on
  `:persistence.txn/intent-log` with the real per-element map shape
  `keys({":op": str_, ":kwargs": map_of(str_, edn-value)})`. Strict
  conformance at commit time (option (3) — see design doc § N1).
- `AuditEntry.txn_commit: str | None = None` first-class field.
  `audit_entry_to_datom` emits `:effect/txn-commit` on provenance only
  when set (symmetric with `:episode`); `datom_to_audit_entry` decodes
  it back. Closes a latent v0.5.0a1 corruption where the
  `_txn_commit` sentinel polluted every audited replay's `args_hash`.
- `Runtime.perform(op, args, *, txn_commit=None)` — typed kwarg path
  for txn-replayed intents; legacy `args["_txn_commit"]` direct path
  still works (audit handler pops the sentinel before hashing).
- `Ref.spec_attr: str = "value"` field (excluded from eq/hash via
  `field(compare=False)`). Allows per-ref attribute specs:
  `db.ref("acct", spec_attr="account/balance")`. Default `"value"`
  preserves v0.5.0a1 behavior bit-for-bit.
- Hypothesis `@given` byte-identity property at `max_examples=200`
  in `test_replay_byte_identity_property.py`. Single-shot `assoc`
  transactions covered; `tx.alter` / `tx.effect` byte-identity
  coverage deferred to v0.5.2.

### Changed
- Helper extraction: `_commit_attempt(tx) -> bool` in
  `transaction.py` is now the single point where spec-validate /
  facts-build / lock+conflict-check / transact happens. Both
  `with db.dosync()` (CM) and `@db.dosync` (decorator) paths route
  through it. `_build_commit_provenance` and
  `_replay_effect_intents` extracted as siblings.
- `_build_commit_provenance` now runs OUTSIDE the
  `with db.store._lock:` block — conformance has no DB-state
  dependency, so commit-time SpecError no longer holds the lock
  under contention.
- `_raise_spec_error(result)` helper centralizes the
  `from persistence.spec._registry import SpecError; raise
  SpecError(result)` pattern (latent v0.5.0a1 export gap;
  `# type: ignore[arg-type]` documented).

### Fixed
- **Audit-chain hash continuity for v0.5.0a1 → v0.5.1.** The
  AuditEntry `content` dict that feeds `prev_hash` linkage now
  inserts `txn_commit` only when not None (mirrors the wire-form
  `:effect/txn-commit` emit-only-when-set semantics). v0.5.0a1
  audit chains continue to verify byte-equal in v0.5.1 for
  non-txn-replayed entries.
- **Latent `args_hash` corruption from v0.5.0a1.** Audit handler
  now `args.pop("_txn_commit", None)` BEFORE
  `canonical_hash(args)`, so two replays of the same intent across
  different commits produce identical `args_hash`. Pinned by
  `test_args_hash_excludes_txn_commit`.

### Design pins held
- `PLAN_CANONICAL_VERSION` stays at 1 (zero canonical-form change).
- Zero proposition impact (Prop 1–5 unchanged).
- 912 + 7 xfailed v0.5.0a1 baseline preserved; +19 new tests
  (931 + 7 xfailed total).

### ARIS gate
- R1 design fitness: 8.06 → re-pass after W1 fix-pass
- R2 code quality: PASS at 9.19 / 8.5
- R3 + R4 skipped — same warrant as v0.4.0a1 (no proposition /
  paper claim change).

### Predecessor
- `v0.5.0a1` at `9377b86` — Module 5 Txn shipped; rev O narrowings
  documented but deferred to keep the tag inside the paper window.

## v0.5.0a1 — 2026-04-27 (Module 5: Txn — atomic multi-datom commit)

### Added
- `persistence.txn` module: atomic multi-datom commit, snapshot-read
  isolation, retry-safe effects via effects-as-intents pattern.
- `db.ref(eid)` / `db.new_ref(initial=...)`: Ref dataclass (frozen,
  slotted, eq/hash over (eid, db_id)).
- `with db.dosync() as tx:` (context-manager) and `@db.dosync` (decorator,
  canonical retryable form). Decorator supports `max_retries` and
  `deadline` kwargs.
- `tx.deref` / `tx.assoc` / `tx.alter` / `tx.effect` / `tx.now`.
- Mandatory immutable values for refs (pyrsistent.PMap/PVector/PSet,
  frozenset, tuple, primitives, frozen dataclass). `RefValueNotImmutable`
  raised on mutable input.
- `persistence.txn.freeze()` helper for dict→PMap, list→PVector
  migration.
- `EffectInIoBlock` raised when raw `effect.perform()` called inside a
  dosync body — use `tx.effect(op, **kwargs)` instead.
- 8 boundary specs registered under `:persistence.txn/*`.
- `DB.transact_batch()`: equivalent to `transact()` for correctness,
  folds N auto-retraction lookups into a single log pass.

### Dependencies
- `pyrsistent>=0.20` added to project dependencies.

### Deferred to later releases
- `tx.commute` (commutative writes) → v0.5.1
- `tx.ensure` (read-set padding) → v0.5.1
- Atoms (single-cell CAS) → v0.5.2
- Agents (async ordered single-cell) → v0.5.3
- Nested `dosync` semantics → v0.5.4

### Design pins held
- `PLAN_CANONICAL_VERSION` stays at 1.
- Zero proposition impact (Prop 1–5 unchanged).
- 832 + 7 xfailed v0.4.0a1 baseline preserved; +80 new tests
  (912 + 7 xfailed total).
- No-GIL forward-compatible (rev N): every mutation guarded by explicit
  lock, `@pytest.mark.no_gil_safe` test in conflict suite.

### Predecessor
- `v0.4.0a1` at `bce93da` — substrate primitives (Dispatcher, Provenance,
  fork, causal_history).

## [0.4.0a1] — 2026-04-25 — v0.4 substrate-primitives (Phases A + C + D)

### Added

- **`Provenance` TypedDict** (`persistence.fact.datom`) — `total=False`
  TypedDict with 7 known keys: `source`, `tx_time`, `handler_id`,
  `canonical_call`, `parent_provenance_hash`, `superseded_by_tx`, `extra`.
  Unknown keys are routed into `extra` by the `provenance_from_dict()`
  coercion helper, which lifts all known keys and collects the remainder
  under the `extra` catch-all. `Datom.provenance` field is now typed
  `Provenance` (documented `# type: ignore[assignment]` covers the
  `default_factory` escape-hatch required by pyright strict structural
  typing). Wire-roundtrip canonical-hash test pins that typed `Provenance`
  produces the same `provenance_hash` as the previous untyped dict shape —
  **`PLAN_CANONICAL_VERSION` stays at 1**; zero canonical-form changes by
  design.
- **`CausalDAG` + `DB.causal_history()`** (`persistence.fact`) —
  `CausalDAG` is a frozen dataclass (`seeds: list[Datom]`,
  `parents: dict[str, list[str]]`). `DB.causal_history(e, max_depth=16)`
  is a single-level walker that reads **both** `parent_provenance_hash`
  (Phase D.4 alias) and `:prev-hash` (legacy) from datom provenance for
  cross-module portability. Multi-level walking deferred to v0.5.
- **`ProjectionAdapter.fork()` + `DictProjection.fork()`**
  (`persistence.fact.projection`) — `fork(branch_id) -> ProjectionAdapter`
  Protocol method returns a fresh empty adapter; caller drives `rebuild()`
  to populate. `DictProjection.fork()` is the reference implementation.
  Standalone refactor also renamed `apply()` parameter `d` → `datom` for
  Protocol-conformance under pyright strict structural typing.
- **New public exports** from `persistence.fact`: `CausalDAG`, `Provenance`,
  `provenance_from_dict`.
- **`Dispatcher` class** (`persistence.plan._dispatch`) with
  `register(tag, handler)` / `has_handler(tag)` / `dispatch(node, env)`.
  Handler-per-tag registration replaces match-on-tag conditional cascades.
  Walk-order property test under Hypothesis. New public exports from
  `persistence.plan`: `Dispatcher`, `Handler`.
- **`_walk.py` rename** (`persistence.plan`) — `_interpret.py` renamed to
  `_walk.py`; back-compat shim re-exports `walk` from the old name.
- **Audit handler `parent_provenance_hash` alias**
  (`persistence.effect.handlers.audit`) — `audit_entry_to_datom` now writes
  a `parent_provenance_hash` alias alongside `:prev-hash`. Both keys point
  to the same value, bridging the audit chain hash to the typed `Provenance`
  schema so `DB.causal_history()` can walk the chain transparently.

## [0.1.0a1] — 2026-04-20 — Module 1: `persistence.fact`

### Added

- **8-tuple Datom dataclass** (`persistence.fact.Datom`) matching
  `docs/agent1-fact-spec.md` §1 and paper §4.1. Frozen, slotted, refuses
  naive datetimes at construction.
- **`DB` + `DBView` query surface** (`persistence.fact.DB`) implementing
  every method from the spec §2 API:
    - `transact` with auto-retraction of superseded cardinality-one
      asserts — a new assert emits a companion `retract` whose `valid_to`
      closes the prior interval.
    - `as_of` (transaction-time slice)
    - `as_of_valid` (valid-time slice, asserts only)
    - `history` (full lineage for an entity, sorted by tx)
    - `since` (incremental sync / replication)
    - `branch` (counterfactual, isolated in-memory store, hypothetical
      datoms tagged `provenance.source = "branch"`)
- **Storage backends** behind a `Store` Protocol:
    - `InMemoryStore` — reference for tests and the CLI demo
    - `SQLiteStore` — persistent, zero-ops deployment
  with a **portable SQL migration** (`migrations/0001_datom_log.sql`) that
  creates the five covering indexes (EAVT, AEVT, AVET, VAET) plus the VT-E
  bitemporal range index and the log-ordered tx-time index called for in
  agent1-fact-spec §4. The same file runs on SQLite 3.37+ and Postgres 14+.
- **Projection rebuilder** (`persistence.fact.projection`) — a
  `ProjectionAdapter` Protocol (`reset()` + `apply(datom)`), a reference
  `DictProjection`, and a `rebuild(db, adapter)` driver. Kuzu / mem0
  adapters are separate concerns; this module provides the seam.
- **mem0 interceptor adapter** (`persistence.fact.interceptors.mem0_adapter`)
  wrapping a duck-typed mem0 client so every `add` / `update` emits a datom
  before the legacy write. `InterceptorError` is raised (blocking the
  legacy write) if the datom emission fails; if the legacy write fails,
  the datom still persists — operators rebuild the projection from the
  log.
- **CLI demo** (`python -m persistence.fact.demo`) reproducing the
  agent1-fact-spec §8 BankabilityAI WACC counterfactual verbatim.
- **Memory Palace integration doc** (`docs/memory-palace-integration.md`)
  covering the Python import pattern, SQL migration step, rollback
  procedure, and a six-step VPS test plan.

### Verified

- **65 tests green** under `pytest tests/fact/ -v`, spanning both
  InMemoryStore and SQLiteStore backends.
- **`python -m persistence.fact.demo`** prints the three-line factual /
  historical / counterfactual output that matches the spec prototype byte
  for byte.
- **`as-of(db, t)` idempotence invariant** from the conductor track's
  `[:verify {:claim "as-of(db, t) is idempotent for t >= now"}]` gate is
  exercised by an explicit test case (`TestAsOfIdempotence`).

### Deferred to later modules / phases

- Kuzu + mem0 production projection adapters (Phase 2 — agent1-fact-spec §7).
- Historical backfill for Memory Palace (Phase 2, same section).
- Postgres CI smoke test — no credentials available in the worktree; SQL
  migration is identical across backends and operators run the Postgres
  path manually per the integration doc.
- Zstd segment compression, content-addressed storage (§4 storage layout).
- ed25519 provenance signing — batched at the transaction level per §9.
