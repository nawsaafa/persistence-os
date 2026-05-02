# persistence.plan CHANGELOG

## v0.9.0a1 (unreleased) — Phase 2.0e: delete_step downstream-execution check live (#175)

Phase 2.0e of the persistence-coder MVP. Closes the substrate-backlog
follow-up tracked under the Phase 2.0a "Known gaps" entry below: the
design § 4.1 line 285 invariant ("``delete_step`` only allowed if no
downstream step has executed") is now substrate-enforced.

### Added

- **`delete_step` downstream-execution check.** The Phase 2.0a
  permissive ``TODO #140 follow-up`` comment in
  ``_edit.py::delete_step`` has been replaced with a live check that
  consults ``Transaction.completed_step_ids`` (new field; see
  ``CHANGELOG-txn.md`` for the substrate-side commitment surface).
  When the target step OR any of its descendants is in
  ``tx.completed_step_ids``, deletion now raises
  ``PlanEditDownstreamExecuted``. The error message names the first
  offending node id encountered in pre-order DFS over the target
  subtree.
- **`_first_executed_in_subtree` helper.** Internal pre-order DFS
  walker dedicated to the downstream-check. Tolerates ``:code`` /
  ``:branch`` leaves without raising
  ``UnimplementedNodeKindError`` — the downstream-execution check is
  orthogonal to executor kind dispatch (the question is "did any
  node in the deletion subtree commit to ``tx.completed_step_ids``",
  not "is every node walkable by the executor").
- **`PlanEditDownstreamExecuted` is now LIVE.** Previously a reserved
  exception (Phase 2.0a) for callers to pre-write ``except`` blocks
  against; Phase 2.0e turns the exception into a real raise path.

### Ordering invariants

The downstream-check runs:
- **AFTER** ``_splice_first`` locates the matched subtree, so a
  missing target continues to surface as ``StepIdNotFound`` (no
  downstream-check false-negative on the not-found path).
- **BEFORE** ``_emit_edit_datom``, so a blocked deletion does not
  leave a ``:plan/edit`` datom on the audit chain (ADR-6 invariant —
  no audit-without-effect).

### Tests

- `tests/plan/test_edit.py` — 5 new cases: positive (no downstream
  executed → succeeds), target itself executed (raises), descendant
  executed (raises), step-not-found-with-completed-ids-still-raises-
  not-found (downstream-check does not pre-empt
  ``StepIdNotFound``), and a unit test for the
  ``Transaction.completed_step_ids`` field shape.
- `tests/plan/test_edit_downstream_executed.py` — Hypothesis property
  test at ``@max_examples=200`` exercising the iff invariant
  (``raise iff target_subtree_ids ∩ completed_step_ids ≠ ∅``) over
  random plans + random execution states. 5 consecutive flake-checks
  green per Phase 2.0c-prime precedent.

### Closes

- Phase 2.0a "Known gaps" entry below — ``delete_step`` is no longer
  permissive. The ``# TODO #140 follow-up`` comment has been removed.
- Substrate-backlog #175 (``Transaction.completed_step_ids`` threading
  prerequisite for the lockfile snapshot at Phase 2.4c).

---

## v0.8.5a1 (unreleased — lands at Phase 2.0d sub-tag) — Plan Edit API (#140)

Phase 2.0a of the persistence-coder MVP (Phase 2 of the v1.0 roadmap).
Ships in-flight Plan mutation under transaction so the agent can revise
its Plan AST mid-execution without aborting and re-transacting. Every
edit emits a ``:plan/edit`` audit datom (ADR-6 invariant) that rides
the existing Merkle chain at ``effect/handlers/audit.py``.

### Added

- **`_edit.py`** — Plan Edit API module.
  - `edit_step(plan, step_id, new_op, *, tx) -> Node` — replace a Plan
    AST node by content-address.
  - `insert_step_after(plan, step_id, new_step, *, tx) -> Node` —
    splice a sibling after the matched step.
  - `insert_step_before(plan, step_id, new_step, *, tx) -> Node` —
    splice a sibling before the matched step.
  - `delete_step(plan, step_id, *, tx) -> Node` — remove a step.
  - All ops are functional / immutable (return a new ``Node``; original
    unchanged via frozen-dataclass invariant).
  - All ops require an enclosing ``db.dosync(...)`` body —
    ``PlanEditOutsideDosync`` raised otherwise.
- **`step_id` resolution:** ``Node.id`` (32-hex content-address). On
  duplicate-content subtrees, edit ops target the first occurrence in
  pre-order DFS walk. See module docstring + scratch impl plan
  (`docs/plans/2026-04-30-phase-2.0a-plan-edit-impl.md`) decision 1.
- **Audit invariant (ADR-6):** every successful edit queues a
  ``:plan/edit`` effect intent via ``tx.effect()`` carrying
  ``{plan_id, step_id, before_op_hash, after_op_hash}``. The
  ``txn_commit`` (commit_id) is injected at intent-replay time by
  ``persistence.txn._replay_effect_intents``. The intent rides the
  existing Merkle chain — no new chain code.
- **Errors:** `StepIdNotFound`, `PlanEditOutsideDosync`,
  `PlanEditDownstreamExecuted` (reserved for substrate-backlog
  follow-up — see Known gaps below). All exported from
  ``persistence.plan``.
- **Public surface in `__init__.py`:** `edit_step`, `insert_step_after`,
  `insert_step_before`, `delete_step`, `StepIdNotFound`,
  `PlanEditOutsideDosync`, `PlanEditDownstreamExecuted`.

### Tests

- `tests/plan/test_edit.py` — 19 cases. Unit + Hypothesis @
  max_examples=200 across 4 properties (round-trip byte-identity under
  edit, sibling preservation under insert, count-reduction under
  delete, sequential-edit composition). The "byte-identity replay
  reconstructs P with the M edits applied at exactly the same
  Plan-AST positions" gate from § 4.1 line 290 is the round-trip
  property.
- `tests/plan/test_edit_audit.py` — 4 cases. 3-edit chain-reconstruction
  via `before_op_hash` / `after_op_hash` on the matched-step axis;
  outside-dosync gate trips before any silent emission;
  `make_audit_handler({:plan/edit})` integration confirms entries
  share the same Merkle chain (`verify_chain` accepts the sequence,
  `entries[1].prev_hash == entries[0].id`); mixed edit / insert /
  delete in one dosync emits 3 chained AuditEntries with shared
  `txn_commit`.

### Known gaps (Phase 2.0a documented; deferred)

- `delete_step` ships permissive: design § 4.1 line 285 ("only allowed
  if no downstream step has executed") requires threading a
  `completed_step_ids` set through `Transaction`. Substrate-backlog
  follow-up — needs cross-module substrate change with own ADR. Until
  then, `delete_step` allows any step inside a dosync to be deleted
  regardless of execution state. `PlanEditDownstreamExecuted` exception
  is exported (reserved) so downstream callers can pre-write `except`
  blocks. A `# TODO #140 follow-up` comment marks the spot in
  `_edit.py::delete_step`.
- A separate `find_step_by_path` helper for path-based addressing on
  duplicate-content subtrees is out of scope for #140 — log if Phase
  2 dogfood surfaces a need.

### Test counts

Pre-2.0a baseline: 1880 passed, 32 skipped, 7 xfailed in 27.91s.
Post-2.0a: 1903 passed, 32 skipped, 7 xfailed in 32.47s. (+23 new
tests, no regressions.)

---

## v0.6.5 (2026-04-28) — MCTS: PUCT search + skill-library 4-gate closed loop

Stream B of the v1.0 ferrari-first roadmap. Adds PUCT tree search over
the content-addressed Plan AST, full `:mcts/iteration` provenance for
replay-from-audit-log alone (Prop 6), and `mcts_promote()` chaining
`mcts_search → promote → SkillLibrary.register`.

ARIS R1 PASS at 8.90 / 8.0 (round 3); R2 PASS at 8.94 / 8.7.

### Added

- **`_mcts.py`** — single-flat-file impl (ADR-11). Action ADT
  (`SubstituteLeafAction`, `AddStepAction`, `ComposeWithSkillAction`),
  `MCTSConfig` (`__post_init__` bool-isinstance-FIRST validation),
  `MCTSNode` / `MCTSEdge` dataclasses, `Expander` / `Evaluator`
  protocols (`@runtime_checkable`), `mcts_search()` PUCT loop, cycle
  detection, `MAX_PLAN_DEPTH = 32`.
- **`_mcts_datoms.py`** — `:mcts/iteration` schema (kebab-case attr
  keys), `mcts/prev-hash` Merkle chain, canonical Node round-trip
  (`_node_canonical` / `_node_from_canonical` for W2 M4 closure
  enabling production-LLMExpander Prop-6 defense), reject-reason
  enum, `_search_summary_datom` (start + end).
- **`_mcts_promote.py`** — `mcts_promote()` orchestrator wiring
  search → promote → SkillLibrary.register.
- Public surface re-exports in `__init__.py`: `Action`,
  `SubstituteLeafAction`, `AddStepAction`, `ComposeWithSkillAction`,
  `apply_action`, `MAX_PLAN_DEPTH`, `MCTSConfig`, `MCTSNode`,
  `MCTSEdge`, `Expander`, `LLMExpander`, `Evaluator`,
  `LLMJudgeEvaluator`, `mcts_search`, `MCTSResult`, `mcts_promote`,
  `MCTSPromotionResult`, `PlanDepthExceeded`,
  `ExpanderContractError`, `EvaluatorContractError`.

### Tests

- `tests/plan/test_action_*.py` + `tests/plan/test_mcts_*.py`
  (28 unit files; +153 tests vs `v0.6.0a1`)
- `tests/integration/test_v0_6_5_mcts.py` (load-bearing Prop 6
  replay-from-datoms-alone with byte-identity on `tree_dump`)

### Property pins

- visit-conservation 3-case (`test_mcts_visit_conservation.py`)
- determinism 5x rerun (`test_mcts_determinism_pin.py`)
- iteration datom schema (`test_mcts_iteration_datom_schema.py`)
- expand-output payload + Node canonical round-trip
  (`test_mcts_expand_output_payload_schema.py`)
- replay-loud-stub (`test_mcts_replay_loud_stub.py`)
- search summary datom (`test_mcts_search_summary_datom.py`)
- terminations + `all_evaluations_failed` (`test_mcts_terminations.py`)
- simple_regret with <2 children + visits-sorted top-2
- evaluator non-finite + raises + invalid-action + unregistered-skill
  + cycle (B9 reject paths)

### W1 micro-pass

- `_classify_apply_failure` substring-match on error messages →
  isinstance dispatch via private `_SkillNotRegistered(ValueError)`
  subclass (cousin of Stream A W1.B/G4 string-coercion anti-pattern;
  closes R2 m1).

## v0.6.0a1 (2026-04-28) — Plan execution + optimization + 4-gate promotion

Stream A of the v1.0 ferrari-first roadmap — closes the
"plan as data → plan as runnable program" boundary.

### Added

- **`_execute.py`** — `execute(plan, db, *, dispatcher=None) → ExecutionResult`
  with frozen `LeafResult` / `FailureInfo` envelopes. Per-leaf failure
  capture; only handler-thrown exceptions of an explicitly-allowed set
  propagate.
- **`_metric_registry.py`** — `register_metric` / `lookup_metric` /
  `unregister_metric` with `MetricRef` and `MetricNotRegistered`.
- **`TrainingExample`** + `_canonicalize_training_set(...)` —
  deterministic ordering + canonical EDN form for reproducible DSPy
  optimization.
- **`_optimize.py`** — `_plan_to_dspy_module` forward adapter (lazy DSPy
  import; `OptimizerNotAvailable` when missing); inverse adapter rebuilds
  a `Node` AST with provenance pinning. `optimize(plan, training_set,
  metric, *, db, max_demos=...) → OptimizedPlan` end-to-end MIPROv2
  wrapper. Caller-injectable dispatcher (W1.A4).
- **`_skill_library.py`** — `SkillLibrary` with `register` / `lookup` /
  `list_skills`. Cross-instance idempotency via fact-store log scan;
  conflicting content raises. `_PromotionRecordLike` `@runtime_checkable`
  Protocol decouples this surface from `PromotionRecord`.
- **`_promotion.py`** — four gates (`gate_g1_replay_byte_identity`,
  `gate_g2_audit_chain`, `gate_g3_score_delta`, `gate_g4_stub`) and a
  `promote()` orchestrator. `PromotionRecord` is `@dataclass(frozen=True,
  slots=True)` with content-addressed `promotion_id`.
- **`GateFailure`** moved to `_errors.py` as a typed class with
  class-level `partial_record: Any` attribute.

### W1 fix-pass (post-ARIS R2)

- W1.A G1 strict-key check on `compare()` dict (`TypeError` on missing
  `divergence_step`).
- W1.B G4 `isinstance(approved_raw, bool)` (rejects truthy non-bool).
- W1.C G2 empty audit window → `False` + `UserWarning`.
- W1.F-1 `:signature` required in `_datom_to_wire_for_audit` (raises
  `ValueError` on absence).
- W1.F-2 Simplified `_raise_gate_failure`.
- W1.G G1/G2 contracts: `@runtime_checkable` ReplayEngine Protocol;
  `divergence_step` required key; empty corpus → False+UserWarning;
  audit-window pull mechanism documented in design doc.

### Preserving changes (id-stability)

`PLAN_CANONICAL_VERSION` unchanged at **1**. No changes to
`_canonical_dict`, `_coerce`, or `_walk` semantics. All v0.6.0a1
additions are new modules around the existing AST surface; existing
`Node.id` hashes are bit-stable across this release.

### Suite

`1018 → 1084 passed, 7 xfailed` (+66 over v0.5.1 baseline; +3 W1
pin tests).

## v0.4.0a1 (2026-04-25) — substrate-primitives: Dispatcher + walk rename

### Added

- **`_interpret.py` → `_walk.py` rename.** `_interpret.py` becomes a
  back-compat shim that re-exports `walk` from `_walk.py`. Existing
  imports from `_interpret` continue to work without change.
- **`Dispatcher` class** (`persistence.plan._dispatch`) — new module with
  `register(tag, handler)` / `has_handler(tag)` / `dispatch(node, env)`
  API. Handler-per-tag registration replaces match-on-tag conditional
  cascades inside the walker. Walk-order property test added under
  Hypothesis covering round-trip tag dispatch.
- **New public exports:** `Dispatcher`, `Handler` re-exported from
  `persistence.plan.__init__`.

### Preserving changes (id-stability)

`_walk.py` contains only visitor/walker logic. No changes to
`_canonical_dict`, coercion registry, or serialization. `PLAN_CANONICAL_VERSION`
stays at 1 — zero canonical-form changes in this release.

## v0.3.0a1 (2026-04-25) — R3-M4 coercion registry

Closes the last R3 MAJOR deferred from the v0.2.0a1 ARIS gate. Plan authors
can now put `datetime`, `date`, `bytes`, `Decimal`, `UUID`, `frozenset`, and
`edn_format.Symbol` directly inside `Node.attrs` without `Node.id` raising
`TypeError`. The registry is **static + manifest** (per §6 of the design
doc): the default coercion table is frozen at module import time, runtime
registration is gated behind a test-only env-var sentinel, and a new
`PLAN_CANONICAL_VERSION = 1` constant marks the canonical form for
schema-evolution callers.

- **New module.** `persistence.plan._coerce` — `register_coercion`,
  `unregister_coercion`, `lookup_coercion`, `Coercion` type alias, and 7
  default coercions (datetime/date → `.isoformat()`, bytes → `.hex()`,
  Decimal/UUID → `str(...)`, frozenset → `sorted(...)`, edn_format.Symbol →
  `str(...)`). All re-exported from `persistence.plan.__init__`.
- **Walker.** `_canonical_dict` now passes `node.attrs` through
  `_coerce_value`, which recursively reduces non-JSON-native values via
  the registry. **Coercion is id-time only**: `node.attrs` keeps the
  author-provided values; only the canonical form (and therefore
  `Node.id`) sees the coerced shape. Two Nodes — one with a `datetime`,
  one with the equivalent ISO string — share `Node.id` but compare
  unequal via dataclass `__eq__` (the intended Q1 trade-off — see §4 of
  the design doc).
- **Strict on miss.** Unregistered types raise
  `TypeError("persistence.plan canonical form: no coercion registered
  for type 'X'. Register one via persistence.plan.register_coercion ...")`.
  No silent `repr()` fallback, no quiet truncation; canonical-form
  determinism is non-negotiable for content addressing.
- **Static + manifest.** `register_coercion` raises `RuntimeError` unless
  `PERSISTENCE_PLAN_ALLOW_RUNTIME_REGISTRATION=1`. This closes the
  cross-host divergence hole: two hosts that compute `Node.id` for the
  same logical tree must agree on the registry, and the only way to
  guarantee that is to forbid runtime mutation. `PLAN_CANONICAL_VERSION`
  bumps signal canonical-form-altering changes; consumers persisting
  ids should pin against this constant in their storage layer (extends
  the `~= 0.Y` contract documented at the bottom of this file).
- **Migration.** Parallel registries + `recompute_ids` helper —
  **deferred to v0.4+** per §9 scope cut. v0.3.0a1 ships with
  break-on-change as the only migration path; acceptable because no
  consumers persist ids yet.
- **Symbol coercion now layered.** The v0.1 `_edn_to_python` Symbol →
  `str` workaround is **kept** as parse-boundary defense-in-depth (same
  pattern as the `:id`/`id` strip + reject from v0.2.0a3). The registry
  absorbs direct-construct cases (`Node(attrs={"sig": Symbol("x")})`)
  while parse continues to clean attrs for downstream emit/round-trip.
  Future cleanup (registry-only) deferred to v0.4 per §10 of the design.

### Forward id-stability note

This release introduces `PLAN_CANONICAL_VERSION = 1` and the registry
walker. Adding the walker is **not** an id-breaking change for any Node
that previously had only JSON-native attrs — `_coerce_value` is a
pass-through for `str`/`int`/`float`/`bool`/`None`/`dict`/`list`/`tuple`,
and the canonical bytes are byte-identical to v0.2.0a3 for those nodes.
For nodes that previously raised `TypeError` at id-time (any `datetime`,
`bytes`, etc. in attrs), there were no persisted ids — they couldn't be
computed before — so v0.3.0a1 expands the addressable space without
moving any existing id.

### Tests

`tests/plan/test_coerce.py` (14 tests) — covers all 10 from the design
doc §8 plus 4 bonuses: lookup default for datetime, lookup None for
unregistered, `PLAN_CANONICAL_VERSION == 1`, and runtime registration
rejected outside the sentinel. Full plan suite: **172 passed + 7
xfailed**, up from 158 at v0.2.0a3.

### Public API additions

- `register_coercion(target_type, fn=None, *, replace=False)` — gated
  on `PERSISTENCE_PLAN_ALLOW_RUNTIME_REGISTRATION` env-var sentinel.
  Usable as decorator or function.
- `unregister_coercion(target_type)` — gated, test-only.
- `lookup_coercion(target_type) -> Coercion | None` — exact-type then
  MRO walk; returns `None` (does not raise) on miss.
- `Coercion` — type alias `Callable[[Any], Any]`.
- `PLAN_CANONICAL_VERSION: int = 1` — manifest version constant.

## v0.2.0a3 (2026-04-24) — Prop 5 round-trip falsifier closed

Closes a Hypothesis-found falsifier for Claim 2 (round-trip preserves
`Node.id`). No API additions; one tightening of the construct-time
contract.

- **Falsifier.** `Node(tag=':seq', attrs={'id': None})` round-tripped to a
  Node with a different `Node.id`. Trace:
  `Node(attrs={'id': None})` → id_A (canonical form includes `id`) →
  `unparse` emits `[:seq {:id nil}]` → `parse` strips `:id` from attrs →
  `Node(attrs={})` → id_B ≠ id_A.
- **Root cause.** Construct-vs-parse asymmetry. `_parse.py::_python_to_node`
  strips both `id` and `:id` from incoming EDN attrs (hash-poisoning
  defense). But `Node.__post_init__` accepted the bare key `id` at
  construction, letting internal callers create a Node whose canonical
  form carried `id` but whose round-trip would not.
- **Fix (Option A — reject at construct time).**
  `Node.__post_init__` now rejects attrs keys `"id"` and `":id"` with a
  dedicated reserved-key error. The existing `:foo` leading-colon error
  still fires for other colon-prefixed keys (with the "strip the ':'"
  advice) — but `:id` and `id` now emit the same reserved-key message
  rather than sending the user in a circle (stripping `:id` yields `id`
  which is also rejected). The parser's strip-at-parse remains in place
  as a defense-in-depth layer for external EDN input (hash-poisoning
  defense; see comment at `_parse.py::_python_to_node`). Two layers,
  symmetric: external EDN is cleaned silently, internal construction is
  rejected loudly.
- **Hypothesis strategy fix.** `tests/plan/test_property.py::_attr_key_strat`
  now filters out `"id"` so the strategy generates only legal attr keys.
- **New test.** `tests/plan/test_ast.py::TestAttrKeyShape::
  test_reserved_id_attr_rejected_at_construction` covers both `id` and
  `:id` forms at construction time.

### Forward id-stability note

This is an id-breaking change for any Node that was constructed with
`attrs={'id': X}` pre-fix — but no consumers are in that state. v0.2.0a2
and earlier ship-side tests rejected this node shape at the parse
boundary (`_python_to_node` strip), so no persisted id in the wild
depends on `id` being present in canonical form. The fix tightens the
construct-time contract to match the parse-time one; nothing downstream
rehashed. Consistent with the "reserved attrs rejected" clause of the
R3-M5 schema-evolution contract at the bottom of this CHANGELOG.

### Tests

783 passed, 7 xfailed full repo. Re-runs clean after
`rm -rf .hypothesis` (no cached falsifier).

## v0.2.0a2 (2026-04-24) — hardening micro-batch

Closes 3 R3 MAJORs deferred from the v0.2.0a1 gate. No behavior change for the
happy path; two API-surface refinements for downstream substrate-wide catchers.

- **R3-M2: `PlanSpecError` now inherits from `persistence.spec.SpecError`.**
  Downstream callers can `except SpecError` to catch spec-validation failures
  from any substrate module uniformly. `exc.conform_error` is preserved for
  back-compat; `exc.error` (from `SpecError`) points to the same object.
  `str(exc)` now routes through `_render_error` — one minor surface change.
- **R3-M3: `:original-tag` escape hatch for alias lowering.** When
  `lower_aliases` rewrites a tag (e.g. `:phase → :seq`), the original tag
  is preserved in `attrs["original-tag"]`. Two nodes that differed only by
  their pre-lowered alias now hash to distinct `Node.id`s — id-space
  faithfulness restored. Hand-authored `original-tag` is never clobbered.
  This is an **id-breaking change** for anyone who persisted ids from
  `v0.2.0a1` output of alias-lowered plans; no consumers in that state yet.
- **R3-M5: schema-evolution id-stability contract** documented in a
  dedicated section at the bottom of this CHANGELOG.

## v0.1 (2026-04-28) — initial release

First release of the homoiconic plan AST module. Commits to three claims:

1. **Content-addressed Merkle DAGs.** Every `Node` carries a deterministic
   32-hex-char (128-bit) sha256 `:id` derived from canonical JSON form
   (sort_keys, no whitespace, `allow_nan=False`, pattern matches
   `persistence.replay._canonical`). Birthday-collision probability 1%
   is reached at ~2.6×10^18 plans. Non-finite floats (NaN, Inf) are
   rejected at `:id` time to preserve reflexive equality. User-supplied
   `:id` in parsed EDN is stripped — the module computes `:id`, not the
   caller.
2. **Byte-identical round-trip.** `unparse(parse(x)) == x` for canonical inputs.
   Canonical form: sorted attrs keys, single-space separator, no extraneous whitespace.
3. **Spec validation.** Parse-time conformance against the registered
   `:persistence.plan/node` spec (shipped in `persistence.spec` Phase 1).
   Internal Node → external vector-form converter (`_to_vector_form`) injects
   computed `:id` ephemerally so `Node.attrs` stays uncluttered while the spec
   sees the canonical `[tag {:id ...} *children]` shape it validates.

### R2 fix-pass changes (pre-GA, no consumers yet)

- **Node.id widened 16 → 32 hex (64 → 128 bit).** Breaking vs R1 preview,
  but the module has not shipped a GA so no downstream depends on the
  narrower width. The `:persistence.spec/sha256` regex already accepts
  variable-length hex, so no spec change was needed.
- **NaN/Inf rejected in attrs.** `Node.id` on a Node containing non-finite
  floats raises `ValueError("non-finite float ...")` instead of silently
  producing a hash that compares non-equal to itself.
- **User `:id` stripped at parse time.** Parsing EDN with `{:id "x"}` no
  longer leaks the user string into `Node.attrs`; `:id` is always
  content-addressed. Closes two attack vectors (Canonical poisoning +
  spec-validation clobber via attrs.items() iteration in _to_vector_form).
- **Attr key shape validated.** `Node.attrs` keys must be plain strings
  without leading colon. `Node(attrs={':foo': ...})` or `{1: ...}` now
  raises at construction time.

### Public API

- `Node` — immutable dataclass (tag, attrs, children) with `.id` computed property
- `ID_HEX_WIDTH: int = 32` — module constant (use instead of hard-coding)
- `parse(edn_text, *, lower_aliases=None, strict=True)` — EDN text → Node
- `unparse(node)` — Node → canonical EDN text
- `walk(node, visitor=None)` — depth-first traversal, returns ordered `:id` list
- `ParseError` — malformed EDN shape
- `PlanSpecError` — wraps `ConformError` for spec validation failures
- `UnimplementedNodeKindError` — raised on `:code` / `:branch` leaves when walked

### Deferred (see design doc §3)

- Edit API (`read`/`splice`/`rewrite`/`compose`/`fork`/`promote`) → v0.2
- `:code` sandbox execution → v0.2
- Skill record storage → v0.3
- Pareto Vector Metric emission → v0.4
- Optimizers (MIPROv2 / MCTS / evolutionary) → Phase 3
- `:branch` speculative search → Phase 3
- Per-kind required-attr spec tightening → v0.2 (7 xfail tests pinning scope)

### Tests

151 passed, 7 xfailed in `tests/plan/` (776 passed, 7 xfailed full repo):
- `test_ast.py` — Node construction, canonical form, :id (content-addressing)
- `test_parse.py` — parse, unparse, round-trip, spec validation, alias lowering
- `test_interpret.py` — walk order, visitor, unimplemented kinds
- `test_meta_target.py` — parse the track's own plan.edn (3 pass, 1 xfail)
- `test_misc.py` — unicode, deep nesting, edge cases (7 pass)
- `test_property.py` — hypothesis property tests for claims 1 + 2 (R2 M4)

### Meta-target findings (test_meta_target.py)

The persistence-os-foundation track plan.edn was exercised as the meta-target.
Partial parse succeeded; two v0.2 scope items block the full walk:

1. **Bare `:seq` shorthand** — `[:seq [:tool-call ...]]` without an attrs dict at
   position 1. The v0.1 parser enforces `[tag, dict, *children]` per spec.
   Fix: `_python_to_node()` should inject `{}` when position 1 is a vector.

2. **`edn_format.Symbol` not JSON-serializable** — `->` and similar symbols in
   `:signature` attr values (from EDN `'[datom-schema -> interceptor-py]`) are
   `edn_format.Symbol` objects. `json.dumps` raises `TypeError` in `Node.id`.
   Fix: `_edn_to_python()` should convert `Symbol` to `str(symbol)`.

EDN quote reader macro (`'[...]`) handled by `_sanitize_edn_quotes()` workaround
(strip leading `'` before parse). 4 quote-macros found in track plan, 0 remaining
after sanitization. Track plan `:track/plan` vector: 8405 chars, bracket-balanced.

### Dependencies

- `persistence.spec` (registered `:persistence.plan/node`)
- `edn_format >= 0.7.5` (PyPI)

### Known v0.1 limitations (see ARIS R2/R3 inputs)

- `:persistence.plan/node` spec is lenient on per-kind required attrs
  (e.g., `:tool-call` without `:tool` passes). Tightening is a v0.2
  spec extension; 7 xfail tests pin the intended behavior.
- Alias lowering (`:phase` → `:seq`) is lossy for round-trip by design.
- Walker is pure depth-first; no parallelism for `:par`, no MCTS for
  `:branch`, no unrolling for `:loop`. Executor semantics land in later
  phases.
- `edn_format.Symbol` handled by `_edn_to_python()` via ``str(symbol)``
  coercion (R2 C4). Symbols like ``->`` now survive through Node.id and
  unparse paths. Previously a consumer-driven v0.2 scope item; shipped
  in v0.1 so the meta-target can parse end-to-end.
- Bare node shorthand `[:tag child1 child2]` (no attrs dict at position 1)
  now accepted (R2 C4). `_python_to_node()` injects `{}` when position 1
  is a list and treats everything from index 1 onward as children.
  Previously rejected with "attrs must be map".

## Schema evolution & id stability (contract)

`Node.id` is pinned to: `sha256(canonical-json(_canonical_dict(node)))[:ID_HEX_WIDTH]`
— where `ID_HEX_WIDTH = 32` (see `_ast.py:ID_HEX_WIDTH`) and the canonical
JSON uses `sort_keys=True`, `separators=(",", ":")`, `allow_nan=False`.
Anything that changes those inputs changes every persisted id.

**Breaking changes** — would move `Node.id` for the same logical node.
Downstream stores that key off `Node.id` must re-hash:

- Attr-name rename (e.g. `prompt` → `message`). `_canonical_dict` serializes
  attrs verbatim; a rename rehashes.
- Any change to `_canonical_dict` serialization — field ordering, type
  coercion, null handling, nested-Node handling in attrs.
- Hash algorithm swap (sha256 → blake3) or width change (e.g. 32 → 40 hex).
- Adding a new structural attr that participates in canonical form —
  `original-tag` (R3-M3) is a recent example: it IS canonicalized because
  it sits in `node.attrs`, so enabling alias lowering on a pre-existing
  plan regenerates ids for the aliased nodes only.
- Alias-lowering policy change (toggling the `original-tag` injection
  on/off, changing which aliases are lowered at read time).

**Preserving changes** — safe, will NOT move `Node.id`:

- Visitor / walker logic (`_walk.py`; `_interpret.py` is a back-compat shim as of v0.4).
- Error-message text on `ParseError` / `PlanSpecError`.
- Parser whitespace / comment handling (EDN input-side only).
- CHANGELOG / docstring edits.
- New attr names that are reserved (`:id`, any future `:meta-*` family)
  and explicitly stripped before `_canonical_dict` — see the
  `{"id", ":id"}` strip in `_python_to_node`.

**Version contract for external pinners.** Callers that persist `Node.id`
should pin `persistence.plan ~= 0.2` (or `~= 0.Y` for whatever minor
they first tested against). Minor and patch bumps within the same
major preserve ids; a major bump (`0.x` → `1.0`, or `1.x` → `2.0`)
signals an id-space break.

**Future breaking-change plan.** If a canonical-form change becomes
unavoidable, the migration path is to add a parallel id namespace
(`:id@v2` alongside `:id@v1` in the spec) plus a migration helper
`recompute_ids(node, target="v1" | "v2")`. This keeps old persisted
ids queryable while letting new writes adopt the new form.
