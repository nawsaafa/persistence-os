"""Shared fixtures for SDK3 MCP server tests."""
from __future__ import annotations

import pytest

from persistence.sdk import Substrate
from persistence.sdk.mcp import MCPServer, create_server


@pytest.fixture
def substrate():
    s = Substrate.open("memory")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def server(substrate) -> MCPServer:
    return create_server(substrate)


@pytest.fixture
def initialized_server(server) -> MCPServer:
    """A server that has completed the MCP lifecycle handshake."""
    server.handle_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0.1"},
        },
    })
    server.handle_request({
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    })
    return server


def call_tool(server, name: str, arguments: dict, req_id: int = 100) -> dict:
    """Helper: call a tool, return the response dict."""
    return server.handle_request({
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
