"""B9 — evaluator raises → ``phase="reject"`` with ``error_class`` set (design §14).

Per design §14:
- Iteration recorded with ``error_class`` / ``error_repr`` populated.
- Expansion not kept in the tree's BACKUP statistics.
- Iteration counter advances; search continues.

Plus: if EVERY iteration's evaluator call raises,
``terminated_by="all_evaluations_failed"`` and a ``UserWarning`` fires
on termination (design §14 vacuous-truth pin).
"""
from __future__ import annotations

import warnings
from collections.abc import Sequence

import pytest

from persistence.fact.db import DB
from persistence.fact.store import InMemoryStore
from persistence.plan import (
    Action,
    LLMJudgeEvaluator,
    MCTSConfig,
    Node,
    SubstituteLeafAction,
    mcts_search,
)
from persistence.plan._mcts import _StaticExpander
from persistence.plan._mcts_datoms import (
    _ATTR_ERROR_CLASS,
    _ATTR_ERROR_REPR,
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


def _raising_evaluator(exc_cls: type[BaseException], msg: str) -> LLMJudgeEvaluator:
    def _provider(_plan: Node) -> float:
        raise exc_cls(msg)

    return LLMJudgeEvaluator(provider=_provider)


# --- Test: a single iteration's raise emits a reject with error class --- #


def test_evaluator_raise_emits_phase_reject_with_error_class_and_repr():
    """A raising evaluator → reject row carries error_class/error_repr.

    The MCTS loop catches Exception; the iteration becomes a
    ``phase="reject"`` row with ``mcts/error-class`` =
    ``RuntimeError`` and ``mcts/error-repr`` containing the message.
    """
    initial = _initial()
    act = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf("A_sub"))
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act, 1.0)],
    }
    expander = _StaticExpander(proposals)
    evaluator = _raising_evaluator(RuntimeError, "boom")

    db = DB(InMemoryStore())
    # Suppress the all_evaluations_failed UserWarning at the end of the
    # search — irrelevant to this test's assertion.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        mcts_search(
            initial,
            expander=expander,
            evaluator=evaluator,
            started_at_ms=1_000_000,
            config=MCTSConfig(max_iter=2, max_unique_plans=8),
            db=db,
        )

    # Group datoms by iter entity.
    by_entity: dict[str, dict[str, object]] = {}
    for d in db.log():
        if not d.e.startswith("mcts-iter/"):
            continue
        by_entity.setdefault(d.e, {})[d.a] = d.v

    eval_rejects = [
        slots for slots in by_entity.values()
        if slots.get(_ATTR_PHASE) == "reject"
        and isinstance(slots.get(_ATTR_OUTPUT), dict)
        and slots[_ATTR_OUTPUT].get("reason") == "evaluator_raised"  # type: ignore[union-attr]
    ]
    assert eval_rejects, (
        "no phase=reject row with reason=evaluator_raised found"
    )
    for slots in eval_rejects:
        assert slots[_ATTR_ERROR_CLASS] == "RuntimeError"
        assert "boom" in str(slots[_ATTR_ERROR_REPR])
        out = slots[_ATTR_OUTPUT]
        assert isinstance(out, dict)
        assert out["error_class"] == "RuntimeError"
        assert "boom" in out["error_repr"]


def test_evaluator_raise_does_not_propagate_search_continues():
    """An evaluator raise does NOT raise out of ``mcts_search``."""
    initial = _initial()
    act = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf("A_sub"))
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act, 1.0)],
    }
    evaluator = _raising_evaluator(ZeroDivisionError, "1/0")
    db = DB(InMemoryStore())

    # Search returns normally; no exception escapes.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        result = mcts_search(
            initial,
            expander=_StaticExpander(proposals),
            evaluator=evaluator,
            started_at_ms=1_000_000,
            config=MCTSConfig(max_iter=3, max_unique_plans=8),
            db=db,
        )
    assert result.terminated_by == "all_evaluations_failed"

    # Verify the rejected iteration recorded ZeroDivisionError.
    by_entity: dict[str, dict[str, object]] = {}
    for d in db.log():
        if not d.e.startswith("mcts-iter/"):
            continue
        by_entity.setdefault(d.e, {})[d.a] = d.v
    err_classes = {
        slots.get(_ATTR_ERROR_CLASS) for slots in by_entity.values()
    }
    assert "ZeroDivisionError" in err_classes


def test_all_evaluations_fail_emits_user_warning_and_terminated_by():
    """Every iter raises → ``UserWarning`` + ``terminated_by="all_evaluations_failed"``."""
    initial = _initial()
    act = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf("A_sub"))
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act, 1.0)],
    }
    evaluator = _raising_evaluator(ValueError, "nope")
    db = DB(InMemoryStore())

    with pytest.warns(UserWarning, match="all_evaluations_failed"):
        result = mcts_search(
            initial,
            expander=_StaticExpander(proposals),
            evaluator=evaluator,
            started_at_ms=1_000_000,
            config=MCTSConfig(max_iter=2, max_unique_plans=8),
            db=db,
        )
    assert result.terminated_by == "all_evaluations_failed"
    assert result.winner_plan_id == initial.id  # falls back to initial
