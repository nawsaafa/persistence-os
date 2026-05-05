# tests/http/test_routes_claim_emit.py
"""Phase 2.1c — POST /v1/claim/emit (Design §4.1, §10.2)."""
import os

import pytest
from fastapi.testclient import TestClient


# app_client fixture is provided by tests/http/conftest.py (yield + substrate teardown).


def _valid_claim_payload(**overrides):
    base = {
        "tool": "Bash", "args": {"command": "ls"},
        "body_hash": None, "body_summary": "ok",
        "body_disposition": "inline",
        "started_at": 1714839028000, "duration_ms": 12, "exit_code": 0,
        "session_id": "session-test", "parent_correlation_id": None,
    }
    base.update(overrides)
    return {"kind": ":claim/tool-exec", "attrs": base}


def test_emit_single_claim_happy_path(app_client):
    r = app_client.post("/v1/claim/emit", json={"claims": [_valid_claim_payload()]})
    assert r.status_code == 200
    body = r.json()
    assert body["tx"] > 0
    assert len(body["claim_ids"]) == 1
    assert body["audit_chain_head"].startswith("sha256:")
    assert body["caller_identity"] is None


def test_emit_multi_claim_atomic(app_client):
    r = app_client.post("/v1/claim/emit", json={"claims": [
        _valid_claim_payload(session_id="s1"),
        _valid_claim_payload(session_id="s2"),
    ]})
    assert r.status_code == 200
    assert len(r.json()["claim_ids"]) == 2


def test_emit_atomic_failure_one_bad_no_commit(app_client):
    """If one claim in a batch fails validation, the whole batch fails — no partial commit."""
    head_before = app_client.post("/v1/claim/emit", json={
        "claims": [_valid_claim_payload(session_id="head-probe")]
    }).json()["audit_chain_head"]

    r = app_client.post("/v1/claim/emit", json={"claims": [
        _valid_claim_payload(session_id="ok"),
        {"kind": ":claim/tool-exec", "attrs": {"tool": "Bash"}},  # missing required fields
    ]})
    assert r.status_code == 422
    assert r.json()["error"] == "attrs_validation_failed"

    head_after = app_client.post("/v1/claim/emit", json={
        "claims": [_valid_claim_payload(session_id="head-probe-2")]
    }).json()["audit_chain_head"]
    q = app_client.get("/v1/claim/query?session_id=ok")
    assert q.status_code == 200
    assert q.json()["claims"] == []


def test_emit_kind_restriction_rejects_fact_kind(app_client):
    r = app_client.post("/v1/claim/emit", json={"claims": [{
        "kind": ":llm/decision",
        "attrs": {"kind": "act"},
    }]})
    assert r.status_code == 400
    assert r.json()["error"] == "not_a_claim_kind"


def test_emit_unknown_claim_kind_rejected(app_client):
    r = app_client.post("/v1/claim/emit", json={"claims": [{
        "kind": ":claim/totally-made-up",
        "attrs": {},
    }]})
    assert r.status_code == 400
    assert r.json()["error"] == "not_a_claim_kind"


def test_emit_attrs_schema_violation_returns_422(app_client):
    """422 attrs_validation_failed for both schema mismatch and cross-field invariants (R1.2 N2)."""
    r = app_client.post("/v1/claim/emit", json={"claims": [{
        "kind": ":claim/tool-exec",
        "attrs": _valid_claim_payload()["attrs"] | {"body_disposition": "blobbed", "body_hash": None},
    }]})
    assert r.status_code == 422
    assert r.json()["error"] == "attrs_validation_failed"


def test_emit_oversize_body_rejected_413(app_client):
    huge = "x" * (1024 * 1024 + 1)  # 1 MiB + 1
    r = app_client.post(
        "/v1/claim/emit",
        json={"claims": [_valid_claim_payload(body_summary="ok"), {"kind": ":claim/tool-exec", "attrs": {"junk": huge}}]},
    )
    assert r.status_code == 413
    assert r.json()["error"] == "oversize_body"


def test_emit_malformed_body_rejected_400(app_client):
    r = app_client.post("/v1/claim/emit", content=b"not-json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"] == "malformed_body"
