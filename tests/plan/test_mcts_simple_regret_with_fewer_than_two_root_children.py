"""Pin: ``simple_regret`` short-circuits when root has < 2 children.

Design §11 line 640: "_termination_reached() short-circuits
(``if len(root.children) < 2: simple_regret_signal = False``). One of
``max_iter``, ``max_unique_plans``, ``wall_clock``, or ``exhausted``
will terminate instead. This avoids a hidden divide-by-zero /
index-error and keeps the user-facing contract crisp ('simple_regret
needs ≥ 2 root children to compute')."

Two fixtures:
1. Root expansion produces exactly 1 child → ``simple_regret`` never
   fires; ``max_iter`` (or another non-SR reason) terminates instead.
2. Root expansion produces 0 children → ``terminated_by="exhausted"``
   (NOT ``"simple_regret"``).
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


def _root_with_two_leaves() -> Node:
    return Node(
        tag=":plan/seq",
        attrs={"version": 1},
        children=(_leaf(prompt="A"), _leaf(prompt="B")),
    )


def test_simple_regret_short_circuits_when_root_has_one_child():
    """Root expansion → exactly 1 child; ``simple_regret`` never fires.

    With ``simple_regret_threshold`` set and a low ``simple_regret_window``,
    the SR check would normally fire on any iter > window. Because the
    root has only ONE child, the SR signal short-circuits to False
    (design §11 line 640). The search runs until ``max_iter`` (or
    another non-SR reason) terminates.
    """
    initial = _root_with_two_leaves()
    new_a = _leaf(prompt="A_sub")
    act_a = SubstituteLeafAction(target_path=(0,), new_leaf=new_a)
    plan_a = apply_action(initial, act_a)

    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act_a, 1.0)],  # exactly one root child
    }
    scores = {initial.id: 0.5, plan_a.id: 0.9}

    config = MCTSConfig(
        max_iter=10,
        simple_regret_threshold=0.0,  # would fire trivially if children >= 2
        simple_regret_window=1,
    )
    result = mcts_search(
        initial,
        expander=_StaticExpander(proposals),
        evaluator=_StaticEvaluator(scores),
        started_at_ms=1_000_000,
        config=config,
    )

    assert result.terminated_by != "simple_regret"
    # With this fixture the search runs until max_iter binds.
    assert result.terminated_by == "max_iter"


def test_simple_regret_short_circuits_when_root_has_zero_children():
    """Root expansion → 0 children; ``terminated_by="exhausted"`` (not SR).

    With ``_StaticExpander({})`` and ``on_unknown="empty"`` the root
    receives ``()`` as its proposal list → ``root.is_terminal = True``.
    The "exhausted" check at iter 0 fires before any SR check could
    even hypothetically run; ``simple_regret`` never fires.
    """
    initial = _root_with_two_leaves()
    expander = _StaticExpander({}, on_unknown="empty")
    config = MCTSConfig(
        max_iter=10,
        simple_regret_threshold=0.0,
        simple_regret_window=1,
    )
    result = mcts_search(
        initial,
        expander=expander,
        evaluator=_StaticEvaluator({initial.id: 0.5}),
        started_at_ms=1_000_000,
        config=config,
    )

    assert result.terminated_by == "exhausted"
    assert result.terminated_by != "simple_regret"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
