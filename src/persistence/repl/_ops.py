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

from persistence.effect.canonical import canonical_hash

from ._protocol import (
    ERR_BRANCH_DEPTH_EXCEEDED,
    ERR_CAPABILITY_DENIED,
    ERR_INTERNAL_ERROR,
    ERR_INVALID_PARAMS,
    ERR_REQUEST_HASH_MISMATCH,
    ERR_SESSION_EXPIRED,
    ERR_STALE_CURSOR_EDIT,
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

    D7 wires this against the fact store. Every ``:repl/op`` AuditEntry
    is persisted via :func:`persistence.repl._audit.persist_repl_audit`
    (one ``audit/repl.op`` datom per entry); this query walks
    ``db.log()`` once for ``audit/...`` datoms in the
    ``[from_iso, to_iso]`` window, optionally narrowed by ``op_filter``,
    and reconstructs each into an :class:`AuditEntry` projected to a
    JSON-friendly dict.

    The query is durable across server restart: the in-memory ring
    drops, but the underlying datoms persist in the fact store, so a
    reconnecting client can backfill the full chain. Programmatic audit
    entries (``audit/llm.call``, ``audit/tool.call``, etc.) are
    returned alongside REPL entries — the chain is one Merkle log.

    Errors: malformed ``from_iso`` / ``to_iso`` /
    ``op_filter`` / ``limit`` → ``ERR_INVALID_PARAMS``. The query
    itself is best-effort: a hand-rolled audit datom missing
    ``provenance[":signature"]`` is silently skipped (the window is
    not a verification gate; ``inspect`` callers wanting Merkle
    integrity should run ``verify_chain`` on the returned entries
    themselves).
    """
    from ._audit import _audit_entry_to_summary, _audit_window_query

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
    entries = _audit_window_query(
        db,
        from_iso=sub.get("from_iso"),
        to_iso=sub.get("to_iso"),
        op_filter=op_filter,
        limit=limit,
    )
    return {
        "entries": [_audit_entry_to_summary(e) for e in entries],
        "limit": limit,
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
    causal-history}``; sub-kind args are read FLAT from ``params``
    (every key other than ``kind``). The capability gate is one
    ``inspect:read`` check up-front — all four sub-kinds share it.
    Audit-tail SUBSCRIPTION (a future server-push flow) will be a
    separate ``inspect:audit-tail`` capability when it ships.

    **Param shape (W3 / ADR-12).** The wire shape is FLAT::

        {"kind": "entity", "entity_id": "user-42"}

    not nested under ``params.params``. The DSL parser (browser UI's
    ``parseCommand`` in ``static/app.js``) emits flat key=value pairs;
    matching that on the server keeps the contract symmetric. Inside
    the handler we strip ``kind`` and pass the remaining keys as
    ``sub`` to the per-kind handler — those still read flat keys
    (``entity_id``, ``plan_id``, ``from_iso``, …) so no inner change.

    Cursor precedence (W2 ADR-5): ``params.view_cursor_tx_time_iso``
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
    handler = _INSPECT_KINDS.get(kind)
    if handler is None:
        raise _op_error(
            ERR_INVALID_PARAMS,
            f"unknown kind: {kind!r}; expected one of {sorted(_INSPECT_KINDS)}",
        )
    # W3 / ADR-12 — flat handler params. Every key OTHER than ``kind``
    # is a sub-kind argument. ``kind`` is the dispatcher; the remaining
    # keys are what the per-kind handler consumes.
    sub = {k: v for k, v in params.items() if k != "kind"}
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
# D6 — edit (two-step propose-confirm without server-side preview)
# ---------------------------------------------------------------------------
def _compute_request_hash(params: dict) -> str:
    """Canonical hash over ``params`` minus ``confirm`` and ``request_hash``.

    Per §5.2 + ADR-7: the hash binds the operator's confirm step to the
    exact bytes proposed in step 1. Stripping ``confirm`` lets the same
    payload hash at both steps (step 1 has ``confirm=False`` or omitted;
    step 2 has ``confirm=True``); stripping ``request_hash`` makes the
    re-hash idempotent (the field carries its own value, so it must
    not feed back into itself). Computed via
    :func:`persistence.effect.canonical.canonical_hash` — the same
    path the rest of the substrate uses for content-addressing.
    """
    minus = {k: v for k, v in params.items() if k not in ("confirm", "request_hash")}
    return canonical_hash(minus)


def _validate_datoms(datoms: Any) -> list:
    """Validate the ``datoms`` field shape; raise ``ERR_INVALID_PARAMS``.

    Returns the list (unchanged) on success. Both the propose and
    confirm steps validate the same shape: a non-empty list of dicts
    each carrying ``e``, ``a``, ``v`` keys. ``valid_from`` is OPTIONAL
    on the wire (``db.transact`` defaults to ``self._clock()`` when
    absent).
    """
    if not isinstance(datoms, list) or not datoms:
        raise _op_error(ERR_INVALID_PARAMS, "datoms must be non-empty list")
    for i, d in enumerate(datoms):
        if not isinstance(d, dict):
            raise _op_error(
                ERR_INVALID_PARAMS,
                f"datoms[{i}] must be a dict with e, a, v keys",
            )
        for k in ("e", "a", "v"):
            if k not in d:
                raise _op_error(
                    ERR_INVALID_PARAMS,
                    f"datoms[{i}] missing required key {k!r}",
                )
    return datoms


async def edit_op(
    session: Session, db: Any, params: dict, *, server: Any = None
) -> Any:
    """REPL edit — two-step propose-confirm without server-side preview.

    See design doc §5.2 + ADR-7. Step 1 (``confirm`` omitted or
    ``False``) returns ``{requires_confirmation: True, request_hash,
    echo, preview_note}`` — the operator inspects the echoed payload,
    re-hashes locally if desired, and re-issues with ``confirm: true``
    AND matching ``request_hash`` to commit (step 2).

    The substrate does NOT compute a server-side auto-retract preview
    (W1.D — the earlier ``preview_transact`` extension to ``db.py``
    was withdrawn). Operators wanting to see what auto-retracts can
    run a prior ``inspect`` against the current state; the
    ``request_hash`` then binds the confirm step to the exact bytes
    that were approved.

    Capability matrix (§ADR-3 + §10):

    - ``edit:write`` grants both propose AND confirm.
    - ``edit:propose-only`` grants ONLY propose; the confirm step
      requires ``edit:write``.
    - Neither → ``ERR_CAPABILITY_DENIED`` on either step.

    Stale-cursor reject (§5.2 + ADR-9 ``-32008``): if
    ``session.view_cursor_tx_time_iso`` is set on the confirm step,
    the edit is rejected — operators must ``branch`` first to fork
    the cursor before editing in the past. The propose step is
    permitted at any cursor (it's a read-only re-hash).

    Errors (ADR-9): ``ERR_CAPABILITY_DENIED``,
    ``ERR_REQUEST_HASH_MISMATCH``, ``ERR_STALE_CURSOR_EDIT``,
    ``ERR_INVALID_PARAMS``, ``ERR_INTERNAL_ERROR`` (transact failure).
    """
    has_write = session.cap_set.has("edit", "write")
    has_propose = session.cap_set.has("edit", "propose-only")
    if not (has_write or has_propose):
        raise _op_error(
            ERR_CAPABILITY_DENIED,
            "edit:write or edit:propose-only required",
        )

    datoms = _validate_datoms(params.get("datoms"))
    confirm = params.get("confirm", False)

    if not confirm:
        # Step 1 — propose. No commit, no cursor check; this is a
        # pure re-hash of the input. Both edit:write and
        # edit:propose-only reach this branch.
        request_hash = _compute_request_hash(params)
        return {
            "requires_confirmation": True,
            "request_hash": request_hash,
            "echo": {"datoms": datoms},
            "preview_note": (
                "No server-side auto-retract preview computed. "
                "Re-issue with confirm:true and matching request_hash to commit."
            ),
        }

    # Step 2 — confirm.
    if not has_write:
        # propose-only operators reach the propose branch; the
        # confirm step demands the broader write capability.
        raise _op_error(
            ERR_CAPABILITY_DENIED, "edit:write required for confirm step"
        )

    # Stale-cursor reject: edits at HEAD only; branch first to fork.
    if session.view_cursor_tx_time_iso is not None:
        raise _op_error(
            ERR_STALE_CURSOR_EDIT,
            "edit at past cursor not allowed; branch first",
        )

    # request_hash binds the confirm step to the exact bytes
    # proposed in step 1.
    expected = _compute_request_hash(params)
    provided = params.get("request_hash")
    if provided != expected:
        raise _op_error(
            ERR_REQUEST_HASH_MISMATCH, "request_hash mismatch; re-propose"
        )

    # Commit via the same db.transact path programmatic callers use.
    # ``db.transact`` returns a NEW DB value (functional wrapper);
    # the freshly-allocated tx-id is read off the last datom in the
    # store. We sample ``session.clock()`` BEFORE transact for the
    # response's ``tx_time_iso``; the clock is the same one db uses
    # internally (both wired through the runtime in production).
    now = session.clock()
    try:
        new_db = db.transact(datoms)
    except Exception as e:
        raise _op_error(ERR_INTERNAL_ERROR, f"transact failed: {e}")

    # Read the freshly-allocated tx id off the store. ``transact``
    # appends in one atomic block per call; the last datom's ``tx``
    # is the just-committed transaction id.
    tx: int | None = None
    try:
        all_datoms = list(new_db.store.all_datoms())
        if all_datoms:
            tx = all_datoms[-1].tx
    except Exception:
        # Defensive: if store introspection fails for any reason,
        # the commit still succeeded; we just can't surface tx.
        tx = None

    return {
        "committed": True,
        "tx_time_iso": now.isoformat(),
        "tx": tx,
        "datom_count": len(datoms),
    }


__all__ = [
    "MAX_BRANCH_DEPTH",
    "_compute_request_hash",
    "branch_op",
    "edit_op",
    "inspect_op",
    "rewind_op",
]
