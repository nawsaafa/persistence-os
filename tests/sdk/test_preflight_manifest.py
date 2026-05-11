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
from pathlib import Path

# R1-fold I2: requires-python is >=3.10 but tomllib is stdlib 3.11+.
# Fall back to the tomli backport on 3.10 so test collection doesn't
# hard-crash on supported Python versions.
if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover — Python 3.10 fallback path
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]

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


def test_subset_check_shape_not_byte_equality():
    """G2 anti-regression (R1-fold I1, was test_no_byte_equality_upgrade_attempt
    which was a no-op `pass`) — scans the body of the primary G2 test
    for byte-equality / set-equality auto-snapshot patterns, codifying
    the LD-2 codex-consensus decider: 'allowed entrypoints is policy,
    not reflection.' If someone 'fixes' G2 by auto-generating from
    `_facade.py` and asserting `manifest == introspected`, this
    assertion catches the source-level drift.

    Falsifier: replace the body of test_manifest_is_closed_allowlist_and_resolves
    with `assert set(manifest['allowed'].keys()) == {n for n in dir(s) ...}` →
    this test FAILS because the target test source now contains the
    equality pattern.
    """
    import inspect
    target_source = inspect.getsource(test_manifest_is_closed_allowlist_and_resolves)
    # Patterns that indicate a byte/set-equality snapshot of the [allowed]
    # surface (the forbidden anti-pattern). Scalar comparisons on
    # manifest["meta"]["..."] are NOT snapshot patterns and stay allowed.
    eq = chr(61) + chr(61)  # "==" via char-codes to avoid self-reference
    forbidden_patterns = [
        "[\"allowed\"] " + eq,    # manifest["allowed"] ==
        "['allowed'] " + eq,      # manifest['allowed'] ==
        "set(manifest",           # set(manifest...) snapshot
        "set(dir(",               # set(dir(s)) introspection-equality
        "getattr_set",            # rename heuristic — auto-gen helper
    ]
    for line in target_source.splitlines():
        for pattern in forbidden_patterns:
            if pattern in line:
                raise AssertionError(
                    f"LD-2 anti-regression: snapshot-equality pattern "
                    f"{pattern!r} found in target test body. Manifest is "
                    f"POLICY not REFLECTION — use subset-check via getattr "
                    f"instead. Offending line: {line.strip()!r}"
                )
