"""SDK3 — stability decorator metadata."""
from __future__ import annotations

from persistence.sdk.mcp import (
    ExperimentalHTTPHandler,
    MCPServer,
    create_server,
)


def test_mcp_server_class_marked_stable_v08():
    md = getattr(MCPServer, "__sdk_stability__", None)
    assert md is not None
    assert md["level"] == "stable"
    assert md["version"] == "v0.8"


def test_create_server_marked_stable_v08():
    md = getattr(create_server, "__sdk_stability__", None)
    assert md is not None
    assert md["level"] == "stable"


def test_experimental_http_handler_marked_experimental():
    md = getattr(ExperimentalHTTPHandler, "__sdk_stability__", None)
    assert md is not None
    assert md["level"] == "experimental"


def test_mcp_subpackage_reexported_from_sdk():
    """The brief allows ONE re-export in `sdk/__init__.py` — verify."""
    from persistence.sdk import mcp as mcp_re_export
    from persistence.sdk import mcp as direct
    assert mcp_re_export is direct
