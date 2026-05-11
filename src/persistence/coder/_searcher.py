"""Phase 2.3b — branch-escalation bridge.

Wires the LLM-driven decision loop's `kind="branch"` decisions into the
`persistence.plan._mcts` engine via:

  Stage 1 (byte budget)  : payload["seed_plan_edn"] ≤ MAX_BRANCH_EDN_BYTES
  Stage 2 (parse)        : parse(seed_plan_edn, strict=False)
  Stage 3 (semantic)     : 2.3a's validate_plan_for_2_3a (strict, RE-USED)
  Stage 4 (mcts_config)  : optional payload["mcts_config"] dict-merge
                            over _BRANCH_BRIDGE_DEFAULT_CONFIG, with
                            bool-numeric rejection + bounds enforcement

After validation, `_escalate_branch_body` constructs LLMExpander +
LLMJudgeEvaluator whose providers route through
`substrate.effect.perform(":llm/call", ...)` (LD3), runs
`s.plan.mcts_search`, then invokes 2.3a's
`_planner._escalate_plan_body(coder, synthesized_decision)` ONCE on the
winner's plan EDN. Losing branches NEVER execute their leaves — they're
explored as Plan-AST structures only (LD0 single-execution invariant).

Branch escalation is a TERMINAL mode-switch — `Coder.run()` returns
immediately after `_escalate_branch_body` (matching 2.3a's _escalate_plan
contract at `_session.py:74-82`).

LD0–LD7 reference: docs/plans/2026-05-07-phase-2.3b-mcts-fork-design.md.
"""
from __future__ import annotations

import datetime as dt
import math
import time
import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Mapping

from persistence.coder._planner import (
    MAX_PLAN_EDN_BYTES,
    _escalate_plan_body,
    validate_plan_for_2_3a,
)
from persistence.coder._planner_errors import PlanPayloadValidation
from persistence.coder._prompt import (
    EMIT_BRANCH_PROPOSAL_TOOL_SCHEMA,
    EMIT_BRANCH_SCORE_TOOL_SCHEMA,
    _BRANCH_EVALUATOR_SYSTEM_PROMPT,
    _BRANCH_EXPANDER_SYSTEM_PROMPT,
)
from persistence.coder._searcher_errors import (
    BranchPayloadValidation,
    BranchSearchFailed,
)
from persistence.coder._types import LLMDecision
from persistence.effect.canonical import canonical_dumps, canonical_hash
from persistence.plan import LLMExpander, LLMJudgeEvaluator, Node, parse, unparse
from persistence.plan._errors import ParseError
from persistence.plan._mcts import (
    Action,
    AddStepAction,
    ComposeWithSkillAction,
    MCTSConfig,
    SubstituteLeafAction,
    apply_action,
)

if TYPE_CHECKING:
    from persistence.coder._session import Coder
    from persistence.plan import SkillLibrary

__all__ = [
    "MAX_BRANCH_EDN_BYTES",
    "MAX_BRANCH_EXPANDER_K",
    "MAX_BRANCH_MAX_ITER",
    "_BRANCH_BRIDGE_DEFAULT_CONFIG",
    "_build_seed_plan",
    "_escalate_branch_body",
    "_resolve_mcts_config",
    "_validate_seed_plan_for_2_3b",
]

#: Stage 1 byte budget. Re-uses 2.3a's `MAX_PLAN_EDN_BYTES` — the
#: post-search winner is executed via 2.3a's `_escalate_plan_body` which
#: enforces this budget on the canonical EDN unparse, so the seed plan
#: must already fit. Re-binding the same constant under the 2.3b name
#: keeps grep-discoverability while preserving the single-source-of-
#: truth shape.
MAX_BRANCH_EDN_BYTES: int = MAX_PLAN_EDN_BYTES

#: Branch-bridge cap on `MCTSConfig.max_iter`. LD6: 50-iter ceiling
#: matches the wall-clock posture (50 iters × 4 expander_k ≈ 200 LLM
#: calls ≈ ~3 min per branch escalation at ~1s per :llm/call).
MAX_BRANCH_MAX_ITER: int = 50

#: Branch-bridge cap on `MCTSConfig.expander_k`. LD6.
MAX_BRANCH_EXPANDER_K: int = 4

#: Branch-bridge default config. LD6: distinct from
#: `MCTSConfig()` engine default (which is `max_iter=200`). ALWAYS used
#: by 2.3b unless `decision.payload["mcts_config"]` overrides.
_BRANCH_BRIDGE_DEFAULT_CONFIG: MCTSConfig = MCTSConfig(
    max_iter=MAX_BRANCH_MAX_ITER,
    expander_k=MAX_BRANCH_EXPANDER_K,
)


def _build_seed_plan(payload: Mapping[str, Any]) -> Node:
    """Stage 1 + Stage 2 of seed-plan ingestion: byte-budget + parse.

    Uses parse(strict=False) — FD2 inherited from 2.3a: 2.3a coder-
    specific tags (:fs/read, :code/run, :git/diff, etc.) are outside
    the closed plan-spec enum that strict=True enforces. The semantic
    validator (`_validate_seed_plan_for_2_3b`, which delegates to
    2.3a's `validate_plan_for_2_3a`) provides Stage 3 safety instead.

    Raises:
        BranchPayloadValidation: missing field, non-string field, byte
            budget exceeded, or parse raised ParseError.
    """
    seed_edn = payload.get("seed_plan_edn")
    if seed_edn is None:
        raise BranchPayloadValidation(
            field="seed_plan_edn",
            reason="missing required field 'seed_plan_edn' in decision payload",
        )
    if not isinstance(seed_edn, str):
        raise BranchPayloadValidation(
            field="seed_plan_edn",
            reason=f"expected str, got {type(seed_edn).__name__}",
        )
    if len(seed_edn.encode("utf-8")) > MAX_BRANCH_EDN_BYTES:
        raise BranchPayloadValidation(
            field="seed_plan_edn",
            reason=f"byte budget exceeded ({MAX_BRANCH_EDN_BYTES} max)",
        )
    try:
        return parse(seed_edn, strict=False)
    except ParseError as e:
        raise BranchPayloadValidation(
            field="seed_plan_edn",
            reason=f"parse error: {e}",
        ) from e


def _validate_seed_plan_for_2_3b(plan: Node) -> None:
    """Stage 3 semantic validation. RE-USES 2.3a's `validate_plan_for_2_3a`.

    LD2 R0-fold N1: 2.3b does NOT introduce a looser sibling validator.
    The post-search winner is executed via 2.3a's `_escalate_plan_body`
    which already calls `validate_plan_for_2_3a`; using the same
    validator at the seed layer ensures the search budget isn't wasted
    on plans that can never execute.

    Note: this raises 2.3a's `PlanPayloadValidation` (NOT
    `BranchPayloadValidation`). Callers in `_searcher.py` are
    responsible for catching `PlanPayloadValidation` and re-raising as
    `BranchPayloadValidation` if the bridge's error contract requires
    it. (For T2 happy path testing, 2.3a's exception class is fine —
    both represent ingestion failures.)
    """
    validate_plan_for_2_3a(plan)


#: Numeric MCTSConfig fields the bridge accepts in payload override.
#: Other fields (`simple_regret_threshold`, `simple_regret_window`,
#: `wall_clock_budget_ms`, `seed`) are NOT exposed via payload — caller
#: would need to subclass the bridge to tune them, which 2.4a will
#: address. Keep the surface narrow.
_PAYLOAD_OVERRIDABLE_FIELDS: frozenset[str] = frozenset({
    "max_iter",
    "expander_k",
})

#: Per-field bounds checks. Keys MUST be subset of _PAYLOAD_OVERRIDABLE_FIELDS.
_FIELD_CAPS: Mapping[str, int] = {
    "max_iter": MAX_BRANCH_MAX_ITER,
    "expander_k": MAX_BRANCH_EXPANDER_K,
}


def _resolve_mcts_config(payload: Mapping[str, Any]) -> MCTSConfig:
    """Stage 4: resolve effective MCTSConfig from optional payload override.

    Algorithm:
        1. If `payload["mcts_config"]` is absent -> return branch-bridge default.
        2. If present, must be a Mapping. Otherwise -> BranchPayloadValidation.
        3. For each key in the override mapping:
            a. Key must be in _PAYLOAD_OVERRIDABLE_FIELDS. Otherwise reject.
            b. Value must NOT be bool (FD2: MCTSConfig.__post_init__ rejects
               with ValueError; we coerce to BranchPayloadValidation here for
               uniform error contract).
            c. Value must be int (the caps are int caps; floats coerce
               unexpectedly under MCTSConfig.__post_init__).
            d. Value must be > 0 AND <= field cap.
        4. Build new MCTSConfig via `dataclasses.replace`.

    LD6: `max_iter <= MAX_BRANCH_MAX_ITER` (50); `expander_k <=
    MAX_BRANCH_EXPANDER_K` (4); both > 0 (positivity is also enforced
    by MCTSConfig.__post_init__ but we surface the BranchPayloadValidation
    contract here BEFORE construction).
    """
    raw = payload.get("mcts_config")
    if raw is None:
        return _BRANCH_BRIDGE_DEFAULT_CONFIG
    if not isinstance(raw, Mapping):
        raise BranchPayloadValidation(
            field="mcts_config",
            reason=f"expected dict/mapping, got {type(raw).__name__}",
        )

    overrides: dict[str, int] = {}
    for k, v in raw.items():
        if k not in _PAYLOAD_OVERRIDABLE_FIELDS:
            raise BranchPayloadValidation(
                field=f"mcts_config.{k}",
                reason=(
                    f"unsupported field; only "
                    f"{sorted(_PAYLOAD_OVERRIDABLE_FIELDS)} are payload-overridable"
                ),
            )
        # FD2: bool BEFORE int (`isinstance(True, int) is True`).
        if isinstance(v, bool):
            raise BranchPayloadValidation(
                field=f"mcts_config.{k}",
                reason=f"bool not allowed (got {v!r}); must be a positive int",
            )
        if not isinstance(v, int):
            raise BranchPayloadValidation(
                field=f"mcts_config.{k}",
                reason=f"expected int, got {type(v).__name__}",
            )
        if v <= 0:
            raise BranchPayloadValidation(
                field=f"mcts_config.{k}",
                reason=f"must be > 0, got {v}",
            )
        cap = _FIELD_CAPS[k]
        if v > cap:
            raise BranchPayloadValidation(
                field=f"mcts_config.{k}",
                reason=f"exceeds 2.3b cap (max {cap}, got {v})",
            )
        overrides[k] = v

    return replace(_BRANCH_BRIDGE_DEFAULT_CONFIG, **overrides)


def _softmax_normalize(logits: Sequence[float]) -> list[float]:
    """Stable softmax: subtract max before exp() to avoid overflow.

    Returns a list of priors summing to 1.0 within _PRIOR_TOL. Empty
    input returns []. Single-element input returns [1.0].
    """
    if not logits:
        return []
    if len(logits) == 1:
        return [1.0]
    max_logit = max(logits)
    exps = [math.exp(l - max_logit) for l in logits]
    total = sum(exps)
    return [e / total for e in exps]


def _decode_action_from_dict(d: Mapping[str, Any]) -> Action:
    """Decode a tool-use proposal dict into an Action ADT instance.

    Raises ValueError if the dict shape is invalid for the declared
    `kind`. The wrapper layer catches and drops these (treats malformed
    proposals as empty contributions).
    """
    kind = d.get("kind")
    # I2 (coderabbit T4 review): explicit list-isinstance guard. Without
    # this, target_path="abc" silently becomes ("a","b","c") and
    # target_path={1:2} becomes (1,). Decoder rejects malformed input;
    # wrapper drops on ValueError.
    target_path_raw = d.get("target_path", [])
    if not isinstance(target_path_raw, list):
        raise ValueError(
            f"target_path must be a list of int, got {type(target_path_raw).__name__}"
        )
    target_path = tuple(target_path_raw)
    if kind == "SubstituteLeafAction":
        leaf_edn = d.get("new_leaf_edn")
        if not isinstance(leaf_edn, str):
            raise ValueError("SubstituteLeafAction requires new_leaf_edn: str")
        return SubstituteLeafAction(
            target_path=target_path,
            new_leaf=parse(leaf_edn, strict=False),
        )
    if kind == "AddStepAction":
        child_edn = d.get("new_child_edn")
        at = d.get("at")
        if not isinstance(child_edn, str):
            raise ValueError("AddStepAction requires new_child_edn: str")
        if not isinstance(at, int) or isinstance(at, bool) or at < 0:
            raise ValueError("AddStepAction requires at: non-negative int")
        return AddStepAction(
            target_path=target_path,
            at=at,
            new_child=parse(child_edn, strict=False),
        )
    if kind == "ComposeWithSkillAction":
        skill_id = d.get("skill_id")
        if not isinstance(skill_id, str):
            raise ValueError("ComposeWithSkillAction requires skill_id: str")
        return ComposeWithSkillAction(target_path=target_path, skill_id=skill_id)
    raise ValueError(f"unknown action kind: {kind!r}")


def _dry_run_apply_action_safely(
    plan: Node,
    action: Action,
    *,
    skill_library: "SkillLibrary | None" = None,
) -> Node | None:
    """Apply action to plan in-memory; return new plan or None on rejection.

    LD2: rejects proposals whose `apply_action` result would fail
    `validate_plan_for_2_3a` (the strict 2.3a validator). Also rejects
    proposals that raise PlanDepthExceeded / ValueError during
    apply_action itself.

    Phase 2.3c.2 LD6: ``skill_library`` is forwarded to
    :func:`apply_action` so ``ComposeWithSkillAction`` proposals can
    dry-run without raising ``_SkillNotRegistered`` at the engine layer.
    A None ``skill_library`` makes any compose proposal reject at
    dry-run (matches the 2.3b reject behavior modulo the wrapper-layer
    drops being lifted).
    """
    try:
        new_plan = apply_action(plan, action, skill_library=skill_library)
    except Exception:
        return None
    try:
        validate_plan_for_2_3a(new_plan)
    except PlanPayloadValidation:
        return None
    return new_plan


def _parse_expander_tool_response(
    response: Mapping[str, Any],
    seed_plan: Node,
    *,
    skill_library: "SkillLibrary | None" = None,
) -> Sequence[tuple[Action, float]]:
    """Parse :llm/call tool-use response → softmax-normalized proposals.

    Algorithm (Phase 2.3c.2 LD6 — FD7 lifted):
        1. Extract response["tool_calls"][0]["input"]["proposals"] (list of dicts).
        2. For each dict:
            a. Decode via `_decode_action_from_dict` (skip on ValueError/ParseError).
            b. Dry-run via `_dry_run_apply_action_safely` with
               ``skill_library`` threaded (skip on None — covers
               ComposeWithSkillAction with unregistered skill_id, missing
               skill_library, and engine-layer cycle/depth raises).
        3. Softmax-normalize the surviving raw logits.
        4. Return [(action, prior)] sequence.

    Phase 2.3c.2 LD6: the kind-string drop (was line 361) and isinstance
    belt-and-braces drop (was line 369) are REMOVED. ComposeWithSkillAction
    proposals are now allowed through to the dry-run layer where the
    engine's ``_apply_compose_with_skill`` enforces the skill-library
    contract (raises ``_SkillNotRegistered`` for unregistered skill_ids
    or None library; raises ``_PlanCycleDetected`` for self-grafts).

    Empty list is acceptable -> MCTS treats as terminal-node signal.
    """
    tool_calls = response.get("tool_calls", [])
    if not tool_calls:
        return ()
    proposals_raw = tool_calls[0].get("input", {}).get("proposals", [])
    # I1 (codex T4 review): defend against non-iterable proposals payload —
    # drop to empty rather than raise TypeError into the LLMExpander caller.
    # Strings ARE Sequences but iterating yields chars; reject explicitly.
    if not isinstance(proposals_raw, Sequence) or isinstance(proposals_raw, str):
        return ()

    surviving: list[tuple[Action, float]] = []
    raw_logits: list[float] = []
    for d in proposals_raw:
        if not isinstance(d, Mapping):
            continue
        try:
            action = _decode_action_from_dict(d)
        except (ValueError, ParseError):
            continue
        if _dry_run_apply_action_safely(
            seed_plan, action, skill_library=skill_library
        ) is None:
            continue
        logit = d.get("logit", 0.0)
        if not isinstance(logit, (int, float)) or isinstance(logit, bool):
            continue
        surviving.append((action, float(logit)))
        raw_logits.append(float(logit))

    priors = _softmax_normalize(raw_logits)
    return tuple(
        (act, prior)
        for (act, _logit), prior in zip(surviving, priors, strict=True)
    )


def _make_branch_expander(
    coder: "Coder",
    skill_library: "SkillLibrary | None" = None,
) -> LLMExpander:
    """Construct an `LLMExpander` whose provider routes through :llm/call.

    LD3 invariant: every expander invocation becomes ONE :llm/call audit
    datom on the canonical chain (under the iteration's :mcts/iteration
    provenance group). The provider closure builds the request shape
    `{model, messages, tools}` per `_session.py:134-139` (NOT
    `{system, messages, tools, response_format}` — those keys aren't
    part of the actual :llm/call dispatch).

    LD2 invariant: the dry-run wrapper rejects proposals whose
    apply_action result would fail validate_plan_for_2_3a (the same
    validator the post-search winner is executed under).

    Phase 2.3c.2 LD6: ``skill_library`` is threaded through to
    :func:`_parse_expander_tool_response` so the dry-run layer can
    actually evaluate ``ComposeWithSkillAction`` proposals (vs the
    2.3b FD7 wrapper-layer drop). Falls back to ``coder.skill_library``
    if not explicitly supplied — production CLI wiring (2.4a) sets it
    on the coder at construction time.
    """
    if skill_library is None:
        skill_library = getattr(coder, "skill_library", None)

    def provider(plan: Node, k: int) -> Sequence[tuple[Action, float]]:
        plan_edn = unparse(plan)
        request = {
            "model": coder.model,
            "messages": [
                {"role": "system", "content": _BRANCH_EXPANDER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Seed plan EDN:\n{plan_edn}\n\n"
                        f"Propose up to k={k} structural Actions."
                    ),
                },
            ],
            "tools": [EMIT_BRANCH_PROPOSAL_TOOL_SCHEMA],
        }
        response = coder.substrate.effect.perform(":llm/call", request)
        return _parse_expander_tool_response(
            response, plan, skill_library=skill_library
        )

    return LLMExpander(provider=provider)


def _parse_evaluator_tool_response(response: Mapping[str, Any]) -> float:
    """Parse :llm/call tool-use response → clamped float score.

    Out-of-range / missing / non-numeric -> 0.0 (engine treats as "no
    signal", not a contract violation — `0.0` is finite per
    `_mcts.py:1069` `_is_finite_score` and accepted as a valid score).
    Bool-isinstance check FIRST (FD2 — `isinstance(True, int) is True`
    would let JSON true/false coerce to 1.0/0.0 silently otherwise).
    """
    tool_calls = response.get("tool_calls", [])
    if not tool_calls:
        return 0.0
    # I4 (coderabbit T5 review): defensive guard — if tool_calls[0] is
    # a string/int/list (not a Mapping), .get(...) would AttributeError.
    # Drop to no-signal rather than leak.
    if not isinstance(tool_calls[0], Mapping):
        return 0.0
    raw = tool_calls[0].get("input", {}).get("score")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0.0
    score = float(raw)
    # I1 (coderabbit T5 review): NaN/Inf guard. JSON schema {"type": "number"}
    # accepts NaN/Inf; both `score < 0.0` and `score > 1.0` are False for NaN,
    # so without this guard NaN leaks to the engine and trips _is_finite_score
    # → phase="reject" silent degradation. Treat as no-signal.
    if not math.isfinite(score):
        return 0.0
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def _make_branch_evaluator(
    coder: "Coder",
    skill_library: "SkillLibrary | None" = None,
) -> LLMJudgeEvaluator:
    """Construct an `LLMJudgeEvaluator` whose provider routes through :llm/call.

    LD3: every evaluator invocation becomes ONE :llm/call audit datom
    on the canonical chain (under the iteration's :mcts/iteration
    provenance group).

    FD4: LLMJudgeEvaluator.provider signature is `Callable[[Node],
    float]` (single positional arg).

    Phase 2.3c.2 LD6: ``skill_library`` is accepted as a sibling
    parameter for symmetry with :func:`_make_branch_expander` even though
    the evaluator itself does not currently consume it (the evaluator
    judges already-grafted plans; the skill library was already
    consumed by the engine at expand time). Reserved for future
    evaluator-side composition discipline (e.g. 2.4a confidence-tied
    skill quality scoring).
    """
    del skill_library  # reserved for future use; unused at 2.3c.2

    def provider(plan: Node) -> float:
        plan_edn = unparse(plan)
        request = {
            "model": coder.model,
            "messages": [
                {"role": "system", "content": _BRANCH_EVALUATOR_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Plan EDN:\n{plan_edn}\n\nReturn a score in [0.0, 1.0].",
                },
            ],
            "tools": [EMIT_BRANCH_SCORE_TOOL_SCHEMA],
        }
        response = coder.substrate.effect.perform(":llm/call", request)
        return _parse_evaluator_tool_response(response)

    return LLMJudgeEvaluator(provider=provider)


def _escalate_branch_body(coder: "Coder", decision: LLMDecision) -> None:
    """Phase 2.3b — terminal mode-switch to s.plan.mcts_search.

    Algorithm (LD0–LD7):
        1. Build seed plan: byte-budget + parse + validate (2.3a strict).
        2. Resolve MCTSConfig: bridge default with optional payload override.
        3. Construct LLMExpander + LLMJudgeEvaluator (providers route through
           :llm/call per LD3).
        4. Synthesize started_at_ms (FD1: time.time_ns()//1e6 — W3-rescoped
           to a follow-up phase as LD-4 site 6; int-ms representation
           requires its own conversion contract — see Phase 2.4b design
           §LD-4 / G5 xfail-strict marker).
        5. Run s.plan.mcts_search with db=substrate._db (LD5 audit chain
           lands on the substrate's fact-store directly).
        6. T7 will wrap with try/except for LD4 path-1 (bubble-out exceptions)
           + post-search detection of terminated_by="all_evaluations_failed"
           (LD4 path-2). T6 = happy path only — search-layer failures
           propagate raw and the test-suite distinguishes them by exception
           class.
        7. Synthesize LLMDecision(kind="plan", payload={"plan_edn":
           unparse(winner)}) and call 2.3a's _escalate_plan_body. LD0:
           single execution — losers were structural search-tree nodes
           only, never dispatched.

    Plan execution is a TERMINAL mode-switch — caller (_session.py) returns
    immediately after this returns successfully (or re-raises).
    """
    # Stage 1+2: ingest seed plan (byte-budget + parse).
    seed_plan = _build_seed_plan(decision.payload)

    # Stage 3: semantic validation (re-uses 2.3a's strict validator per LD2).
    # Re-raise as BranchPayloadValidation to keep the bridge's error contract
    # uniform — _validate_seed_plan_for_2_3b raises 2.3a's PlanPayloadValidation
    # but the branch ingestion path's contract is BranchPayloadValidation.
    try:
        _validate_seed_plan_for_2_3b(seed_plan)
    except PlanPayloadValidation as e:
        raise BranchPayloadValidation(field=e.field, reason=e.reason) from e

    # Stage 4: resolve MCTSConfig (Stage 4 of ingestion).
    config = _resolve_mcts_config(decision.payload)

    # Phase 2.3c.2 LD6: thread coder.skill_library through to expander +
    # evaluator factories AND through to mcts_search itself. None is OK
    # (preserves backward compat for coders constructed without a
    # skill_library); the engine's _apply_compose_with_skill rejects
    # proposals at the dry-run layer when skill_library is None.
    skill_library = getattr(coder, "skill_library", None)

    # Stage 5: build LLM-driven expander + evaluator. Providers route through
    # substrate.effect.perform(":llm/call", ...) per LD3 — verified at
    # _searcher.py:_make_branch_expander / _make_branch_evaluator.
    expander = _make_branch_expander(coder, skill_library=skill_library)
    evaluator = _make_branch_evaluator(coder, skill_library=skill_library)

    # FD1: mcts_search.started_at_ms must be a positive int (NOT bool, NOT
    # float; enforced at _mcts.py:870-877). 2.4b W3-rescoped this to a
    # follow-up phase as LD-4 site 6 — int-ms representation requires
    # its own conversion contract (G5 xfail-strict marker pending). For
    # now use raw time module.
    started_at_ms = time.time_ns() // 1_000_000  # noqa: wall-clock — FD1 + W3-rescoped per 2.4b LD-4 site 6

    # Stage 6: run search. db=coder.substrate._db lands :mcts/* provenance
    # datoms directly on the substrate's fact-store (LD5).
    #
    # LD4 Path-1 (bubble-out): try/except catches ExpanderContractError +
    # any raw expander provider exception. Per design R1-fold I1,
    # PlanDepthExceeded is engine-caught at _populate_children
    # phase="reject" — does NOT bubble. We catch broadly (Exception)
    # because any raw provider exception is a possible bubble-out.
    try:
        result = coder.substrate.plan.mcts_search(
            seed_plan,
            expander=expander,
            evaluator=evaluator,
            started_at_ms=started_at_ms,
            config=config,
            db=coder.substrate._db,
            skill_library=skill_library,
        )
    except Exception as exc:
        error_repr = f"{type(exc).__name__}: {exc}"
        _emit_search_failure_act_result(
            coder,
            error_repr=error_repr,
            search_id=None,
            iter_count=None,
            terminated_by=None,
        )
        raise BranchSearchFailed(
            error_repr=error_repr,
            search_id=None,
            iter_count=None,
            terminated_by=None,
        ) from exc

    # LD4 Path-2 (post-search detection): mcts_search returned cleanly
    # but every evaluation failed → no useful winner. Funnel to the same
    # BranchSearchFailed shape (with populated search_id/iter_count/
    # terminated_by) so Coder.run() exits uniformly.
    if result.terminated_by == "all_evaluations_failed":
        error_repr = "all_evaluations_failed"
        _emit_search_failure_act_result(
            coder,
            error_repr=error_repr,
            search_id=result.search_id,
            iter_count=result.iter_count,
            terminated_by=result.terminated_by,
        )
        raise BranchSearchFailed(
            error_repr=error_repr,
            search_id=result.search_id,
            iter_count=result.iter_count,
            terminated_by=result.terminated_by,
        )

    # Stage 7: execute winner ONCE via 2.3a's bridge. LD0 invariant —
    # losers' leaves are structural Plan-AST nodes, never dispatched.
    # Synthesize an LLMDecision(kind="plan") so 2.3a's _escalate_plan_body
    # consumes it identically to a real LLM-emitted plan decision.
    winner_edn = unparse(result.winner)
    synthesized_decision = LLMDecision(
        kind="plan",
        confidence=1.0,
        payload={"plan_edn": winner_edn},
    )
    _escalate_plan_body(coder, synthesized_decision)


def _emit_search_failure_act_result(
    coder: "Coder",
    *,
    error_repr: str,
    search_id: str | None,
    iter_count: int | None,
    terminated_by: str | None,
) -> None:
    """Emit ONE :act/result datom with op=":mcts/search" + error fields.

    Mirrors `_session.py:209-222` (_act._record). Used by BOTH LD4
    failure paths (bubble-out + post-search detection) so observers
    see a uniform shape regardless of which path failed.

    `latency_ms=0` is a deliberate FD — per-leaf wall-clock latency
    tracking is W3-rescoped to a follow-up phase along with the rest
    of the latency metrics work (gated by the `started_at_ms` int-ms
    contract that drove site 6 deferral; see Phase 2.4b design §LD-4).
    """
    now = coder.substrate.effect.perform(":sys/now", {})
    coder.substrate.fact.transact([{
        "e": uuid.uuid4().hex,  # noqa: wall-clock — entity-id (mirrors _session.py:213 _act._record precedent)
        "a": ":act/result",
        "v": canonical_dumps({
            "op": ":mcts/search",
            "args_hash": canonical_hash({
                "search_id": search_id,
                "terminated_by": terminated_by,
            }),
            "result_summary": {
                "search_id": search_id,
                "iter_count": iter_count,
                "terminated_by": terminated_by,
            },
            "error": error_repr,
            "latency_ms": 0,  # FD — latency tracking deferred to 2.4a
        }),
        "valid_from": now,
    }])
