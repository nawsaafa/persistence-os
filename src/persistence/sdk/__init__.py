"""persistence.sdk — Adapter SDK foundation (v0.8.0a1, SDK1 slice).

Per the design doc ``docs/plans/2026-04-29-adapter-sdk-contract-design.md``,
this package ships the in-tree adapter contract for Persistence OS v0.8.
It is the curated public surface every external integrator (LangChain,
OpenAI Assistants, MCP-speaking LLMs, future non-Python clients) binds to.

The package is built in slices per the § 8 task table:

- **SDK1 (this slice)** — URI-scheme dispatch, stability decorators, and
  the empty ``Substrate`` placeholder. Foundation that later SDK + PG
  tasks import against.
- SDK2 — lifecycle helpers (``health_check`` / ``version_info`` /
  ``module_status``) and the curated module subsurfaces
  (``s.fact`` / ``s.effect`` / etc.).
- SDK3 — MCP server core (``persistence.sdk.mcp``).
- SDK4 — runnable MCP entrypoint + AGPL banner.
- SDK5 — spec-doc + lockfile generator (CI gate G7 / G10).

Public surface (SDK1):

- :class:`Substrate`        — curated-namespace facade (placeholder body
                              until SDK2; class is importable today).
- :func:`open_store`        — URI dispatch returning a
                              :class:`~persistence.fact.Store`.
- :class:`UnknownStoreScheme`,
  :class:`BackendNotInstalled` — raised by :func:`open_store`.
- :func:`stable` / :func:`experimental` / :func:`deprecated`
                              — stability decorators per ADR-5 / ADR-16.

Adapter authors should pin imports to ``from persistence.sdk import …``
and treat any reach-through into private modules (``persistence.sdk._*``,
or escape-hatch attributes once SDK2 lands) as out-of-contract per
ADR-1's escape-hatch boundary.
"""
from __future__ import annotations

from persistence.fact import (
    ForkBranchResult,
    ForkChooseError,
    ForkOutsideDosync,
    ForkResult,
)
# Phase 2.0c-prime #147 + Phase 2.0d (#148 closed-as-redundant) —
# SDK-level re-exports of persistence.plan value-shape types AND
# MCTS configuration / protocol vocabulary. The 7 value-shape types
# (``Node`` / ``ExecutionResult`` / ``OptimizedPlan`` /
# ``PromotionRecord`` / ``TrainingExample`` / ``LeafResult`` /
# ``FailureInfo``) are the load-bearing return-and-argument shapes
# for ``s.plan.*`` callers, added under 2.0c-prime.
#
# Phase 2.0d folds in the MCTS configuration + protocol vocabulary
# adapter authors need to drive ``s.plan.mcts_search`` /
# ``s.plan.mcts_promote`` / ``s.plan.apply_action``: ``MCTSConfig``,
# ``MCTSEdge``, ``MCTSNode``, ``MCTSResult``, ``MCTSPromotionResult``,
# the ``Action`` ADT (``Action`` / ``AddStepAction`` /
# ``SubstituteLeafAction`` / ``ComposeWithSkillAction``), and the
# evaluator / expander surface (``Evaluator``, ``Expander``,
# ``LLMExpander``, ``LLMJudgeEvaluator``).
#
# The remaining un-re-exported names — ``Dispatcher`` / ``Handler``
# (dispatch-system types, non-MCTS), ``MetricRef`` / ``Coercion`` /
# ``SkillLibrary`` (registry / factory types), and the plan-level
# error classes — stay in :mod:`persistence.plan` because they are
# either non-MCTS or registry/factory types whose canonical home is
# the underlying module. The split keeps the SDK contract surface
# narrow per ADR-1.
from persistence.plan import (
    Action,
    AddStepAction,
    ComposeWithSkillAction,
    Evaluator,
    ExecutionResult,
    Expander,
    FailureInfo,
    LeafResult,
    LLMExpander,
    LLMJudgeEvaluator,
    MCTSConfig,
    MCTSEdge,
    MCTSNode,
    MCTSPromotionResult,
    MCTSResult,
    Node,
    OptimizedPlan,
    PromotionRecord,
    SubstituteLeafAction,
    TrainingExample,
)
from persistence.repl._caps import Capability, CapabilitySet
from persistence.sdk import mcp  # SDK3: first-party MCP server sub-package
from persistence.sdk._facade import Substrate
from persistence.sdk._fold_into import (
    FoldBranchScore,
    FoldIntoChooseError,
    FoldIntoOutsideDosync,
    FoldIntoResult,
)
from persistence.sdk._stability import deprecated, experimental, stable
from persistence.sdk.uri import (
    BackendNotInstalled,
    UnknownStoreScheme,
    open_store,
    register_backend,
)

__all__ = [
    "Action",
    "AddStepAction",
    "BackendNotInstalled",
    "Capability",
    "CapabilitySet",
    "ComposeWithSkillAction",
    "Evaluator",
    "ExecutionResult",
    "Expander",
    "FailureInfo",
    "FoldBranchScore",
    "FoldIntoChooseError",
    "FoldIntoOutsideDosync",
    "FoldIntoResult",
    "ForkBranchResult",
    "ForkChooseError",
    "ForkOutsideDosync",
    "ForkResult",
    "LLMExpander",
    "LLMJudgeEvaluator",
    "LeafResult",
    "MCTSConfig",
    "MCTSEdge",
    "MCTSNode",
    "MCTSPromotionResult",
    "MCTSResult",
    "Node",
    "OptimizedPlan",
    "PromotionRecord",
    "Substrate",
    "SubstituteLeafAction",
    "TrainingExample",
    "UnknownStoreScheme",
    "deprecated",
    "experimental",
    "mcp",
    "open_store",
    "register_backend",
    "stable",
]
