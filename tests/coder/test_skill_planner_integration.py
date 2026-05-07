"""Phase 2.3c.1 G5 — `_planner.py` REGISTERED_LEAF_TAGS extension drift-pin
+ `_register_substrate_handlers` integration.

Per design § 4 G5 + LD6 R0-fold I2 single-source-of-truth verification:

  G5.1 — ``REGISTERED_LEAF_TAGS == {12 expected tags}`` — the FULL
         expected set asserted as a single literal. Drift-pin so future
         additions trip this test rather than silently extending the
         closed set.
  G5.2 — ``_register_substrate_handlers(dispatcher, substrate)`` registers
         exactly 12 adapter clauses; spy on ``dispatcher.register`` call
         count + first-arg membership in ``REGISTERED_LEAF_TAGS``.
  G5.3 — No DUPLICATE hardcoded tag list outside ``REGISTERED_LEAF_TAGS``.
         Scan ``_planner.py`` source for the 10-tag literal pattern from
         2.3a; should appear in the constant definition ONLY.
  G5.4 — Plan with ``:skill/define`` leaf passes
         :func:`validate_plan_for_2_3a` (proves the tag is in the
         closed set).
  G5.5 — Plan with ``:skill/lookup`` leaf passes
         :func:`validate_plan_for_2_3a`.
  G5.6 — Plan with bogus ``:skill/unknown`` leaf is REJECTED by
         :func:`validate_plan_for_2_3a` (proves the closed-set
         enforcement still works post-extension).

LD6 R0-fold I2: ``REGISTERED_LEAF_TAGS`` is the SINGLE enforcement point
for the closed leaf-tag set. The grep over ``src/persistence/coder/``
post-2.3c.1 must continue to show exactly three reference points:
  1. Definition at ``_planner.py``
  2. Public re-export in ``__all__``
  3. Runtime check inside ``_check_nodes_recursive``
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from persistence.coder._planner import (
    REGISTERED_LEAF_TAGS,
    _register_substrate_handlers,
    validate_plan_for_2_3a,
)
from persistence.coder._planner_errors import PlanPayloadValidation
from persistence.plan import parse
from persistence.sdk import Substrate


# ---------------------------------------------------------------------------
# G5.1 — REGISTERED_LEAF_TAGS drift-pin
# ---------------------------------------------------------------------------


def test_g5_1_registered_leaf_tags_is_exact_12_tag_set():
    """LD6 + LD6 R0-fold I2 single-source-of-truth: the constant equals
    EXACTLY the 12 expected tags (10 carried from 2.3a/2.3b + the 2
    skill ops added at 2.3c.1).
    """
    expected = frozenset({
        ":fs/read", ":fs/write", ":fs/glob", ":fs/grep",
        ":shell/exec", ":code/run",
        ":git/diff", ":git/status", ":git/log", ":git/commit",
        ":skill/define", ":skill/lookup",
    })
    assert REGISTERED_LEAF_TAGS == expected, (
        f"REGISTERED_LEAF_TAGS drift detected.\n"
        f"  expected (12): {sorted(expected)}\n"
        f"  actual   ({len(REGISTERED_LEAF_TAGS)}): "
        f"{sorted(REGISTERED_LEAF_TAGS)}"
    )
    assert len(REGISTERED_LEAF_TAGS) == 12


# ---------------------------------------------------------------------------
# G5.2 — _register_substrate_handlers spy: exactly 12 register() calls
# ---------------------------------------------------------------------------


def test_g5_2_register_substrate_handlers_makes_exactly_12_register_calls():
    """Spy on ``dispatcher.register`` and verify exactly 12 calls — one
    per tag in ``REGISTERED_LEAF_TAGS``. Each call's first arg must be
    in the constant (no surprise tags from drift).
    """
    mock_dispatcher = MagicMock()

    with Substrate.open("memory") as s:
        _register_substrate_handlers(mock_dispatcher, s)

    assert mock_dispatcher.register.call_count == 12, (
        f"expected 12 dispatcher.register calls, got "
        f"{mock_dispatcher.register.call_count}"
    )

    # Each call's first positional arg must be a tag in REGISTERED_LEAF_TAGS.
    registered_tags = set()
    for call in mock_dispatcher.register.call_args_list:
        args, _ = call
        assert len(args) >= 1, f"register() called without positional tag arg: {call}"
        tag = args[0]
        assert tag in REGISTERED_LEAF_TAGS, (
            f"_register_substrate_handlers registered unknown tag {tag!r} "
            f"not in REGISTERED_LEAF_TAGS"
        )
        registered_tags.add(tag)

    # And the union of all registered tags equals the constant — proves
    # no tag was missed and no tag was registered twice.
    assert registered_tags == set(REGISTERED_LEAF_TAGS), (
        f"register-call coverage mismatch.\n"
        f"  registered: {sorted(registered_tags)}\n"
        f"  constant:   {sorted(REGISTERED_LEAF_TAGS)}"
    )


# ---------------------------------------------------------------------------
# G5.3 — No duplicate hardcoded tag list outside REGISTERED_LEAF_TAGS
# ---------------------------------------------------------------------------


def test_g5_3_no_duplicate_hardcoded_tag_list_outside_registered_leaf_tags():
    """Scan ``_planner.py`` for the 3-tag prefix literal pattern
    (``":fs/read", ":fs/write", ":fs/glob"``). Should appear EXACTLY
    ONCE, inside the ``REGISTERED_LEAF_TAGS = frozenset({...})`` block.

    LD6 R0-fold I2 guards against the failure mode where a later
    refactor accidentally hardcodes a duplicate list in
    ``_register_substrate_handlers`` or
    ``validate_plan_for_2_3a`` and silently drifts from the constant.
    """
    planner_path = (
        Path(__file__).parent.parent.parent
        / "src" / "persistence" / "coder" / "_planner.py"
    )
    src = planner_path.read_text(encoding="utf-8")
    needle = '":fs/read", ":fs/write", ":fs/glob"'
    occurrences = src.count(needle)
    assert occurrences == 1, (
        f"expected 1 occurrence of {needle!r} (in REGISTERED_LEAF_TAGS "
        f"definition), got {occurrences}. A duplicate hardcoded tag list "
        f"has crept into _planner.py — refactor to reference "
        f"REGISTERED_LEAF_TAGS instead."
    )


# ---------------------------------------------------------------------------
# G5.4 — :skill/define leaf passes validate_plan_for_2_3a
# ---------------------------------------------------------------------------


def test_g5_4_plan_with_skill_define_leaf_passes_validation():
    """A plan whose only leaf is ``:skill/define`` validates cleanly —
    proves the new tag joined the closed set.
    """
    edn = (
        '[:seq {} '
        '[:skill/define {'
        ':plan-edn "[:seq {} [:fs/read {:path \\"x\\"}]]" '
        ':promotion-id "p-1" '
        ':registered-at-ms 1700000000000'
        '}]]'
    )
    plan = parse(edn, strict=False)
    # Should not raise.
    validate_plan_for_2_3a(plan)


# ---------------------------------------------------------------------------
# G5.5 — :skill/lookup leaf passes validate_plan_for_2_3a
# ---------------------------------------------------------------------------


def test_g5_5_plan_with_skill_lookup_leaf_passes_validation():
    edn = '[:seq {} [:skill/lookup {:skill-id "skill/abc1234567890def"}]]'
    plan = parse(edn, strict=False)
    # Should not raise.
    validate_plan_for_2_3a(plan)


# ---------------------------------------------------------------------------
# G5.6 — bogus :skill/unknown leaf is REJECTED
# ---------------------------------------------------------------------------


def test_g5_6_plan_with_unknown_skill_op_is_rejected():
    """Even though ``:skill/unknown`` shares the ``:skill/`` prefix, it is
    NOT in ``REGISTERED_LEAF_TAGS``. The closed-set enforcement at
    :func:`_check_nodes_recursive` MUST reject it.
    """
    edn = '[:seq {} [:skill/unknown {:foo "bar"}]]'
    plan = parse(edn, strict=False)
    with pytest.raises(PlanPayloadValidation) as exc_info:
        validate_plan_for_2_3a(plan)
    assert exc_info.value.field == "leaf_tag"
    assert ":skill/unknown" in exc_info.value.reason
