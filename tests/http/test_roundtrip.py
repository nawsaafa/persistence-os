"""Phase 2.1c — roundtrip integration tests (Design §10.4)."""
from __future__ import annotations

import hashlib

# app_client fixture is provided by tests/http/conftest.py (yield + substrate teardown).


def test_emit_query_byte_for_byte_roundtrip(app_client):
    """A claim emitted via /v1/claim/emit should be retrievable byte-for-byte
    via /v1/claim/query session_id filter."""
    payload = {
        "tool": "Bash", "args": {"command": "ls -la"},
        "body_hash": None, "body_summary": "5 files",
        "body_disposition": "inline",
        "started_at": 1714839028000, "duration_ms": 12, "exit_code": 0,
        "session_id": "rt-1", "parent_correlation_id": None,
    }
    emit = app_client.post("/v1/claim/emit", json={
        "claims": [{"kind": ":claim/tool-exec", "attrs": payload}]
    })
    assert emit.status_code == 200

    q = app_client.get("/v1/claim/query?session_id=rt-1")
    assert q.status_code == 200
    claims = q.json()["claims"]
    assert len(claims) == 1
    # Each attr round-trips byte-for-byte
    for k, v in payload.items():
        assert claims[0]["attrs"][k] == v


def test_blob_put_emit_reference_get_roundtrip(app_client):
    """Put a blob, emit a :claim/tool-exec referencing its hash with
    body_disposition='blobbed', query the claim, fetch the blob → contents match."""
    blob_content = b"this is the body that we want to retrieve later"
    expected_hash = "sha256:" + hashlib.sha256(blob_content).hexdigest()

    # Put blob
    put = app_client.post(
        "/v1/blob/put",
        content=blob_content,
        headers={"Content-Type": "application/octet-stream", "X-Session-Id": "rt-2"},
    )
    assert put.status_code == 200
    assert put.json()["hash"] == expected_hash

    # Emit a claim referencing the blob hash (body_disposition='blobbed')
    payload = {
        "tool": "BashOutput", "args": {"id": "x"},
        "body_hash": expected_hash, "body_summary": "(see blob)",
        "body_disposition": "blobbed",
        "started_at": 1714839028000, "duration_ms": 1, "exit_code": 0,
        "session_id": "rt-2", "parent_correlation_id": None,
    }
    emit = app_client.post("/v1/claim/emit", json={
        "claims": [{"kind": ":claim/tool-exec", "attrs": payload}]
    })
    assert emit.status_code == 200

    # Query the claim back (tool-exec only, to skip the blob-put datom)
    q = app_client.get("/v1/claim/query?session_id=rt-2&kind=:claim/tool-exec")
    assert q.status_code == 200
    claims = q.json()["claims"]
    matching = [c for c in claims if c["attrs"]["body_hash"] == expected_hash]
    assert len(matching) == 1

    # Fetch the blob and verify contents match
    get = app_client.get(f"/v1/blob/get/{expected_hash}")
    assert get.status_code == 200
    assert get.content == blob_content


def test_100_emit_fuzz(app_client):
    """Emit 100 claims, query all back, count matches."""
    for i in range(100):
        r = app_client.post("/v1/claim/emit", json={"claims": [{
            "kind": ":claim/tool-exec",
            "attrs": {
                "tool": f"Tool{i}", "args": {"i": i},
                "body_hash": None, "body_summary": str(i),
                "body_disposition": "inline",
                "started_at": 1714839028000, "duration_ms": 0, "exit_code": 0,
                "session_id": "fuzz-100", "parent_correlation_id": None,
            },
        }]})
        assert r.status_code == 200

    # Use generous limit to retrieve all (max allowed is 500 per design §4.4; old impl had 1000)
    q = app_client.get("/v1/claim/query?session_id=fuzz-100&limit=500")
    assert q.status_code == 200
    assert len(q.json()["claims"]) == 100
