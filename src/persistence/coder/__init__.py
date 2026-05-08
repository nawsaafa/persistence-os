"""Persistence-coder MVP — agent built on the persistence-os substrate.

Phase 2.1a (v0.9.0a1 unreleased): no-op ReAct skeleton.
`run()` raises `CoderStubNotImplemented` on the first un-filled stub.
Subsequent sub-phases (2.1b LLM provider, 2.1c G1 lockfile, 2.2/2.3/2.4)
fill the methods.

Public surface: `Coder`, `CoderStubNotImplemented`. Substrate-side
imports allowed only via `persistence.sdk` (curated SDK discipline,
G1 lockfile contract test lands in 2.1c).
"""

from ._session import Coder, CoderStubNotImplemented
from ._planner_errors import (
    PlanExecutionFailed,
    PlanPayloadValidation,
)
from ._searcher_errors import (
    BranchPayloadValidation,
    BranchSearchFailed,
)
# Phase 2.3c.2 — :llm/call recursion dispatcher errors. Public surface
# for callers catching recursion-budget violations + skill-cycle
# detection at the runtime layer. The DispatcherContext / RecursionBudget
# value types stay private to the coder package (T3-T6 import them
# directly from ``persistence.coder._recursion``).
from ._recursion import (
    LLMRecursionBudgetExceeded,
    SkillCycleDetected,
)

__all__ = [
    "Coder",
    "CoderStubNotImplemented",
    "PlanExecutionFailed",
    "PlanPayloadValidation",
    "BranchPayloadValidation",
    "BranchSearchFailed",
    "LLMRecursionBudgetExceeded",
    "SkillCycleDetected",
]
