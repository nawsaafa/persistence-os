"""Phase 2.1c — audit chain integrity tests (Design §10.4 last bullet, §10.5)."""
from __future__ import annotations

import datetime as dt
import json
import uuid

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


def test_audit_chain_head_advances_with_emits(app_client):
    """audit_chain_head must be a valid sha256:<hex> string and must change
    between two consecutive emits.

    Phase 2.1c.6 (R1 BLOCKING fold from 2.1c R1.1 W3 rescope): the audit
    chain advances on each :claim/emit perform via the canonical audit
    middleware (see _audit_stack.py CANONICAL_AUDIT_WRAPPED_OPS). This
    test asserts the head shape AND that two consecutive emits produce
    distinct heads — the falsifiable acceptance signal that confirmed
    2.1c.6 shipped (was xfail-strict in 2.1c, flipped PASS here).

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
    # Heads must advance — the 2.1c.6 acceptance contract.
    assert h1 != h2, (
        f"audit_chain_head did not advance: {h1!r} == {h2!r}. Either the audit chain "
        "is not wired against build_app's substrate, OR emits are not committing. "
        "Check substrate._canonical_audit_entries length growth across emits."
    )


def test_concurrent_emits_serialize_distinct_datoms(app_client):
    """Two rapid emits must produce 2 distinct claim_ids and the audit
    chain head must be valid sha256:... shape for both.

    Phase 2.1c.6 (G8 — R1 BLOCKING 2 fold): post-2.1c.6 this test
    asserts (a) both heads are valid sha256:<hex>, (b) both heads are
    present in _canonical_audit_entries, (c) the entries form a valid
    prev-hash chain (each entry's prev_hash references either nothing
    -as-genesis or a prior entry's id). This is the regression gate
    proving concurrent perform doesn't corrupt the chain.

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

    # Phase 2.1c.6: post-fix assertions (G8 R1 BLOCKING 2 fold).
    h1 = r1.json()["audit_chain_head"]
    h2 = r2.json()["audit_chain_head"]
    assert h1.startswith("sha256:") and h2.startswith("sha256:"), (
        f"both heads must be sha256-prefixed; got {h1!r}, {h2!r}"
    )
    assert h1 != h2, f"distinct concurrent emits → distinct heads; got {h1!r} == {h2!r}"

    # Chain integrity: both heads must appear as ids in _canonical_audit_entries
    # AND form a valid prev-hash chain.
    substrate = app_client.app.state.substrate
    entries = list(substrate._canonical_audit_entries)
    ids = {e.id for e in entries}
    assert h1 in ids and h2 in ids, (
        f"both concurrent emit heads must be present in canonical entries: "
        f"h1={h1!r}, h2={h2!r}, ids={sorted(ids)!r}"
    )

    # prev-hash chain integrity: every entry except the genesis must reference
    # a prior entry's id.
    seen: set[str] = set()
    for e in entries:
        if e.prev_hash is not None:
            assert e.prev_hash in seen, (
                f"entry {e.id!r} has prev_hash={e.prev_hash!r} which does not "
                f"reference a prior entry id; chain is corrupt under concurrent "
                f"perform. Prior ids seen: {sorted(seen)!r}"
            )
        seen.add(e.id)

    # Verify both claims are retrievable
    q = app_client.get("/v1/claim/query?session_id=concurrent&limit=100")
    assert q.status_code == 200
    returned_ids = {c["attrs"].get("tool") for c in q.json()["claims"]}
    assert "Concurrent0" in returned_ids
    assert "Concurrent1" in returned_ids


def test_emit_audit_entry_args_hash(app_client):
    """Phase 2.1c.6 G2 — :claim/emit audit entry has expected op + args_hash.

    Per design § 3.4 + R1 BLOCKING 1 fold: AuditEntry persists args_hash,
    NOT the raw args dict. Test computes the expected hash via the same
    canonical_hash function the audit middleware uses and asserts equality.
    """
    from persistence.effect import AuditEntry
    from persistence.effect.canonical import canonical_hash

    substrate = app_client.app.state.substrate

    payload = {
        "kind": ":claim/tool-exec",
        "attrs": {
            "tool": "ls",
            "args": {},
            "body_hash": None,
            "body_summary": "shape-test",
            "body_disposition": "inline",
            "started_at": 0,
            "duration_ms": 0,
            "exit_code": 0,
            "session_id": "shape-test-sess",
            "parent_correlation_id": None,
        },
    }
    r = app_client.post("/v1/claim/emit", json={"claims": [payload]})
    assert r.status_code == 200
    body = r.json()
    tx = body["tx"]
    claim_ids_from_response = body["claim_ids"]
    assert len(claim_ids_from_response) == 1

    entries = substrate._canonical_audit_entries
    assert entries is not None and len(entries) >= 1, (
        "expected at least one canonical audit entry after emit; got "
        f"{None if entries is None else len(entries)}"
    )
    last: AuditEntry = entries[-1]
    assert last.op == ":claim/emit", (
        f"expected last audit entry op == ':claim/emit', got {last.op!r}"
    )

    # Reconstruct the exact perform args dict using the claim_id from response
    # and the tx and kind_counts that the route computed.
    expected_args = {
        "claim_ids": claim_ids_from_response,
        "tx": tx,
        "kind_counts": {":claim/tool-exec": 1},
    }
    expected_hash = canonical_hash(expected_args)
    assert last.args_hash == expected_hash, (
        f"args_hash mismatch: expected {expected_hash!r} for "
        f"args={expected_args!r}, got {last.args_hash!r}"
    )


def test_audit_perform_failure_does_not_roll_back_facts(app_client):
    """Phase 2.1c.6 G4 — fact-write survives audit-perform failure.

    Per design § 5.2: facts go to the log first; if the audit perform
    raises, the HTTP route returns 500, but the claim datoms ARE in
    the fact log and the audit chain did NOT advance. This is the
    'provenance survives audit failure' guarantee that matches the
    Phase 2.1b :llm/messages-survives-:llm/call-failure pattern.
    """
    from persistence.effect.runtime import Handler

    substrate = app_client.app.state.substrate
    entries_before = len(substrate._canonical_audit_entries)
    log_before = len(list(substrate._db.log()))

    # Install a top-position handler that raises on :claim/emit.
    def _raise_on_emit(_args, _k, _ctx):
        raise RuntimeError("simulated audit perform failure (G4)")

    failing = Handler(
        name="g4-failing-claim-emit",
        wraps={":claim/emit"},
        clauses={":claim/emit": _raise_on_emit},
    )
    substrate.effect.install_handler(failing, position="top")

    payload = {
        "kind": ":claim/tool-exec",
        "attrs": {
            "tool": "g4-tool",
            "args": {},
            "body_hash": None,
            "body_summary": "g4 test",
            "body_disposition": "inline",
            "started_at": 0,
            "duration_ms": 0,
            "exit_code": 0,
            "session_id": "g4-sess",
            "parent_correlation_id": None,
        },
    }

    # Use raise_server_exceptions=False so the TestClient returns a 500
    # response instead of re-raising the RuntimeError (Starlette default
    # is raise_server_exceptions=True).
    from fastapi.testclient import TestClient

    no_raise_client = TestClient(
        app_client.app,
        client=("127.0.0.1", 9999),
        raise_server_exceptions=False,
    )

    try:
        r = no_raise_client.post("/v1/claim/emit", json={"claims": [payload]})
        # FastAPI default unhandled-exception → 500
        assert r.status_code == 500, (
            f"expected 500 from audit-perform failure, got {r.status_code} "
            f"with body {r.text!r}"
        )

        # Critical assertion: the fact-write happened BEFORE the perform.
        log_after = list(substrate._db.log())
        assert len(log_after) > log_before, (
            f"fact log did not grow despite fact.transact happening before "
            f"audit perform: {log_before} → {len(log_after)}"
        )

        # Audit chain did NOT advance (perform raised before middleware
        # could append the AuditEntry).
        entries_after = len(substrate._canonical_audit_entries)
        assert entries_after == entries_before, (
            f"audit chain advanced despite perform failure: "
            f"{entries_before} → {entries_after}"
        )
    finally:
        # Uninstall the failing handler so subsequent tests aren't poisoned.
        runtime = substrate._runtime
        runtime.handlers = [
            h for h in runtime.handlers if h.name != "g4-failing-claim-emit"
        ]
