"""SDK3 — error envelopes (validation, capability, budget, not_found)."""
from __future__ import annotations

import pytest

from persistence.sdk import Substrate
from persistence.sdk.mcp import Budgets, create_server

from tests.sdk.mcp.conftest import call_tool


def _init(server):
    server.handle_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "x", "version": "0.1"},
        },
    })
    server.handle_request({
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    })


def test_remember_missing_content_returns_validation_error(
    initialized_server,
):
    resp = call_tool(initialized_server, "persistence_remember", {})
    assert resp["result"]["isError"] is True
    assert resp["result"]["_meta"]["category"] == "validation_error"


def test_remember_too_large_content_rejected(initialized_server):
    resp = call_tool(
        initialized_server,
        "persistence_remember",
        {"content": "x" * 100000},  # > 16384
    )
    assert resp["result"]["isError"] is True
    assert resp["result"]["_meta"]["category"] == "validation_error"


def test_recall_missing_query_returns_validation_error(initialized_server):
    resp = call_tool(initialized_server, "persistence_recall", {})
    assert resp["result"]["isError"] is True
    assert resp["result"]["_meta"]["category"] == "validation_error"


def test_recall_k_above_max_rejected(initialized_server):
    resp = call_tool(
        initialized_server,
        "persistence_recall",
        {"query": "x", "k": 999},
    )
    assert resp["result"]["isError"] is True
    assert resp["result"]["_meta"]["category"] == "validation_error"


def test_unknown_tool_returns_validation_error(initialized_server):
    resp = call_tool(
        initialized_server,
        "persistence_does_not_exist",
        {},
    )
    assert resp["result"]["isError"] is True
    assert resp["result"]["_meta"]["category"] == "validation_error"


def test_capability_denied_when_caps_lack_remember():
    """Tool call with insufficient cap → capability_denied envelope."""
    s = Substrate.open("memory")
    try:
        # Server with NO mcp.remember capability.
        server = create_server(s, token_caps={"mcp.recall"})
        _init(server)
        resp = call_tool(server, "persistence_remember", {"content": "x"})
        assert resp["result"]["isError"] is True
        assert resp["result"]["_meta"]["category"] == "capability_denied"
        assert "mcp.remember" in resp["result"]["content"][0]["text"]
    finally:
        s.close()


def test_capability_denied_for_audit_window_without_audit_read():
    s = Substrate.open("memory")
    try:
        server = create_server(s, token_caps={"mcp.remember"})
        _init(server)
        resp = call_tool(
            server, "persistence_audit_window", {"from_tx": 0}
        )
        assert resp["result"]["isError"] is True
        assert resp["result"]["_meta"]["category"] == "capability_denied"
    finally:
        s.close()


def test_capability_denied_for_each_tool():
    """Each tool denied with empty cap-set."""
    s = Substrate.open("memory")
    try:
        server = create_server(s, token_caps=set())
        _init(server)
        denied = [
            ("persistence_remember", {"content": "x"}),
            ("persistence_recall", {"query": "x"}),
            ("persistence_forget", {"eid": "00000000-0000-0000-0000-000000000000"}),
            ("persistence_audit_window", {"from_tx": 0}),
            ("persistence_replay_check", {"tx": 0}),
            ("persistence_view_at", {"tx": 0}),
        ]
        for name, args in denied:
            resp = call_tool(server, name, args)
            assert resp["result"]["isError"] is True
            assert resp["result"]["_meta"]["category"] == "capability_denied"
    finally:
        s.close()


def test_g8a_replay_check_window_too_large_returns_budget_error(
    initialized_server,
):
    """G8a: window > MCP_REPLAY_MAX_WINDOW returns budget envelope."""
    resp = call_tool(
        initialized_server,
        "persistence_replay_check",
        {"tx": 0, "window": 257},
    )
    # The schema validator catches window > 256 as an input violation
    # FIRST; either path is acceptable for this gate as long as it's
    # an error envelope flagging the budget.
    assert resp["result"]["isError"] is True
    cat = resp["result"]["_meta"]["category"]
    assert cat in ("budget_exceeded", "validation_error")


def test_g8a_replay_check_window_at_budget_max_via_direct_handler():
    """Verify the budget path is reachable without schema gating: bypass
    the dispatcher and call the tool fn with window > 256."""
    from persistence.sdk.mcp._tools import tool_replay_check
    from persistence.sdk.mcp._budgets import Budgets
    from datetime import datetime, timezone

    s = Substrate.open("memory")
    try:
        env = tool_replay_check(
            s,
            {"tx": 0, "window": 1024},
            token_caps={"mcp.replay"},
            budgets=Budgets(),
            clock=lambda: datetime.now(tz=timezone.utc),
        )
        assert env["isError"] is True
        assert env["_meta"]["category"] == "budget_exceeded"
        assert env["structuredContent"]["reason_code"] == "window_too_large"
    finally:
        s.close()


def test_g8b_replay_check_synthetic_slow_wallclock():
    """G8b: synthetic-slow handler test — wallclock > budget aborts
    with reason_code='replay_aborted_budget'."""
    from persistence.sdk.mcp._tools import tool_replay_check
    from persistence.sdk.mcp._budgets import Budgets
    from datetime import datetime, timezone

    s = Substrate.open("memory")
    try:
        env = tool_replay_check(
            s,
            {"tx": 0, "window": 4},
            token_caps={"mcp.replay"},
            budgets=Budgets(),
            clock=lambda: datetime.now(tz=timezone.utc),
            synthetic_slow_wallclock=99.0,  # > 5s budget
        )
        assert env["isError"] is True
        assert env["structuredContent"]["reason_code"] == "replay_aborted_budget"
    finally:
        s.close()


def test_g8c_replay_check_rate_limit():
    """G8c: 7 successive successful calls → 7th rejected."""
    s = Substrate.open("memory")
    try:
        budgets = Budgets(replay_rate_limit_per_token=6)
        server = create_server(s, budgets=budgets)
        _init(server)
        # Need at least one entry so replay_check has data
        call_tool(server, "persistence_remember", {"content": "x"})
        results = []
        for i in range(8):
            r = call_tool(
                server,
                "persistence_replay_check",
                {"tx": 0, "window": 4},
                req_id=200 + i,
            )
            results.append(r)
        # First 6 should succeed; 7th should fail with budget
        success_count = sum(
            1 for r in results if r["result"]["isError"] is False
        )
        assert success_count == 6
        # 7th + 8th should be rate-limited.
        budget_errs = [
            r for r in results
            if r["result"]["isError"]
            and r["result"]["_meta"]["category"] == "budget_exceeded"
        ]
        assert len(budget_errs) >= 1
    finally:
        s.close()


def test_g8d_replay_check_never_returns_diff_keys(initialized_server):
    """G8d: replay_check output has NO diff/byte_diff/user_log keys."""
    call_tool(initialized_server, "persistence_remember", {"content": "x"})
    resp = call_tool(
        initialized_server,
        "persistence_replay_check",
        {"tx": 0, "window": 2},
    )
    sc = resp["result"]["structuredContent"]
    forbidden = {"diff", "byte_diff", "user_log", "args", "result", "store"}
    leaked = set(sc.keys()) & forbidden
    assert not leaked, f"replay_check leaked keys: {leaked}"
    # Closed-key-set assertion per G8d.
    expected_keys = {"ok", "reason_code", "window_actual", "head_hash"}
    assert set(sc.keys()) == expected_keys


def test_audit_window_negative_from_tx_rejected(initialized_server):
    resp = call_tool(
        initialized_server,
        "persistence_audit_window",
        {"from_tx": -1},
    )
    # Schema check catches this first.
    assert resp["result"]["isError"] is True


def test_view_at_negative_tx_rejected(initialized_server):
    resp = call_tool(
        initialized_server,
        "persistence_view_at",
        {"tx": -5},
    )
    assert resp["result"]["isError"] is True


def test_view_at_label_too_long_rejected(initialized_server):
    resp = call_tool(
        initialized_server,
        "persistence_view_at",
        {"tx": 0, "label": "x" * 100},
    )
    assert resp["result"]["isError"] is True


def test_remember_eid_format_is_uuid(initialized_server):
    """Remember should always return a valid UUID for eid (output schema)."""
    import uuid as _uuid

    resp = call_tool(
        initialized_server,
        "persistence_remember",
        {"content": "y"},
    )
    eid = resp["result"]["structuredContent"]["eid"]
    _uuid.UUID(eid)


def test_recall_returns_empty_hits_for_no_match(initialized_server):
    call_tool(initialized_server, "persistence_remember", {"content": "x"})
    resp = call_tool(
        initialized_server,
        "persistence_recall",
        {"query": "definitely-not-there"},
    )
    assert resp["result"]["structuredContent"]["hits"] == []


def test_input_schema_violation_caught_by_dispatcher(initialized_server):
    """Schema validator runs BEFORE handler — invalid types rejected."""
    # remember.content must be a string; passing an int violates schema
    resp = call_tool(
        initialized_server,
        "persistence_remember",
        {"content": 12345},
    )
    assert resp["result"]["isError"] is True
    assert resp["result"]["_meta"]["category"] == "validation_error"
