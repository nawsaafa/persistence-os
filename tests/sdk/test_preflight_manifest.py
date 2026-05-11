"""Phase 2.4c LD-2 G2 — preflight_manifest.toml subset-resolution test.

The manifest at tests/preflight_manifest.toml enumerates the curated
SDK methods the persistence-coder agent (and Phase 7 skill consumers)
are allowed to call at v0.9.0a1. This test verifies the manifest is a
subset of available _facade.Substrate surfaces — NOT byte-equality.

Per LD-2 (codex consensus REJECT-FOR-NEW-OPTION-Z): "Allowed entrypoints
is policy, not reflection." Auto-generation makes adding a new curated
method an implicit allowlist expansion — the inverse of "lockfile."
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

from persistence.sdk._facade import Substrate

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "preflight_manifest.toml"


def _load_manifest() -> dict:
    with MANIFEST_PATH.open("rb") as fh:
        return tomllib.load(fh)


def test_manifest_is_closed_allowlist_and_resolves():
    """G2 — every dotted entry in [allowed.*] resolves to a callable
    attribute on Substrate.open(...); no entry starts with `escape.`;
    escape_callsites is empty.
    """
    manifest = _load_manifest()
    assert manifest["meta"]["version"] == "0.9.0a1"
    assert manifest.get("escape_callsites") == [], (
        "v0.9.0a1 contract: escape_callsites MUST be empty"
    )
    assert "allowed" in manifest

    s = Substrate.open("memory")
    try:
        for namespace_name, methods in manifest["allowed"].items():
            assert namespace_name != "escape", (
                f"escape namespace in [allowed] violates v0.9.0a1 contract"
            )
            namespace = getattr(s, namespace_name, None)
            assert namespace is not None, (
                f"manifest entry [allowed.{namespace_name}] but Substrate "
                f"has no `{namespace_name}` attribute"
            )
            for method_name in methods:
                attr = getattr(namespace, method_name, None)
                assert attr is not None, (
                    f"manifest entry [allowed.{namespace_name}.{method_name}] "
                    f"but Substrate.{namespace_name} has no `{method_name}` attribute"
                )
                assert callable(attr), (
                    f"manifest entry [allowed.{namespace_name}.{method_name}] "
                    f"is not callable on Substrate.{namespace_name}"
                )
    finally:
        s.close()


def test_escape_callsites_remain_empty():
    """G2 sister — explicit assertion, separate test so failure messaging
    is sharp if someone non-empty's the list during a v0.9.x track.
    """
    manifest = _load_manifest()
    assert manifest["escape_callsites"] == [], (
        "v0.9.0a1 contract violation: escape_callsites must be empty. "
        "Use s.escape.* only in v0.9.x tracks with explicit ADR."
    )


def test_no_byte_equality_upgrade_attempt():
    """G2 anti-regression — assert the test SHAPE remains subset-check,
    not byte-equality (codex consensus LD-2 decider: 'allowed entrypoints
    is policy, not reflection'). If someone "fixes" this by adding a
    byte-equality assertion that auto-generates from _facade, the new
    assertion will fail because _FactNamespace methods are undecorated.

    This test passes vacuously today; it documents the design intent.
    The regression vector is a code-review check, not a runtime check.
    """
    pass


def test_tomllib_available():
    """Sanity — tomllib is stdlib since Python 3.11. requires-python is
    >=3.10 (pyproject.toml). Verify tomllib is importable at runtime;
    if Python is 3.10, fall back to tomli (not currently in deps —
    G2 effectively requires Python 3.11+ at test time).
    """
    if sys.version_info < (3, 11):
        pytest.skip("tomllib requires Python 3.11+; install `tomli` for 3.10 support")
    assert tomllib is not None
