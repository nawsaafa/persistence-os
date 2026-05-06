"""T2/G1 — `_build_plan_from_payload`: Stage 1 (byte budget) + Stage 2 (parse).

Pipeline: payload["plan_edn"] str → byte-budget check → s.plan.parse(strict=False (FD2: plan-spec enum at _parse.py:211 excludes coder ops))
→ Node. Malformed EDN wraps as PlanPayloadValidation, NOT bare ParseError.

Forced spec deviation vs impl plan line 398:
  The impl plan states `Node.tag` is BARE form ("seq") after EDN parse.
  Calibration of `_ast.py:67-103` shows `Node.tag` MUST be keyword-form
  (":seq", ":fs/read", etc.); `__post_init__` raises ValueError if tag
  does not start with ":". The impl plan's calibration instruction was
  incorrect. All tag comparisons in this file use keyword-form.
"""
from __future__ import annotations

import pytest

from persistence.coder._planner import _build_plan_from_payload
from persistence.coder._planner_errors import PlanPayloadValidation
from persistence.plan import Node


def test_build_plan_canonical_seq_with_three_leaves():
    plan_edn = '[:seq {} [:fs/read {:path "x.txt"}] [:code/run {:source "x=1"}] [:git/diff {}]]'
    node = _build_plan_from_payload({"plan_edn": plan_edn})
    assert isinstance(node, Node)
    # Node.tag is keyword-form after EDN parse (e.g. ":seq" not "seq").
    assert node.tag == ":seq"
    assert len(node.children) == 3


def test_build_plan_missing_plan_edn_field_raises_validation():
    with pytest.raises(PlanPayloadValidation) as exc_info:
        _build_plan_from_payload({})
    assert exc_info.value.field == "plan_edn"
    assert "missing" in exc_info.value.reason.lower()


def test_build_plan_non_string_plan_edn_raises_validation():
    with pytest.raises(PlanPayloadValidation) as exc_info:
        _build_plan_from_payload({"plan_edn": 123})
    assert exc_info.value.field == "plan_edn"


def test_build_plan_byte_budget_8192_enforced():
    """Stage 1 budget: payload bytes > 8192 → reject BEFORE parse."""
    huge = "[:seq {} " + ('[:fs/read {:path "' + "a" * 64 + '"}] ') * 200 + "]"
    assert len(huge.encode("utf-8")) > 8192
    with pytest.raises(PlanPayloadValidation) as exc_info:
        _build_plan_from_payload({"plan_edn": huge})
    assert exc_info.value.field == "plan_edn"
    assert "8192" in exc_info.value.reason


def test_build_plan_malformed_edn_wraps_as_validation_not_parse_error():
    """Stage 2: ParseError → PlanPayloadValidation (consistent error class)."""
    with pytest.raises(PlanPayloadValidation):
        _build_plan_from_payload({"plan_edn": "[:seq this is broken"})


def test_build_plan_canonical_round_trips_byte_identical():
    """LD6 byte-identical round-trip discipline: unparse(parse(x)) == x."""
    from persistence.plan import unparse
    plan_edn = '[:seq {} [:fs/read {:path "x.txt"}]]'
    node = _build_plan_from_payload({"plan_edn": plan_edn})
    assert unparse(node) == plan_edn
