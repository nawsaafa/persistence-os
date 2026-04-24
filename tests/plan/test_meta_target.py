# tests/plan/test_meta_target.py
"""Meta-target test — parse the persistence-os-foundation track's own plan.edn.

When this test passes, persistence.plan has honored the track thesis:
'this plan IS the first test case for the Plan module. When plan/eval can
execute this file, Phase 3 ships by definition.' v0.1 delivers parse + walk.

## v0.1 parsing status (after R2 C4 — both blockers closed)

The track plan.edn exercises three EDN features beyond strict-spec parse
scope; all three are handled at the parser layer:

1. **EDN quote reader macro** ``'[datom-schema -> interceptor-py]``
   edn_format raises ``Illegal character '''`` on this Clojure-style reader
   macro. Workaround: strip ``'[`` → ``[`` before parsing (safe for the
   :track/plan vector). Applied in ``_sanitize_edn_quotes()``.

2. **Bare :seq nodes** — ``[:seq [:tool-call ...] ...]`` without an attrs
   dict at position 1. R2 C4: ``_python_to_node()`` injects ``{}`` when
   position 1 is a list and treats index 1+ as children. Malformed shapes
   with a non-list, non-dict at position 1 still raise the original error.

3. **EDN Symbol type** — ``->`` in signature lists after quote-stripping
   is an ``edn_format.Symbol``. R2 C4: ``_edn_to_python()`` stringifies
   ``Symbol`` instances so ``json.dumps`` in ``Node.id`` stays total.

``test_track_plan_vector_parses`` exercises the end-to-end parse+walk of
the actual track plan.edn.
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

    def test_track_plan_vector_parses(self):
        """The meta-target: parse the track's own plan.edn end-to-end.

        R2 C4 shipped the two parser capabilities this needs:

        1. Bare ``[:seq [child] [child] ...]`` shorthand. _python_to_node
           injects ``{}`` when position 1 is a list (children), not a dict
           (attrs). Only applied when the position-0 tag is keyword-form,
           so malformed shapes still raise the original "attrs must be map".

        2. ``edn_format.Symbol`` coercion to ``str``. _edn_to_python sees
           ``->`` in a quoted signature and stringifies it. json.dumps no
           longer chokes on Symbol when Node.id is computed.
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
