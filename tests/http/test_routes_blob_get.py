"""Phase 2.1c — GET /v1/blob/get/{hash} (Design §4.3, §10.2)."""
import os

import pytest
from fastapi.testclient import TestClient


# app_client fixture is provided by tests/http/conftest.py (yield + substrate teardown).


def test_get_returns_bytes_for_known_hash(app_client):
    content = b"\x00\x01round\xfftrip"
    put_response = app_client.post(
        "/v1/blob/put",
        content=content,
        headers={"Content-Type": "application/octet-stream", "X-Session-Id": "s"},
    )
    h = put_response.json()["hash"]

    r = app_client.get(f"/v1/blob/get/{h}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.content == content


def test_get_returns_404_with_uniform_envelope_for_unknown_hash(app_client):
    h = "sha256:" + "0" * 64
    r = app_client.get(f"/v1/blob/get/{h}")
    assert r.status_code == 404
    body = r.json()
    assert body["error"] == "blob_not_found"
    assert "detail" in body


def test_get_rejects_malformed_hash_400(app_client):
    r = app_client.get("/v1/blob/get/not-a-sha256")
    assert r.status_code == 400
    assert r.json()["error"] == "malformed_hash"


def test_get_rejects_unauthenticated_when_no_bypass(app_client, monkeypatch, tmp_path):
    # Re-build app with bypass off and no API key
    monkeypatch.delenv("PERSISTENCE_API_KEY", raising=False)
    monkeypatch.setenv("PERSISTENCE_HTTP_LOOPBACK_BYPASS", "0")
    # The server.py check should refuse to start when API key unset AND bypass off,
    # so we set api key but no bypass to land in the auth path
    monkeypatch.setenv("PERSISTENCE_API_KEY", "secret")
    monkeypatch.setenv("PERSISTENCE_BLOB_ROOT", str(tmp_path / "blobs2"))
    from persistence.http.server import build_app
    app = build_app()
    client = TestClient(app, client=("100.64.0.5", 9999))  # non-loopback
    h = "sha256:" + "1" * 64
    r = client.get(f"/v1/blob/get/{h}")
    assert r.status_code == 401
    assert r.json()["error"] == "bearer_required"
