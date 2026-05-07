"""T2/G2 — `validate_plan_for_2_3a`: Stage 3 semantic validator.

Five constraints:
1. Root tag must be `:seq` (keyword-form; Node.tag always has leading colon).
2. Plan node count ≤ 64.
3. Plan depth ≤ 4.
4. No `:branch` or `:code` leaves (walk raises UnimplementedNodeKindError).
5. Every leaf tag MUST be in REGISTERED_LEAF_TAGS — Dispatcher silently
   skips unregistered tags per _dispatch.py:73-80.

Forced spec deviations vs impl plan:
  - Impl plan line 374 claimed Node.tag is BARE ("seq"); calibration of
    _ast.py:67-103 shows it is KEYWORD-FORM (":seq"). All comparisons use
    keyword-form. REGISTERED_LEAF_TAGS uses keyword-form too.
  - Impl plan test `test_validate_rejects_unregistered_leaf_tag` called
    parse('[:seq {} [:fictional/op {}]]') without strict=False; PlanSpecError
    would fire before validate_plan_for_2_3a sees the node. Uses strict=False.
  - Impl plan test `test_registered_leaf_tags_constant_lists_all_10_ops`
    expected frozenset of bare forms; corrected to keyword-form here.
"""
from __future__ import annotations

import pytest

from persistence.coder._planner import REGISTERED_LEAF_TAGS, validate_plan_for_2_3a
from persistence.coder._planner_errors import PlanPayloadValidation
from persistence.plan import parse


def test_validate_accepts_canonical_seq_with_registered_leaves():
    # :fs/read and :code/run are in REGISTERED_LEAF_TAGS (keyword form).
    # strict=False: substrate spec only knows :seq/:tool-call/:llm-call etc.
    node = parse(
        '[:seq {} [:fs/read {:path "x.txt"}] [:code/run {:source "x=1"}]]',
        strict=False,
    )
    validate_plan_for_2_3a(node)  # no raise


def test_validate_rejects_non_seq_root():
    # leaf-as-root — :fs/read is not in plan spec, use strict=False.
    node = parse('[:fs/read {:path "x.txt"}]', strict=False)
    with pytest.raises(PlanPayloadValidation) as exc_info:
        validate_plan_for_2_3a(node)
    assert exc_info.value.field == "root_tag"


def test_validate_rejects_branch_leaf():
    # :branch IS in plan spec; parse with strict=True (default) is fine.
    # After parse, child.tag == ":branch" (keyword-form).
    node = parse('[:seq {} [:branch {}]]')
    with pytest.raises(PlanPayloadValidation) as exc_info:
        validate_plan_for_2_3a(node)
    assert "branch" in exc_info.value.reason.lower()


def test_validate_rejects_code_leaf():
    # :code IS in plan spec; parse with strict=True (default) is fine.
    # After parse, child.tag == ":code" (keyword-form).
    node = parse('[:seq {} [:code {:source "x=1"}]]')
    with pytest.raises(PlanPayloadValidation) as exc_info:
        validate_plan_for_2_3a(node)
    assert "code" in exc_info.value.reason.lower()


def test_validate_rejects_unregistered_leaf_tag():
    # :fictional/op is NOT in plan spec → use strict=False to bypass spec.
    # validate_plan_for_2_3a sees the node and rejects on unregistered tag.
    node = parse('[:seq {} [:fictional/op {}]]', strict=False)
    with pytest.raises(PlanPayloadValidation) as exc_info:
        validate_plan_for_2_3a(node)
    assert (
        "fictional/op" in exc_info.value.reason
        or "unregistered" in exc_info.value.reason.lower()
    )


def test_registered_leaf_tags_constant_lists_all_12_ops():
    """Sanity: REGISTERED_LEAF_TAGS frozen set matches design LD5/LD6 enumeration.

    Was 10 ops in 2.3a/2.3b; Phase 2.3c.1 LD6 added :skill/define +
    :skill/lookup. Tags are keyword-form (with leading colon) to match
    Node.tag after EDN parse — Node.__post_init__ enforces this invariant.
    """
    expected = frozenset({
        ":fs/read", ":fs/write", ":fs/glob", ":fs/grep",
        ":shell/exec", ":code/run",
        ":git/diff", ":git/status", ":git/log", ":git/commit",
        # Phase 2.3c.1 — skill library coder integration (LD6).
        ":skill/define", ":skill/lookup",
    })
    assert REGISTERED_LEAF_TAGS == expected
