"""B9 — evaluator returns NaN / +Inf / -Inf / bool → loop reject (design §14).

The MCTS loop boundary rejects non-finite scores from the evaluator with
``phase="reject"`` + ``reason="evaluator_returned_non_finite"`` per
design §14. BACKUP is skipped; the search continues. ``True``/``False``
are also rejected (Stream A W1.B / G4 anti-pattern: ``isinstance(True,
int) is True`` — bool would silently pass an ``int|float`` check
otherwise; design §9 + ``_is_finite_score`` pin in ``_mcts.py``).

Together with ``test_mcts_evaluator_raises.py`` and
``test_mcts_evaluator_returns_none.py``, this file closes design §14's
evaluator-side reject paths.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from persistence.fact.db import DB
from persistence.fact.store import InMemoryStore
from persistence.plan import (
    Action,
    LLMJudgeEvaluator,
    MCTSConfig,
    Node,
    SubstituteLeafAction,
    apply_action,
    mcts_search,
)
from persistence.plan._mcts import _StaticExpander
from persistence.plan._mcts_datoms import (
    _ATTR_OUTPUT,
    _ATTR_PHASE,
)


# --- Fixtures ----------------------------------------------------------- #


def _leaf(prompt: str = "x") -> Node:
    return Node(tag=":leaf/predict", attrs={"prompt": prompt})


def _initial() -> Node:
    return Node(
        tag=":plan/seq",
        attrs={"v": 1},
        children=(_leaf("A"), _leaf("B")),
    )


def _make_db() -> DB:
    return DB(InMemoryStore())


def _reject_outputs_with_reason(db: DB, reason: str) -> list[dict]:
    """Collect ``mcts/output`` payloads for every ``phase="reject"`` row
    whose ``reason`` matches.
    """
    by_entity: dict[str, dict[str, object]] = {}
    for d in db.log():
        if not d.e.startswith("mcts-iter/"):
            continue
        by_entity.setdefault(d.e, {})[d.a] = d.v
    matches: list[dict] = []
    for slots in by_entity.values():
        if slots.get(_ATTR_PHASE) != "reject":
            continue
        out = slots.get(_ATTR_OUTPUT)
        if isinstance(out, dict) and out.get("reason") == reason:
            matches.append(out)
    return matches


# --- NaN / +Inf / -Inf reject ------------------------------------------ #


def _run_with_score(score: float, db: DB) -> None:
    """Drive ``mcts_search`` with an evaluator that returns ``score`` once,
    then keeps returning it. The MCTS loop boundary rejects every call;
    ``terminated_by="all_evaluations_failed"`` after ``max_iter`` attempts.
    """
    initial = _initial()
    new_a = _leaf("A_sub")
    act = SubstituteLeafAction(target_path=(0,), new_leaf=new_a)
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act, 1.0)],
    }
    # All non-finite returns hit `_is_finite_score` rejection → BACKUP
    # skipped. We use LLMJudgeEvaluator (production wiring) so the
    # boundary check is the loop's, not the stub's.
    evaluator = LLMJudgeEvaluator(provider=lambda _plan: score)
    expander = _StaticExpander(proposals)
    mcts_search(
        initial,
        expander=expander,
        evaluator=evaluator,
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=2, max_unique_plans=8),
        db=db,
    )


def test_evaluator_returning_nan_emits_evaluator_returned_non_finite_reject():
    """``NaN`` evaluator return → exactly one reject datom per attempt."""
    db = _make_db()
    _run_with_score(float("nan"), db)

    matches = _reject_outputs_with_reason(db, "evaluator_returned_non_finite")
    assert matches, "no evaluator_returned_non_finite reject datom emitted"
    # Each match should record the raw score's repr for forensics.
    for rec in matches:
        assert "raw_score_repr" in rec
        assert "nan" in rec["raw_score_repr"].lower()
        # Action-side payload empty (evaluator-side reject).
        assert rec["action_hash"] is None
        assert rec["action_kind"] is None
        assert rec["action_payload"] is None


def test_evaluator_returning_pos_inf_emits_non_finite_reject():
    """+Inf evaluator return → ``evaluator_returned_non_finite`` reject."""
    db = _make_db()
    _run_with_score(float("inf"), db)

    matches = _reject_outputs_with_reason(db, "evaluator_returned_non_finite")
    assert matches
    assert all("inf" in m["raw_score_repr"].lower() for m in matches)


def test_evaluator_returning_neg_inf_emits_non_finite_reject():
    """-Inf evaluator return → ``evaluator_returned_non_finite`` reject."""
    db = _make_db()
    _run_with_score(-math.inf, db)

    matches = _reject_outputs_with_reason(db, "evaluator_returned_non_finite")
    assert matches
    assert all("inf" in m["raw_score_repr"].lower() for m in matches)


def test_evaluator_returning_bool_true_emits_non_finite_reject():
    """``True`` is rejected (Stream A W1.B / G4 anti-pattern preempted).

    ``isinstance(True, int)`` is ``True`` — without an explicit bool
    check the loop would silently let ``True`` through as ``1.0``.
    ``_is_finite_score`` rejects ``bool`` even when finite-numeric.
    """
    db = _make_db()
    # The provider type signature claims ``float`` — passing ``True``
    # exercises the boundary that the loop CANNOT trust caller types
    # (W1.B precedent: bool isinstance is fragile).
    _run_with_score(True, db)  # type: ignore[arg-type]
    matches = _reject_outputs_with_reason(db, "evaluator_returned_non_finite")
    assert matches, "True (bool) was not rejected at the MCTS loop boundary"
    for rec in matches:
        assert rec["raw_score_repr"] == "True"


def test_non_finite_reject_skips_backup_no_visit_increment():
    """A rejected evaluator return does NOT increment any edge's visits.

    Pin the §14 invariant: BACKUP is skipped on reject. Counter pin: a
    fully-NaN search produces ``terminated_by="all_evaluations_failed"``
    with zero edge visits, which is the only way the loop signals
    "winner = initial_plan, no signal".
    """
    initial = _initial()
    new_a = _leaf("A_sub")
    act = SubstituteLeafAction(target_path=(0,), new_leaf=new_a)
    plan_a = apply_action(initial, act)
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act, 1.0)],
        plan_a.id: [],  # no further expansion
    }
    expander = _StaticExpander(proposals)
    evaluator = LLMJudgeEvaluator(provider=lambda _plan: float("nan"))
    db = _make_db()
    result = mcts_search(
        initial,
        expander=expander,
        evaluator=evaluator,
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=4, max_unique_plans=8),
        db=db,
    )
    # Every edge visits == 0 (BACKUP never ran).
    for _parent, _child, _hash, visits, q in result.tree_dump:
        assert visits == 0
        assert q == 0.0
    assert result.terminated_by == "all_evaluations_failed"
    assert result.winner_plan_id == initial.id
