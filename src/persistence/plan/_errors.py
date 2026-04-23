"""Error types for persistence.plan."""
from __future__ import annotations


class ParseError(ValueError):
    """EDN parse failure with source position."""


class UnimplementedNodeKindError(NotImplementedError):
    """Walker encountered a node kind not supported in this version."""
