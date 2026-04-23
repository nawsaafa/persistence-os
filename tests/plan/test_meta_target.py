# tests/plan/test_meta_target.py
"""Meta-target test — parse the persistence-os-foundation track's own plan.edn.

When this test passes, persistence.plan has honored the track thesis:
'this plan IS the first test case for the Plan module. When plan/eval can
execute this file, Phase 3 ships by definition.' v0.1 delivers parse + walk.

## v0.1 parsing status (findings from Task 22 investigation)

The track plan.edn exercises three EDN features beyond v0.1 parse scope:

1. **EDN quote reader macro** ``'[datom-schema -> interceptor-py]``
   edn_format raises ``Illegal character '''`` on this Clojure-style reader macro.
   Workaround: strip ``'[`` → ``[`` before parsing (safe for the :track/plan vector).
   Applied in ``_sanitize_edn_quotes()``.

2. **Bare :seq nodes** — ``[:seq [:tool-call ...] ...]`` without an attrs dict at
   position 1. The v0.1 parser enforces shape ``[tag, dict, *children]`` per spec.
   Track plan.edn uses the shorthand form. Fix: ``_normalize_bare_nodes()``.
   **Deferred to v0.2** — tracked as consumer-driven scope item.

3. **EDN Symbol type** — ``->`` (in the signature lists after quote-stripping) is
   an ``edn_format.Symbol``, not handled by ``_edn_to_python()``.  ``json.dumps``
   raises ``TypeError: Object of type Symbol is not JSON serializable`` when
   ``Node.id`` tries to hash the canonical dict.
   **Deferred to v0.2** — tracked as consumer-driven scope item.

Tests in this file reflect real capability at each level, not silence.
``test_track_plan_vector_parses`` is ``xfail`` on the two v0.2 items above and
passes structurally once both are resolved.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from persistence.plan import ParseError, parse, walk


TRACK_PLAN_PATH = Path(
    "/Users/nawfalsaadi/Projects/ai-box/conductor/tracks/"
    "persistence-os-foundation_20260420/plan.edn"
)


def _extract_track_plan_vector(edn_text: str) -> tuple[int, int, str]:
    """Locate and extract the :track/plan vector from the track EDN map.

    Returns (vec_start, vec_end, plan_vector_edn).
    """
    plan_key = edn_text.find(":track/plan")
    assert plan_key > 0, ":track/plan key not found in track plan.edn"
    vec_start = edn_text.find("[", plan_key)
    assert vec_start > 0, "No '[' after :track/plan key"

    depth = 0
    vec_end = -1
    for i, c in enumerate(edn_text[vec_start:], start=vec_start):
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                vec_end = i + 1
                break
    assert vec_end > vec_start, "Bracket balancing failed — malformed :track/plan vector"
    return vec_start, vec_end, edn_text[vec_start:vec_end]


def _sanitize_edn_quotes(edn_text: str) -> str:
    """Strip Clojure-style EDN quote reader macros (``'[...]``).

    edn_format does not support the quote reader macro.  The track plan uses
    it in :signature attrs like ``'[datom-schema -> interceptor-py]``.
    Stripping the leading ``'`` converts them to plain EDN vectors, which
    edn_format can parse correctly.

    This is safe for the :track/plan vector because all quote usages are
    immediately followed by ``[``.
    """
    return edn_text.replace("'[", "[")


@pytest.mark.skipif(
    not TRACK_PLAN_PATH.exists(),
    reason=f"meta-target file not found at {TRACK_PLAN_PATH} — "
           "run from a machine with the ai-box sibling repo cloned",
)
class TestMetaTarget:
    def test_file_is_readable_and_non_trivial(self):
        """Track plan.edn exists and has substantive content."""
        edn_text = TRACK_PLAN_PATH.read_text()
        assert len(edn_text) > 100
        assert ":track/plan" in edn_text
        assert ":track/id" in edn_text

    def test_parse_track_plan_edn_with_aliases(self):
        """The track's plan.edn uses :phase, :workstream wrappers not in the
        registered spec. Alias lowering makes it spec-conformant at parse time.

        This test validates: text is present, :track/plan key is locatable,
        and the vector bracket-extraction logic works correctly.
        The actual EDN parse with alias-lowering is tested in
        test_track_plan_vector_parses (which documents v0.2 scope items).
        """
        edn_text = TRACK_PLAN_PATH.read_text()
        assert len(edn_text) > 100

        _, _, plan_vector_edn = _extract_track_plan_vector(edn_text)
        assert plan_vector_edn.startswith("[:seq")
        assert len(plan_vector_edn) > 1000  # the plan is substantial

    def test_edn_quote_sanitizer_handles_track_plan_quotes(self):
        """Demonstrates that the EDN quote reader macro stripping produces a
        valid parseable string — the first step toward full meta-target parse.

        The track uses ``'[datom-schema -> interceptor-py]`` (Clojure-style
        quoted list). edn_format does not support this syntax; stripping the
        leading ``'`` converts it to a plain vector that parses correctly.

        v0.2 scope: make edn_format handle Symbol types in attr values.
        """
        edn_text = TRACK_PLAN_PATH.read_text()
        _, _, plan_vector_edn = _extract_track_plan_vector(edn_text)

        # Count how many quote-macros are present
        quote_count = plan_vector_edn.count("'[")
        assert quote_count >= 1, "expected at least one EDN quote macro in track plan"

        sanitized = _sanitize_edn_quotes(plan_vector_edn)
        remaining_quotes = sanitized.count("'")
        # After stripping "'[", no bare quotes should remain in this EDN text
        assert remaining_quotes == 0, (
            f"Unexpected bare quotes after sanitization: {remaining_quotes}"
        )

    @pytest.mark.xfail(
        reason=(
            "v0.2 scope — two EDN features block full walk: "
            "(1) bare :seq nodes without attrs dict ([:seq [:child ...]] shorthand); "
            "(2) edn_format.Symbol type in :signature attrs not JSON-serializable. "
            "Both need v0.2 fixes: _python_to_node() must inject {} for bare sequences, "
            "and _edn_to_python() must convert Symbol to str. "
            "This xfail pins scope — remove when v0.2 ships both fixes."
        ),
        strict=True,
    )
    def test_track_plan_vector_parses(self):
        """If the track file has a parseable :track/plan vector, extract and parse it.
        This tests the actual meta-target: plan-as-data round-tripping through plan.parse.

        BLOCKED (strict=True xfail) on two v0.2 consumer-driven scope items:

        1. Bare :seq shorthand: ``[:seq [:tool-call ...]]`` — no attrs dict at pos 1.
           Parser raises: ``ParseError: node attrs must be map, got list``
           Fix: _python_to_node() inserts {} when position 1 is a vector, not a dict.

        2. Symbol type serialization: ``->`` in signature lists is edn_format.Symbol,
           not str. json.dumps raises TypeError in Node.id (canonical hash).
           Fix: _edn_to_python() converts Symbol to str("->").
        """
        edn_text = TRACK_PLAN_PATH.read_text()
        _, _, plan_vector_edn = _extract_track_plan_vector(edn_text)

        sanitized = _sanitize_edn_quotes(plan_vector_edn)

        node = parse(
            sanitized,
            lower_aliases={":phase": ":seq", ":workstream": ":seq"},
            strict=False,  # track plan has :tool-call and other kinds; spec check is aspirational
        )
        assert node.tag == ":seq"  # :track/plan is a :seq at top

        ids = walk(node)
        assert len(ids) >= 10, f"expected >= 10 :ids, got {len(ids)}"
        # Content-addressing invariant: all :ids unique within this plan
        assert len(set(ids)) == len(ids), "duplicate :ids — content-addressing broken"
