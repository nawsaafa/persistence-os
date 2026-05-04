"""Phase 2.1c — GET /v1/claim/query (Design §4.4, §10.2)."""
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
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


def _emit_one(client, session_id="default-session", **attrs_overrides):
    base = {
        "tool": "Bash", "args": {"command": "ls"},
        "body_hash": None, "body_summary": "ok",
        "body_disposition": "inline",
        "started_at": 1714839028000, "duration_ms": 12, "exit_code": 0,
        "session_id": session_id, "parent_correlation_id": None,
    }
    base.update(attrs_overrides)
    return client.post("/v1/claim/emit", json={"claims": [{"kind": ":claim/tool-exec", "attrs": base}]})


def test_query_requires_session_id(app_client):
    r = app_client.get("/v1/claim/query")
    assert r.status_code == 400
    assert r.json()["error"] == "missing_session_id"


def test_query_returns_session_filtered_claims(app_client):
    _emit_one(app_client, session_id="alpha")
    _emit_one(app_client, session_id="alpha")
    _emit_one(app_client, session_id="beta")
    r = app_client.get("/v1/claim/query?session_id=alpha")
    assert r.status_code == 200
    body = r.json()
    assert len(body["claims"]) == 2
    for c in body["claims"]:
        assert c["attrs"]["session_id"] == "alpha"


def test_query_kind_filter_accepts_claim_kind(app_client):
    _emit_one(app_client, session_id="kx")
    r = app_client.get("/v1/claim/query?session_id=kx&kind=:claim/tool-exec")
    assert r.status_code == 200
    assert all(c["kind"] == ":claim/tool-exec" for c in r.json()["claims"])


def test_query_kind_filter_rejects_fact_kind(app_client):
    r = app_client.get("/v1/claim/query?session_id=any&kind=:llm/decision")
    assert r.status_code == 400
    assert r.json()["error"] == "not_a_claim_kind"


def test_query_kind_filter_rejects_unknown_claim_kind(app_client):
    r = app_client.get("/v1/claim/query?session_id=any&kind=:claim/unknown")
    assert r.status_code == 400
    assert r.json()["error"] == "not_a_claim_kind"


def test_query_pagination_via_next_since(app_client):
    for _ in range(5):
        _emit_one(app_client, session_id="page")
    r = app_client.get("/v1/claim/query?session_id=page&limit=2")
    assert r.status_code == 200
    body = r.json()
    assert len(body["claims"]) == 2
    next_since = body["next_since"]
    r2 = app_client.get(f"/v1/claim/query?session_id=page&limit=2&since={next_since}")
    assert r2.status_code == 200
    body2 = r2.json()
    assert len(body2["claims"]) == 2
    # Different claims (no overlap)
    ids_first = {c["attrs"]["session_id"] + str(c["tx"]) for c in body["claims"]}
    ids_second = {c["attrs"]["session_id"] + str(c["tx"]) for c in body2["claims"]}
    assert not (ids_first & ids_second)


def test_query_mixed_namespace_only_returns_claims(app_client):
    """G3 invariant: in-process :llm/decision datoms must NOT leak via /v1/claim/query.

    Note: this test directly reaches into app.state.substrate to write a fact
    datom alongside the HTTP-emitted claim. The query MUST filter to claim
    kinds only.
    """
    import datetime as dt
    import uuid as _uuid
    _emit_one(app_client, session_id="mixed")

    # Write a fact-namespace datom directly into the same substrate
    substrate = app_client.app.state.substrate
    fact_id = _uuid.uuid4().hex
    substrate.fact.transact([{
        "e": fact_id,
        "a": ":llm/decision",
        "v": '{"kind":"act"}',
        "valid_from": dt.datetime.now(dt.timezone.utc),
    }])

    r = app_client.get("/v1/claim/query?session_id=mixed")
    assert r.status_code == 200
    for c in r.json()["claims"]:
        assert c["kind"].startswith(":claim/"), f"fact-namespace leak: {c['kind']}"
