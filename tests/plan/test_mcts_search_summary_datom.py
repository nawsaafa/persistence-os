"""B8 — ``:mcts/search`` summary datom (design §13).

Pins:
- search_id format: ``"mcts/<16-hex>"`` (regex pin).
- Iter 0's transact prepends the up-front summary group:
  ``mcts/initial-plan-id``, ``mcts/config-hash``, ``mcts/started-at``,
  ``mcts/schema-version``.
- Search-end summary group: ``mcts/winner-plan-id``, ``mcts/iter-count``,
  ``mcts/terminated-by``, ``mcts/finished-at``.
- ``mcts/started-at`` matches caller-supplied ``started_at_ms``.
- ``mcts/finished-at`` = ``started_at_ms + iter_count`` (synthetic-time
  discipline; design §13).
- ``mcts/schema-version`` matches the constant in ``_mcts_datoms``.
"""
from __future__ import annotations

import re

from persistence.fact.db import DB
from persistence.fact.store import InMemoryStore
from persistence.plan import (
    MCTSConfig,
    Node,
    SubstituteLeafAction,
    apply_action,
    mcts_search,
)
from persistence.plan._mcts import _StaticEvaluator, _StaticExpander
from persistence.plan._mcts_datoms import (
    _ATTR_CONFIG_HASH,
    _ATTR_FINISHED_AT,
    _ATTR_INITIAL_PLAN_ID,
    _ATTR_ITER_COUNT,
    _ATTR_SCHEMA_VERSION,
    _ATTR_STARTED_AT,
    _ATTR_TERMINATED_BY,
    _ATTR_WINNER_PLAN_ID,
    _REQUIRED_SEARCH_FINISH_ATTRS,
    _REQUIRED_SEARCH_START_ATTRS,
    _SCHEMA_VERSION,
)


_SEARCH_ID_RE = re.compile(r"^mcts/[0-9a-f]{16}$")


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


def _scan_search_summary(db: DB, search_id: str) -> dict[str, object]:
    """Return the merged ``mcts/search`` entity attribute → value map."""
    out: dict[str, object] = {}
    for d in db.log():
        if d.e != search_id:
            continue
        out[d.a] = d.v
    return out


# --- Test 1: search_id format --------------------------------------- #


def test_search_id_format_matches_mcts_16hex():
    initial = _initial()
    act = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf("A_sub"))
    plan_a = apply_action(initial, act)
    db = _make_db()
    result = mcts_search(
        initial,
        expander=_StaticExpander({initial.id: [(act, 1.0)]}),
        evaluator=_StaticEvaluator({initial.id: 0.5, plan_a.id: 0.7}),
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=2, max_unique_plans=8),
        db=db,
    )
    assert _SEARCH_ID_RE.match(result.search_id), (
        f"search_id {result.search_id!r} does not match mcts/<16-hex>"
    )


# --- Test 2: start summary group complete --------------------------- #


def test_search_summary_start_group_has_all_required_attrs():
    """Iter 0's transact prepends the start-summary group (4 attrs)."""
    initial = _initial()
    act = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf("A_sub"))
    plan_a = apply_action(initial, act)
    db = _make_db()
    started = 1_000_000
    result = mcts_search(
        initial,
        expander=_StaticExpander({initial.id: [(act, 1.0)]}),
        evaluator=_StaticEvaluator({initial.id: 0.5, plan_a.id: 0.7}),
        started_at_ms=started,
        config=MCTSConfig(max_iter=2, max_unique_plans=8),
        db=db,
    )
    summary = _scan_search_summary(db, result.search_id)
    for required in _REQUIRED_SEARCH_START_ATTRS:
        assert required in summary, (
            f"start-summary missing {required!r}; got {summary!r}"
        )

    assert summary[_ATTR_INITIAL_PLAN_ID] == initial.id
    assert summary[_ATTR_STARTED_AT] == started
    assert summary[_ATTR_SCHEMA_VERSION] == _SCHEMA_VERSION
    # config-hash is a 64-hex sha256 string.
    cfg_hash = summary[_ATTR_CONFIG_HASH]
    assert isinstance(cfg_hash, str) and len(cfg_hash) == 64


# --- Test 3: finish summary group complete -------------------------- #


def test_search_summary_finish_group_has_all_required_attrs():
    """Search-end transact appends the finish-summary group (4 attrs)."""
    initial = _initial()
    act = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf("A_sub"))
    plan_a = apply_action(initial, act)
    db = _make_db()
    started = 1_000_000
    result = mcts_search(
        initial,
        expander=_StaticExpander({initial.id: [(act, 1.0)]}),
        evaluator=_StaticEvaluator({initial.id: 0.5, plan_a.id: 0.7}),
        started_at_ms=started,
        config=MCTSConfig(max_iter=2, max_unique_plans=8),
        db=db,
    )
    summary = _scan_search_summary(db, result.search_id)
    for required in _REQUIRED_SEARCH_FINISH_ATTRS:
        assert required in summary, (
            f"finish-summary missing {required!r}; got {summary!r}"
        )

    assert summary[_ATTR_WINNER_PLAN_ID] == result.winner_plan_id
    assert summary[_ATTR_ITER_COUNT] == result.iter_count
    assert summary[_ATTR_TERMINATED_BY] == result.terminated_by
    assert summary[_ATTR_FINISHED_AT] == started + result.iter_count


# --- Test 4: db is None emits no datoms ----------------------------- #


def test_db_none_emits_zero_datoms():
    """``db=None`` short-circuits all transact calls."""
    initial = _initial()
    act = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf("A_sub"))
    plan_a = apply_action(initial, act)
    # Run without db; result must still be produced and identical.
    no_db = mcts_search(
        initial,
        expander=_StaticExpander({initial.id: [(act, 1.0)]}),
        evaluator=_StaticEvaluator({initial.id: 0.5, plan_a.id: 0.7}),
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=2, max_unique_plans=8),
    )
    db = _make_db()
    with_db = mcts_search(
        initial,
        expander=_StaticExpander({initial.id: [(act, 1.0)]}),
        evaluator=_StaticEvaluator({initial.id: 0.5, plan_a.id: 0.7}),
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=2, max_unique_plans=8),
        db=db,
    )
    # Determinism: same tree_dump regardless of db side-effects.
    assert no_db.tree_dump == with_db.tree_dump
    assert no_db.search_id == with_db.search_id
    assert no_db.winner_plan_id == with_db.winner_plan_id


# --- Test 5: started_at_ms validation ------------------------------- #


def test_started_at_ms_must_be_positive_int():
    """``started_at_ms`` must be a positive int — bool / float / 0 rejected."""
    initial = _initial()
    act = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf("A_sub"))
    expander = _StaticExpander({initial.id: [(act, 1.0)]})
    evaluator = _StaticEvaluator({initial.id: 0.5})
    config = MCTSConfig(max_iter=2)

    for bad in (0, -1, True, False, 1.5, "1000"):
        try:
            mcts_search(
                initial,
                expander=expander,
                evaluator=evaluator,
                started_at_ms=bad,  # type: ignore[arg-type]
                config=config,
            )
        except ValueError:
            continue
        raise AssertionError(
            f"started_at_ms={bad!r} accepted; expected ValueError"
        )
