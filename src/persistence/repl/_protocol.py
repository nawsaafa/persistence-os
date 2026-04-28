"""JSON-RPC 2.0 envelope for the REPL WebSocket transport (D2).

See design doc ``docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md``
sections ADR-2 (JSON-RPC 2.0 envelope) and ADR-9 (error codes).

The wire format on ``GET /ws`` is the standard JSON-RPC 2.0 shape:

- Client request:  ``{"jsonrpc": "2.0", "id": <int|str>, "method": <str>, "params": {...}}``
- Server response: ``{"jsonrpc": "2.0", "id": <same>, "result": <any>}`` OR
                    ``{"jsonrpc": "2.0", "id": <same>, "error": {"code": <int>, "message": <str>, "data": <any>?}}``
- Server-initiated notification: ``{"jsonrpc": "2.0", "method": <str>, "params": {...}}`` (no ``id``)

Error codes are split between JSON-RPC-reserved (-32700..-32603) and
Persistence-OS-application-specific (-32001..-32008). The application
codes are the authoritative ADR-9 set; D3-D7 ops raise via
``_ws._OpError(code, message, data)`` to dispatch to
``make_error_response``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 reserved error codes
# ---------------------------------------------------------------------------
ERR_PARSE_ERROR = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL_ERROR = -32603


# ---------------------------------------------------------------------------
# Application-specific error codes (ADR-9)
# ---------------------------------------------------------------------------
ERR_CAPABILITY_DENIED = -32001
ERR_AUTH_FAILED = -32002
ERR_TOKEN_INVALID = -32003
ERR_VERIFY_CHAIN_FAILED = -32004
ERR_REQUEST_HASH_MISMATCH = -32005
ERR_SESSION_EXPIRED = -32006
ERR_BRANCH_DEPTH_EXCEEDED = -32007
ERR_STALE_CURSOR_EDIT = -32008


# ---------------------------------------------------------------------------
# Envelope shapes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Request:
    """A parsed JSON-RPC 2.0 request.

    ``id`` is ``None`` for notifications (per JSON-RPC spec). The REPL
    transport uses notifications for server-pushed events; clients are
    expected to always include an ``id`` on incoming calls. ``params`` is
    canonicalised to ``dict[str, Any]`` (positional arrays are not used
    on the REPL surface).
    """

    jsonrpc: Literal["2.0"]
    method: str
    params: dict[str, Any]
    id: int | str | None = None


@dataclass(frozen=True)
class Response:
    """A JSON-RPC 2.0 response container (success xor error).

    Kept as a typed dataclass for type-checkers / docs; the wire format
    is constructed via ``make_response`` / ``make_error_response`` (which
    return plain dicts ready for ``ws.send_json``).
    """

    jsonrpc: Literal["2.0"]
    id: int | str | None
    result: Any = None
    error: dict | None = None


@dataclass(frozen=True)
class Notification:
    """A server-initiated notification (no ``id``).

    Used for stream-pushed events in D7+ (e.g. ``repl/audit-emitted``).
    """

    jsonrpc: Literal["2.0"]
    method: str
    params: dict[str, Any]


# ---------------------------------------------------------------------------
# Parsers + builders
# ---------------------------------------------------------------------------
def parse_request(payload: dict) -> Request:
    """Validate + construct a :class:`Request` from a decoded JSON body.

    Raises :class:`ValueError` on:

    - missing or non-``"2.0"`` ``jsonrpc`` field,
    - missing or non-string ``method``,
    - non-dict ``params`` (positional arrays not supported by the REPL).

    The caller is expected to map ``ValueError`` to an
    ``ERR_INVALID_REQUEST`` JSON-RPC error response.
    """
    if payload.get("jsonrpc") != "2.0":
        raise ValueError("missing or invalid jsonrpc field")
    method = payload.get("method")
    if not isinstance(method, str):
        raise ValueError("missing or invalid method")
    params = payload.get("params", {})
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    return Request(
        jsonrpc="2.0",
        method=method,
        params=params,
        id=payload.get("id"),
    )


def make_response(req_id: int | str | None, result: Any) -> dict:
    """Construct a JSON-RPC success response dict ready for ``ws.send_json``."""
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def make_error_response(
    req_id: int | str | None,
    code: int,
    message: str,
    data: Any = None,
) -> dict:
    """Construct a JSON-RPC error response dict.

    ``data`` is omitted from the wire response when ``None`` (per
    JSON-RPC 2.0 §5.1: "data" is optional). Op handlers raise
    ``_ws._OpError(code, message, data)`` to surface a structured
    error.
    """
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def make_notification(method: str, params: dict) -> dict:
    """Construct a server-initiated notification (no ``id``)."""
    return {"jsonrpc": "2.0", "method": method, "params": params}


__all__ = [
    "ERR_AUTH_FAILED",
    "ERR_BRANCH_DEPTH_EXCEEDED",
    "ERR_CAPABILITY_DENIED",
    "ERR_INTERNAL_ERROR",
    "ERR_INVALID_PARAMS",
    "ERR_INVALID_REQUEST",
    "ERR_METHOD_NOT_FOUND",
    "ERR_PARSE_ERROR",
    "ERR_REQUEST_HASH_MISMATCH",
    "ERR_SESSION_EXPIRED",
    "ERR_STALE_CURSOR_EDIT",
    "ERR_TOKEN_INVALID",
    "ERR_VERIFY_CHAIN_FAILED",
    "Notification",
    "Request",
    "Response",
    "make_error_response",
    "make_notification",
    "make_response",
    "parse_request",
]
