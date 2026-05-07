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

from dataclasses import replace
from typing import TYPE_CHECKING, Any, Mapping

from persistence.coder._planner import (
    MAX_PLAN_EDN_BYTES,
    validate_plan_for_2_3a,
)
from persistence.coder._planner_errors import PlanPayloadValidation
from persistence.coder._searcher_errors import (
    BranchPayloadValidation,
    BranchSearchFailed,
)
from persistence.plan import Node, parse
from persistence.plan._errors import ParseError
from persistence.plan._mcts import MCTSConfig

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


def _resolve_mcts_config(payload: Mapping[str, Any]) -> MCTSConfig:
    """Stage 4: payload-override resolution + bounds enforcement.

    Stub for T3 — minimal implementation returns the bridge default.
    T3 fills in the dict-merge + bool-numeric rejection + bounds check.
    """
    return _BRANCH_BRIDGE_DEFAULT_CONFIG


def _escalate_branch_body(coder: "Coder", decision: "LLMDecision") -> None:
    """Stub for T6 — happy path lands here. T7 adds failure paths."""
    raise NotImplementedError("T4–T7 fill this body")
