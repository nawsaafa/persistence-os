"""REPL op pre-skeleton (D2).

Each handler matches the :class:`OpHandler` signature in ``_ws``:

    async def handler(session: Session, db: Any, params: dict) -> Any: ...

D3 fills :func:`inspect_op`; D4 fills :func:`rewind_op`; D5 fills
:func:`branch_op`; D6 fills :func:`edit_op`. Pre-creating the four
stubs so D3+D4+D5+D6 can be dispatched in parallel after D2 — each
worker only edits its own function body.

Op handlers raise ``_ws._OpError(code, message, data)`` to map to a
JSON-RPC error response with the application-specific codes from
``_protocol`` (ADR-9). Any other exception is caught by the WS
dispatcher and surfaced as an ``ERR_INTERNAL_ERROR`` envelope.
"""
from __future__ import annotations

from typing import Any

from ._session import Session


async def inspect_op(session: Session, db: Any, params: dict) -> Any:
    """REPL inspect (read-only). Ships in D3."""
    raise NotImplementedError("D3")


async def edit_op(session: Session, db: Any, params: dict) -> Any:
    """REPL edit (two-step propose-confirm). Ships in D6."""
    raise NotImplementedError("D6")


async def rewind_op(session: Session, db: Any, params: dict) -> Any:
    """REPL rewind (set view-cursor). Ships in D4."""
    raise NotImplementedError("D4")


async def branch_op(session: Session, db: Any, params: dict) -> Any:
    """REPL branch (fork from cursor). Ships in D5."""
    raise NotImplementedError("D5")


__all__ = [
    "branch_op",
    "edit_op",
    "inspect_op",
    "rewind_op",
]
