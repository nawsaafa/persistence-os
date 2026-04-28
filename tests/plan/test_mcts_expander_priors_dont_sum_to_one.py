"""B6 — Expander prior-sum-to-1.0 contract enforcement (design §8 / §14).

When the expander's first cache-miss returns a non-empty proposal list
whose priors don't sum to ``1.0 ± _PRIOR_TOL``, the MCTS loop raises
``ExpanderContractError`` at the boundary (no datom, no recovery —
expander-contract violation, NOT a search-runtime issue).

Empty proposals are exempt (terminal-node signal; mark ``is_terminal``,
search continues).
"""
from __future__ import annotations

import pytest

from persistence.plan import (
    ExpanderContractError,
    MCTSConfig,
    Node,
    SubstituteLeafAction,
    apply_action,
    mcts_search,
)
from persistence.plan._mcts import _StaticEvaluator, _StaticExpander


def _leaf(tag: str = ":leaf/predict", prompt: str = "x") -> Node:
    return Node(tag=tag, attrs={"prompt": prompt})


def _initial() -> Node:
    return Node(
        tag=":plan/seq",
        attrs={"v": 1},
        children=(_leaf(prompt="A"), _leaf(prompt="B")),
    )


def test_priors_sum_to_1_1_raises_expander_contract_error():
    """Priors summing to 1.1 (well outside ``_PRIOR_TOL=1e-6``) raise."""
    initial = _initial()
    new_a = _leaf(prompt="a_sub")
    new_b = _leaf(prompt="b_sub")
    act_a = SubstituteLeafAction(target_path=(0,), new_leaf=new_a)
    act_b = SubstituteLeafAction(target_path=(1,), new_leaf=new_b)
    plan_a = apply_action(initial, act_a)
    plan_b = apply_action(initial, act_b)

    expander = _StaticExpander({initial.id: [(act_a, 0.6), (act_b, 0.5)]})
    evaluator = _StaticEvaluator(
        {initial.id: 0.5, plan_a.id: 0.7, plan_b.id: 0.3}
    )
    with pytest.raises(ExpanderContractError, match="not 1.0"):
        mcts_search(
            initial,
            expander=expander,
            evaluator=evaluator,
            started_at_ms=1_000_000,
            config=MCTSConfig(max_iter=10),
        )


def test_priors_sum_to_0_5_raises_expander_contract_error():
    """Priors summing to 0.5 (well outside tolerance) raise."""
    initial = _initial()
    new_a = _leaf(prompt="a_sub")
    new_b = _leaf(prompt="b_sub")
    act_a = SubstituteLeafAction(target_path=(0,), new_leaf=new_a)
    act_b = SubstituteLeafAction(target_path=(1,), new_leaf=new_b)
    plan_a = apply_action(initial, act_a)
    plan_b = apply_action(initial, act_b)

    expander = _StaticExpander({initial.id: [(act_a, 0.3), (act_b, 0.2)]})
    evaluator = _StaticEvaluator(
        {initial.id: 0.5, plan_a.id: 0.7, plan_b.id: 0.3}
    )
    with pytest.raises(ExpanderContractError):
        mcts_search(
            initial,
            expander=expander,
            evaluator=evaluator,
            started_at_ms=1_000_000,
            config=MCTSConfig(max_iter=10),
        )


def test_priors_sum_exactly_1_0_no_raise():
    """Priors summing to exactly 1.0 — no raise."""
    initial = _initial()
    new_a = _leaf(prompt="a_sub")
    act_a = SubstituteLeafAction(target_path=(0,), new_leaf=new_a)
    plan_a = apply_action(initial, act_a)

    expander = _StaticExpander({initial.id: [(act_a, 1.0)]})
    evaluator = _StaticEvaluator({initial.id: 0.5, plan_a.id: 0.7})

    result = mcts_search(
        initial,
        expander=expander,
        evaluator=evaluator,
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=5),
    )
    assert result.terminated_by == "max_iter"


def test_priors_within_tolerance_no_raise():
    """Priors summing to ``1.0 + 0.5e-6`` (inside ``_PRIOR_TOL=1e-6``) — no raise."""
    initial = _initial()
    new_a = _leaf(prompt="a_sub")
    new_b = _leaf(prompt="b_sub")
    act_a = SubstituteLeafAction(target_path=(0,), new_leaf=new_a)
    act_b = SubstituteLeafAction(target_path=(1,), new_leaf=new_b)
    plan_a = apply_action(initial, act_a)
    plan_b = apply_action(initial, act_b)

    # Sum = 0.5 + 0.5 + 5e-7 = 1.0000005, |1.0 - sum| = 5e-7 < 1e-6.
    expander = _StaticExpander(
        {initial.id: [(act_a, 0.5 + 5e-7), (act_b, 0.5)]}
    )
    evaluator = _StaticEvaluator(
        {initial.id: 0.5, plan_a.id: 0.7, plan_b.id: 0.3}
    )
    result = mcts_search(
        initial,
        expander=expander,
        evaluator=evaluator,
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=5),
    )
    assert result.terminated_by == "max_iter"


def test_empty_proposals_no_raise_terminal_marked():
    """Empty proposals — exempt from the prior-sum check; node marked terminal."""
    initial = _initial()
    expander = _StaticExpander({initial.id: ()})  # empty signal
    evaluator = _StaticEvaluator({initial.id: 0.5})

    result = mcts_search(
        initial,
        expander=expander,
        evaluator=evaluator,
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=5),
    )
    # "exhausted" fires at iter 0: root has no proposals.
    assert result.terminated_by == "exhausted"
    assert result.winner_plan_id == initial.id
