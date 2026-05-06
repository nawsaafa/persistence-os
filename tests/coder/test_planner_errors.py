"""T1 — Phase 2.3a planner exception classes.

Two exception classes for the plan-bridge module:
- `PlanPayloadValidation(field, reason)` — raised by `_build_plan_from_payload`
  + `validate_plan_for_2_3a` BEFORE `s.plan.execute` runs.
- `PlanExecutionFailed(failure: FailureInfo)` — raised by
  `_emit_failure_summary_and_raise` AFTER `s.plan.execute` returns
  `status="failed"`. Carries structured failure info for caller
  introspection. Native traceback unavailable (LD4 documented limitation).
"""
from __future__ import annotations

from persistence.coder._planner_errors import (
    PlanExecutionFailed,
    PlanPayloadValidation,
)
from persistence.plan._execute import FailureInfo


def test_plan_payload_validation_carries_field_and_reason():
    exc = PlanPayloadValidation(field="plan_edn", reason="exceeds 8192-byte budget")
    assert exc.field == "plan_edn"
    assert exc.reason == "exceeds 8192-byte budget"
    # Stringifies into a human-readable form including both pieces.
    msg = str(exc)
    assert "plan_edn" in msg
    assert "8192-byte budget" in msg


def test_plan_payload_validation_is_value_error_subclass():
    """ValueError subclass so callers can `except ValueError` for general
    bad-input handling without losing the specific class."""
    assert issubclass(PlanPayloadValidation, ValueError)


def test_plan_execution_failed_carries_failure_info():
    fi = FailureInfo(
        failed_node_id="abc123",
        failed_tag=":code/run",
        error_class="RuntimeError",
        error_repr="RuntimeError('SENTINEL')",
    )
    exc = PlanExecutionFailed(failure=fi)
    assert exc.failure is fi
    # str() includes failed tag + error_repr for caller-visible signal.
    msg = str(exc)
    assert ":code/run" in msg
    assert "RuntimeError('SENTINEL')" in msg
