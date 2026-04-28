"""B5 — Pin: design §16 invariant 5 — evaluator runs on the JUST-EXPANDED PARENT, not on its children.

Full loop test in B6. This module is unit-only: it constructs an
evaluator, calls ``evaluate(parent_plan)``, and verifies the score is
the parent's pinned score (not a child's). The contract being pinned
here is that ``Evaluator.evaluate`` is invoked with the parent ``plan``
object, so the MCTS loop is free to wire B6's expansion step as
"compute children, then call evaluator(parent)" with no ambiguity at
the call site.
"""
from __future__ import annotations

import pytest

from persistence.plan import Evaluator, LLMJudgeEvaluator, Node
from persistence.plan._mcts import _StaticEvaluator


# --- Fixtures ------------------------------------------------------------ #


def _leaf(tag: str = ":leaf/predict", prompt: str = "x") -> Node:
    return Node(tag=tag, attrs={"prompt": prompt})


@pytest.fixture
def parent_plan() -> Node:
    """The parent plan — the JUST-EXPANDED node passed to the evaluator."""
    return Node(tag=":plan/parent", attrs={"role": "parent"}, children=(_leaf(),))


@pytest.fixture
def child_plan() -> Node:
    """A spawned-but-not-yet-evaluated child plan — must NOT be the eval target."""
    return Node(tag=":plan/child", attrs={"role": "child"}, children=(_leaf(),))


# --- _StaticEvaluator: parent score, not child score -------------------- #


def test_static_evaluator_returns_parent_score_not_child_score(parent_plan, child_plan):
    """Calling ``evaluate(parent_plan)`` returns the parent's pinned score."""
    evaluator = _StaticEvaluator({parent_plan.id: 0.9, child_plan.id: 0.1})
    score = evaluator.evaluate(parent_plan)
    assert score == 0.9


# --- LLMJudgeEvaluator: parent identity preserved through delegation ---- #


def test_llm_judge_evaluator_provider_receives_parent_plan(parent_plan, child_plan):
    """``LLMJudgeEvaluator`` forwards the parent ``plan`` to the provider closure.

    The B6 loop site calls ``evaluator.evaluate(parent_plan)``; the
    provider receives ``parent_plan`` (by identity), not any child.
    """
    captured: dict[str, Node] = {}

    def provider(plan: Node) -> float:
        captured["plan"] = plan
        return 0.5

    evaluator = LLMJudgeEvaluator(provider=provider)
    score = evaluator.evaluate(parent_plan)
    assert captured["plan"] is parent_plan
    assert captured["plan"] is not child_plan
    assert score == 0.5


# --- Protocol satisfaction (smoke) -------------------------------------- #


def test_llm_judge_evaluator_satisfies_evaluator_protocol():
    """``LLMJudgeEvaluator`` is structurally an ``Evaluator`` (``@runtime_checkable``)."""
    evaluator = LLMJudgeEvaluator(provider=lambda _plan: 0.5)
    assert isinstance(evaluator, Evaluator)
