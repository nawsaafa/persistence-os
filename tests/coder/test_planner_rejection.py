"""T6/G6 — `_escalate_plan_body` rejects invalid plans BEFORE
s.plan.execute or Dispatcher.dispatch fire. Verified via spy on
substrate.effect.perform — zero invocations on reject paths.

9 tests total:
  - 7 spec'd G6 tests (Stage 1/2/3 reject paths)
  - 2 T2 IMPORTANT residual closures (coderabbit code-review T2):
    (a) Interior unregistered tags rejected (field="interior_tag")
    (b) Empty [:seq {}] raises field="plan_body" (not "leaf_tag :seq unregistered")
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest

from persistence.coder._planner import _escalate_plan_body
from persistence.coder._planner_errors import PlanPayloadValidation
from persistence.coder._types import LLMDecision
from persistence.sdk import Substrate


@pytest.fixture
def s_with_spy():
    """Substrate with a spy on s.effect.perform — must NOT fire on reject paths."""
    with Substrate.open("memory") as substrate:
        substrate.effect.perform = MagicMock(side_effect=AssertionError(
            "s.effect.perform must NOT be called on reject paths"
        ))
        yield substrate


def _make_coder_stub(substrate):
    from persistence.coder._session import Coder
    coder = Coder(task="test", substrate=substrate)
    coder._session_start_dt = dt.datetime.now(dt.timezone.utc)
    coder._iter_count = 0
    return coder


def _decision(plan_edn: str) -> LLMDecision:
    return LLMDecision(kind="plan", confidence=0.9, payload={"plan_edn": plan_edn})


# ---------------------------------------------------------------------------
# G6 — 7 spec'd tests (Stage 1 + Stage 2 + Stage 3 reject paths)
# ---------------------------------------------------------------------------

def test_reject_branch_root(s_with_spy):
    """Stage 3: root tag != :seq is rejected (here :branch is the root)."""
    coder = _make_coder_stub(s_with_spy)
    with pytest.raises(PlanPayloadValidation) as exc_info:
        _escalate_plan_body(coder, _decision('[:branch {} [:fs/read {}]]'))
    assert exc_info.value.field == "root_tag"
    s_with_spy.effect.perform.assert_not_called()


def test_reject_code_leaf(s_with_spy):
    """Stage 3: :code leaf is banned (would crash walk per _walk.py:49)."""
    coder = _make_coder_stub(s_with_spy)
    with pytest.raises(PlanPayloadValidation) as exc_info:
        _escalate_plan_body(coder, _decision('[:seq {} [:code {:source "x"}]]'))
    assert "code" in exc_info.value.reason.lower()
    s_with_spy.effect.perform.assert_not_called()


def test_reject_unregistered_leaf_tag(s_with_spy):
    """Stage 3: leaf tag not in REGISTERED_LEAF_TAGS is rejected."""
    coder = _make_coder_stub(s_with_spy)
    with pytest.raises(PlanPayloadValidation) as exc_info:
        _escalate_plan_body(coder, _decision('[:seq {} [:fictional/op {}]]'))
    assert (
        "fictional/op" in exc_info.value.reason
        or "unregistered" in exc_info.value.reason.lower()
    )
    s_with_spy.effect.perform.assert_not_called()


def test_reject_byte_budget_exceeded(s_with_spy):
    """Stage 1: plan_edn bytes > 8192 is rejected before parse."""
    coder = _make_coder_stub(s_with_spy)
    huge = "[:seq {} " + ('[:fs/read {:path "' + "a" * 64 + '"}] ') * 200 + "]"
    assert len(huge.encode("utf-8")) > 8192
    with pytest.raises(PlanPayloadValidation) as exc_info:
        _escalate_plan_body(coder, _decision(huge))
    assert "8192" in exc_info.value.reason
    s_with_spy.effect.perform.assert_not_called()


def test_reject_node_count_exceeded(s_with_spy):
    """Stage 3: 65 leaves under :seq → node count 66 > 64 = MAX_PLAN_NODES."""
    coder = _make_coder_stub(s_with_spy)
    leaves = " ".join('[:fs/read {}]' for _ in range(65))
    plan_edn = f"[:seq {{}} {leaves}]"
    # Sanity: under 8192 bytes (Stage 1 must not fire here).
    assert len(plan_edn.encode("utf-8")) <= 8192
    with pytest.raises(PlanPayloadValidation) as exc_info:
        _escalate_plan_body(coder, _decision(plan_edn))
    assert exc_info.value.field == "plan_nodes"
    s_with_spy.effect.perform.assert_not_called()


def test_reject_depth_exceeded(s_with_spy):
    """Stage 3: depth 5 = root :seq → :seq → :seq → :seq → :fs/read > MAX_PLAN_DEPTH=4."""
    coder = _make_coder_stub(s_with_spy)
    plan_edn = '[:seq {} [:seq {} [:seq {} [:seq {} [:fs/read {}]]]]]'
    with pytest.raises(PlanPayloadValidation) as exc_info:
        _escalate_plan_body(coder, _decision(plan_edn))
    assert exc_info.value.field == "plan_depth"
    s_with_spy.effect.perform.assert_not_called()


def test_reject_malformed_edn_wraps_as_validation(s_with_spy):
    """Stage 2: malformed EDN is caught and re-raised as PlanPayloadValidation."""
    coder = _make_coder_stub(s_with_spy)
    with pytest.raises(PlanPayloadValidation):
        _escalate_plan_body(coder, _decision('[:seq this is broken'))
    s_with_spy.effect.perform.assert_not_called()


# ---------------------------------------------------------------------------
# G6 — 2 T2 IMPORTANT residual closures (coderabbit code-review T2)
# ---------------------------------------------------------------------------

def test_reject_interior_par_tag(s_with_spy):
    """T2 IMPORTANT (coderabbit): interior unregistered tags must NOT slip
    through. T2's leaves-only validator let [:seq {} [:par {} [:fs/read {}]]]
    pass — :par is interior so the leaf walk skipped it. Tightened validator
    now rejects any non-root interior node whose tag != ':seq'."""
    coder = _make_coder_stub(s_with_spy)
    with pytest.raises(PlanPayloadValidation) as exc_info:
        _escalate_plan_body(coder, _decision('[:seq {} [:par {} [:fs/read {:path "x"}]]]'))
    assert (
        "par" in exc_info.value.reason.lower()
        or exc_info.value.field == "interior_tag"
    )
    s_with_spy.effect.perform.assert_not_called()


def test_reject_empty_seq_body(s_with_spy):
    """T2 IMPORTANT (coderabbit): empty [:seq {}] should raise with a coherent
    'empty plan body' message (field='plan_body'), not 'leaf tag :seq unregistered'
    (which was the wrong defect class)."""
    coder = _make_coder_stub(s_with_spy)
    with pytest.raises(PlanPayloadValidation) as exc_info:
        _escalate_plan_body(coder, _decision('[:seq {}]'))
    assert exc_info.value.field == "plan_body"
    assert "empty" in exc_info.value.reason.lower()
    s_with_spy.effect.perform.assert_not_called()


def test_reject_nested_empty_seq(s_with_spy):
    """T6 IMPORTANT (coderabbit): nested empty [:seq {}] must raise
    field='plan_body' (same defect class as root empty), NOT field='leaf_tag'
    saying ':seq is unregistered'.

    Without the leaf-branch :seq pre-check, validate_plan_for_2_3a would
    pass the root-:seq Constraint 2 short-circuit (root has 1 child) then
    descend into the nested empty :seq via _check_nodes_recursive's leaf
    branch, where :seq is not in REGISTERED_LEAF_TAGS — the wrong defect
    class. This test pins field='plan_body' as the contract."""
    coder = _make_coder_stub(s_with_spy)
    with pytest.raises(PlanPayloadValidation) as exc_info:
        _escalate_plan_body(coder, _decision('[:seq {} [:seq {}]]'))
    assert exc_info.value.field == "plan_body", (
        f"nested empty :seq mis-classified: field={exc_info.value.field!r}, "
        f"reason={exc_info.value.reason!r}"
    )
    assert "empty" in exc_info.value.reason.lower()
    s_with_spy.effect.perform.assert_not_called()
