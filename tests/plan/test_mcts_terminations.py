"""Pin: 5 termination reasons in v0.6.5 (max_iter, max_unique_plans,
simple_regret, all_evaluations_failed, exhausted). The wall_clock
fixture is intentionally omitted — design §11 specifies wall_clock
budget enforcement is deferred to v0.7+ and the reason never fires
in v0.6.5.

Design references:
- §11 — termination policy + 6-reason union, OR-combined.
- §16 — termination is checked at the top of each iteration.
- §18 — test plan: every termination reason has at least one fixture.
- impl plan §B7 — five fixtures (no wall_clock).
"""
from __future__ import annotations

import warnings
from collections.abc import Sequence

import pytest

from persistence.plan import (
    Action,
    LLMJudgeEvaluator,
    MCTSConfig,
    Node,
    SubstituteLeafAction,
    apply_action,
    mcts_search,
)
from persistence.plan._mcts import _StaticEvaluator, _StaticExpander


# --- Fixtures ------------------------------------------------------------ #


def _leaf(prompt: str = "x") -> Node:
    return Node(tag=":leaf/predict", attrs={"prompt": prompt})


def _root_with_two_leaves() -> Node:
    """A 3-node plan: root with two leaf children."""
    return Node(
        tag=":plan/seq",
        attrs={"version": 1},
        children=(_leaf(prompt="A"), _leaf(prompt="B")),
    )


def _make_substitute_actions(initial: Node) -> tuple[
    SubstituteLeafAction, SubstituteLeafAction, Node, Node
]:
    """Build two SubstituteLeaf actions hitting distinct leaves."""
    new_a = _leaf(prompt="A_sub")
    new_b = _leaf(prompt="B_sub")
    act_a = SubstituteLeafAction(target_path=(0,), new_leaf=new_a)
    act_b = SubstituteLeafAction(target_path=(1,), new_leaf=new_b)
    plan_a = apply_action(initial, act_a)
    plan_b = apply_action(initial, act_b)
    return act_a, act_b, plan_a, plan_b


# --- 1. terminated_by="max_iter" ---------------------------------------- #


def test_terminated_by_max_iter():
    """``max_iter`` binds when the search has not exhausted before the cap.

    With ``max_iter=5`` and ``max_unique_plans=10000`` the iteration
    counter is the binding budget; the loop exits with
    ``iter_count == 5`` and ``terminated_by == "max_iter"``.
    """
    initial = _root_with_two_leaves()
    act_a, act_b, plan_a, plan_b = _make_substitute_actions(initial)

    # Cascading proposals: every visited plan keeps generating one new
    # leaf substitution → the search has plenty of frontier and won't
    # exhaust before max_iter binds.
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act_a, 0.5), (act_b, 0.5)],
    }
    config = MCTSConfig(max_iter=5, max_unique_plans=10000)

    result = mcts_search(
        initial,
        expander=_StaticExpander(proposals),
        evaluator=_StaticEvaluator({initial.id: 0.5, plan_a.id: 0.7, plan_b.id: 0.3}),
        started_at_ms=1_000_000,
        config=config,
    )

    assert result.terminated_by == "max_iter"
    assert result.iter_count == 5


# --- 2. terminated_by="max_unique_plans" -------------------------------- #


def test_terminated_by_max_unique_plans():
    """``max_unique_plans`` binds when the unique-plan counter hits the cap.

    With ``max_iter=10000`` and ``max_unique_plans=3`` the unique-plan
    counter binds first. The transposition table starts at 1 (root) and
    grows by one per distinct child plan; once it reaches 3, the next
    top-of-loop check terminates.
    """
    initial = _root_with_two_leaves()
    act_a, act_b, plan_a, plan_b = _make_substitute_actions(initial)

    # 3 distinct plans expected: {initial, plan_a, plan_b}.
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act_a, 0.5), (act_b, 0.5)],
    }
    config = MCTSConfig(max_iter=10000, max_unique_plans=3)

    result = mcts_search(
        initial,
        expander=_StaticExpander(proposals),
        evaluator=_StaticEvaluator({initial.id: 0.5, plan_a.id: 0.7, plan_b.id: 0.3}),
        started_at_ms=1_000_000,
        config=config,
    )

    assert result.terminated_by == "max_unique_plans"
    assert result.unique_plans_visited == 3


# --- 3. terminated_by="simple_regret" ----------------------------------- #


def test_terminated_by_simple_regret():
    """``simple_regret`` fires when visits-sorted top-2 root Q-gap exceeds
    threshold for ``simple_regret_window`` consecutive iterations.

    Fixture: root expands to two children A and B; A evaluates higher
    than B; PUCT favours A (positive Q + tied prior); after a few iters
    the visits-sorted top-2 Q-gap exceeds the threshold and the search
    terminates by simple_regret.
    """
    initial = _root_with_two_leaves()
    act_a, act_b, plan_a, plan_b = _make_substitute_actions(initial)

    # Strong Q-gap: plan_a scores 1.0, plan_b scores 0.0.
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act_a, 0.5), (act_b, 0.5)],
    }
    scores = {initial.id: 0.5, plan_a.id: 1.0, plan_b.id: 0.0}

    # Threshold 0.1, window 2 — short window so SR can fire deterministically.
    # max_iter is generous (50) so that simple_regret binds before max_iter.
    config = MCTSConfig(
        max_iter=50,
        max_unique_plans=10000,
        simple_regret_threshold=0.1,
        simple_regret_window=2,
    )

    result = mcts_search(
        initial,
        expander=_StaticExpander(proposals),
        evaluator=_StaticEvaluator(scores),
        started_at_ms=1_000_000,
        config=config,
    )

    assert result.terminated_by == "simple_regret"
    # SR cannot fire before the window-size minimum number of iterations.
    assert result.iter_count >= config.simple_regret_window


# --- 4. terminated_by="all_evaluations_failed" -------------------------- #


def test_terminated_by_all_evaluations_failed():
    """Every evaluator call raises → ``all_evaluations_failed``.

    Fires on the FIRST top-of-loop check where ``eval_attempts ==
    eval_failures > 0``. Winner falls back to ``initial_plan`` and a
    ``UserWarning`` is emitted (design §18 + impl plan §B7).
    """
    initial = _root_with_two_leaves()
    act_a, act_b, _plan_a, _plan_b = _make_substitute_actions(initial)
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act_a, 0.5), (act_b, 0.5)],
    }

    def _always_raise(_plan: Node) -> float:
        raise RuntimeError("provider unavailable")

    evaluator = LLMJudgeEvaluator(provider=_always_raise)
    config = MCTSConfig(max_iter=10)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = mcts_search(
            initial,
            expander=_StaticExpander(proposals),
            evaluator=evaluator,
            started_at_ms=1_000_000,
            config=config,
        )

    assert result.terminated_by == "all_evaluations_failed"
    # Winner falls back to initial_plan when no edge has positive visits.
    assert result.winner_plan_id == initial.id
    # UserWarning emitted at least once with the relevant message.
    matching = [
        w
        for w in caught
        if issubclass(w.category, UserWarning)
        and "all_evaluations_failed" in str(w.message)
    ]
    assert matching, f"expected UserWarning, got: {[str(w.message) for w in caught]}"


# --- 5. terminated_by="exhausted" --------------------------------------- #


def test_terminated_by_exhausted():
    """Root produces 0 proposals → ``exhausted`` fires at iter 0.

    Uses ``_StaticExpander({})`` with the default ``on_unknown="empty"``
    so the expander returns ``()`` for the root plan id. The first
    iteration marks root terminal; the post-EXPAND ``exhausted`` check
    at iter 0 triggers immediate termination. ``winner = initial_plan``.
    """
    initial = _root_with_two_leaves()

    expander = _StaticExpander({}, on_unknown="empty")
    config = MCTSConfig(max_iter=100)

    result = mcts_search(
        initial,
        expander=expander,
        evaluator=_StaticEvaluator({initial.id: 0.5}),
        started_at_ms=1_000_000,
        config=config,
    )

    assert result.terminated_by == "exhausted"
    # iter_count is 1: the FIRST iteration marks root as terminal and
    # the exhausted check immediately breaks the loop.
    assert result.iter_count == 1
    # Winner falls back to initial_plan because root has no children.
    assert result.winner_plan_id == initial.id
    # Sanity: also assert no spurious termination by another reason.
    valid_reasons = {
        "max_iter",
        "max_unique_plans",
        "simple_regret",
        "wall_clock",
        "exhausted",
        "all_evaluations_failed",
    }
    assert result.terminated_by in valid_reasons


# --- meta: pin the v0.6.5 reason set (wall_clock unreachable) ----------- #


def test_terminated_by_literal_set_does_not_include_wall_clock_in_v065():
    """``wall_clock`` is never produced by the v0.6.5 search loop.

    Sets a positive ``wall_clock_budget_ms`` on the config and asserts
    the resulting search does not terminate by ``wall_clock`` (it
    terminates by ``max_iter`` instead). Pins design §11: the
    ``wall_clock`` reason ships in the union for forward-compat but is
    deferred to v0.7+ for actual enforcement.
    """
    initial = _root_with_two_leaves()
    act_a, act_b, plan_a, plan_b = _make_substitute_actions(initial)
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act_a, 0.5), (act_b, 0.5)],
    }
    # 1 ms budget — would fire instantly if v0.6.5 honoured it.
    config = MCTSConfig(max_iter=3, wall_clock_budget_ms=1)

    result = mcts_search(
        initial,
        expander=_StaticExpander(proposals),
        evaluator=_StaticEvaluator(
            {initial.id: 0.5, plan_a.id: 0.7, plan_b.id: 0.3}
        ),
        started_at_ms=1_000_000,
        config=config,
    )

    assert result.terminated_by != "wall_clock"
    assert result.terminated_by == "max_iter"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
