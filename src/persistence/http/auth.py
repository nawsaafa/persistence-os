"""HTTP auth dependency: bearer + opt-in loopback bypass (Phase 2.1c, Design §7.1).

Loopback determination uses TCP socket peer ONLY (``request.client.host``).
Headers (X-Forwarded-For / Forwarded / X-Real-IP) are explicitly ignored.
The uvicorn entry pins ``proxy_headers=False, forwarded_allow_ips=""`` so the
ASGI runtime cannot rewrite ``request.client.host`` from headers (Design §7.1,
R1.2 N3 closure).
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, HTTPException, Request


class BearerRequiredError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=401,
            detail={"error": "bearer_required", "detail": detail},
        )


class MalformedAuthorizationError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=401,
            detail={"error": "malformed_authorization", "detail": detail},
        )


class LoopbackPeer:
    """Loopback peer-address discriminator. Socket-peer-only — no header trust."""

    _LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})

    @staticmethod
    def is_loopback(host: Optional[str]) -> bool:
        if host is None:
            return False
        return host in LoopbackPeer._LOOPBACK_HOSTS


def _bypass_enabled() -> bool:
    return os.environ.get("PERSISTENCE_HTTP_LOOPBACK_BYPASS") == "1"


def _expected_token() -> Optional[str]:
    return os.environ.get("PERSISTENCE_API_KEY")


def _check_auth(request: Request) -> None:
    """The auth check itself. Returns None on success, raises on failure."""
    peer_host = request.client.host if request.client else None
    is_loopback = LoopbackPeer.is_loopback(peer_host)

    if is_loopback and _bypass_enabled():
        return  # opt-in bypass: loopback peer with PERSISTENCE_HTTP_LOOPBACK_BYPASS=1

    auth_header = request.headers.get("Authorization")
    if auth_header is None:
        raise BearerRequiredError("Authorization header missing")
    if not auth_header.startswith("Bearer "):
        raise MalformedAuthorizationError(
            f"Authorization scheme must be 'Bearer',"
            f" got {auth_header.split(' ', 1)[0]!r}"
        )
    token = auth_header[len("Bearer "):].strip()
    if not token:
        raise MalformedAuthorizationError("Bearer token empty")

    expected = _expected_token()
    if expected is None or token != expected:
        raise BearerRequiredError("Invalid bearer token")


def require_auth() -> Depends:  # type: ignore[type-arg]
    """FastAPI dependency factory. Use as ``_=require_auth()`` in route signatures."""
    return Depends(_check_auth)
