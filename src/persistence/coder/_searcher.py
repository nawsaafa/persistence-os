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

import math
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Mapping

from persistence.coder._planner import (
    MAX_PLAN_EDN_BYTES,
    validate_plan_for_2_3a,
)
from persistence.coder._planner_errors import PlanPayloadValidation
from persistence.coder._prompt import (
    EMIT_BRANCH_PROPOSAL_TOOL_SCHEMA,
    _BRANCH_EXPANDER_SYSTEM_PROMPT,
)
from persistence.coder._searcher_errors import (
    BranchPayloadValidation,
    BranchSearchFailed,
)
from persistence.plan import LLMExpander, Node, parse, unparse
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
    from persistence.coder._types import LLMDecision

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
    target_path = tuple(d.get("target_path", []))
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


def _dry_run_apply_action_safely(plan: Node, action: Action) -> Node | None:
    """Apply action to plan in-memory; return new plan or None on rejection.

    LD2: rejects proposals whose `apply_action` result would fail
    `validate_plan_for_2_3a` (the strict 2.3a validator). Also rejects
    proposals that raise PlanDepthExceeded / ValueError during
    apply_action itself.
    """
    try:
        new_plan = apply_action(plan, action)
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
) -> Sequence[tuple[Action, float]]:
    """Parse :llm/call tool-use response → softmax-normalized proposals.

    Algorithm:
        1. Extract response["tool_calls"][0]["input"]["proposals"] (list of dicts).
        2. For each dict:
            a. Drop ComposeWithSkillAction (LD3: 2.3c defer) by kind string.
            b. Decode via `_decode_action_from_dict` (skip on ValueError/ParseError).
            c. Belt-and-braces isinstance check on ComposeWithSkillAction (FD7).
            d. Dry-run via `_dry_run_apply_action_safely` (skip on None).
        3. Softmax-normalize the surviving raw logits.
        4. Return [(action, prior)] sequence.

    Empty list is acceptable -> MCTS treats as terminal-node signal.
    """
    tool_calls = response.get("tool_calls", [])
    if not tool_calls:
        return ()
    proposals_raw = tool_calls[0].get("input", {}).get("proposals", [])

    surviving: list[tuple[Action, float]] = []
    raw_logits: list[float] = []
    for d in proposals_raw:
        if not isinstance(d, Mapping):
            continue
        # Drop ComposeWithSkillAction at the wrapper (LD3).
        if d.get("kind") == "ComposeWithSkillAction":
            continue
        try:
            action = _decode_action_from_dict(d)
        except (ValueError, ParseError):
            continue
        # Belt-and-braces: reject ComposeWithSkillAction by isinstance even
        # if the LLM mislabeled the kind.
        if isinstance(action, ComposeWithSkillAction):
            continue
        if _dry_run_apply_action_safely(seed_plan, action) is None:
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


def _make_branch_expander(coder: "Coder") -> LLMExpander:
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
    """

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
        return _parse_expander_tool_response(response, plan)

    return LLMExpander(provider=provider)


def _escalate_branch_body(coder: "Coder", decision: "LLMDecision") -> None:
    """Stub for T6 — happy path lands here. T7 adds failure paths."""
    raise NotImplementedError("T4–T7 fill this body")
