"""SDK3 — MCP lifecycle conformance (G2, stdio-only)."""
from __future__ import annotations

import json

import pytest

from persistence.sdk import Substrate
from persistence.sdk.mcp import MCPServer, create_server
from persistence.sdk.mcp._server import (
    JSONRPC_INVALID_REQUEST,
    MCP_PROTOCOL_VERSION_FALLBACK,
    MCP_PROTOCOL_VERSION_PRIMARY,
)


def _initialize_request(version: str = "2025-06-18") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": version,
            "capabilities": {"tools": {}, "resources": {"subscribe": True}},
            "clientInfo": {"name": "g2-test", "version": "0.1"},
        },
    }


def test_initialize_replies_with_matching_protocol_version(server):
    resp = server.handle_request(_initialize_request("2025-06-18"))
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == "2025-06-18"


def test_initialize_falls_back_for_older_known_revision(server):
    resp = server.handle_request(_initialize_request("2025-03-26"))
    assert resp["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION_FALLBACK


def test_initialize_with_unknown_version_returns_primary(server):
    resp = server.handle_request(_initialize_request("9999-12-31"))
    # Server replies with its primary version when client asks for unknown.
    assert resp["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION_PRIMARY


def test_initialize_advertises_server_capabilities(server):
    resp = server.handle_request(_initialize_request())
    caps = resp["result"]["capabilities"]
    assert "tools" in caps
    assert "resources" in caps
    assert caps["resources"]["subscribe"] is True


def test_initialize_returns_server_info(server):
    resp = server.handle_request(_initialize_request())
    info = resp["result"]["serverInfo"]
    assert info["name"] == "persistence-os-mcp"
    assert info["version"] == "0.8.0a1"


def test_initialize_records_client_info(server):
    server.handle_request(_initialize_request())
    assert server._client_info == {"name": "g2-test", "version": "0.1"}


def test_g11_init_instructions_contain_no_confidentiality_clause(server):
    """G11: confidentiality non-goal posted in artifacts (ADR-17)."""
    resp = server.handle_request(_initialize_request())
    instructions = resp["result"]["instructions"]
    assert "NO confidentiality guarantees" in instructions


def test_initialized_notification_marks_server_ready(server):
    server.handle_request(_initialize_request())
    notif = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }
    out = server.handle_request(notif)
    assert out is None  # notifications get no reply
    assert server._initialized is True


def test_tools_list_rejected_before_initialized(server):
    server.handle_request(_initialize_request())
    # Notifications/initialized NOT sent yet.
    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    })
    assert "error" in resp
    assert resp["error"]["code"] == JSONRPC_INVALID_REQUEST


def test_tools_list_after_initialized(initialized_server):
    resp = initialized_server.handle_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    })
    assert "result" in resp
    assert len(resp["result"]["tools"]) == 6


def test_tools_list_advertises_all_six_tools_in_order(initialized_server):
    resp = initialized_server.handle_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    })
    expected = [
        "persistence_remember",
        "persistence_recall",
        "persistence_forget",
        "persistence_audit_window",
        "persistence_replay_check",
        "persistence_view_at",
    ]
    actual = [t["name"] for t in resp["result"]["tools"]]
    assert actual == expected


def test_each_tool_has_input_and_output_schema(initialized_server):
    resp = initialized_server.handle_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    })
    for tool in resp["result"]["tools"]:
        assert "inputSchema" in tool
        assert "outputSchema" in tool
        assert tool["inputSchema"]["type"] == "object"
        assert tool["outputSchema"]["type"] == "object"


def test_each_tool_has_a_description(initialized_server):
    resp = initialized_server.handle_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    })
    for tool in resp["result"]["tools"]:
        assert isinstance(tool["description"], str)
        assert len(tool["description"]) > 0


def test_unknown_method_returns_method_not_found(initialized_server):
    resp = initialized_server.handle_request({
        "jsonrpc": "2.0",
        "id": 99,
        "method": "totally/not/a/method",
        "params": {},
    })
    assert "error" in resp
    assert resp["error"]["code"] == -32601  # JSONRPC_METHOD_NOT_FOUND


def test_invalid_jsonrpc_version_rejected(server):
    resp = server.handle_request({
        "jsonrpc": "1.0",
        "id": 1,
        "method": "initialize",
        "params": {},
    })
    assert "error" in resp
    assert resp["error"]["code"] == JSONRPC_INVALID_REQUEST


def test_non_object_request_rejected(server):
    resp = server.handle_request("not a dict")  # type: ignore
    assert "error" in resp
    assert resp["error"]["code"] == JSONRPC_INVALID_REQUEST


def test_missing_method_rejected(server):
    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 1,
        "params": {},
    })
    assert "error" in resp


def test_ping_works_pre_initialize(server):
    resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "ping",
    })
    assert resp["result"] == {}


def test_resources_list_after_initialized(initialized_server):
    resp = initialized_server.handle_request({
        "jsonrpc": "2.0",
        "id": 5,
        "method": "resources/list",
        "params": {},
    })
    assert "result" in resp
    resources = resp["result"]["resources"]
    assert len(resources) == 1
    assert resources[0]["uri"] == "persistence-os://audit/tail"


def test_serve_stdio_basic_smoke(server, tmp_path):
    """Drive serve_stdio() with synthetic stdin lines and capture stdout."""
    import io

    stdin_data = "\n".join([
        json.dumps(_initialize_request()),
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
        "",
    ])
    stdin = io.StringIO(stdin_data)
    stdout = io.StringIO()
    server.serve_stdio(stdin=stdin, stdout=stdout)
    output_lines = [
        json.loads(line) for line in stdout.getvalue().splitlines() if line
    ]
    # Two replies: initialize, tools/list (notifications/initialized has no reply).
    assert len(output_lines) == 2
    assert output_lines[0]["id"] == 1
    assert output_lines[0]["result"]["protocolVersion"] == "2025-06-18"
    assert output_lines[1]["id"] == 2
    assert len(output_lines[1]["result"]["tools"]) == 6


def test_serve_stdio_handles_parse_error(server):
    import io

    stdin = io.StringIO("not valid json\n")
    stdout = io.StringIO()
    server.serve_stdio(stdin=stdin, stdout=stdout)
    out = json.loads(stdout.getvalue().strip())
    assert "error" in out
    assert out["error"]["code"] == -32700  # parse error
