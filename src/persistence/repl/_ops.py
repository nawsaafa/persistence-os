"""REPL op handlers (D3/D4/D5/D6).

Each handler matches the :class:`OpHandler` signature in ``_ws``::

    async def handler(
        session: Session,
        db: Any,
        params: dict,
        *,
        server: Any = None,
    ) -> Any: ...

Handlers that mutate session state (D4 rewind, D5 branch) register the
new :class:`Session` in ``server._active_sessions[session.session_id]``;
the WS dispatcher re-reads from that dict after each successful op so
the next message sees the updated cursor / depth. Read-only handlers
(D3 inspect) ignore the ``server`` kwarg.

Op handlers raise :class:`persistence.repl._ws._OpError` ``(code,
message, data)`` to map to a JSON-RPC error response with the
application-specific codes from ``_protocol`` (ADR-9). Any other
exception is caught by the WS dispatcher and surfaced as
``ERR_INTERNAL_ERROR``.
"""
from __future__ import annotations

import dataclasses
import hashlib
from datetime import datetime
from typing import Any

from ._protocol import (
    ERR_BRANCH_DEPTH_EXCEEDED,
    ERR_CAPABILITY_DENIED,
    ERR_INVALID_PARAMS,
    ERR_SESSION_EXPIRED,
)
from ._session import Session


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _op_error(*args, **kwargs):
    """Lazy ``_OpError`` factory to avoid a top-level circular import.

    ``_ws`` imports ``_ops`` at module construction (``_default_ops_skeleton``
    pulls the four handlers off ``_ops``); a top-level
    ``from ._ws import _OpError`` here would cycle. Defer to first call.
    """
    from ._ws import _OpError

    return _OpError(*args, **kwargs)


def _coerce_iso(iso_str: str | None, *, field: str) -> datetime | None:
    """Parse an ISO-8601 string to a datetime, or ``None`` if input is None.

    Raises ``_OpError(ERR_INVALID_PARAMS)`` on malformed input. ``field``
    is the param name surfaced in the error message.
    """
    if iso_str is None:
        return None
    if not isinstance(iso_str, str):
        raise _op_error(
            ERR_INVALID_PARAMS, f"{field} must be ISO-8601 string, got {type(iso_str).__name__}"
        )
    try:
        return datetime.fromisoformat(iso_str)
    except ValueError as e:
        raise _op_error(ERR_INVALID_PARAMS, f"{field} must be ISO-8601: {e}")


def _resolve_cursor(
    sub_cursor: str | None, session: Session
) -> tuple[datetime, str]:
    """Resolve the inspection coordinate.

    Precedence: ``sub_cursor`` (op param) → ``session.view_cursor_tx_time_iso``
    (per-session cursor) → ``session.clock()`` (HEAD). Returns
    ``(datetime, iso_str)`` so callers can echo ``cursor_iso`` back to the
    client for traceability.
    """
    iso = sub_cursor if sub_cursor is not None else session.view_cursor_tx_time_iso
    if iso is None:
        t = session.clock()
        return t, t.isoformat()
    t = _coerce_iso(iso, field="view_cursor_tx_time_iso")
    assert t is not None  # not-None guarded by the if-branch above
    return t, iso


def _serialize_datom(d: Any) -> dict:
    """Project a ``Datom`` to a JSON-safe dict.

    The substrate's :class:`persistence.fact.Datom` is a frozen
    dataclass with ``datetime`` fields (``tx_time``, ``valid_from``,
    ``valid_to``). ``dataclasses.asdict`` doesn't render datetimes to
    ISO strings, so we walk the field set explicitly. ``provenance``
    passes through as-is (already a JSON-friendly dict in v0.4.0a1+).
    """
    return {
        "e": d.e,
        "a": d.a,
        "v": d.v,
        "tx": d.tx,
        "tx_time": d.tx_time.isoformat(),
        "valid_from": d.valid_from.isoformat(),
        "valid_to": d.valid_to.isoformat() if d.valid_to is not None else None,
        "op": d.op,
        "provenance": dict(d.provenance),
        "invalidated_by": d.invalidated_by,
    }


# ---------------------------------------------------------------------------
# D3 — inspect
# ---------------------------------------------------------------------------
_INSPECT_DEFAULT_LIMIT = 50
_INSPECT_MAX_LIMIT = 1000


def _inspect_entity(session: Session, db: Any, sub: dict) -> dict:
    """``kind=entity`` projection.

    Reads ``view.entity(entity_id)`` at the resolved cursor coordinate
    and returns ``{"entity": <dict|None>, "cursor_iso": <ISO-8601>}``.
    Empty-projection (entity not in view) returns ``entity: None`` —
    we do NOT raise: "absent" is a valid query result.
    """
    eid = sub.get("entity_id")
    if not isinstance(eid, str) or not eid:
        raise _op_error(ERR_INVALID_PARAMS, "entity_id (non-empty string) required")
    t, cursor_iso = _resolve_cursor(sub.get("view_cursor_tx_time_iso"), session)
    view = db.as_of(t)
    entity = view.entity(eid)
    return {
        "entity": entity if entity else None,
        "cursor_iso": cursor_iso,
    }


def _inspect_audit_window(session: Session, db: Any, sub: dict) -> dict:
    """``kind=audit-window`` projection.

    D7 will wire ``:repl/op`` AuditEntry persistence — the op writes
    each REPL op to the fact store via ``audit_entry_to_datom`` +
    ``db.transact``, and this kind queries that path. Until D7 lands,
    the substrate has no in-DB audit-window query primitive (the
    audit handler appends to a caller-owned list at runtime, not the
    fact store), so this kind returns an empty list with a clear
    indicator that the persistent path is pending.

    Validates ``from_iso`` / ``to_iso`` shape so D7 only has to wire
    the query body — the param contract is locked here.
    """
    _coerce_iso(sub.get("from_iso"), field="from_iso")
    _coerce_iso(sub.get("to_iso"), field="to_iso")
    op_filter = sub.get("op_filter")
    if op_filter is not None and not isinstance(op_filter, str):
        raise _op_error(ERR_INVALID_PARAMS, "op_filter must be string or null")
    limit = sub.get("limit", 100)
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise _op_error(ERR_INVALID_PARAMS, "limit must be int")
    if limit < 0:
        raise _op_error(ERR_INVALID_PARAMS, "limit must be >= 0")
    limit = min(limit, _INSPECT_MAX_LIMIT)
    return {
        "entries": [],
        "limit": limit,
        "pending": "D7 will wire :repl/op persistence",
    }


def _inspect_plan(session: Session, db: Any, sub: dict) -> dict:
    """``kind=plan`` projection.

    v0.6.0a1 (Stream A) lands plan persistence as datoms but does not
    expose a first-class plan-id-to-AST helper — plans live in the
    fact store as ordinary entities keyed by ``plan_id``. We project
    via ``view.entity(plan_id)`` at the resolved cursor; the result
    is the same attribute-bag a programmatic caller would get from
    ``db.as_of(t).entity(plan_id)``. Future plan-AST reification (a
    typed ``PlanAST`` view) is a separate substrate enhancement; the
    REPL surface stays stable through that change because the
    request shape (``plan_id`` + cursor) is unchanged.
    """
    plan_id = sub.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        raise _op_error(ERR_INVALID_PARAMS, "plan_id (non-empty string) required")
    t, cursor_iso = _resolve_cursor(sub.get("view_cursor_tx_time_iso"), session)
    view = db.as_of(t)
    entity = view.entity(plan_id)
    return {
        "plan": entity if entity else None,
        "cursor_iso": cursor_iso,
    }


def _inspect_causal_history(session: Session, db: Any, sub: dict) -> dict:
    """``kind=causal-history`` projection.

    Reads ``db.causal_history(entity_id)``. The substrate returns a
    :class:`CausalDAG` (``seeds`` + ``parents`` map). For the REPL
    surface we project to a JSON-friendly shape: a list of seed datoms
    truncated to ``limit`` plus the parents map. ``limit`` defaults
    to 50, capped at 1000.
    """
    eid = sub.get("entity_id")
    if not isinstance(eid, str) or not eid:
        raise _op_error(ERR_INVALID_PARAMS, "entity_id (non-empty string) required")
    raw_limit = sub.get("limit", _INSPECT_DEFAULT_LIMIT)
    if not isinstance(raw_limit, int) or isinstance(raw_limit, bool):
        raise _op_error(ERR_INVALID_PARAMS, "limit must be int")
    if raw_limit < 0:
        raise _op_error(ERR_INVALID_PARAMS, "limit must be >= 0")
    limit = min(raw_limit, _INSPECT_MAX_LIMIT)
    dag = db.causal_history(eid)
    seeds = list(dag.seeds)[:limit]
    return {
        "seeds": [_serialize_datom(d) for d in seeds],
        "parents": dict(dag.parents),
        "limit": limit,
    }


_INSPECT_KINDS = {
    "entity": _inspect_entity,
    "audit-window": _inspect_audit_window,
    "plan": _inspect_plan,
    "causal-history": _inspect_causal_history,
}


async def inspect_op(
    session: Session, db: Any, params: dict, *, server: Any = None
) -> Any:
    """REPL inspect — capability-gated read-only entity / audit / plan / DAG.

    Dispatch on ``params.kind`` ∈ ``{entity, audit-window, plan,
    causal-history}``; sub-params under ``params.params``. The
    capability gate is one ``inspect:read`` check up-front — all four
    sub-kinds share it. Audit-tail SUBSCRIPTION (a future server-push
    flow) will be a separate ``inspect:audit-tail`` capability when it
    ships.

    Cursor precedence (W2 ADR-5): ``params.params.view_cursor_tx_time_iso``
    overrides ``session.view_cursor_tx_time_iso``; both null → HEAD
    via ``session.clock()``. The resolved cursor is echoed back as
    ``cursor_iso`` for replay-trace alignment.

    Errors (ADR-9): ``inspect:read`` missing →
    ``ERR_CAPABILITY_DENIED``; unknown kind / malformed param /
    out-of-range limit → ``ERR_INVALID_PARAMS``; entity not found →
    ``{entity: null}`` (NOT an error).
    """
    if not session.cap_set.has("inspect", "read"):
        raise _op_error(ERR_CAPABILITY_DENIED, "inspect:read required")
    kind = params.get("kind")
    if not isinstance(kind, str):
        raise _op_error(ERR_INVALID_PARAMS, "kind (string) required")
    sub = params.get("params", {})
    if not isinstance(sub, dict):
        raise _op_error(ERR_INVALID_PARAMS, "params.params must be an object")
    handler = _INSPECT_KINDS.get(kind)
    if handler is None:
        raise _op_error(
            ERR_INVALID_PARAMS,
            f"unknown kind: {kind!r}; expected one of {sorted(_INSPECT_KINDS)}",
        )
    return handler(session, db, sub)


# ---------------------------------------------------------------------------
# D4 — rewind
# ---------------------------------------------------------------------------
async def rewind_op(
    session: Session, db: Any, params: dict, *, server: Any = None
) -> Any:
    """REPL rewind — set the per-session view-cursor.

    NOT a substrate mutation: rewind only updates the cursor on the
    :class:`Session` dataclass via ``dataclasses.replace`` and registers
    the new session in ``server._active_sessions[session_id]``. The WS
    dispatcher re-reads after each op so the next message sees the
    rewound view.

    Setting both ``tx_time_iso`` and ``vt_iso`` to ``None`` clears the
    cursor (subsequent inspect ops fall back to HEAD via
    ``session.clock()``). Capability: ``rewind:any``. The
    ``rewind:branch-only`` qualifier exists for a future
    rewind-only-inside-a-branch policy and is NOT sufficient for
    general rewind.

    Errors (ADR-9): ``rewind:any`` missing → ``ERR_CAPABILITY_DENIED``;
    malformed ISO-8601 → ``ERR_INVALID_PARAMS``.
    """
    if not session.cap_set.has("rewind", "any"):
        raise _op_error(ERR_CAPABILITY_DENIED, "rewind:any required")
    tx_iso = params.get("tx_time_iso")
    vt_iso = params.get("vt_iso")
    # Validate both ISO strings if present (raises ERR_INVALID_PARAMS).
    _coerce_iso(tx_iso, field="tx_time_iso")
    _coerce_iso(vt_iso, field="vt_iso")
    new_session = dataclasses.replace(
        session,
        view_cursor_tx_time_iso=tx_iso,
        view_cursor_vt_iso=vt_iso,
    )
    if server is not None:
        server._active_sessions[session.session_id] = new_session
    return {
        "view_cursor_tx_time_iso": tx_iso,
        "view_cursor_vt_iso": vt_iso,
    }


# ---------------------------------------------------------------------------
# D5 — branch
# ---------------------------------------------------------------------------
MAX_BRANCH_DEPTH = 16


def _derive_branch_id(session_id: str, tx_iso: str) -> str:
    """Deterministic branch handle from ``(session_id, tx_iso)``.

    Replay-pinable: the same parent session + same branch coordinate
    always yields the same ``branch_id`` (16-hex SHA-256 prefix). The
    handle is the operator's surface for follow-up ops; the WS-level
    session_id stays stable across the branch (multi-branch session-DB
    swapping is out of scope per design §3).
    """
    return "branch:" + hashlib.sha256(
        f"{session_id}:{tx_iso}".encode()
    ).hexdigest()[:16]


async def branch_op(
    session: Session, db: Any, params: dict, *, server: Any = None
) -> Any:
    """REPL branch — capability-gated fork-from-cursor.

    Records the branch coordinate on the :class:`Session` dataclass:
    ``view_cursor_tx_time_iso`` is set to the branch's tx_time and
    ``parent_chain_depth`` increments by one. The deterministic
    ``branch_id`` (16-hex of ``sha256(session_id + tx_iso)``) is
    surfaced to the operator as a stable handle. The WS-level
    session_id does NOT change — this sidesteps registry-rekeying
    complexity.

    Failure modes (ADR-9):

    - ``branch:fork`` missing → ``ERR_CAPABILITY_DENIED``.
    - Cap-set ``is_expired(now)`` → ``ERR_SESSION_EXPIRED``. Per
      design §6.4 + W2 MINOR-6, the parent's expiry gates the branch:
      a token that expired in flight may not fork. Checked BEFORE
      depth so an expired session never even reaches the depth gate.
    - ``parent_chain_depth >= 16`` → ``ERR_BRANCH_DEPTH_EXCEEDED``.
    - Malformed ``tx_time_iso`` → ``ERR_INVALID_PARAMS``.
    - Non-string ``label`` → ``ERR_INVALID_PARAMS``.
    """
    if not session.cap_set.has("branch", "fork"):
        raise _op_error(ERR_CAPABILITY_DENIED, "branch:fork required")
    now = session.clock()
    if session.cap_set.is_expired(now):
        raise _op_error(
            ERR_SESSION_EXPIRED, "session token expired; re-auth required"
        )
    if session.parent_chain_depth >= MAX_BRANCH_DEPTH:
        raise _op_error(
            ERR_BRANCH_DEPTH_EXCEEDED,
            f"max_branch_depth ({MAX_BRANCH_DEPTH}) exceeded",
        )

    raw_tx_iso = params.get("tx_time_iso")
    if raw_tx_iso is not None:
        # Validate ISO format (raises ERR_INVALID_PARAMS).
        _coerce_iso(raw_tx_iso, field="tx_time_iso")
        tx_iso = raw_tx_iso
    elif session.view_cursor_tx_time_iso is not None:
        tx_iso = session.view_cursor_tx_time_iso
    else:
        tx_iso = now.isoformat()

    label = params.get("label", "")
    if not isinstance(label, str):
        raise _op_error(ERR_INVALID_PARAMS, "label must be string")

    branch_id = _derive_branch_id(session.session_id, tx_iso)

    new_session = dataclasses.replace(
        session,
        view_cursor_tx_time_iso=tx_iso,
        parent_chain_depth=session.parent_chain_depth + 1,
    )
    if server is not None:
        server._active_sessions[session.session_id] = new_session

    return {
        "branch_id": branch_id,
        "tx_time_iso": tx_iso,
        "parent_chain_depth": new_session.parent_chain_depth,
        "label": label,
    }


# ---------------------------------------------------------------------------
# D6 — edit (stub; pre-skeleton for D5+D6 parallel dispatch)
# ---------------------------------------------------------------------------
async def edit_op(
    session: Session, db: Any, params: dict, *, server: Any = None
) -> Any:
    """REPL edit (two-step propose-confirm). Ships in D6."""
    raise NotImplementedError("D6")


__all__ = [
    "MAX_BRANCH_DEPTH",
    "branch_op",
    "edit_op",
    "inspect_op",
    "rewind_op",
]
