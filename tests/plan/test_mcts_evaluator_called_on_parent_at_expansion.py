"""B5/B6 — Pin: design §16 invariant 5 — evaluator runs on the JUST-EXPANDED PARENT, not on its children.

B5 surface unit (top of file) constructs an evaluator and calls
``evaluate(parent_plan)``, verifying the score is the parent's pinned
score (not a child's). The contract being pinned here is that
``Evaluator.evaluate`` is invoked with the parent ``plan`` object.

B6 loop tests (bottom of file) drive the full ``mcts_search`` loop and
assert: on iter 0 (root EXPAND fires, 3 children added), the evaluator
was called exactly once with the ROOT plan and zero times with any
child. A second iteration (when SELECT picks one of the children)
shows the child IS evaluated then.
"""
from __future__ import annotations

import pytest

from persistence.plan import (
    Evaluator,
    LLMJudgeEvaluator,
    MCTSConfig,
    Node,
    SubstituteLeafAction,
    apply_action,
    mcts_search,
)
from persistence.plan._mcts import _StaticEvaluator, _StaticExpander


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


# --- B6: full loop test for design §16 invariant 5 --------------------- #


class _RecordingEvaluator:
    """Records every plan passed to ``evaluate`` (call-by-call)."""

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores
        self.calls: list[Node] = []

    def evaluate(self, plan: Node) -> float:
        self.calls.append(plan)
        return self._scores.get(plan.id, 0.0)


def test_mcts_loop_evaluator_called_on_root_iter0_not_children():
    """Iter 0: SELECT lands at root, EXPAND adds 3 children, EVALUATE on ROOT.

    Design §16 invariant 5. The evaluator's call list must contain
    exactly one entry on iter 0, with the ROOT plan. No child plan
    appears in the iter-0 call list."""
    initial = Node(
        tag=":plan/seq",
        attrs={"v": 1},
        children=(_leaf(prompt="root_a"), _leaf(prompt="root_b")),
    )
    new_x = _leaf(prompt="x")
    new_y = _leaf(prompt="y")
    new_z = _leaf(prompt="z")
    act_x = SubstituteLeafAction(target_path=(0,), new_leaf=new_x)
    act_y = SubstituteLeafAction(target_path=(0,), new_leaf=new_y)
    act_z = SubstituteLeafAction(target_path=(1,), new_leaf=new_z)
    plan_x = apply_action(initial, act_x)
    plan_y = apply_action(initial, act_y)
    plan_z = apply_action(initial, act_z)

    expander = _StaticExpander(
        {initial.id: [(act_x, 0.5), (act_y, 0.3), (act_z, 0.2)]}
    )
    evaluator = _RecordingEvaluator(
        {
            initial.id: 0.5,
            plan_x.id: 0.7,
            plan_y.id: 0.3,
            plan_z.id: 0.4,
        }
    )

    mcts_search(
        initial,
        expander=expander,
        evaluator=evaluator,
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=1),
    )

    # Iter 0: SELECT terminates at root (no children); EXPAND populates
    # 3 children; EVALUATE on ROOT (parent).
    assert len(evaluator.calls) == 1
    assert evaluator.calls[0].id == initial.id
    # No child plan was evaluated on iter 0:
    for call in evaluator.calls:
        assert call.id != plan_x.id
        assert call.id != plan_y.id
        assert call.id != plan_z.id


def test_mcts_loop_evaluator_called_on_child_after_select_picks_it():
    """Iter 1: SELECT picks one of the children; that child IS evaluated."""
    initial = Node(
        tag=":plan/seq",
        attrs={"v": 1},
        children=(_leaf(prompt="root_a"), _leaf(prompt="root_b")),
    )
    new_x = _leaf(prompt="x")
    act_x = SubstituteLeafAction(target_path=(0,), new_leaf=new_x)
    plan_x = apply_action(initial, act_x)

    expander = _StaticExpander({initial.id: [(act_x, 1.0)]})
    evaluator = _RecordingEvaluator({initial.id: 0.5, plan_x.id: 0.7})

    mcts_search(
        initial,
        expander=expander,
        evaluator=evaluator,
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=2),
    )

    # Iter 0: EVALUATE on ROOT.
    # Iter 1: SELECT picks the only child edge → walks to plan_x →
    #         EXPAND on plan_x (no proposals → terminal) → EVALUATE on
    #         plan_x.
    assert len(evaluator.calls) == 2
    call_ids = [c.id for c in evaluator.calls]
    assert initial.id in call_ids
    assert plan_x.id in call_ids
    # Order matters: root first, then child.
    assert call_ids[0] == initial.id
    assert call_ids[1] == plan_x.id
