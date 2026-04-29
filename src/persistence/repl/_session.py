"""Session dataclass + deterministic session_id derivation.

See design doc ``docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md``
sections 6.2 (deterministic session_id under replay clock) and ADR-5
(view-cursor semantics: per-session, decoupled from branch).

The session is ``frozen=True``; mutations on rewind / branch use
``dataclasses.replace`` and the WSServer holds the latest version per
WS connection. ``session_id`` is content-derived from
``(token_id, auth_clock_iso)`` so a replay with a captured clock value
regenerates the same id byte-identically — pinning the W1.E claim
"deterministic session_id under replay clock". ``uuid4()`` is NOT used.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from ._caps import CapabilitySet


def _derive_session_id(token_id: str, auth_clock_iso: str) -> str:
    """Deterministic session_id from ``(token_id, auth_clock_iso)``.

    Returns 16-hex characters. Replay-pinable: the same
    ``(token_id, auth_clock_iso)`` ALWAYS produces the same session_id,
    so replay with a captured ``auth_clock_iso`` regenerates the id
    byte-identically. This is the W1.E pin from the design doc §6.2.
    """
    return hashlib.sha256(
        f"{token_id}:{auth_clock_iso}".encode()
    ).hexdigest()[:16]


@dataclass(frozen=True)
class Session:
    """One authenticated REPL session.

    ``frozen=True`` per design — rewind / branch return a new
    ``Session`` via ``dataclasses.replace``; the WSServer rebinds the
    connection's session ref to the new value. The view-cursor lives
    on this dataclass (ADR-5); ``parent_chain_depth`` increments on
    branch (root = 0, capped at 16 in D5).

    ``clock`` is the substrate's ``:clock/now``-pinned
    ``Callable[[], datetime]`` injected via
    ``WSServer.serve(host, port, db, *, runtime_clock=...)``. Under
    replay it is substituted by the captured-clock function so
    ``session.clock().timestamp()`` reproduces every ``:repl/op``
    AuditEntry's ``recorded_at`` byte-identically.
    """

    session_id: str
    token_id: str
    cap_set: CapabilitySet
    auth_clock_iso: str
    clock: Callable[[], datetime]
    view_cursor_tx_time_iso: str | None = None
    view_cursor_vt_iso: str | None = None
    parent_chain_depth: int = 0


def make_session(
    token_id: str,
    cap_set: CapabilitySet,
    *,
    runtime_clock: Callable[[], datetime],
) -> Session:
    """Construct a fresh ``Session`` for a successful auth handshake.

    ``auth_clock_iso = runtime_clock().isoformat()`` is captured at
    construction; the session_id is derived from
    ``(token_id, auth_clock_iso)``. Both are fixed for the session's
    lifetime — subsequent ``rewind`` / ``branch`` ops produce new
    Sessions but never regenerate the auth-time clock value.
    """
    auth_clock_iso = runtime_clock().isoformat()
    session_id = _derive_session_id(token_id, auth_clock_iso)
    return Session(
        session_id=session_id,
        token_id=token_id,
        cap_set=cap_set,
        auth_clock_iso=auth_clock_iso,
        clock=runtime_clock,
    )


__all__ = [
    "Session",
    "_derive_session_id",
    "make_session",
]
