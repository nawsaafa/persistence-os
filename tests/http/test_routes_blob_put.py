# tests/http/test_routes_blob_put.py
"""Phase 2.1c — POST /v1/blob/put (Design §4.2, §10.2)."""
import hashlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(tmp_path):
    os.environ["PERSISTENCE_API_KEY"] = "test-token"
    os.environ["PERSISTENCE_HTTP_LOOPBACK_BYPASS"] = "1"
    os.environ["PERSISTENCE_BLOB_ROOT"] = str(tmp_path / "blobs")
    from persistence.http.server import build_app
    app = build_app()
    return TestClient(app, client=("127.0.0.1", 9999))


def test_put_returns_hash_size_duplicate_false_on_first(app_client):
    content = b"hello world"
    expected_hash = "sha256:" + hashlib.sha256(content).hexdigest()
    r = app_client.post(
        "/v1/blob/put",
        content=content,
        headers={"Content-Type": "application/octet-stream", "X-Session-Id": "s"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["hash"] == expected_hash
    assert body["size_bytes"] == len(content)
    assert body["duplicate"] is False


def test_put_idempotent_second_call_returns_duplicate_true(app_client):
    content = b"identical bytes"
    headers = {"Content-Type": "application/octet-stream", "X-Session-Id": "s"}
    r1 = app_client.post("/v1/blob/put", content=content, headers=headers)
    r2 = app_client.post("/v1/blob/put", content=content, headers=headers)
    assert r1.json()["duplicate"] is False
    assert r2.json()["duplicate"] is True
    assert r1.json()["hash"] == r2.json()["hash"]


def test_put_idempotent_two_blob_put_datoms_emitted(app_client):
    """First and second put each emit a :claim/blob-put datom; single file on disk."""
    content = b"twice please"
    headers = {"Content-Type": "application/octet-stream", "X-Session-Id": "s"}
    app_client.post("/v1/blob/put", content=content, headers=headers)
    app_client.post("/v1/blob/put", content=content, headers=headers)

    # Query for blob-put datoms
    q = app_client.get("/v1/claim/query?session_id=s&kind=:claim/blob-put")
    assert q.status_code == 200
    datoms = q.json()["claims"]
    assert len(datoms) == 2
    assert datoms[0]["attrs"]["hash"] == datoms[1]["attrs"]["hash"]


def test_put_missing_session_id_rejected(app_client):
    r = app_client.post(
        "/v1/blob/put",
        content=b"x",
        headers={"Content-Type": "application/octet-stream"},  # no X-Session-Id
    )
    assert r.status_code == 400
    assert r.json()["error"] == "missing_session_id"


def test_put_oversize_body_rejected_413(app_client):
    huge = b"x" * (16 * 1024 * 1024 + 1)  # 16 MiB + 1
    r = app_client.post(
        "/v1/blob/put",
        content=huge,
        headers={"Content-Type": "application/octet-stream", "X-Session-Id": "s"},
    )
    assert r.status_code == 413
    assert r.json()["error"] == "oversize_body"
