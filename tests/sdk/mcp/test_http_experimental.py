"""SDK3 — G12: experimental HTTP transport regression tests.

Per ADR-15b these are NOT part of the v0.8 conformance suite; they
ensure the experimental defaults the doc commits to are actually
enforced. Skipped unless the ``PERSISTENCE_MCP_EXPERIMENTAL_HTTP=1``
env var is set, per design § "experimental HTTP" gating posture.
"""
from __future__ import annotations

import json
import os

import pytest

from persistence.sdk import Substrate
from persistence.sdk.mcp import (
    ExperimentalHTTPHandler,
    create_server,
)


# NOTE: G12 is the HTTP-experimental regression suite (per ADR-15b — NOT
# part of v0.8 conformance). The tests below run by default in CI to keep
# the experimental defaults honest, but the surface they exercise is
# explicitly @experimental and may break in any patch release. Set
# ``PERSISTENCE_MCP_SKIP_HTTP=1`` to skip them on slow CI runners.
pytestmark = pytest.mark.skipif(
    os.environ.get("PERSISTENCE_MCP_SKIP_HTTP") == "1",
    reason="G12 HTTP-experimental skipped via PERSISTENCE_MCP_SKIP_HTTP=1",
)


@pytest.fixture
def http_substrate():
    s = Substrate.open("memory")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def http_handler(http_substrate):
    server = create_server(http_substrate)
    server.handle_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "x"},
        },
    })
    server.handle_request({
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    })
    return ExperimentalHTTPHandler(
        server,
        bind_host="127.0.0.1",
        port=8765,
        bearer_token="test-token-123",
        allowed_origins={
            "http://localhost:8765",
            "http://127.0.0.1:8765",
        },
    )


def test_g12a_non_loopback_bind_refused(http_substrate):
    server = create_server(http_substrate)
    with pytest.raises(ValueError, match="bind_non_loopback_refused"):
        ExperimentalHTTPHandler(
            server, bind_host="0.0.0.0", port=8765, bearer_token="x"
        )


def test_g12b_origin_null_literal_rejected(http_handler):
    status, _, body = http_handler.handle_http_request(
        method="POST",
        headers={
            "origin": "null",
            "authorization": "Bearer test-token-123",
        },
        body="{}",
    )
    assert status == 403
    assert json.loads(body)["error"] == "origin_not_allowed"


def test_g12b_origin_disallowed_rejected(http_handler):
    status, _, body = http_handler.handle_http_request(
        method="POST",
        headers={
            "origin": "http://evil.example",
            "authorization": "Bearer test-token-123",
        },
        body="{}",
    )
    assert status == 403


def test_g12b_origin_allowlisted_succeeds_after_auth(http_handler):
    # No origin header - allowed
    status, _, _ = http_handler.handle_http_request(
        method="POST",
        headers={"authorization": "Bearer test-token-123"},
        body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
    )
    assert status == 200


def test_g12c_no_authorization_rejected(http_handler):
    status, _, body = http_handler.handle_http_request(
        method="POST",
        headers={},
        body="{}",
    )
    assert status == 401
    assert json.loads(body)["error"] == "capability_denied"


def test_g12c_wrong_authorization_rejected(http_handler):
    status, _, body = http_handler.handle_http_request(
        method="POST",
        headers={"authorization": "Bearer wrong-token"},
        body="{}",
    )
    assert status == 401


def test_g12e_rate_limit_enforced(http_handler):
    # Each handler shares one rate limiter; 60 succeed, 61st hits 429.
    headers = {"authorization": "Bearer test-token-123"}
    for _ in range(60):
        status, _, _ = http_handler.handle_http_request(
            method="POST",
            headers=headers,
            body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
        )
        assert status == 200
    status, _, body = http_handler.handle_http_request(
        method="POST",
        headers=headers,
        body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
    )
    assert status == 429
    assert json.loads(body)["error"] == "rate_limit_exceeded"
