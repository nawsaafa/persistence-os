"""B5 — ``_StaticEvaluator`` lookup, ``on_unknown`` branches, Protocol satisfaction.

Pinned signature so subsequent B-series fixtures (B6/B-INT) can wire
deterministic per-plan scores uniformly. The full NaN/Inf rejection
boundary lives in B9's MCTS loop reject path; this module is surface-
only and only covers the test-stub semantics described in design §9.
"""
from __future__ import annotations

import pytest

from persistence.plan import Evaluator, Node
from persistence.plan._mcts import _StaticEvaluator


# --- Fixtures ------------------------------------------------------------ #


def _leaf(tag: str = ":leaf/predict", prompt: str = "x") -> Node:
    """Minimal leaf Node for test plans."""
    return Node(tag=tag, attrs={"prompt": prompt})


@pytest.fixture
def plan_a() -> Node:
    """Pinned plan A — present in the scores dict."""
    return Node(tag=":plan/a", attrs={"k": 1}, children=(_leaf(),))


@pytest.fixture
def plan_b() -> Node:
    """Pinned plan B — present in the scores dict."""
    return Node(tag=":plan/b", attrs={"k": 2}, children=(_leaf(),))


@pytest.fixture
def plan_unknown() -> Node:
    """Plan whose ``id`` is not in any scores dict — probes ``on_unknown``."""
    return Node(tag=":plan/unknown", attrs={"k": 99}, children=(_leaf(),))


# --- Protocol satisfaction ----------------------------------------------- #


def test_static_evaluator_satisfies_evaluator_protocol(plan_a):
    """``_StaticEvaluator`` is structurally an ``Evaluator`` (``@runtime_checkable``)."""
    evaluator = _StaticEvaluator({plan_a.id: 0.7})
    assert isinstance(evaluator, Evaluator)


# --- Known-plan lookup --------------------------------------------------- #


def test_evaluate_returns_pinned_score_for_known_plan(plan_a, plan_b):
    """Lookup by ``plan.id`` returns the pinned float."""
    evaluator = _StaticEvaluator({plan_a.id: 0.7, plan_b.id: 0.3})
    assert evaluator.evaluate(plan_a) == 0.7
    assert evaluator.evaluate(plan_b) == 0.3


def test_evaluate_returns_zero_score_for_known_plan_with_zero_value(plan_a):
    """A pinned score of ``0.0`` is returned (not treated as a miss)."""
    evaluator = _StaticEvaluator({plan_a.id: 0.0})
    assert evaluator.evaluate(plan_a) == 0.0


# --- Unknown-plan branches ----------------------------------------------- #


def test_evaluate_unknown_plan_default_returns_zero(plan_a, plan_unknown):
    """Default ``on_unknown="zero"``: unknown ``plan.id`` -> ``0.0``."""
    evaluator = _StaticEvaluator({plan_a.id: 0.7})
    assert evaluator.evaluate(plan_unknown) == 0.0


def test_evaluate_unknown_plan_with_on_unknown_raise_raises_keyerror(
    plan_a, plan_unknown
):
    """``on_unknown="raise"``: unknown ``plan.id`` -> ``KeyError`` carrying the id."""
    evaluator = _StaticEvaluator(
        {plan_a.id: 0.7},
        on_unknown="raise",
    )
    with pytest.raises(KeyError) as excinfo:
        evaluator.evaluate(plan_unknown)
    assert excinfo.value.args[0] == plan_unknown.id


# --- Empty scores dict --------------------------------------------------- #


def test_evaluate_empty_scores_default_returns_zero_for_any_plan(plan_a):
    """Empty ``scores`` + ``on_unknown="zero"``: any plan -> ``0.0``."""
    evaluator = _StaticEvaluator({})
    assert evaluator.evaluate(plan_a) == 0.0


def test_evaluate_empty_scores_with_raise_raises_keyerror(plan_a):
    """Empty ``scores`` + ``on_unknown="raise"``: any plan -> ``KeyError``."""
    evaluator = _StaticEvaluator({}, on_unknown="raise")
    with pytest.raises(KeyError):
        evaluator.evaluate(plan_a)
