"""SDK3 — audit emission contract (every tool call emits exactly 1 entry)."""
from __future__ import annotations

from persistence.sdk.mcp._names import _NAMES

from tests.sdk.mcp.conftest import call_tool


def test_remember_emits_exactly_one_audit_entry(initialized_server, substrate):
    pre = len(substrate._audit_entries)
    call_tool(initialized_server, "persistence_remember", {"content": "hi"})
    post = len(substrate._audit_entries)
    assert post - pre == 1


def test_recall_emits_exactly_one_audit_entry(initialized_server, substrate):
    pre = len(substrate._audit_entries)
    call_tool(initialized_server, "persistence_recall", {"query": "x"})
    post = len(substrate._audit_entries)
    assert post - pre == 1


def test_forget_emits_exactly_one_audit_entry(initialized_server, substrate):
    r = call_tool(
        initialized_server,
        "persistence_remember",
        {"content": "to forget"},
    )
    eid = r["result"]["structuredContent"]["eid"]
    pre = len(substrate._audit_entries)
    call_tool(initialized_server, "persistence_forget", {"eid": eid})
    post = len(substrate._audit_entries)
    assert post - pre == 1


def test_audit_window_emits_exactly_one_audit_entry(
    initialized_server, substrate
):
    pre = len(substrate._audit_entries)
    call_tool(
        initialized_server,
        "persistence_audit_window",
        {"from_tx": 0},
    )
    post = len(substrate._audit_entries)
    assert post - pre == 1


def test_replay_check_emits_exactly_one_audit_entry(
    initialized_server, substrate
):
    call_tool(initialized_server, "persistence_remember", {"content": "x"})
    pre = len(substrate._audit_entries)
    call_tool(
        initialized_server,
        "persistence_replay_check",
        {"tx": 0, "window": 4},
    )
    post = len(substrate._audit_entries)
    assert post - pre == 1


def test_view_at_emits_exactly_one_audit_entry(initialized_server, substrate):
    pre = len(substrate._audit_entries)
    call_tool(initialized_server, "persistence_view_at", {"tx": 0})
    post = len(substrate._audit_entries)
    assert post - pre == 1


def test_audit_entry_has_correct_op_for_each_tool(
    initialized_server, substrate
):
    """ADR-15: each tool's audit op is `:mcp/op-<verb>`."""
    pre_len = len(substrate._audit_entries)
    call_tool(initialized_server, "persistence_remember", {"content": "x"})
    entry = substrate._audit_entries[pre_len]
    assert entry["op"] == _NAMES["remember"]["audit_op"]
    assert entry["op"] == ":mcp/op-remember"


def test_audit_entry_for_recall_has_correct_op(initialized_server, substrate):
    pre = len(substrate._audit_entries)
    call_tool(initialized_server, "persistence_recall", {"query": "x"})
    assert substrate._audit_entries[pre]["op"] == ":mcp/op-recall"


def test_audit_entry_records_args_hash(initialized_server, substrate):
    pre = len(substrate._audit_entries)
    call_tool(initialized_server, "persistence_remember", {"content": "z"})
    entry = substrate._audit_entries[pre]
    assert entry["args"]["args_hash"]
    assert isinstance(entry["args"]["args_hash"], str)
    assert len(entry["args"]["args_hash"]) == 64  # sha256 hex


def test_audit_entry_records_result_hash(initialized_server, substrate):
    pre = len(substrate._audit_entries)
    call_tool(initialized_server, "persistence_remember", {"content": "z"})
    entry = substrate._audit_entries[pre]
    assert entry["args"]["result_hash"]


def test_audit_entry_records_verdict_ok_for_success(
    initialized_server, substrate
):
    pre = len(substrate._audit_entries)
    call_tool(initialized_server, "persistence_remember", {"content": "z"})
    entry = substrate._audit_entries[pre]
    assert entry["args"]["verdict"] == "ok"


def test_audit_entry_records_verdict_error_for_failure(
    initialized_server, substrate
):
    pre = len(substrate._audit_entries)
    call_tool(initialized_server, "persistence_forget", {"eid": "00000000-0000-0000-0000-000000000000"})
    entry = substrate._audit_entries[pre]
    assert entry["args"]["verdict"] == "error"


def test_audit_chain_prev_hash_links_correctly(
    initialized_server, substrate
):
    """Two consecutive entries: the second's prev_hash == the first's id."""
    call_tool(initialized_server, "persistence_remember", {"content": "a"})
    call_tool(initialized_server, "persistence_remember", {"content": "b"})
    entries = substrate._audit_entries
    assert len(entries) >= 2
    e1 = entries[-2]
    e2 = entries[-1]
    assert e2["prev_hash"] == e1["id"]


def test_audit_chain_first_entry_has_null_prev_hash(substrate, server):
    """A fresh substrate's first MCP audit entry has prev_hash=None."""
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
    assert substrate._audit_entries == []
    call_tool(server, "persistence_remember", {"content": "first"})
    e = substrate._audit_entries[0]
    assert e["prev_hash"] is None


def test_audit_entry_has_id_field(initialized_server, substrate):
    call_tool(initialized_server, "persistence_remember", {"content": "id"})
    entry = substrate._audit_entries[-1]
    assert "id" in entry
    assert len(entry["id"]) == 64  # sha256 hex


def test_audit_entry_records_tool_wire_name(initialized_server, substrate):
    call_tool(initialized_server, "persistence_recall", {"query": "x"})
    entry = substrate._audit_entries[-1]
    assert entry["args"]["tool"] == "persistence_recall"


def test_capability_denied_still_emits_audit_entry(substrate):
    """Even cap-denied calls emit an audit entry — defense-in-depth."""
    from persistence.sdk.mcp import create_server

    server = create_server(substrate, token_caps={"mcp.recall"})
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
    pre = len(substrate._audit_entries)
    call_tool(server, "persistence_remember", {"content": "denied"})
    post = len(substrate._audit_entries)
    assert post - pre == 1
    assert substrate._audit_entries[-1]["args"]["verdict"] == "error"
