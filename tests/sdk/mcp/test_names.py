"""SDK3 — _NAMES single-source-of-truth invariants."""
from __future__ import annotations

import pytest

from persistence.sdk.mcp._names import (
    _ALL_MCP_CAPS,
    _AUDIT_TAIL_CAP,
    _AUDIT_TAIL_URI,
    _NAMES,
    _TOOL_ORDER,
    wire_to_verb,
)


def test_six_tools_in_names():
    """Per ADR-15 § 5: exactly 6 tools."""
    assert len(_NAMES) == 6
    assert len(_TOOL_ORDER) == 6


def test_tool_order_matches_names_keys():
    assert set(_TOOL_ORDER) == set(_NAMES.keys())


def test_every_tool_has_four_name_forms():
    for verb, names in _NAMES.items():
        assert "wire" in names
        assert "capability" in names
        assert "datom_a" in names
        assert "audit_op" in names


def test_wire_names_use_persistence_prefix():
    """ADR-15: every wire tool name carries the `persistence_` prefix."""
    for names in _NAMES.values():
        assert names["wire"].startswith("persistence_")


def test_capability_names_use_mcp_dot_prefix():
    """ADR-15: capability strings are `mcp.<verb>` (or `mcp.audit-read`)."""
    for names in _NAMES.values():
        cap = names["capability"]
        assert cap.startswith("mcp.")


def test_audit_op_has_leading_colon():
    """ADR-15: AuditEntry.op is colon-prefixed (`:mcp/op-<verb>`)."""
    for names in _NAMES.values():
        assert names["audit_op"].startswith(":")


def test_datom_a_has_no_leading_colon():
    """ADR-15: Datom.a is bare (no leading colon)."""
    for names in _NAMES.values():
        assert not names["datom_a"].startswith(":")


def test_audit_window_uses_audit_read_capability():
    """ADR-15 capability-asymmetry: audit_window uses `mcp.audit-read`."""
    assert _NAMES["audit_window"]["capability"] == "mcp.audit-read"


def test_audit_tail_resource_shares_audit_read_cap():
    """ADR-15 capability-asymmetry: the audit_tail resource shares
    `mcp.audit-read` with the audit_window tool — single resource-shaped
    cap for all audit-chain reads.
    """
    assert _AUDIT_TAIL_CAP == "mcp.audit-read"
    assert _AUDIT_TAIL_CAP == _NAMES["audit_window"]["capability"]


def test_audit_tail_uri_uses_persistence_os_scheme():
    assert _AUDIT_TAIL_URI == "persistence-os://audit/tail"


def test_all_mcp_caps_is_closed_set():
    expected = {names["capability"] for names in _NAMES.values()}
    assert _ALL_MCP_CAPS == frozenset(expected)


def test_wire_to_verb_roundtrip():
    for verb, names in _NAMES.items():
        assert wire_to_verb(names["wire"]) == verb


def test_wire_to_verb_unknown_returns_none():
    assert wire_to_verb("not_a_real_tool") is None


def test_canonical_six_verbs():
    """ADR-15 verbatim verb list."""
    assert set(_NAMES.keys()) == {
        "remember",
        "recall",
        "forget",
        "audit_window",
        "replay_check",
        "view_at",
    }
