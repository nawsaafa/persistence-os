"""SDK3 — audit-tail resource (list/read/subscribe/notify)."""
from __future__ import annotations

import json

from persistence.sdk import Substrate
from persistence.sdk.mcp import create_server

from tests.sdk.mcp.conftest import call_tool


def test_resources_list_contains_audit_tail(initialized_server):
    resp = initialized_server.handle_request({
        "jsonrpc": "2.0",
        "id": 5,
        "method": "resources/list",
        "params": {},
    })
    resources = resp["result"]["resources"]
    assert any(
        r["uri"] == "persistence-os://audit/tail" for r in resources
    )


def test_resources_read_audit_tail_returns_json(initialized_server):
    call_tool(initialized_server, "persistence_remember", {"content": "x"})
    resp = initialized_server.handle_request({
        "jsonrpc": "2.0",
        "id": 6,
        "method": "resources/read",
        "params": {"uri": "persistence-os://audit/tail"},
    })
    contents = resp["result"]["contents"]
    assert len(contents) == 1
    assert contents[0]["mimeType"] == "application/json"
    body = json.loads(contents[0]["text"])
    assert "entries" in body


def test_resources_read_unknown_uri_returns_not_found(initialized_server):
    resp = initialized_server.handle_request({
        "jsonrpc": "2.0",
        "id": 7,
        "method": "resources/read",
        "params": {"uri": "unknown://blah"},
    })
    assert resp["result"]["isError"] is True
    assert resp["result"]["_meta"]["category"] == "not_found"


def test_resources_subscribe_audit_tail(initialized_server):
    resp = initialized_server.handle_request({
        "jsonrpc": "2.0",
        "id": 8,
        "method": "resources/subscribe",
        "params": {"uri": "persistence-os://audit/tail"},
    })
    assert resp["result"]["ok"] is True


def test_resources_subscribe_then_tool_call_emits_notification(
    initialized_server,
):
    initialized_server.handle_request({
        "jsonrpc": "2.0",
        "id": 8,
        "method": "resources/subscribe",
        "params": {"uri": "persistence-os://audit/tail"},
    })
    call_tool(initialized_server, "persistence_remember", {"content": "x"})
    notes = initialized_server.drain_notifications()
    assert any(
        n.get("method") == "notifications/resources/updated"
        for n in notes
    )


def test_resources_unsubscribe_stops_notifications(initialized_server):
    initialized_server.handle_request({
        "jsonrpc": "2.0",
        "id": 8,
        "method": "resources/subscribe",
        "params": {"uri": "persistence-os://audit/tail"},
    })
    initialized_server.handle_request({
        "jsonrpc": "2.0",
        "id": 9,
        "method": "resources/unsubscribe",
        "params": {"uri": "persistence-os://audit/tail"},
    })
    initialized_server.drain_notifications()  # clear pending
    call_tool(initialized_server, "persistence_remember", {"content": "x"})
    notes = initialized_server.drain_notifications()
    assert all(
        n.get("method") != "notifications/resources/updated"
        for n in notes
    )


def test_resources_subscribe_unknown_uri_rejected(initialized_server):
    resp = initialized_server.handle_request({
        "jsonrpc": "2.0",
        "id": 8,
        "method": "resources/subscribe",
        "params": {"uri": "no-such://thing"},
    })
    assert resp["result"]["isError"] is True
    assert resp["result"]["_meta"]["category"] == "not_found"


def test_resources_subscribe_capability_denied_without_audit_read():
    s = Substrate.open("memory")
    try:
        server = create_server(s, token_caps={"mcp.remember"})
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
        resp = server.handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "resources/subscribe",
            "params": {"uri": "persistence-os://audit/tail"},
        })
        assert resp["result"]["isError"] is True
        assert resp["result"]["_meta"]["category"] == "capability_denied"
    finally:
        s.close()
