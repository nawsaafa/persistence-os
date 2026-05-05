"""Phase 2.1c.6 G6 — :blob/put advances the canonical audit chain."""
from __future__ import annotations


def test_blob_put_advances_audit_chain(app_client):
    """Two distinct blobs put → audit_chain_head advances; both heads sha256:<hex>."""
    r1 = app_client.post(
        "/v1/blob/put",
        content=b"first-blob",
        headers={"Content-Type": "application/octet-stream", "X-Session-Id": "blob-audit-1"},
    )
    assert r1.status_code == 200
    h1 = r1.json()["audit_chain_head"]
    assert h1 is not None
    assert h1.startswith("sha256:"), f"audit_chain_head not sha256-prefixed: {h1!r}"

    r2 = app_client.post(
        "/v1/blob/put",
        content=b"second-blob",
        headers={"Content-Type": "application/octet-stream", "X-Session-Id": "blob-audit-2"},
    )
    assert r2.status_code == 200
    h2 = r2.json()["audit_chain_head"]
    assert h2.startswith("sha256:")
    assert h1 != h2, (
        f"audit_chain_head did not advance across distinct blob puts: {h1!r} == {h2!r}"
    )


def test_duplicate_blob_put_still_advances_audit_chain(app_client):
    """Duplicate blob put returns duplicate=True but still anchors a fresh audit entry."""
    payload = b"duplicate-content"
    headers = {"Content-Type": "application/octet-stream", "X-Session-Id": "blob-dup"}

    r1 = app_client.post("/v1/blob/put", content=payload, headers=headers)
    assert r1.status_code == 200
    h1 = r1.json()["audit_chain_head"]

    r2 = app_client.post("/v1/blob/put", content=payload, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["duplicate"] is True
    h2 = r2.json()["audit_chain_head"]
    assert h2.startswith("sha256:")
    # Even when storage is no-op, audit entry is fresh — distinct head.
    assert h1 != h2
