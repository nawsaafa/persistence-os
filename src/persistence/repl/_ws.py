"""aiohttp single-port WebSocket REPL server (D2).

See design doc ``docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md``
sections ADR-1 (transport: WebSocket+JSON-RPC), ADR-6 (UI bundling:
single aiohttp port serving both static / and ws://), 6.2 (auth
handshake), 7.1 (server module layout), and 7.2 (request/response/event
flows).

Routes:

- ``GET /``    — serves the static UI (D8 will populate
  ``./static/index.html``); a placeholder is returned in the
  pre-D8 window so the server is exercisable in tests.
- ``GET /ws``  — WebSocket upgrade. Each connection maps to one
  :class:`Session` after a successful ``repl/auth`` handshake.

Pre-auth invariant: any op other than ``repl/auth`` on a connection
without a session is rejected with ``ERR_AUTH_FAILED``. The single
entry point is the auth handshake.

Session-mutation pattern (D3-D6 must honor):

    The :class:`Session` dataclass is ``frozen=True``. Ops that change
    the cursor (rewind) or fork (branch) MUST return a new ``Session``
    via ``dataclasses.replace`` and update
    ``self._active_sessions[session.session_id]`` so the WS dispatcher
    picks up the new value on the next message. The current dispatcher
    re-reads ``self._active_sessions`` after each successful op so a
    same-id replacement (rewind) flows through; a fresh-id replacement
    (branch) requires the op to also return the new id to the client
    so subsequent messages can be tagged. (Branch semantics are
    finalised in D5.)
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import WSMsgType, web

from persistence.effect.canonical import canonical_hash
from persistence.effect.handlers.audit import AuditEntry

from ._audit import emit_repl_op_audit, persist_repl_audit
from ._caps import _token_id, validate_token
from ._protocol import (
    ERR_AUTH_FAILED,
    ERR_BRANCH_DEPTH_EXCEEDED,
    ERR_CAPABILITY_DENIED,
    ERR_INTERNAL_ERROR,
    ERR_INVALID_PARAMS,
    ERR_INVALID_REQUEST,
    ERR_METHOD_NOT_FOUND,
    ERR_PARSE_ERROR,
    ERR_REQUEST_HASH_MISMATCH,
    ERR_SESSION_EXPIRED,
    ERR_STALE_CURSOR_EDIT,
    ERR_TOKEN_INVALID,
    ERR_VERIFY_CHAIN_FAILED,
    Request,
    make_error_response,
    make_response,
    parse_request,
)
from ._session import Session, make_session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ADR-9 verdict mapping (W2.B)
# ---------------------------------------------------------------------------
# Application-specific error codes that map to ``verdict="deny"`` on the
# emitted ``:repl/op`` AuditEntry. Per design doc §ADR-9 / §ADR-4 verdict
# table: capability rejection, hash mismatch, stale cursor, expired
# session, depth exceeded, token invalid, and auth-failed are all
# operator-policy denials — distinct from internal-error verdicts which
# signal substrate failures. Any other code (or any non-_OpError
# exception) maps to ``verdict="error"``. Pinned at module level so the
# dispatcher and the per-error-code tests share one source of truth.
_DENY_ERROR_CODES: frozenset[int] = frozenset(
    {
        ERR_AUTH_FAILED,
        ERR_BRANCH_DEPTH_EXCEEDED,
        ERR_CAPABILITY_DENIED,
        ERR_REQUEST_HASH_MISMATCH,
        ERR_SESSION_EXPIRED,
        ERR_STALE_CURSOR_EDIT,
        ERR_TOKEN_INVALID,
    }
)


def _verdict_for_op_error(code: int) -> str:
    """Map an ``_OpError.code`` to the canonical substrate verdict.

    See ``_DENY_ERROR_CODES`` for the explicit deny set. Any other
    application code (e.g. ``ERR_INVALID_PARAMS``,
    ``ERR_VERIFY_CHAIN_FAILED``, ``ERR_INTERNAL_ERROR``) and any
    JSON-RPC-reserved code maps to ``"error"``.
    """
    if code in _DENY_ERROR_CODES:
        return "deny"
    return "error"


# OpHandler signature: (session, db, params, *, server=None) -> result
#
# Handlers that mutate session state (D4 rewind, D5 branch) read the
# ``server`` keyword to register the new ``Session`` in
# ``server._active_sessions[session.session_id]``; the dispatcher's
# post-op re-read picks it up on the next message. Handlers that don't
# need it (D3 inspect, D6 edit pre-confirm) ignore the kwarg.
OpHandler = Callable[..., Awaitable[Any]]


class _OpError(Exception):
    """Raised by op handlers to map to a JSON-RPC error response.

    The ``code`` should be one of the application-specific codes from
    :mod:`persistence.repl._protocol` (ADR-9). The dispatcher catches
    this exception and writes a single error response on the wire;
    other exceptions become ``ERR_INTERNAL_ERROR``.
    """

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class WSServer:
    """aiohttp-backed REPL server (single port, static UI + ws://).

    Construct with the substrate ``db`` and a ``runtime_clock``; the
    clock is threaded through ``validate_token`` and ``make_session``
    so tests can pin a deterministic value (replay-pinable session_id,
    W1.E claim).

    The ``ops`` mapping is overridable for test-injection; the default
    is the four-op skeleton from :mod:`persistence.repl._ops`. D3-D6
    will replace these with real implementations by editing
    ``_ops.py``.
    """

    def __init__(
        self,
        db: Any,
        *,
        runtime_clock: Callable[[], datetime],
        ops: dict[str, OpHandler] | None = None,
        static_dir: Path | None = None,
    ) -> None:
        self.db = db
        self.runtime_clock = runtime_clock
        self.ops: dict[str, OpHandler] = ops if ops is not None else _default_ops_skeleton()
        self.static_dir = static_dir if static_dir is not None else _default_static_dir()
        self.app = web.Application()
        self.app.router.add_get("/", self._serve_index)
        self.app.router.add_get("/ws", self._handle_ws)
        # Active sessions keyed by session_id. Surfaced for testability
        # and so D4/D5 ops can swap the session record after rewind /
        # branch (frozen dataclass + dataclasses.replace pattern).
        self._active_sessions: dict[str, Session] = {}
        # In-memory audit ring (W2.C — hot cache for the WS-tail
        # subscription). Durability lives in the fact store via
        # ``persist_repl_audit``; the ring is bounded so a slow client
        # cannot grow it without bound. v0.7.0a1 uses one process-wide
        # ring; per-session rings would partition the Merkle chain so
        # one sequential ring is the right shape for ``verify_chain``.
        self._audit_entries: list[AuditEntry] = []
        # Default 256 per design §8.2; override-able for tests.
        self._max_ring_size: int = 256

    # ------------------------------------------------------------------
    # HTTP handlers
    # ------------------------------------------------------------------
    async def _serve_index(self, request: web.Request) -> web.Response:
        """Serve the static UI index. D8 will populate ``static/index.html``;
        a placeholder is returned in the pre-D8 window so the route is
        exercisable.
        """
        index_path = self.static_dir / "index.html"
        if not index_path.exists():
            return web.Response(
                text=(
                    "<!DOCTYPE html><meta charset=utf-8>"
                    "<title>persistence.repl</title>"
                    "<p>UI ships in D8.</p>"
                ),
                content_type="text/html",
            )
        return web.FileResponse(index_path)

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        """Accept a WS upgrade and dispatch messages.

        One session per connection. The session ref is plumbed through
        each ``_handle_message`` call as a local variable; rewind /
        branch return new sessions which the dispatcher picks up via
        ``self._active_sessions``.
        """
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        session: Session | None = None
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    session = await self._handle_message(ws, msg.data, session)
                elif msg.type == WSMsgType.ERROR:
                    logger.warning("ws connection error: %s", ws.exception())
                    break
        finally:
            if session is not None:
                self._active_sessions.pop(session.session_id, None)
        return ws

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------
    async def _handle_message(
        self,
        ws: web.WebSocketResponse,
        raw: str,
        session: Session | None,
    ) -> Session | None:
        """Process one TEXT frame. Returns the (possibly-new) session ref.

        D7 wires audit emission across every dispatch path that reaches
        a session-bound op (success, ``_OpError``, internal exception).
        Pre-envelope failures (parse error, invalid request) and
        unauthenticated non-auth ops do NOT emit — there is no session
        principal to bind. Auth (success or fail) is audited inside
        :meth:`_handle_auth`.
        """
        # 1. JSON parse — pre-envelope, no audit possible
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            await ws.send_json(
                make_error_response(None, ERR_PARSE_ERROR, f"parse error: {e}")
            )
            return session
        if not isinstance(payload, dict):
            await ws.send_json(
                make_error_response(
                    None, ERR_INVALID_REQUEST, "request must be a JSON object"
                )
            )
            return session

        # 2. Envelope parse — pre-envelope, no audit possible
        try:
            req = parse_request(payload)
        except ValueError as e:
            await ws.send_json(
                make_error_response(payload.get("id"), ERR_INVALID_REQUEST, str(e))
            )
            return session

        # 3. Auth handshake — audited inside _handle_auth (success+fail
        # both bind a principal; a failed auth audits with the synthetic
        # session-like object built from the presented token's hashed id)
        if req.method == "repl/auth":
            new_session = await self._handle_auth(ws, req, session)
            return new_session if new_session is not None else session

        # 4. Pre-auth gate — no session principal, no audit binding
        if session is None:
            await ws.send_json(
                make_error_response(req.id, ERR_AUTH_FAILED, "must call repl/auth first")
            )
            return None

        # 5. Op dispatch — every path (success, _OpError, exception)
        # emits exactly one :repl/op AuditEntry chained off the prior
        # entry. Latency is wall-clock between ``runtime_clock`` start
        # and end, computed AFTER the op resolves so the audit record's
        # latency_ms is the true op duration.
        op_kind = (
            req.method.removeprefix("repl/")
            if req.method.startswith("repl/")
            else req.method
        )
        start = self.runtime_clock()
        result: Any = None
        verdict = "ok"
        error_str: str | None = None

        op_handler = self.ops.get(req.method)
        if op_handler is None:
            # Unknown method on an authenticated session — audit as
            # ``verdict="error"`` (not in the deny-error-code set per
            # ADR-9: ERR_METHOD_NOT_FOUND is JSON-RPC-reserved, not an
            # operator-policy denial). The op_kind echoes the bare
            # post-``repl/`` segment so principal records what was
            # attempted.
            await ws.send_json(
                make_error_response(
                    req.id, ERR_METHOD_NOT_FOUND, f"unknown method: {req.method}"
                )
            )
            verdict = "error"
            error_str = f"unknown method: {req.method}"
            self._emit_audit_for_op(
                session=session,
                op_kind=op_kind,
                args=req.params,
                verdict=verdict,
                latency_ms=_latency_ms(start, self.runtime_clock()),
                result_hash=None,
                error=error_str,
            )
            return session

        try:
            result = await op_handler(session, self.db, req.params, server=self)
            await ws.send_json(make_response(req.id, result))
        except _OpError as e:
            verdict = _verdict_for_op_error(e.code)
            error_str = e.message
            await ws.send_json(make_error_response(req.id, e.code, e.message, e.data))
        except Exception as e:  # noqa: BLE001 — surface as JSON-RPC envelope
            verdict = "error"
            error_str = str(e)
            logger.exception("op handler error: %s", req.method)
            await ws.send_json(
                make_error_response(req.id, ERR_INTERNAL_ERROR, str(e))
            )

        # 6. Session-mutation pickup BEFORE audit emission so the audit
        # entry's view-cursor reflects the post-rewind / post-branch
        # state. D4 (rewind) / D5 (branch) ops swap the session record
        # in self._active_sessions; we read the latest version here.
        latest_session = self._active_sessions.get(session.session_id, session)

        # 7. Audit emission — exactly one entry per op invocation,
        # whether success / deny / error. Bound to the post-op session
        # so cursors reflect the post-mutation state.
        try:
            result_hash = canonical_hash(result) if result is not None else None
        except (TypeError, ValueError):
            # Defensive: a result that isn't canonical-JSON-serializable
            # still gets audited; we just can't pin its hash. The
            # args_hash + recorded_at + verdict still capture intent.
            result_hash = None

        self._emit_audit_for_op(
            session=latest_session,
            op_kind=op_kind,
            args=req.params,
            verdict=verdict,
            latency_ms=_latency_ms(start, self.runtime_clock()),
            result_hash=result_hash,
            error=error_str,
        )

        return latest_session

    # ------------------------------------------------------------------
    # Audit emission (W2.A + W2.B + W2.C)
    # ------------------------------------------------------------------
    def _emit_audit_for_op(
        self,
        *,
        session: Session,
        op_kind: str,
        args: dict,
        verdict: str,
        latency_ms: int,
        result_hash: str | None,
        error: str | None,
    ) -> None:
        """Emit one ``:repl/op`` AuditEntry + persist it.

        Best-effort persistence: if ``persist_repl_audit`` raises
        (e.g. a write-only fact-store backend, transient I/O), the
        in-memory ring entry is still in ``self._audit_entries`` so the
        WS-tail subscription continues to see it. The next-restart
        backfill will lose this entry, but durability degrades
        gracefully rather than dropping the live op.

        Bounded ring: when the ring exceeds ``self._max_ring_size`` we
        drop the oldest entry. The dropped entry's tx-window is the
        source of the design-doc §8.2 ``repl/audit-event-overflow``
        notification (D8 will surface that on the WS tail).
        """
        entry = emit_repl_op_audit(
            self._audit_entries,
            session=session,
            op_kind=op_kind,
            args=args,
            verdict=verdict,
            latency_ms=latency_ms,
            result_hash=result_hash,
            error=error,
            view_cursor_tx_time_iso=session.view_cursor_tx_time_iso,
            view_cursor_vt_iso=session.view_cursor_vt_iso,
        )
        # Bounded ring — drop oldest if over limit. Drop AFTER the new
        # entry's prev_hash has already been computed off the prior
        # last-of-ring, so the chain pointer in the dropped-window
        # remains intact on the durable side (the fact store has all
        # of it).
        if len(self._audit_entries) > self._max_ring_size:
            del self._audit_entries[0 : len(self._audit_entries) - self._max_ring_size]
        try:
            persist_repl_audit(self.db, entry)
        except Exception as exc:  # noqa: BLE001
            logger.warning("audit persistence failed: %s", exc)

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    async def _handle_auth(
        self,
        ws: web.WebSocketResponse,
        req: Request,
        prior_session: Session | None,
    ) -> Session | None:
        """Validate ``params.token`` + mint a fresh session.

        On success: emits ``{result: {session_id, auth_clock_iso, caps}}``
        and registers the session in ``self._active_sessions``. Re-auth
        on the same WS replaces the prior session record (single
        session per connection invariant).

        D7: every auth attempt is audited. Success emits with the
        newly-minted session (real principal). Denial emits with a
        synthetic session-like object built from the presented token's
        hashed ``token_id`` so the audit trail records WHICH token was
        rejected without leaking the raw token string. Malformed-token
        request (no params.token) audits with token_id="<unknown>".
        """
        start = self.runtime_clock()
        token_str = req.params.get("token")

        # ---------- Malformed request ----------
        if not isinstance(token_str, str):
            await ws.send_json(
                make_error_response(req.id, ERR_INVALID_REQUEST, "params.token required")
            )
            self._emit_auth_audit(
                token_id="<unknown>",
                session_id="<pre-auth>",
                args=req.params,
                verdict="error",
                latency_ms=_latency_ms(start, self.runtime_clock()),
                error="params.token required",
            )
            return None

        # ---------- Validate token ----------
        cap_set = validate_token(self.db, token_str, runtime_clock=self.runtime_clock)
        token_id_for_audit = _token_id(token_str)

        if cap_set is None:
            await ws.send_json(
                make_error_response(
                    req.id, ERR_TOKEN_INVALID, "token invalid, expired, or revoked"
                )
            )
            self._emit_auth_audit(
                token_id=token_id_for_audit,
                session_id="<pre-auth>",
                args=req.params,
                verdict="deny",
                latency_ms=_latency_ms(start, self.runtime_clock()),
                error="token invalid, expired, or revoked",
            )
            return None

        # ---------- Success ----------
        # Drop the prior session (re-auth overwrites).
        if prior_session is not None:
            self._active_sessions.pop(prior_session.session_id, None)
        session = make_session(
            token_id_for_audit, cap_set, runtime_clock=self.runtime_clock
        )
        self._active_sessions[session.session_id] = session
        result_payload = {
            "session_id": session.session_id,
            "auth_clock_iso": session.auth_clock_iso,
            "caps": [
                {"op": c.op, "qualifier": c.qualifier} for c in cap_set.caps
            ],
        }
        await ws.send_json(make_response(req.id, result_payload))
        # Bind the audit to the freshly-minted session so the principal
        # carries the real session_id.
        try:
            result_hash = canonical_hash(result_payload)
        except (TypeError, ValueError):
            result_hash = None
        self._emit_audit_for_op(
            session=session,
            op_kind="auth",
            args=req.params,
            verdict="ok",
            latency_ms=_latency_ms(start, self.runtime_clock()),
            result_hash=result_hash,
            error=None,
        )
        return session

    def _emit_auth_audit(
        self,
        *,
        token_id: str,
        session_id: str,
        args: dict,
        verdict: str,
        latency_ms: int,
        error: str | None,
    ) -> None:
        """Emit a ``:repl/op`` AuditEntry for an auth-failure path.

        No real :class:`Session` exists yet, so we build a minimal
        synthetic principal that satisfies :func:`emit_repl_op_audit`'s
        attribute access (``token_id``, ``session_id``, ``clock``,
        ``view_cursor_tx_time_iso``, ``view_cursor_vt_iso``). The hashed
        ``token_id`` records which token was rejected without exposing
        the raw token string; ``session_id`` is the literal sentinel
        ``"<pre-auth>"`` so audit-window readers can distinguish
        unauthenticated audit rows from session-bound rows.
        """

        class _SyntheticAuthSession:
            def __init__(
                self,
                token_id: str,
                session_id: str,
                clock: Callable[[], datetime],
            ) -> None:
                self.token_id = token_id
                self.session_id = session_id
                self.clock = clock
                self.view_cursor_tx_time_iso: str | None = None
                self.view_cursor_vt_iso: str | None = None

        synth = _SyntheticAuthSession(token_id, session_id, self.runtime_clock)
        self._emit_audit_for_op(
            session=synth,  # type: ignore[arg-type]
            op_kind="auth",
            args=args,
            verdict=verdict,
            latency_ms=latency_ms,
            result_hash=None,
            error=error,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def serve(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        """Run the server until cancelled.

        Used by ``persistence.repl._cli`` (D8 will wire the entry
        point). Tests bypass this via ``aiohttp.test_utils.TestServer``
        on the existing ``self.app``.
        """
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info("REPL listening on http://%s:%d", host, port)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _latency_ms(start: datetime, end: datetime) -> int:
    """Compute the wall-clock latency in non-negative integer
    milliseconds.

    Mirror of the audit handler's ``max(0, int((t1 - t0) * 1000))``
    convention (``audit.py:445``) so REPL-emitted entries carry the
    same ``latency_ms`` shape (``int`` per ``AuditEntry.latency_ms``;
    non-negative even if the substrate clock skews backward under a
    poorly-mocked replay scenario).
    """
    delta = (end - start).total_seconds()
    return max(0, int(delta * 1000))


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
def _default_ops_skeleton() -> dict[str, OpHandler]:
    """Default op map: thin async wrappers around the :mod:`._ops` stubs.

    D3/D4/D5/D6 fill in the bodies of ``inspect_op`` / ``rewind_op`` /
    ``branch_op`` / ``edit_op`` directly in ``_ops.py``; this map
    re-exports them under their JSON-RPC method names so test-injection
    can override individual ops without touching the module.
    """
    from . import _ops

    return {
        "repl/inspect": _ops.inspect_op,
        "repl/edit": _ops.edit_op,
        "repl/rewind": _ops.rewind_op,
        "repl/branch": _ops.branch_op,
    }


def _default_static_dir() -> Path:
    """Default static dir for the REPL UI (D8 populates ``index.html``)."""
    return Path(__file__).parent / "static"


__all__ = [
    "OpHandler",
    "WSServer",
    "_OpError",
]
