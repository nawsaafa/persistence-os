"""B9 — ``ComposeWithSkillAction`` for unregistered ``skill_id`` (design §14).

Two cases share the same reject ``reason`` per design §12:
1. ``skill_library`` is provided but does not contain the ``skill_id``.
2. ``skill_library=None`` (the "no library" case is subsumed).

Both produce one ``phase="reject"`` datom with
``reason="skill_not_registered"``.
"""
from __future__ import annotations

import warnings
from collections.abc import Sequence
from datetime import datetime, timezone

from persistence.fact.db import DB
from persistence.fact.store import InMemoryStore
from persistence.plan import (
    Action,
    ComposeWithSkillAction,
    MCTSConfig,
    Node,
    SkillLibrary,
    mcts_search,
)
from persistence.plan._mcts import _StaticEvaluator, _StaticExpander
from persistence.plan._mcts_datoms import _ATTR_OUTPUT, _ATTR_PHASE


def _leaf(prompt: str = "x") -> Node:
    return Node(tag=":leaf/predict", attrs={"prompt": prompt})


def _initial() -> Node:
    return Node(
        tag=":plan/seq",
        attrs={"v": 1},
        children=(_leaf("A"), _leaf("B")),
    )


def _frozen_clock(start_ms: int = 1_000_000):
    counter = [start_ms]

    def _now() -> datetime:
        ts = datetime.fromtimestamp(counter[0] / 1000, tz=timezone.utc)
        counter[0] += 1
        return ts

    return _now


def _skill_not_registered_rejects(db: DB) -> list[dict]:
    by_entity: dict[str, dict[str, object]] = {}
    for d in db.log():
        if not d.e.startswith("mcts-iter/"):
            continue
        by_entity.setdefault(d.e, {})[d.a] = d.v
    out: list[dict] = []
    for slots in by_entity.values():
        if slots.get(_ATTR_PHASE) != "reject":
            continue
        rec = slots.get(_ATTR_OUTPUT)
        if isinstance(rec, dict) and rec.get("reason") == "skill_not_registered":
            out.append(rec)
    return out


def test_compose_with_unregistered_skill_id_via_library_rejects():
    """A skill_library exists but does NOT contain the action's skill_id.

    The reject reason is ``skill_not_registered`` (the lookup miss is
    detected by ``_apply_compose_with_skill``'s explicit
    ``looked_up is None`` check).
    """
    initial = _initial()
    act = ComposeWithSkillAction(target_path=(0,), skill_id="skill/nope")
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act, 1.0)],
    }

    # An EMPTY skill_library — no skills registered.
    skill_db = DB(InMemoryStore(), clock=_frozen_clock())
    library = SkillLibrary(skill_db)

    expander = _StaticExpander(proposals)
    evaluator = _StaticEvaluator({initial.id: 0.5})

    db = DB(InMemoryStore())
    result = mcts_search(
        initial,
        expander=expander,
        evaluator=evaluator,
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=2, max_unique_plans=8),
        skill_library=library,
        db=db,
    )

    rejects = _skill_not_registered_rejects(db)
    assert rejects, "no skill_not_registered reject emitted (library case)"
    # Tree exhausted: action rejected, no children survive.
    assert result.terminated_by == "exhausted"


def test_compose_with_skill_library_none_rejects_with_same_reason():
    """``skill_library=None`` subsumes the unregistered case (design §12)."""
    initial = _initial()
    act = ComposeWithSkillAction(target_path=(0,), skill_id="skill/nope")
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act, 1.0)],
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
            skill_library=None,
            db=db,
        )

    rejects = _skill_not_registered_rejects(db)
    assert rejects, (
        "no skill_not_registered reject emitted (skill_library=None case)"
    )
    assert result.terminated_by == "exhausted"
    assert result.tree_dump == ()
