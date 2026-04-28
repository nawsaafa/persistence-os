"""persistence.repl — Module 7 of Persistence OS.

A capability-gated WebSocket REPL surface over the substrate. Operators
authenticate with an opaque-random 256-bit token (`mint_token`,
`store_token`, `validate_token`, `revoke_token`); the server validates
the token against the bitemporal fact store, derives a deterministic
``session_id``, and exposes four ops: ``inspect`` / ``edit`` / ``rewind``
/ ``branch``. Every op emits one ``:repl/op`` AuditEntry into the same
Merkle chain as programmatic traffic.

This package ships in stages — D1 lands the capability + token + session
scaffolding; D2 lands the aiohttp WS server + JSON-RPC envelope; D3-D8
land the op handlers, audit emission, and the browser console UI. See
``docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md``.

Public surface (D1+D2):

- :class:`Capability`         — one ``(op, qualifier)`` pair
- :class:`CapabilitySet`      — frozen set + expiry + label
- :class:`Token`              — minted token (raw string + cap-set)
- :class:`Session`            — authenticated session (per WS connection)
- :class:`UnknownCapability`  — raised on out-of-set ``Capability``
- :func:`mint_token`          — generate a fresh opaque token
- :func:`store_token`         — persist a token to the fact store
- :func:`validate_token`      — validate a presented raw token string
- :func:`revoke_token`        — write ``repl/revoked = True`` (idempotent)
- :func:`list_tokens`         — enumerate active (non-revoked) tokens
- :func:`make_session`        — construct a Session post auth handshake
- :func:`_token_id`           — single helper for sha256(token_str)[:16]
- :class:`WSServer`           — aiohttp single-port REPL server
- :class:`Request` / :class:`Response` / :class:`Notification` — JSON-RPC shapes
- :func:`parse_request` / :func:`make_response` /
  :func:`make_error_response` / :func:`make_notification` — envelope builders
- :data:`ERR_*`               — JSON-RPC + ADR-9 error codes
- :func:`inspect_op` / :func:`edit_op` / :func:`rewind_op` /
  :func:`branch_op` — pre-skeleton op handlers (D3/D4/D5/D6 fill bodies)
"""
from __future__ import annotations

from ._caps import (
    ALL_CAPS,
    Capability,
    CapabilitySet,
    OP_NAMES,
    QUALIFIERS_BY_OP,
    Token,
    UnknownCapability,
    _token_id,
    list_tokens,
    mint_token,
    revoke_token,
    store_token,
    validate_token,
)
from ._ops import branch_op, edit_op, inspect_op, rewind_op
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
    Notification,
    Request,
    Response,
    make_error_response,
    make_notification,
    make_response,
    parse_request,
)
from ._session import Session, _derive_session_id, make_session
from ._ws import WSServer

__all__ = [
    "ALL_CAPS",
    "Capability",
    "CapabilitySet",
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
    "OP_NAMES",
    "QUALIFIERS_BY_OP",
    "Request",
    "Response",
    "Session",
    "Token",
    "UnknownCapability",
    "WSServer",
    "_derive_session_id",
    "_token_id",
    "branch_op",
    "edit_op",
    "inspect_op",
    "list_tokens",
    "make_error_response",
    "make_notification",
    "make_response",
    "make_session",
    "mint_token",
    "parse_request",
    "revoke_token",
    "rewind_op",
    "store_token",
    "validate_token",
]
