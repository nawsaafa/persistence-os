"""``persistence.sdk.mcp`` — first-party MCP server (SDK3 slice, v0.8.0a1).

Per the design doc ``docs/plans/2026-04-29-adapter-sdk-contract-design.md``
§ 5 + ADR-15: this sub-package ships a Model Context Protocol server
that exposes 6 memory + audit tools and 1 audit-tail resource over
stdio JSON-RPC. Stdio is the SOLE v0.8 *stable* transport (G2);
HTTP is EXPERIMENTAL per ADR-15b (G12 only).

# MCP_SERVER design notes (inline, v0.8.0a1)
#
# * 6 tools, all `persistence_*`-prefixed for host collision-safety
#   (per ADR-7 + ADR-15). Single source of truth in `_names.py`.
# * Schema dialect: JSON-Schema draft-07 subset (Schema Profile v0.8 per
#   ADR-15 § 5.1.1). Hand-authored as plain dicts — NOT pydantic-emitted.
#   This avoids the `$ref` inliner that R3 SHOULD-FIX 1 called for; the
#   "thin shim" claim from the design is honored by skipping pydantic
#   altogether. Profile-validated at import time + per-tool SHA-256
#   recorded in `schema_lockfile.json` (G13).
# * Lifecycle: `initialize` → `notifications/initialized` → ready;
#   then `tools/list` / `tools/call` / `resources/{list,read,subscribe}`.
#   `ping` allowed pre-initialize.
# * Capability gating: each tool checks its `mcp.<verb>` cap-string in
#   `MCPServer._token_caps` BEFORE dispatch. The `mcp.audit-read` cap
#   is asymmetric per ADR-15 — audit_window AND audit_tail share it.
# * Audit emission: each tool call appends ONE entry to the substrate's
#   `_audit_entries` list (chains into the same Merkle root as
#   `:repl/...`, `:sdk/...` per ADR-4). Entry shape is `@experimental`
#   per ADR-1 W3 NIT-5.
# * Replay safety budget per ADR-13: `MCP_REPLAY_MAX_WINDOW=256`,
#   wallclock=5s, rate=6/min/token. NEVER returns raw byte diffs.
# * `view_at` is a server-side cursor handle — NOT a store fork
#   (ADR-14). Cursors are token-scoped, server-state only.
# * HTTP transport (`ExperimentalHTTPHandler`) is `@experimental`,
#   refuses non-loopback bind, rejects `Origin: null` literal, requires
#   per-request `Authorization: Bearer <token>` (no per-TCP session
#   state). Excluded from G2 conformance suite per ADR-15b.

Public surface:

- :class:`MCPServer`              — the server class (`@stable("v0.8")`)
- :func:`create_server`            — factory (`@stable("v0.8")`)
- :class:`Budgets`                 — server-side budget bundle
- :class:`ExperimentalHTTPHandler` — `@experimental` HTTP wrapper
- :data:`SCHEMA_LOCKFILE_PATH`     — path to the schema lockfile (G13)
"""
from __future__ import annotations

from pathlib import Path

from persistence.sdk.mcp._budgets import (
    Budgets,
    MCP_REPLAY_MAX_WALLCLOCK_S,
    MCP_REPLAY_MAX_WINDOW,
    MCP_REPLAY_RATE_LIMIT_PER_TOKEN,
)
from persistence.sdk.mcp._names import (
    _ALL_MCP_CAPS,
    _AUDIT_TAIL_URI,
    _NAMES,
    _TOOL_ORDER,
)
from persistence.sdk.mcp._schemas import (
    INPUT_SCHEMAS,
    OUTPUT_SCHEMAS,
    SchemaProfileViolation,
    all_tool_schema_shas,
    canonical_schema_sha256,
    validate_profile,
)
from persistence.sdk.mcp._server import (
    MCP_PROTOCOL_VERSION_FALLBACK,
    MCP_PROTOCOL_VERSION_PRIMARY,
    MCP_SUPPORTED_VERSIONS,
    SERVER_NAME,
    SERVER_VERSION,
    ExperimentalHTTPHandler,
    MCPServer,
    create_server,
)


SCHEMA_LOCKFILE_PATH: Path = Path(__file__).parent / "schema_lockfile.json"


__all__ = [
    "Budgets",
    "ExperimentalHTTPHandler",
    "INPUT_SCHEMAS",
    "MCPServer",
    "MCP_PROTOCOL_VERSION_FALLBACK",
    "MCP_PROTOCOL_VERSION_PRIMARY",
    "MCP_REPLAY_MAX_WALLCLOCK_S",
    "MCP_REPLAY_MAX_WINDOW",
    "MCP_REPLAY_RATE_LIMIT_PER_TOKEN",
    "MCP_SUPPORTED_VERSIONS",
    "OUTPUT_SCHEMAS",
    "SCHEMA_LOCKFILE_PATH",
    "SERVER_NAME",
    "SERVER_VERSION",
    "SchemaProfileViolation",
    "_ALL_MCP_CAPS",
    "_AUDIT_TAIL_URI",
    "_NAMES",
    "_TOOL_ORDER",
    "all_tool_schema_shas",
    "canonical_schema_sha256",
    "create_server",
    "validate_profile",
]
