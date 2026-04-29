"""B9 — ``ComposeWithSkillAction`` cycle detection (design §14, W2 MINOR-5).

Per design §14: when ``skill_plan`` already contains the candidate
plan's content-hash as a subtree, composition would graft the candidate
inside a copy of itself — a cycle in the search reach. Cheap subtree-
hash scan rejects the action with ``reason="compose_creates_cycle"``.

Surgical fix in ``_apply_compose_with_skill``: a ``_PlanCycleDetected``
``ValueError`` subclass is raised when ``plan.id`` is found in
``skill_plan``'s descendants; ``_classify_apply_failure`` maps it to
the design §14 reason tag.
"""
from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class _StubPromotionRecord:
    promotion_id: str


def _frozen_clock(start_ms: int = 1_000_000):
    counter = [start_ms]

    def _now() -> datetime:
        ts = datetime.fromtimestamp(counter[0] / 1000, tz=timezone.utc)
        counter[0] += 1
        return ts

    return _now


def _leaf(prompt: str = "x") -> Node:
    return Node(tag=":leaf/predict", attrs={"prompt": prompt})


def test_compose_with_skill_containing_candidate_subtree_rejects_with_cycle_reason():
    """Build a candidate ``initial`` and a skill_plan that contains
    ``initial`` as a subtree. ``ComposeWithSkillAction`` must reject
    with ``reason="compose_creates_cycle"``.
    """
    # Candidate: a small seq with two leaves.
    initial = Node(
        tag=":plan/seq",
        attrs={"v": 1},
        children=(_leaf("A"), _leaf("B")),
    )
    # Skill plan: a wrapper :plan/seq whose first child IS the candidate.
    # Composing this skill at any path would graft the candidate inside
    # a copy of itself.
    skill_plan = Node(
        tag=":skill/wrap",
        attrs={"v": 1},
        children=(initial, _leaf("C")),
    )
    skill_db = DB(InMemoryStore(), clock=_frozen_clock())
    library = SkillLibrary(skill_db)
    skill_id = library.register(
        skill_plan,
        _StubPromotionRecord(promotion_id="p-1"),
        registered_at_ms=42,
    )

    # The expander proposes composing the cycle-inducing skill.
    act = ComposeWithSkillAction(target_path=(0,), skill_id=skill_id)
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
            skill_library=library,
            db=db,
        )

    by_entity: dict[str, dict[str, object]] = {}
    for d in db.log():
        if not d.e.startswith("mcts-iter/"):
            continue
        by_entity.setdefault(d.e, {})[d.a] = d.v

    cycle_rejects = [
        slots for slots in by_entity.values()
        if slots.get(_ATTR_PHASE) == "reject"
        and isinstance(slots.get(_ATTR_OUTPUT), dict)
        and slots[_ATTR_OUTPUT].get("reason") == "compose_creates_cycle"  # type: ignore[union-attr]
    ]
    assert cycle_rejects, "no compose_creates_cycle reject emitted"
    rec = cycle_rejects[0][_ATTR_OUTPUT]
    assert isinstance(rec, dict)
    # The action payload survives in the reject record.
    assert rec["action_kind"] == "ComposeWithSkillAction"
    # No surviving children; root exhausted.
    assert result.tree_dump == ()


def test_compose_without_cycle_succeeds():
    """Sanity pin: a skill_plan that does NOT contain the candidate as a
    subtree composes without raising the cycle guard.
    """
    initial = Node(
        tag=":plan/seq",
        attrs={"v": 1},
        children=(_leaf("A"),),
    )
    # Skill plan structurally distinct — no candidate subtree.
    skill_plan = Node(
        tag=":skill/no-cycle",
        attrs={"v": 1},
        children=(_leaf("Z"),),
    )
    skill_db = DB(InMemoryStore(), clock=_frozen_clock())
    library = SkillLibrary(skill_db)
    skill_id = library.register(
        skill_plan,
        _StubPromotionRecord(promotion_id="p-1"),
        registered_at_ms=42,
    )
    act = ComposeWithSkillAction(target_path=(0,), skill_id=skill_id)
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
            skill_library=library,
            db=db,
        )
    # An edge survived → the compose succeeded.
    assert result.tree_dump
