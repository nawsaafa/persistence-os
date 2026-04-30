"""Single source of truth for MCP tool names per ADR-15.

Every MCP tool has four name forms with precise, distinct shapes:

| Surface              | Form                  | Has leading ``:`` | Example                    |
|----------------------|-----------------------|-------------------|----------------------------|
| MCP wire             | ``persistence_<verb>``| no                | ``persistence_remember``   |
| Capability           | ``mcp.<verb>``        | no                | ``mcp.remember``           |
| ``Datom.a`` storage  | ``mcp/op-<verb>``     | no                | ``mcp/op-remember``        |
| ``AuditEntry.op``    | ``:mcp/op-<verb>``    | yes               | ``:mcp/op-remember``       |

The :data:`_NAMES` dict is the single source of truth — every other module
in :mod:`persistence.sdk.mcp` reads from it. Drift between forms is
impossible by construction (``test_names`` asserts the table is closed +
internally consistent).

Note the capability asymmetry on ``audit_window`` per ADR-15: five tools
take ``mcp.<wire-verb>`` caps; ``audit_window`` takes ``mcp.audit-read``
because that capability grants *read access to the audit chain* as a
resource (covers both this tool AND the ``audit_tail`` resource), not
*use of one specific tool*. The ``audit_tail`` resource (per § 5
Resources) reuses ``mcp.audit-read`` for the same reason.
"""
from __future__ import annotations

from typing import TypedDict


class ToolNames(TypedDict):
    """The four name forms for one MCP tool."""

    wire: str
    capability: str
    datom_a: str
    audit_op: str


_NAMES: dict[str, ToolNames] = {
    "remember": {
        "wire": "persistence_remember",
        "capability": "mcp.remember",
        "datom_a": "mcp/op-remember",
        "audit_op": ":mcp/op-remember",
    },
    "recall": {
        "wire": "persistence_recall",
        "capability": "mcp.recall",
        "datom_a": "mcp/op-recall",
        "audit_op": ":mcp/op-recall",
    },
    "forget": {
        "wire": "persistence_forget",
        "capability": "mcp.forget",
        "datom_a": "mcp/op-forget",
        "audit_op": ":mcp/op-forget",
    },
    "audit_window": {
        "wire": "persistence_audit_window",
        "capability": "mcp.audit-read",
        "datom_a": "mcp/op-audit-window",
        "audit_op": ":mcp/op-audit-window",
    },
    "replay_check": {
        "wire": "persistence_replay_check",
        "capability": "mcp.replay",
        "datom_a": "mcp/op-replay",
        "audit_op": ":mcp/op-replay",
    },
    "view_at": {
        "wire": "persistence_view_at",
        "capability": "mcp.view",
        "datom_a": "mcp/op-view",
        "audit_op": ":mcp/op-view",
    },
}


# The ordered tuple of verb keys (insertion order in :data:`_NAMES`). The
# ``tools/list`` reply uses this order so the wire output is deterministic.
_TOOL_ORDER: tuple[str, ...] = (
    "remember",
    "recall",
    "forget",
    "audit_window",
    "replay_check",
    "view_at",
)


# Resource URI for the audit-tail resource (per § 5 Resources).
_AUDIT_TAIL_URI: str = "persistence-os://audit/tail"
_AUDIT_TAIL_CAP: str = "mcp.audit-read"


# All capability strings the server understands. Closed set — token caps
# outside this set are ignored (denied) at dispatch time.
_ALL_MCP_CAPS: frozenset[str] = frozenset(
    [t["capability"] for t in _NAMES.values()]
)


def wire_to_verb(wire_name: str) -> str | None:
    """Return the verb key for a wire-form tool name, or ``None``."""
    for verb, names in _NAMES.items():
        if names["wire"] == wire_name:
            return verb
    return None


__all__ = [
    "ToolNames",
    "_ALL_MCP_CAPS",
    "_AUDIT_TAIL_CAP",
    "_AUDIT_TAIL_URI",
    "_NAMES",
    "_TOOL_ORDER",
    "wire_to_verb",
]
