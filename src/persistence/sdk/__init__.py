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
# Phase 2.0c-prime #147 — SDK-level re-exports of persistence.plan
# value-shape types adapter authors actually pass and receive. The
# curated subset is intentional: ``Node`` / ``ExecutionResult`` /
# ``OptimizedPlan`` / ``PromotionRecord`` / ``TrainingExample`` /
# ``LeafResult`` / ``FailureInfo`` are the load-bearing return-and-
# argument shapes for ``s.plan.*`` callers. The configuration /
# protocol vocabulary (``MCTSConfig`` / ``Action`` subclasses /
# ``Evaluator`` / ``Expander`` / ``Dispatcher`` / ``Handler`` /
# ``MetricRef`` / ``Coercion`` / ``SkillLibrary`` / error classes)
# stays in :mod:`persistence.plan` — adapter authors who need it
# import directly. The split keeps the SDK contract surface narrow
# (per ADR-1) without forcing every adapter to import the whole
# plan module just to type-hint a return value.
from persistence.plan import (
    ExecutionResult,
    FailureInfo,
    LeafResult,
    Node,
    OptimizedPlan,
    PromotionRecord,
    TrainingExample,
)
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
    "BackendNotInstalled",
    "ExecutionResult",
    "FailureInfo",
    "FoldBranchScore",
    "FoldIntoChooseError",
    "FoldIntoOutsideDosync",
    "FoldIntoResult",
    "ForkBranchResult",
    "ForkChooseError",
    "ForkOutsideDosync",
    "ForkResult",
    "LeafResult",
    "Node",
    "OptimizedPlan",
    "PromotionRecord",
    "Substrate",
    "TrainingExample",
    "UnknownStoreScheme",
    "deprecated",
    "experimental",
    "mcp",
    "open_store",
    "register_backend",
    "stable",
]
