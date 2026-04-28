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

from ._caps import _token_id, validate_token
from ._protocol import (
    ERR_AUTH_FAILED,
    ERR_INTERNAL_ERROR,
    ERR_INVALID_REQUEST,
    ERR_METHOD_NOT_FOUND,
    ERR_PARSE_ERROR,
    ERR_TOKEN_INVALID,
    Request,
    make_error_response,
    make_response,
    parse_request,
)
from ._session import Session, make_session

logger = logging.getLogger(__name__)


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
        """Process one TEXT frame. Returns the (possibly-new) session ref."""
        # 1. JSON parse
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

        # 2. Envelope parse
        try:
            req = parse_request(payload)
        except ValueError as e:
            await ws.send_json(
                make_error_response(payload.get("id"), ERR_INVALID_REQUEST, str(e))
            )
            return session

        # 3. Auth handshake (re-auth on the same socket replaces the session)
        if req.method == "repl/auth":
            new_session = await self._handle_auth(ws, req, session)
            return new_session if new_session is not None else session

        # 4. Pre-auth gate
        if session is None:
            await ws.send_json(
                make_error_response(req.id, ERR_AUTH_FAILED, "must call repl/auth first")
            )
            return None

        # 5. Op dispatch
        op_handler = self.ops.get(req.method)
        if op_handler is None:
            await ws.send_json(
                make_error_response(
                    req.id, ERR_METHOD_NOT_FOUND, f"unknown method: {req.method}"
                )
            )
            return session

        try:
            result = await op_handler(session, self.db, req.params, server=self)
            await ws.send_json(make_response(req.id, result))
        except _OpError as e:
            await ws.send_json(make_error_response(req.id, e.code, e.message, e.data))
            return session
        except Exception as e:  # noqa: BLE001 — surface as JSON-RPC envelope
            logger.exception("op handler error: %s", req.method)
            await ws.send_json(
                make_error_response(req.id, ERR_INTERNAL_ERROR, str(e))
            )
            return session

        # 6. Session-mutation pickup: D4 (rewind) / D5 (branch) ops swap
        # the session record in self._active_sessions; re-read so the
        # dispatcher uses the latest version on the next message.
        return self._active_sessions.get(session.session_id, session)

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
        """
        token_str = req.params.get("token")
        if not isinstance(token_str, str):
            await ws.send_json(
                make_error_response(req.id, ERR_INVALID_REQUEST, "params.token required")
            )
            return None
        cap_set = validate_token(self.db, token_str, runtime_clock=self.runtime_clock)
        if cap_set is None:
            await ws.send_json(
                make_error_response(
                    req.id, ERR_TOKEN_INVALID, "token invalid, expired, or revoked"
                )
            )
            return None
        # Drop the prior session (re-auth overwrites).
        if prior_session is not None:
            self._active_sessions.pop(prior_session.session_id, None)
        token_id = _token_id(token_str)
        session = make_session(token_id, cap_set, runtime_clock=self.runtime_clock)
        self._active_sessions[session.session_id] = session
        await ws.send_json(
            make_response(
                req.id,
                {
                    "session_id": session.session_id,
                    "auth_clock_iso": session.auth_clock_iso,
                    "caps": [
                        {"op": c.op, "qualifier": c.qualifier} for c in cap_set.caps
                    ],
                },
            )
        )
        return session

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
