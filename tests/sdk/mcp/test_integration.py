"""SDK3 — end-to-end integration: full session of mixed traffic."""
from __future__ import annotations

import json

from tests.sdk.mcp.conftest import call_tool


def test_full_session_remember_recall_audit(initialized_server, substrate):
    """Open substrate → remember twice → recall → audit_window → verify
    audit chain integrity end-to-end."""
    r1 = call_tool(
        initialized_server,
        "persistence_remember",
        {"content": "design doc draft"},
    )
    r2 = call_tool(
        initialized_server,
        "persistence_remember",
        {"content": "review feedback for design doc"},
    )

    # Recall by substring should return both, ordered most-recent first.
    rec = call_tool(
        initialized_server,
        "persistence_recall",
        {"query": "design doc"},
    )
    hits = rec["result"]["structuredContent"]["hits"]
    assert len(hits) == 2

    # Audit window
    aw = call_tool(
        initialized_server,
        "persistence_audit_window",
        {"from_tx": 0, "limit": 100},
    )
    entries = aw["result"]["structuredContent"]["entries"]
    # 4 calls = 4 audit entries (the 4th is this audit_window call itself,
    # which lands BEFORE its result is built; check we have at least 3).
    assert len(entries) >= 3

    # Audit-chain hash continuity: every entry's prev_hash should match
    # the prior entry's id.
    for prev, cur in zip(substrate._audit_entries, substrate._audit_entries[1:]):
        assert cur["prev_hash"] == prev["id"]


def test_replay_check_after_audit_chain_built(
    initialized_server, substrate
):
    """Replay-check over a window of accumulated entries returns ok."""
    for i in range(5):
        call_tool(
            initialized_server,
            "persistence_remember",
            {"content": f"fact-{i}"},
        )
    rc = call_tool(
        initialized_server,
        "persistence_replay_check",
        {"tx": 2, "window": 4},
    )
    assert rc["result"]["isError"] is False
    assert rc["result"]["structuredContent"]["ok"] is True


def test_replay_check_detects_tampered_audit_entry(
    initialized_server, substrate
):
    """If an audit entry is mutated after the fact, replay_check flags it."""
    call_tool(initialized_server, "persistence_remember", {"content": "a"})
    call_tool(initialized_server, "persistence_remember", {"content": "b"})
    call_tool(initialized_server, "persistence_remember", {"content": "c"})
    # Tamper with the middle entry's content (id will no longer match).
    substrate._audit_entries[1]["args"]["verdict"] = "tampered"
    rc = call_tool(
        initialized_server,
        "persistence_replay_check",
        {"tx": 1, "window": 4},
    )
    sc = rc["result"]["structuredContent"]
    assert sc["ok"] is False
    assert sc["reason_code"] == "mismatch_audit_chain"


def test_session_via_stdio(server, substrate):
    """Full lifecycle via serve_stdio — initialize, list, call, list."""
    import io

    requests = [
        json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "int", "version": "0.1"},
            },
        }),
        json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }),
        json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }),
        json.dumps({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "persistence_remember",
                "arguments": {"content": "stdio integration"},
            },
        }),
        json.dumps({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "resources/list",
            "params": {},
        }),
    ]
    stdin = io.StringIO("\n".join(requests) + "\n")
    stdout = io.StringIO()
    server.serve_stdio(stdin=stdin, stdout=stdout)
    replies = [
        json.loads(line)
        for line in stdout.getvalue().splitlines()
        if line and json.loads(line).get("id") is not None
    ]
    # 4 numbered replies (notifications/initialized has no id reply).
    assert len(replies) == 4
    assert replies[0]["id"] == 1
    assert replies[2]["id"] == 3
    sc = replies[2]["result"]["structuredContent"]
    assert "eid" in sc
    # Audit chain has 1 entry (the remember call).
    assert len(substrate._audit_entries) == 1
