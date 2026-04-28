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


class ExpanderContractError(ValueError):
    """`Expander.propose` returned proposals violating the prior-sum-to-1.0 contract.

    Raised by the MCTS search loop (B6) on every cache-miss expansion
    when ``abs(sum(prior for _, prior in proposals) - 1.0) >= _PRIOR_TOL``
    (design §8). Empty proposal lists are exempt (terminal-node signal).
    Subclasses ``ValueError`` so callers can ``except ValueError`` if
    they want to bundle expander-contract failures with other invalid-
    input errors.
    """


class EvaluatorContractError(ValueError):
    """`Evaluator.evaluate` returned a non-finite score (NaN/+Inf/-Inf).

    Raised by the MCTS search loop (B9) at the reject-path boundary when
    ``not _is_finite_score(score)`` (design §9 + §13). The corresponding
    ``:mcts/iteration`` datom is written with ``phase="reject"`` and
    ``reason="evaluator_returned_non_finite"``; BACKUP is skipped for that
    iteration; the search continues. Subclasses ``ValueError`` so callers
    can ``except ValueError`` to bundle evaluator-contract failures with
    other invalid-input errors.
    """


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
