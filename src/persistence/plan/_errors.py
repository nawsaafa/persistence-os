"""Error types for persistence.plan."""
from __future__ import annotations


class ParseError(ValueError):
    """EDN parse failure with source position."""


class UnimplementedNodeKindError(NotImplementedError):
    """Walker encountered a node kind not supported in this version."""


class OptimizerNotAvailable(ImportError):
    """`optimize()` invoked but optional dep unavailable."""


class MetricNotRegistered(KeyError):
    """`MetricRef` lookup miss in the metric registry."""


class GateFailure(RuntimeError):
    """A promotion gate (G1/G2/G3/G4) returned False; raised by `promote()`."""
