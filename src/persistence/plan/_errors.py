"""Error types for persistence.plan."""
from __future__ import annotations

from typing import Any


class ParseError(ValueError):
    """EDN parse failure with source position."""


class UnimplementedNodeKindError(NotImplementedError):
    """Walker encountered a node kind not supported in this version."""


class OptimizerNotAvailable(ImportError):
    """`optimize()` invoked but optional dep unavailable."""


class MetricNotRegistered(KeyError):
    """`MetricRef` lookup miss in the metric registry."""


class PlanDepthExceeded(ValueError):
    """`apply_action` produced a Plan AST whose depth exceeds MAX_PLAN_DEPTH."""


class GateFailure(RuntimeError):
    """A promotion gate (G1/G2/G3/G4) returned False; raised by `promote()`.

    The ``partial_record`` attribute carries the
    ``persistence.plan.PromotionRecord`` snapshot reflecting which gates
    ran (and what their outcomes were) before the failure. ``None`` if
    the failure happened before any gate state was committed.

    Typed as ``Any`` here to avoid an import cycle with
    ``persistence.plan._promotion``; the runtime value is always a
    :class:`persistence.plan.PromotionRecord`.
    """

    partial_record: Any

    def __init__(self, message: str, partial_record: Any = None) -> None:
        super().__init__(message)
        self.partial_record = partial_record
