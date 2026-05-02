"""Phase 2.0f: top-level ``persistence.plan.judge`` — thin invocation
surface over the ``Evaluator`` Protocol.

The function is a pure pass-through to ``evaluator.evaluate(plan)``;
no MCTS, no defaults, no rubric encoding. Caller embeds scoring
policy inside their ``Evaluator`` (typically
``LLMJudgeEvaluator(provider=...)``).

Test coverage:
1. Delegation: returns ``evaluator.evaluate(plan)`` verbatim.
2. Exception propagation: evaluator-raised errors propagate unchanged.
3. Keyword-only ``evaluator``: positional invocation raises ``TypeError``.
4. Missing ``evaluator``: invocation without the kwarg raises
   ``TypeError`` (no default is provided — a judge call without an
   evaluator is vacuous, design § 2 Bhatt-1).
"""
from __future__ import annotations

import pytest

from persistence.plan import Node, judge
from persistence.plan._mcts import _StaticEvaluator


def _make_simple_plan() -> Node:
    """Single-leaf ``:llm-call`` plan; mirrors the test_plan_namespace
    smoke pattern."""
    return Node(tag=":llm-call", attrs={"prompt": "hi"}, children=())


def test_judge_delegates_to_evaluator() -> None:
    """``judge(plan, evaluator=ev)`` returns ``ev.evaluate(plan)`` verbatim."""
    plan = _make_simple_plan()
    evaluator = _StaticEvaluator(scores={plan.id: 0.75})

    result = judge(plan, evaluator=evaluator)

    assert result == 0.75


def test_judge_propagates_evaluator_exceptions() -> None:
    """If the evaluator raises, ``judge`` propagates the exception
    unchanged (no wrapping). Mirrors the MCTS reject-path discipline
    (design § 13: ``error_class != null`` is the unambiguous signal)."""
    plan = _make_simple_plan()
    evaluator = _StaticEvaluator(scores={}, on_unknown="raise")

    with pytest.raises(KeyError):
        judge(plan, evaluator=evaluator)


def test_judge_evaluator_is_keyword_only() -> None:
    """The ``evaluator`` argument is keyword-only — design choice
    mirroring MCTS evaluator threading at the call site, prevents
    accidental positional misuse."""
    plan = _make_simple_plan()
    evaluator = _StaticEvaluator(scores={plan.id: 0.5})

    with pytest.raises(TypeError):
        judge(plan, evaluator)  # type: ignore[misc]


def test_judge_requires_evaluator() -> None:
    """No default evaluator: ``judge(plan)`` raises ``TypeError``.
    Design § 2 Bhatt-1: a judge call without an evaluator is vacuous."""
    plan = _make_simple_plan()

    with pytest.raises(TypeError):
        judge(plan)  # type: ignore[call-arg]
