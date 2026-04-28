"""persistence.repl — Module 7 of Persistence OS.

A capability-gated WebSocket REPL surface over the substrate. Operators
authenticate with an opaque-random 256-bit token (`mint_token`,
`store_token`, `validate_token`, `revoke_token`); the server validates
the token against the bitemporal fact store, derives a deterministic
``session_id``, and exposes four ops: ``inspect`` / ``edit`` / ``rewind``
/ ``branch``. Every op emits one ``:repl/op`` AuditEntry into the same
Merkle chain as programmatic traffic.

This package ships in stages — D1 lands the capability + token + session
scaffolding; D2-D8 land the WS server, op handlers, audit emission, and
the browser console UI. See
``docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md``.

Public surface (D1):

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
from ._session import Session, _derive_session_id, make_session

__all__ = [
    "ALL_CAPS",
    "Capability",
    "CapabilitySet",
    "OP_NAMES",
    "QUALIFIERS_BY_OP",
    "Session",
    "Token",
    "UnknownCapability",
    "_derive_session_id",
    "_token_id",
    "list_tokens",
    "make_session",
    "mint_token",
    "revoke_token",
    "store_token",
    "validate_token",
]
