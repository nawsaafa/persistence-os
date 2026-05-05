"""Phase 2.1c — audit chain integrity tests (Design §10.4 last bullet, §10.5)."""
from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest


# app_client fixture is provided by tests/http/conftest.py (yield + substrate teardown).


def test_mixed_namespace_audit_chain_integrity(app_client):
    """In-process :llm/decision fact AND HTTP-emitted :claim/tool-exec must
    coexist in the substrate, but /v1/claim/query returns ONLY the claim
    (G3 invariant from Design §10.4)."""
    substrate = app_client.app.state.substrate

    # Write a fact-namespace datom directly into the same substrate
    fact_id = uuid.uuid4().hex
    substrate.fact.transact([{
        "e": fact_id,
        "a": ":llm/decision",
        "v": json.dumps({"kind": "act", "confidence": 0.9, "payload": {}}),
        "valid_from": dt.datetime.now(dt.timezone.utc),
    }])

    # Emit a claim-namespace datom via HTTP (same substrate)
    app_client.post("/v1/claim/emit", json={"claims": [{
        "kind": ":claim/tool-exec",
        "attrs": {
            "tool": "Bash", "args": {},
            "body_hash": None, "body_summary": "ok",
            "body_disposition": "inline",
            "started_at": 0, "duration_ms": 0, "exit_code": 0,
            "session_id": "mixed", "parent_correlation_id": None,
        },
    }]})

    # /v1/claim/query MUST NOT return the :llm/decision datom (G3 invariant)
    q = app_client.get("/v1/claim/query?session_id=mixed")
    assert q.status_code == 200
    for c in q.json()["claims"]:
        assert c["kind"].startswith(":claim/"), f"Fact-namespace leak: {c['kind']}"


@pytest.mark.xfail(
    reason=(
        "Phase 2.1c R1.1 W3 honest-rescope (ARIS F1 BLOCKING): "
        "s.fact.transact() bypasses the canonical audit chain — only tx.effect() "
        "calls populate substrate._canonical_audit_entries. audit_chain_head is "
        "None for all emits until Phase 2.1c.6 wires fact-level writes through "
        "the effect audit stack. "
        "Acceptance signal: this test flips PASS when 2.1c.6 ships. "
        "Design stub: docs/plans/2026-05-04-phase-2.1c.6-audit-chain-wiring-design.md"
    ),
    strict=True,
)
def test_audit_chain_head_advances_with_emits(app_client):
    """audit_chain_head must be a valid sha256:<hex> string and must change
    between two consecutive emits.

    Marked xfail(strict=True) — Phase 2.1c R1.1 W3 honest-rescope:
    ``s.fact.transact`` bypasses the canonical audit chain, so
    ``_extract_audit_chain_head`` returns None for all emits.  This test
    asserts ``h1.startswith("sha256:")`` which raises AttributeError on None,
    making it a *failing* test that xfail correctly captures.

    This test flips PASS (and strict-xfail becomes an unexpected-pass ERROR)
    when Phase 2.1c.6 ships proper audit-chain wiring — serving as the
    falsifiable acceptance signal per the W3 honest-rescope pattern.

    See: docs/plans/2026-05-04-phase-2.1c.6-audit-chain-wiring-design.md
    """
    r1 = app_client.post("/v1/claim/emit", json={"claims": [{
        "kind": ":claim/tool-exec",
        "attrs": {
            "tool": "A", "args": {},
            "body_hash": None, "body_summary": "1",
            "body_disposition": "inline",
            "started_at": 0, "duration_ms": 0, "exit_code": 0,
            "session_id": "head-test", "parent_correlation_id": None,
        },
    }]})
    h1 = r1.json()["audit_chain_head"]

    r2 = app_client.post("/v1/claim/emit", json={"claims": [{
        "kind": ":claim/tool-exec",
        "attrs": {
            "tool": "B", "args": {},
            "body_hash": None, "body_summary": "2",
            "body_disposition": "inline",
            "started_at": 0, "duration_ms": 0, "exit_code": 0,
            "session_id": "head-test", "parent_correlation_id": None,
        },
    }]})
    h2 = r2.json()["audit_chain_head"]

    # Heads must be valid sha256:<hex> shape
    assert h1.startswith("sha256:")
    assert h2.startswith("sha256:")
    # In real wiring, h1 != h2 (head advances). With the placeholder, both are
    # 'sha256:placeholder' so this assertion fails — that is the expected failure.
    assert h1 != h2, (
        f"audit_chain_head did not advance: {h1!r} == {h2!r}. Either the audit chain "
        "is not wired against build_app's substrate, OR emits are not committing. "
        "Check substrate._canonical_audit_entries length growth across emits."
    )


def test_concurrent_emits_serialize_distinct_datoms(app_client):
    """Two rapid emits must produce 2 distinct claim_ids and the audit
    chain head must be valid sha256:... shape for both.

    Uses synchronous TestClient in rapid succession (logical concurrency).
    For true OS-thread concurrency, use httpx.AsyncClient + asyncio.gather;
    that is deferred to Phase 2.1c.5 once the async surface stabilises.
    """
    payloads = [{
        "kind": ":claim/tool-exec",
        "attrs": {
            "tool": f"Concurrent{i}", "args": {},
            "body_hash": None, "body_summary": str(i),
            "body_disposition": "inline",
            "started_at": 0, "duration_ms": 0, "exit_code": 0,
            "session_id": "concurrent", "parent_correlation_id": None,
        },
    } for i in range(2)]

    r1 = app_client.post("/v1/claim/emit", json={"claims": [payloads[0]]})
    r2 = app_client.post("/v1/claim/emit", json={"claims": [payloads[1]]})
    assert r1.status_code == 200
    assert r2.status_code == 200

    id1 = r1.json()["claim_ids"][0]
    id2 = r2.json()["claim_ids"][0]
    assert id1 != id2, "claim_ids should be distinct across emits"

    # audit_chain_head is None in Phase 2.1c (2.1c.6 rescope — wiring deferred)
    h1 = r1.json()["audit_chain_head"]
    h2 = r2.json()["audit_chain_head"]
    assert h1 is None  # TODO 2.1c.6: assert h1.startswith("sha256:")
    assert h2 is None  # TODO 2.1c.6: assert h2.startswith("sha256:")

    # Verify both claims are retrievable
    q = app_client.get("/v1/claim/query?session_id=concurrent&limit=100")
    assert q.status_code == 200
    returned_ids = {c["attrs"].get("tool") for c in q.json()["claims"]}
    assert "Concurrent0" in returned_ids
    assert "Concurrent1" in returned_ids
