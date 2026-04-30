"""Server-side budgets for MCP tools per ADR-13.

These constants are server-only — they are NEVER overridable via the wire.
Operators may adjust them by constructing the server with explicit kwargs
(see :class:`persistence.sdk.mcp.MCPServer`); end clients cannot.

Defaults per ADR-13:

- ``MCP_REPLAY_MAX_WINDOW``       = 256   entries
- ``MCP_REPLAY_MAX_WALLCLOCK_S``  = 5.0   seconds
- ``MCP_REPLAY_RATE_LIMIT_PER_TOKEN`` = 6 / minute
- ``MCP_AUDIT_WINDOW_MAX_LIMIT``  = 1000  entries (per § 5 inputSchema)
- ``MCP_REMEMBER_MAX_CONTENT_BYTES`` = 16384 bytes (per § 5 inputSchema)
- ``MCP_RECALL_MAX_K``            = 50    hits
- ``MCP_VIEW_AT_MAX_LABEL_LEN``   = 64    chars
"""
from __future__ import annotations

from dataclasses import dataclass


MCP_REPLAY_MAX_WINDOW: int = 256
MCP_REPLAY_MAX_WALLCLOCK_S: float = 5.0
MCP_REPLAY_RATE_LIMIT_PER_TOKEN: int = 6  # per 60 seconds
MCP_REPLAY_RATE_WINDOW_S: float = 60.0

MCP_AUDIT_WINDOW_MAX_LIMIT: int = 1000
MCP_REMEMBER_MAX_CONTENT_BYTES: int = 16384
MCP_RECALL_MAX_K: int = 50
MCP_VIEW_AT_MAX_LABEL_LEN: int = 64


@dataclass(frozen=True)
class Budgets:
    """Server-side budget bundle. Frozen so dispatch can't mutate."""

    replay_max_window: int = MCP_REPLAY_MAX_WINDOW
    replay_max_wallclock_s: float = MCP_REPLAY_MAX_WALLCLOCK_S
    replay_rate_limit_per_token: int = MCP_REPLAY_RATE_LIMIT_PER_TOKEN
    replay_rate_window_s: float = MCP_REPLAY_RATE_WINDOW_S
    audit_window_max_limit: int = MCP_AUDIT_WINDOW_MAX_LIMIT
    remember_max_content_bytes: int = MCP_REMEMBER_MAX_CONTENT_BYTES
    recall_max_k: int = MCP_RECALL_MAX_K
    view_at_max_label_len: int = MCP_VIEW_AT_MAX_LABEL_LEN


__all__ = [
    "Budgets",
    "MCP_AUDIT_WINDOW_MAX_LIMIT",
    "MCP_RECALL_MAX_K",
    "MCP_REMEMBER_MAX_CONTENT_BYTES",
    "MCP_REPLAY_MAX_WALLCLOCK_S",
    "MCP_REPLAY_MAX_WINDOW",
    "MCP_REPLAY_RATE_LIMIT_PER_TOKEN",
    "MCP_REPLAY_RATE_WINDOW_S",
    "MCP_VIEW_AT_MAX_LABEL_LEN",
]
