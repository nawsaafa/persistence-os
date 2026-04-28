"""Pin: ``simple_regret`` Q-gap is computed on **visits-sorted** top-2
root edges, NOT Q-sorted top-2.

Design §11 line 644: "The 'best - second-best' gap is computed by
sorting ``root.children.values()`` by ``visits_through_edge``
descending, then taking ``top[0].q_value() - top[1].q_value()``."

Why visits-sort: AlphaZero / MCTS literature uses "most-visited as
posterior commitment" (matches §16 winner-edge selection); Q-sorted
alternative would compute "regret on point estimate" which is not
what the convergence proofs cover.

This test pins the W2 MINOR-6 fix. Failing it would catch a regression
to Q-sorted top-2.

Fixture construction:
- Root has TWO children A and B.
- Priors: A=0.85 (heavy), B=0.15 (light) — A wins early visits.
- Scores: Q_A=0.3 (low), Q_B=0.8 (high) — B is the better leaf.
- After ~10 iters: A has ~10 visits at Q=0.3; B has ~5 visits at Q=0.8.
- Visits-sorted top-2 = [A (Q=0.3), B (Q=0.8)] → gap = 0.3 - 0.8 = -0.5.
- Q-sorted     top-2 = [B (Q=0.8), A (Q=0.3)] → gap = 0.8 - 0.3 = +0.5.
- Threshold 0.4: visits-sort gap (-0.5) is BELOW; Q-sort gap (+0.5) is
  ABOVE. With visits-sort impl: SR never fires. With Q-sort impl: SR
  fires.

Asserting ``terminated_by != "simple_regret"`` (and explicitly
``== "max_iter"``) under this fixture pins the visits-sort
implementation.
"""
from __future__ import annotations

from collections.abc import Sequence

import pytest

from persistence.plan import (
    Action,
    MCTSConfig,
    Node,
    SubstituteLeafAction,
    apply_action,
    mcts_search,
)
from persistence.plan._mcts import _StaticEvaluator, _StaticExpander


def _leaf(prompt: str = "x") -> Node:
    return Node(tag=":leaf/predict", attrs={"prompt": prompt})


def test_simple_regret_uses_visits_sort_not_q_sort():
    """visits-sort top-2 differs from Q-sort top-2 → SR must use visits.

    See module docstring for full fixture rationale.
    """
    initial = Node(
        tag=":plan/seq",
        attrs={"version": 1},
        children=(_leaf(prompt="A"), _leaf(prompt="B")),
    )
    act_a = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf(prompt="A_sub"))
    act_b = SubstituteLeafAction(target_path=(1,), new_leaf=_leaf(prompt="B_sub"))
    plan_a = apply_action(initial, act_a)
    plan_b = apply_action(initial, act_b)

    # Heavy prior on A; B has the higher Q. PUCT visits A more in
    # the short-iteration regime even though Q_B > Q_A.
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act_a, 0.85), (act_b, 0.15)],
    }
    scores = {initial.id: 0.5, plan_a.id: 0.3, plan_b.id: 0.8}

    # max_iter chosen so visits stay (A > B) for the entire window where
    # SR could fire under either sort. At max_iter=20 the final state is
    # A=visits=10/Q=0.3, B=visits=9/Q=0.8 — A still leads visits, but B
    # would dominate Q-sort throughout iters 12-20.
    config = MCTSConfig(
        max_iter=20,
        simple_regret_threshold=0.4,
        simple_regret_window=2,
    )

    result = mcts_search(
        initial,
        expander=_StaticExpander(proposals),
        evaluator=_StaticEvaluator(scores),
        started_at_ms=1_000_000,
        config=config,
    )

    # --- Pin precondition: post-search state shows visits-sort vs Q-sort
    #     ordering DIFFERS (otherwise the test would be vacuous) ----------
    edges_by_action_hash: dict[str, tuple[int, float]] = {
        row[2]: (row[3], row[4]) for row in result.tree_dump
    }
    # Two root edges expected (no transposition collisions in this fixture).
    rows = list(result.tree_dump)
    assert len(rows) == 2, f"expected 2 root edges, got {len(rows)}"
    e1, e2 = rows[0], rows[1]
    # Identify A and B by Q (Q_A < Q_B by fixture construction).
    if e1[4] < e2[4]:
        a_visits, a_q = e1[3], e1[4]
        b_visits, b_q = e2[3], e2[4]
    else:
        a_visits, a_q = e2[3], e2[4]
        b_visits, b_q = e1[3], e1[4]
    # Precondition: visits-sort top-1 is A (lower Q), Q-sort top-1 is B.
    assert a_visits > b_visits, (
        f"fixture broke: expected A.visits > B.visits "
        f"(A=v{a_visits},Q{a_q:.3f}; B=v{b_visits},Q{b_q:.3f})"
    )
    assert b_q > a_q, (
        f"fixture broke: expected Q_B > Q_A "
        f"(A=v{a_visits},Q{a_q:.3f}; B=v{b_visits},Q{b_q:.3f})"
    )
    # Visits-sort gap = a_q - b_q (NEGATIVE under fixture)
    visits_sort_gap = a_q - b_q
    # Q-sort gap = b_q - a_q (POSITIVE)
    q_sort_gap = b_q - a_q
    # Sanity: threshold lies BETWEEN |visits_sort_gap| and |q_sort_gap|
    # — actually, the visits gap is NEGATIVE so it's automatically below
    # any non-negative threshold. The Q-sort gap is positive and >= threshold.
    assert visits_sort_gap < config.simple_regret_threshold  # type: ignore[operator]
    assert q_sort_gap >= config.simple_regret_threshold  # type: ignore[operator]

    # --- The pin: SR did NOT fire under visits-sort impl -------------- #
    assert result.terminated_by != "simple_regret", (
        f"simple_regret fired under a fixture where visits-sort gap "
        f"({visits_sort_gap:.3f}) is below threshold "
        f"({config.simple_regret_threshold}); this indicates the impl "
        f"regressed to Q-sort top-2 (Q-sort gap = {q_sort_gap:.3f})"
    )
    assert result.terminated_by == "max_iter"
    # Reachable visits-sort guard: the unused mapping holds the per-edge
    # (visits, q) — we keep the variable assignment to surface it in
    # debugger output if the test ever fails.
    _ = edges_by_action_hash


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
