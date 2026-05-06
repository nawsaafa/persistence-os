"""Phase 2.3a planner exception module.

Two exception classes used by `persistence.coder._planner` for the
plan-escalation bridge:

- `PlanPayloadValidation(field, reason)` — raised BEFORE `s.plan.execute`
  by `_build_plan_from_payload` (Stage 1 byte budget; Stage 2 parse-error
  wrapper) and `validate_plan_for_2_3a` (Stage 3 semantic validator).
  ValueError subclass for ergonomic broad catches.

- `PlanExecutionFailed(failure: FailureInfo)` — raised AFTER
  `s.plan.execute` returns `ExecutionResult.status == "failed"`.
  Carries structured `FailureInfo` for caller introspection. Native
  Python traceback is UNAVAILABLE in 2.3a — `execute()` captures
  exceptions into a string-only dataclass at `_execute.py:96-114`.
  Documented limitation per design § 7; queued v0.9.x.
"""
from __future__ import annotations

from persistence.plan._execute import FailureInfo

__all__ = ["PlanExecutionFailed", "PlanPayloadValidation"]


class PlanPayloadValidation(ValueError):
    """LLM-emitted plan payload failed pre-execute validation.

    Raised at any of Stage 1 (byte budget) / Stage 2 (parse) /
    Stage 3 (semantic validator) of the plan ingestion pipeline.
    Always surfaced BEFORE `s.plan.execute` runs.
    """

    def __init__(self, *, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"PlanPayloadValidation[{field}]: {reason}")


class PlanExecutionFailed(RuntimeError):
    """A plan handler raised; `s.plan.execute` captured the failure.

    Carries the structured `FailureInfo` so callers can recover
    `failed_node_id`, `failed_tag`, `error_class`, `error_repr`
    without parsing strings. The original exception's `__traceback__`
    is unavailable here — `execute()` captures into FailureInfo
    (string-only) per `_execute.py:96-114`. Documented limitation.
    """

    def __init__(self, *, failure: FailureInfo) -> None:
        self.failure = failure
        super().__init__(
            f"PlanExecutionFailed at {failure.failed_tag}: {failure.error_repr}"
        )
