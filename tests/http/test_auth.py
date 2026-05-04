"""Phase 2.1c — auth tests (Design §10.3).

Auth determination uses ONLY TCP socket peer (request.client.host); request
headers (X-Forwarded-For / Forwarded / X-Real-IP) are explicitly ignored
(Design §7.1, R1 BLOCKING B2). Tests use Starlette TestClient's `client=...`
peer fixture, NEVER header spoofing.
"""
import os

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from persistence.http.auth import (
    BearerRequiredError,
    LoopbackPeer,
    require_auth,
)


def _make_app(bypass: bool = False, api_key: str | None = "test-token"):
    """Helper: build a tiny app exercising the auth dependency.

    Forced spec deviation (documented): FastAPI's default exception envelope
    wraps detail under {"detail": ...}.  To get the uniform flat shape
    {"error": "...", "detail": "..."} that the tests assert (and that Task 13
    build_app will register globally), we add an exception_handler here.
    """
    if api_key is None:
        os.environ.pop("PERSISTENCE_API_KEY", None)
    else:
        os.environ["PERSISTENCE_API_KEY"] = api_key
    os.environ["PERSISTENCE_HTTP_LOOPBACK_BYPASS"] = "1" if bypass else "0"

    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _flatten_envelope(request, exc: HTTPException):  # type: ignore[type-arg]
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": "unknown", "detail": str(exc.detail)},
        )

    @app.get("/_protected")
    def _protected(_=require_auth()):
        return {"ok": True}

    return app


# ---------- bypass DEFAULT-OFF ----------

def test_loopback_no_token_rejected_when_bypass_off():
    app = _make_app(bypass=False)
    client = TestClient(app, client=("127.0.0.1", 9999))
    r = client.get("/_protected")
    assert r.status_code == 401
    assert r.json()["error"] == "bearer_required"


def test_loopback_with_token_passes_when_bypass_off():
    app = _make_app(bypass=False)
    client = TestClient(app, client=("127.0.0.1", 9999))
    r = client.get("/_protected", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200


def test_non_loopback_no_token_rejected():
    app = _make_app(bypass=False)
    client = TestClient(app, client=("100.64.0.5", 9999))
    r = client.get("/_protected")
    assert r.status_code == 401
    assert r.json()["error"] == "bearer_required"


def test_non_loopback_with_token_passes():
    app = _make_app(bypass=False)
    client = TestClient(app, client=("100.64.0.5", 9999))
    r = client.get("/_protected", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200


def test_malformed_authorization_returns_401_malformed_authorization():
    app = _make_app(bypass=False)
    client = TestClient(app, client=("100.64.0.5", 9999))
    r = client.get("/_protected", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401
    assert r.json()["error"] == "malformed_authorization"


# ---------- bypass OPT-IN ----------

def test_loopback_no_token_passes_when_bypass_on():
    app = _make_app(bypass=True)
    client = TestClient(app, client=("127.0.0.1", 9999))
    r = client.get("/_protected")
    assert r.status_code == 200


def test_non_loopback_no_token_still_rejected_with_bypass_on():
    """Bypass applies ONLY to loopback peer; non-loopback path is unaffected."""
    app = _make_app(bypass=True)
    client = TestClient(app, client=("100.64.0.5", 9999))
    r = client.get("/_protected")
    assert r.status_code == 401
    assert r.json()["error"] == "bearer_required"


# ---------- header-spoof regressions (R1 BLOCKING B2 closure) ----------

def test_xff_header_does_not_unlock_bypass_from_non_loopback_peer():
    """X-Forwarded-For: 127.0.0.1 from non-loopback TCP peer must NOT trigger bypass,
    even when bypass is opt-in enabled."""
    app = _make_app(bypass=True)
    client = TestClient(app, client=("100.64.0.5", 9999))
    r = client.get("/_protected", headers={"X-Forwarded-For": "127.0.0.1"})
    assert r.status_code == 401


def test_xff_header_does_not_force_non_loopback_path_from_loopback_peer():
    """X-Forwarded-For: 100.64.0.5 from loopback peer must NOT switch to non-loopback path."""
    app = _make_app(bypass=True)
    client = TestClient(app, client=("127.0.0.1", 9999))
    r = client.get("/_protected", headers={"X-Forwarded-For": "100.64.0.5"})
    assert r.status_code == 200  # bypass still applies; header ignored


def test_forwarded_header_ignored():
    app = _make_app(bypass=True)
    client = TestClient(app, client=("100.64.0.5", 9999))
    r = client.get("/_protected", headers={"Forwarded": "for=127.0.0.1"})
    assert r.status_code == 401


def test_x_real_ip_header_ignored():
    app = _make_app(bypass=True)
    client = TestClient(app, client=("100.64.0.5", 9999))
    r = client.get("/_protected", headers={"X-Real-IP": "127.0.0.1"})
    assert r.status_code == 401


# ---------- 2.1c.5 acceptance signal (xfail-strict, flips PASS when 2.1c.5 lands) ----------

@pytest.mark.xfail(strict=True, reason="2.1c.5 — caller identity attestation")
def test_non_loopback_without_caller_signature_rejected_even_with_valid_bearer():
    """Per 2.1c §16: non-loopback request with valid bearer but NO caller signature
    must be rejected 401 caller_signature_required. Bearer-only auth (2.1c) cannot
    enforce this — requires Ed25519 caller identity attestation (2.1c.5)."""
    app = _make_app(bypass=False)
    client = TestClient(app, client=("100.64.0.5", 9999))
    r = client.get("/_protected", headers={"Authorization": "Bearer test-token"})
    # 2.1c returns 200 here (bearer alone is sufficient).
    # 2.1c.5 will return 401 caller_signature_required.
    assert r.status_code == 401
    assert r.json()["error"] == "caller_signature_required"


# ---------- LoopbackPeer helper unit ----------

def test_loopback_peer_recognizes_ipv4_loopback():
    assert LoopbackPeer.is_loopback("127.0.0.1") is True


def test_loopback_peer_recognizes_ipv6_loopback():
    assert LoopbackPeer.is_loopback("::1") is True


def test_loopback_peer_rejects_tailscale_cgnat():
    assert LoopbackPeer.is_loopback("100.64.0.5") is False
