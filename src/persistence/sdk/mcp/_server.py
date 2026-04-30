"""MCP server core — stdio JSON-RPC envelope + lifecycle.

Per ADR-9, ADR-15, ADR-15a, ADR-15b, G2 (stdio conformance) + G12 (HTTP
experimental, NOT v0.8 conformance):

- ``MCPServer`` constructs from a :class:`persistence.sdk.Substrate` plus
  a per-server token cap-set (a plain ``set[str]`` of MCP capability
  strings — see :mod:`persistence.sdk.mcp._names`).
- The server speaks the MCP ``2025-06-18`` lifecycle:
  ``initialize`` → ``notifications/initialized`` → ``tools/list``
  / ``tools/call`` / ``resources/list`` / ``resources/read``
  / ``resources/subscribe``. Falls back to ``2025-03-26`` when the
  client requests a known older revision.
- The server is transport-agnostic: :meth:`MCPServer.handle_request`
  takes a parsed JSON-RPC dict, returns a parsed JSON-RPC dict (or a
  list of dicts for batch / no response for notifications).
- :meth:`MCPServer.serve_stdio` is a sync stdio loop reading
  newline-delimited JSON from a readable stream and writing replies
  to a writable stream — the v0.8 stable transport (G2).
- HTTP transport is shipped via :class:`ExperimentalHTTPHandler` —
  feature-flagged, off by default, marked ``@experimental``, NOT in
  the conformance suite (G12 only).
"""
from __future__ import annotations

import json
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, IO, Optional

from persistence.sdk._stability import experimental, stable
from persistence.sdk.mcp._budgets import Budgets
from persistence.sdk.mcp._names import (
    _ALL_MCP_CAPS,
    _AUDIT_TAIL_CAP,
    _AUDIT_TAIL_URI,
    _NAMES,
    _TOOL_ORDER,
    wire_to_verb,
)
from persistence.sdk.mcp._schemas import (
    INPUT_SCHEMAS,
    OUTPUT_SCHEMAS,
)
from persistence.sdk.mcp._tools import (
    TOOL_HANDLERS,
    make_error,
    validate_against_schema,
)


# ---------------------------------------------------------------------------
# JSON-RPC error codes
# ---------------------------------------------------------------------------
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

# Persistence-OS application bands (reused from REPL ADR-9)
ERR_CAPABILITY_DENIED = -32001
ERR_TOKEN_INVALID = -32003


# ---------------------------------------------------------------------------
# MCP version pins
# ---------------------------------------------------------------------------
MCP_PROTOCOL_VERSION_PRIMARY: str = "2025-06-18"
MCP_PROTOCOL_VERSION_FALLBACK: str = "2025-03-26"
MCP_SUPPORTED_VERSIONS: tuple[str, ...] = (
    MCP_PROTOCOL_VERSION_PRIMARY,
    MCP_PROTOCOL_VERSION_FALLBACK,
)


# ---------------------------------------------------------------------------
# Server info banner
# ---------------------------------------------------------------------------
SERVER_NAME: str = "persistence-os-mcp"
SERVER_VERSION: str = "0.8.0a1"


def _make_error_response(
    req_id: Any, code: int, message: str, data: Any = None
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _make_result_response(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


# ---------------------------------------------------------------------------
# Per-token rate limiter (sliding-window, used by replay_check)
# ---------------------------------------------------------------------------
@dataclass
class _RateLimiter:
    """Sliding-window rate limiter. Used for ADR-13's per-token
    ``MCP_REPLAY_RATE_LIMIT_PER_TOKEN``.
    """

    limit: int
    window_s: float
    clock: Callable[[], float]
    _events: deque = field(default_factory=deque)

    def allow(self) -> bool:
        now = self.clock()
        cutoff = now - self.window_s
        while self._events and self._events[0] < cutoff:
            self._events.popleft()
        if len(self._events) >= self.limit:
            return False
        self._events.append(now)
        return True


# ---------------------------------------------------------------------------
# MCPServer — the core SDK3 surface
# ---------------------------------------------------------------------------
@stable("v0.8")
class MCPServer:
    """First-party MCP server for the v0.8 adapter contract.

    Construct via :func:`create_server` (the curated public surface).
    Adapter authors who need a directly-instantiable handle go through
    this class — its constructor is part of the v0.8 contract.

    The server is single-token-per-instance in v0.8 (per ADR-15a). Token
    capabilities are passed in at construction; the wire-side ``tools/list``
    advertises ALL six tools regardless of token caps (clients see the
    full surface), but ``tools/call`` checks the cap and returns
    ``isError: true`` with category ``capability_denied`` on mismatch.
    """

    def __init__(
        self,
        substrate: Any,
        *,
        token_caps: Optional[set[str]] = None,
        budgets: Optional[Budgets] = None,
        clock: Optional[Callable[[], datetime]] = None,
        wallclock: Optional[Callable[[], float]] = None,
        server_name: str = SERVER_NAME,
        server_version: str = SERVER_VERSION,
    ) -> None:
        self._substrate = substrate
        # Default to the full cap-set so a freshly-opened server with
        # no explicit caps is fully usable in single-trust scenarios
        # (in-process tests / desktop dogfooding). Production hosts pass
        # an explicit subset.
        self._token_caps: set[str] = (
            set(token_caps) if token_caps is not None else set(_ALL_MCP_CAPS)
        )
        # Restrict to known caps — silently drop unknown strings so an
        # adapter typo doesn't accidentally grant nothing on an unrelated
        # cap that exists for v0.9.
        self._token_caps = self._token_caps & set(_ALL_MCP_CAPS)
        self._budgets = budgets if budgets is not None else Budgets()
        self._clock = clock or (lambda: datetime.now(tz=timezone.utc))  # noqa: wall-clock -- injection seam, default fallback
        self._wallclock = wallclock or time.monotonic
        self._server_name = server_name
        self._server_version = server_version
        # MCP lifecycle state.
        self._initialized: bool = False
        self._negotiated_version: Optional[str] = None
        self._client_info: Optional[dict[str, Any]] = None
        # Resource subscriptions (URIs the client subscribed to). The
        # server-pushed ``notifications/resources/updated`` is queued
        # here and pulled by transport on tool calls; transports can
        # also poll :meth:`drain_notifications`.
        self._subscriptions: set[str] = set()
        self._pending_notifications: list[dict[str, Any]] = []
        # Per-server replay rate limiter.
        self._replay_rate = _RateLimiter(
            limit=self._budgets.replay_rate_limit_per_token,
            window_s=self._budgets.replay_rate_window_s,
            clock=self._wallclock,
        )
        # Track the audit-tail head for change detection.
        self._last_audit_len: int = len(self._substrate._audit_entries)

    # ------------------------------------------------------------------
    # Public dispatch
    # ------------------------------------------------------------------
    def handle_request(
        self, payload: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """Dispatch a parsed JSON-RPC request payload.

        Returns ``None`` when the request was a notification (per
        JSON-RPC 2.0 — notifications have no ``id`` and no reply).
        Otherwise returns a JSON-RPC result-or-error response dict.
        """
        if not isinstance(payload, dict):
            return _make_error_response(
                None, JSONRPC_INVALID_REQUEST, "request must be an object"
            )
        if payload.get("jsonrpc") != "2.0":
            return _make_error_response(
                payload.get("id"),
                JSONRPC_INVALID_REQUEST,
                "missing or invalid jsonrpc field",
            )
        method = payload.get("method")
        if not isinstance(method, str):
            return _make_error_response(
                payload.get("id"),
                JSONRPC_INVALID_REQUEST,
                "missing or invalid method",
            )
        params = payload.get("params", {})
        if not isinstance(params, dict):
            return _make_error_response(
                payload.get("id"),
                JSONRPC_INVALID_PARAMS,
                "params must be an object",
            )
        req_id = payload.get("id")
        is_notification = "id" not in payload

        # Lifecycle methods
        if method == "initialize":
            result = self._handle_initialize(params)
            if isinstance(result, dict) and "error" in result:
                return _make_error_response(
                    req_id, result["error"]["code"], result["error"]["message"]
                )
            return _make_result_response(req_id, result)

        if method == "notifications/initialized":
            self._initialized = True
            return None  # notification — no reply

        if method == "ping":
            return _make_result_response(req_id, {})

        # Operational methods require initialization
        if not self._initialized:
            if is_notification:
                return None
            return _make_error_response(
                req_id,
                JSONRPC_INVALID_REQUEST,
                "server not initialized — send 'initialize' first",
            )

        if method == "tools/list":
            return _make_result_response(req_id, self._handle_tools_list())

        if method == "tools/call":
            return _make_result_response(
                req_id, self._handle_tools_call(params)
            )

        if method == "resources/list":
            return _make_result_response(
                req_id, self._handle_resources_list()
            )

        if method == "resources/read":
            return _make_result_response(
                req_id, self._handle_resources_read(params)
            )

        if method == "resources/subscribe":
            return _make_result_response(
                req_id, self._handle_resources_subscribe(params)
            )

        if method == "resources/unsubscribe":
            return _make_result_response(
                req_id, self._handle_resources_unsubscribe(params)
            )

        if is_notification:
            return None

        return _make_error_response(
            req_id,
            JSONRPC_METHOD_NOT_FOUND,
            f"method not found: {method}",
        )

    # ------------------------------------------------------------------
    # Lifecycle: initialize
    # ------------------------------------------------------------------
    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        client_version = params.get("protocolVersion")
        if client_version in MCP_SUPPORTED_VERSIONS:
            self._negotiated_version = client_version
        else:
            # Per spec §lifecycle: server selects the highest mutually-
            # supported version. If the client requested an unknown
            # one, the server replies with its primary; clients are
            # responsible for reconciling.
            self._negotiated_version = MCP_PROTOCOL_VERSION_PRIMARY
        self._client_info = params.get("clientInfo")
        return {
            "protocolVersion": self._negotiated_version,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {
                    "subscribe": True,
                    "listChanged": False,
                },
            },
            "serverInfo": {
                "name": self._server_name,
                "version": self._server_version,
            },
            "instructions": (
                "Persistence OS MCP server v0.8 — first-party adapter. "
                "v0.8 MCP makes NO confidentiality guarantees — for "
                "trusted-host scenarios only; sensitive-data deployments "
                "wait for v0.9 privacy-arch."
            ),
        }

    # ------------------------------------------------------------------
    # tools/list
    # ------------------------------------------------------------------
    def _handle_tools_list(self) -> dict[str, Any]:
        tools = []
        for verb in _TOOL_ORDER:
            wire = _NAMES[verb]["wire"]
            tools.append({
                "name": wire,
                "description": _tool_description(verb),
                "inputSchema": INPUT_SCHEMAS[verb],
                "outputSchema": OUTPUT_SCHEMAS[verb],
            })
        return {"tools": tools}

    # ------------------------------------------------------------------
    # tools/call
    # ------------------------------------------------------------------
    def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        wire_name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(wire_name, str):
            return make_error(
                category="validation_error",
                message="tools/call requires a string 'name'",
            )
        verb = wire_to_verb(wire_name)
        if verb is None:
            return make_error(
                category="validation_error",
                message=f"unknown tool: {wire_name}",
            )
        if not isinstance(arguments, dict):
            return make_error(
                category="validation_error",
                message="tools/call 'arguments' must be an object",
            )
        # Validate input against the published schema BEFORE dispatch.
        err = validate_against_schema(arguments, INPUT_SCHEMAS[verb])
        if err is not None:
            return make_error(
                category="validation_error",
                message=f"input schema violation: {err}",
            )

        handler = TOOL_HANDLERS[verb]
        kwargs: dict[str, Any] = {
            "token_caps": self._token_caps,
            "budgets": self._budgets,
            "clock": self._clock,
        }
        if verb == "replay_check":
            kwargs["rate_limiter"] = self._replay_rate.allow

        result = handler(self._substrate, arguments, **kwargs)

        # Notify any audit-tail subscribers if the audit list grew.
        self._maybe_emit_audit_tail_notification()

        return result

    # ------------------------------------------------------------------
    # resources/*
    # ------------------------------------------------------------------
    def _handle_resources_list(self) -> dict[str, Any]:
        return {
            "resources": [
                {
                    "uri": _AUDIT_TAIL_URI,
                    "name": "audit_tail",
                    "description": (
                        "Live audit-chain projection (last N entries). "
                        "Subscribe via resources/subscribe to receive "
                        "notifications/resources/updated when the chain "
                        "advances."
                    ),
                    "mimeType": "application/json",
                }
            ]
        }

    def _handle_resources_read(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri")
        if uri != _AUDIT_TAIL_URI:
            return make_error(
                category="not_found",
                message=f"unknown resource uri: {uri}",
            )
        if _AUDIT_TAIL_CAP not in self._token_caps:
            return make_error(
                category="capability_denied",
                message=f"capability denied: {_AUDIT_TAIL_CAP}",
            )
        entries: list = list(self._substrate._audit_entries)
        # Default tail size: last 32 entries.
        tail = entries[-32:]
        rendered: list[dict[str, Any]] = []
        for tx, e in enumerate(tail, start=max(0, len(entries) - 32)):
            if isinstance(e, dict):
                rendered.append({
                    "op": e.get("op", ""),
                    "tx": tx,
                    "id": e.get("id", ""),
                })
            else:
                rendered.append({
                    "op": getattr(e, "op", ""),
                    "tx": tx,
                    "id": getattr(e, "id", "") or "",
                })
        return {
            "contents": [
                {
                    "uri": _AUDIT_TAIL_URI,
                    "mimeType": "application/json",
                    "text": json.dumps({"entries": rendered}),
                }
            ]
        }

    def _handle_resources_subscribe(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        uri = params.get("uri")
        if uri != _AUDIT_TAIL_URI:
            return make_error(
                category="not_found",
                message=f"unknown resource uri: {uri}",
            )
        if _AUDIT_TAIL_CAP not in self._token_caps:
            return make_error(
                category="capability_denied",
                message=f"capability denied: {_AUDIT_TAIL_CAP}",
            )
        self._subscriptions.add(uri)
        return {"ok": True}

    def _handle_resources_unsubscribe(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        uri = params.get("uri")
        self._subscriptions.discard(uri)
        return {"ok": True}

    def _maybe_emit_audit_tail_notification(self) -> None:
        """Queue a ``notifications/resources/updated`` if the audit-tail
        is subscribed and the chain advanced.
        """
        if _AUDIT_TAIL_URI not in self._subscriptions:
            self._last_audit_len = len(self._substrate._audit_entries)
            return
        cur_len = len(self._substrate._audit_entries)
        if cur_len > self._last_audit_len:
            self._pending_notifications.append({
                "jsonrpc": "2.0",
                "method": "notifications/resources/updated",
                "params": {"uri": _AUDIT_TAIL_URI},
            })
            self._last_audit_len = cur_len

    def drain_notifications(self) -> list[dict[str, Any]]:
        """Pop and return all queued server-initiated notifications."""
        out = list(self._pending_notifications)
        self._pending_notifications.clear()
        return out

    # ------------------------------------------------------------------
    # stdio transport (G2 — stable v0.8 conformance path)
    # ------------------------------------------------------------------
    def serve_stdio(
        self,
        stdin: Optional[IO[str]] = None,
        stdout: Optional[IO[str]] = None,
    ) -> None:
        """Block-read newline-delimited JSON-RPC requests from stdin and
        write replies to stdout. Stops on stdin EOF.

        This is the v0.8 *stable* transport (G2). The server runs until
        the client closes stdin; per MCP spec, the lifecycle ends with
        an EOF on stdin.
        """
        rs = stdin if stdin is not None else sys.stdin
        ws = stdout if stdout is not None else sys.stdout
        for line in rs:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                response = _make_error_response(
                    None,
                    JSONRPC_PARSE_ERROR,
                    f"parse error: {exc}",
                )
                ws.write(json.dumps(response) + "\n")
                ws.flush()
                continue
            response = self.handle_request(payload)
            if response is not None:
                ws.write(json.dumps(response) + "\n")
                ws.flush()
            # Drain any queued notifications onto the same stream.
            for note in self.drain_notifications():
                ws.write(json.dumps(note) + "\n")
                ws.flush()


# ---------------------------------------------------------------------------
# Tool descriptions (used by tools/list)
# ---------------------------------------------------------------------------
def _tool_description(verb: str) -> str:
    return {
        "remember": (
            "Assert a fact (content + optional tags) into the substrate's "
            "bitemporal store. Returns the new entity id."
        ),
        "recall": (
            "Substring-match facts in the substrate. Returns up to k hits "
            "with their entity ids and tags. v0.8 = substring + tag mode "
            "(no vector search; per ADR-8)."
        ),
        "forget": (
            "Retract a previously-remembered fact by entity id."
        ),
        "audit_window": (
            "Return a window of audit-chain entries from from_tx through "
            "to_tx (inclusive). Capped at limit."
        ),
        "replay_check": (
            "Re-verify a window of the audit chain around tx. Returns "
            "{ok, reason_code, window_actual, head_hash}; NEVER returns "
            "raw byte diffs (per ADR-13)."
        ),
        "view_at": (
            "Allocate a server-side cursor handle anchored at tx. Returns "
            "{cursor_id, view_cursor_tx_time_iso, parent_chain_depth}. "
            "NOT a store fork (per ADR-14 / Module 7 ADR-13)."
        ),
    }[verb]


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------
@stable("v0.8")
def create_server(
    substrate: Any,
    *,
    token_caps: Optional[set[str]] = None,
    budgets: Optional[Budgets] = None,
    clock: Optional[Callable[[], datetime]] = None,
) -> MCPServer:
    """Construct an :class:`MCPServer` bound to ``substrate``.

    This is the curated v0.8 entrypoint per ADR-15. Adapter authors
    write::

        from persistence.sdk import Substrate
        from persistence.sdk.mcp import create_server

        s = Substrate.open("memory")
        server = create_server(s)
        server.serve_stdio()

    Args:
        substrate: a live :class:`persistence.sdk.Substrate`.
        token_caps: optional MCP capability-string set (e.g.
            ``{"mcp.remember", "mcp.recall"}``). Defaults to all six
            caps for in-process use; production hosts pass an explicit
            subset (per ADR-15a).
        budgets: optional :class:`Budgets` override; defaults to ADR-13
            pinned values.
        clock: optional clock callable (``() -> datetime``); defaults
            to ``datetime.now(tz=utc)``.

    Returns:
        a fresh :class:`MCPServer` ready for ``serve_stdio()``.
    """
    return MCPServer(
        substrate,
        token_caps=token_caps,
        budgets=budgets,
        clock=clock,
    )


# ---------------------------------------------------------------------------
# Experimental HTTP transport (per ADR-15b — NOT v0.8 conformance)
# ---------------------------------------------------------------------------
@experimental(reason="ADR-15b — HTTP transport is EXPERIMENTAL in v0.8")
class ExperimentalHTTPHandler:
    """EXPERIMENTAL Streamable-HTTP wrapper for an :class:`MCPServer`.

    Per ADR-15b: bind-loopback enforcement, Origin allowlist + DNS-rebinding
    mitigation, per-request Bearer-token authn, ``Origin: null`` literal
    rejection. NOT covered by the v0.8 stability contract.

    The implementation is intentionally minimal — it does NOT bind a
    real socket; it provides ``handle_http_request(method, headers,
    body)`` so a transport (any ASGI / WSGI / aiohttp) can wire it up.
    G12 exercises this path against synthetic header dicts.
    """

    def __init__(
        self,
        server: MCPServer,
        *,
        bind_host: str = "127.0.0.1",
        port: int = 0,
        bearer_token: Optional[str] = None,
        allowed_origins: Optional[set[str]] = None,
        rate_limit_per_window: int = 60,
        rate_window_s: float = 1.0,
        wallclock: Optional[Callable[[], float]] = None,
    ) -> None:
        if bind_host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(
                f"bind_non_loopback_refused: {bind_host!r} is not a "
                "loopback address; v0.8 experimental HTTP refuses "
                "non-loopback binds (ADR-15b)"
            )
        self._server = server
        self._bind_host = bind_host
        self._port = port
        self._bearer_token = bearer_token
        self._allowed_origins: set[str] = set(
            allowed_origins
            or {f"http://localhost:{port}", f"http://127.0.0.1:{port}"}
        )
        self._wallclock = wallclock or time.monotonic
        self._rate = _RateLimiter(
            limit=rate_limit_per_window,
            window_s=rate_window_s,
            clock=self._wallclock,
        )

    def handle_http_request(
        self,
        *,
        method: str,
        headers: dict[str, str],
        body: str,
    ) -> tuple[int, dict[str, str], str]:
        """Process one HTTP request. Returns ``(status, headers_out, body)``.

        Implements the four ADR-15b checks in order: Origin (NIT-4 with
        the ``null`` literal distinction), Authorization, rate-limit,
        then dispatches to :meth:`MCPServer.handle_request`.
        """
        # Normalize headers to lowercase keys
        h = {k.lower(): v for k, v in headers.items()}
        # 1. Origin check — ADR-15b NIT-4 distinct three cases.
        origin = h.get("origin")
        if origin is not None:
            if origin == "null":
                return (
                    403,
                    {"content-type": "application/json"},
                    json.dumps({
                        "error": "origin_not_allowed",
                        "origin": "null",
                    }),
                )
            if origin not in self._allowed_origins:
                return (
                    403,
                    {"content-type": "application/json"},
                    json.dumps({
                        "error": "origin_not_allowed",
                        "origin": origin,
                    }),
                )
        # 2. Authorization check
        auth = h.get("authorization", "")
        if not auth.startswith("Bearer "):
            return (
                401,
                {"content-type": "application/json"},
                json.dumps({"error": "capability_denied"}),
            )
        presented = auth[len("Bearer "):]
        if (
            self._bearer_token is None
            or presented != self._bearer_token
        ):
            return (
                401,
                {"content-type": "application/json"},
                json.dumps({"error": "capability_denied"}),
            )
        # 3. Rate-limit
        if not self._rate.allow():
            return (
                429,
                {
                    "content-type": "application/json",
                    "retry-after": "1",
                },
                json.dumps({"error": "rate_limit_exceeded"}),
            )
        # 4. Dispatch
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return (
                400,
                {"content-type": "application/json"},
                json.dumps({"error": "parse_error"}),
            )
        response = self._server.handle_request(payload)
        if response is None:
            return (204, {}, "")
        return (
            200,
            {"content-type": "application/json"},
            json.dumps(response),
        )


__all__ = [
    "ERR_CAPABILITY_DENIED",
    "ERR_TOKEN_INVALID",
    "ExperimentalHTTPHandler",
    "JSONRPC_INTERNAL_ERROR",
    "JSONRPC_INVALID_PARAMS",
    "JSONRPC_INVALID_REQUEST",
    "JSONRPC_METHOD_NOT_FOUND",
    "JSONRPC_PARSE_ERROR",
    "MCPServer",
    "MCP_PROTOCOL_VERSION_FALLBACK",
    "MCP_PROTOCOL_VERSION_PRIMARY",
    "MCP_SUPPORTED_VERSIONS",
    "SERVER_NAME",
    "SERVER_VERSION",
    "create_server",
]
