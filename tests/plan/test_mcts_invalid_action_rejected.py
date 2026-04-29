"""B9 — invalid action (out-of-range path) → ``phase="reject"`` (design §14).

Per design §14 row "All proposed actions produce invalid plans": an
``apply_action`` raise (``IndexError``/``ValueError``) is logged as a
rejected action with ``reason="plan_construction_raised"``. The action
is NOT added to the tree; statistics are unchanged.

Edge case pinned by impl plan §B9: an out-of-bounds ``target_path``
raises ``IndexError`` from ``_replace_at_path`` before any Node
construction.
"""
from __future__ import annotations

import warnings
from collections.abc import Sequence

from persistence.fact.db import DB
from persistence.fact.store import InMemoryStore
from persistence.plan import (
    Action,
    MCTSConfig,
    Node,
    SubstituteLeafAction,
    mcts_search,
)
from persistence.plan._mcts import _StaticEvaluator, _StaticExpander
from persistence.plan._mcts_datoms import _ATTR_OUTPUT, _ATTR_PHASE


def _leaf(prompt: str = "x") -> Node:
    return Node(tag=":leaf/predict", attrs={"prompt": prompt})


def _initial() -> Node:
    # Two children → valid target_paths are (0,) and (1,).
    return Node(
        tag=":plan/seq",
        attrs={"v": 1},
        children=(_leaf("A"), _leaf("B")),
    )


def test_substitute_with_out_of_range_target_path_emits_reject():
    """``target_path=(99,)`` raises IndexError → reject reason
    ``plan_construction_raised``. Action not added to tree.
    """
    initial = _initial()
    bad = SubstituteLeafAction(target_path=(99,), new_leaf=_leaf("BAD"))
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(bad, 1.0)],
    }
    expander = _StaticExpander(proposals)
    evaluator = _StaticEvaluator({initial.id: 0.5})

    db = DB(InMemoryStore())
    # All proposals fail, so the root has no children → "exhausted"
    # termination on iter 0; fine for this test.
    result = mcts_search(
        initial,
        expander=expander,
        evaluator=evaluator,
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=4, max_unique_plans=8),
        db=db,
    )

    by_entity: dict[str, dict[str, object]] = {}
    for d in db.log():
        if not d.e.startswith("mcts-iter/"):
            continue
        by_entity.setdefault(d.e, {})[d.a] = d.v

    rejects = [
        slots for slots in by_entity.values()
        if slots.get(_ATTR_PHASE) == "reject"
        and isinstance(slots.get(_ATTR_OUTPUT), dict)
        and slots[_ATTR_OUTPUT].get("reason") == "plan_construction_raised"  # type: ignore[union-attr]
    ]
    assert rejects, "no plan_construction_raised reject emitted"
    rec = rejects[0][_ATTR_OUTPUT]
    assert isinstance(rec, dict)
    assert rec["error_class"] in {"IndexError", "ValueError"}

    # Tree dump: no edges (the rejected action was never added).
    assert result.tree_dump == ()
    # Root has no surviving children → exhausted at iter 0.
    assert result.terminated_by == "exhausted"


def test_invalid_action_does_not_increment_visits():
    """Visit conservation: a rejected action emits a reject datom but
    leaves all root statistics at zero."""
    initial = _initial()
    bad = SubstituteLeafAction(target_path=(99,), new_leaf=_leaf("BAD"))
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(bad, 1.0)],
    }
    expander = _StaticExpander(proposals)
    evaluator = _StaticEvaluator({initial.id: 0.5})

    db = DB(InMemoryStore())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        result = mcts_search(
            initial,
            expander=expander,
            evaluator=evaluator,
            started_at_ms=1_000_000,
            config=MCTSConfig(max_iter=2, max_unique_plans=8),
            db=db,
        )
    assert result.unique_plans_visited == 1  # only the root
    assert result.root_q == 0.0
