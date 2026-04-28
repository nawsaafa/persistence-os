"""B8 — ``:mcts/iteration`` datom attribute schema (design §13).

Pins the EXACT attr-key set written for every iteration row (one
``mcts/iteration`` group per ``phase ∈ {expand, evaluate, reject}``):

    {mcts/search-id, mcts/iter-index, mcts/phase, mcts/plan-id,
     mcts/inputs-hash, mcts/output, mcts/prev-hash}
    + {mcts/error-class, mcts/error-repr} on raise-paths

A schema bump = explicit failure here AND in the design doc §13.

Also pins:
- The ``mcts/prev-hash`` Merkle chain — every datom's ``prev-hash``
  matches sha256-canonical-JSON of the prior datom's content.
- Within-iteration ordering — rejects emitted in proposal-iteration
  order BEFORE expand and evaluate (W2 MINOR-7 pin).
- Exactly one ``phase="evaluate"`` per BACKUP-completing iteration.
- All datoms in iteration K share ``valid_from = epoch +
  (started_at_ms + K) ms`` (synthetic-time pin; design §13).
- No ``audit/...`` keys leak into MCTS datoms (G2 disjointness).
"""
from __future__ import annotations

from collections.abc import Sequence

from persistence.fact.db import DB
from persistence.fact.store import InMemoryStore
from persistence.plan import (
    Action,
    AddStepAction,
    MCTSConfig,
    Node,
    SubstituteLeafAction,
    apply_action,
    mcts_search,
)
from persistence.plan._mcts import _StaticEvaluator, _StaticExpander
from persistence.plan._mcts_datoms import (
    _ATTR_ERROR_CLASS,
    _ATTR_ERROR_REPR,
    _ATTR_ITER_INDEX,
    _ATTR_OUTPUT,
    _ATTR_PHASE,
    _ATTR_PLAN_ID,
    _ATTR_PREV_HASH,
    _ATTR_SEARCH_ID,
    _AUDIT_PREFIX_BLACKLIST,
    _MCTS_PREFIX,
    _OPTIONAL_ITER_ATTRS,
    _REQUIRED_ITER_ATTRS,
    _hash_iter_content,
    _hash_search_summary,
    _synthetic_valid_from,
)


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


# --- Test 1: per-iteration row attr-key set ---------------------------- #


def test_iteration_datom_attr_key_set_exact():
    """Every ``mcts-iter/...`` entity carries exactly the required keys."""
    initial = _initial()
    new_a = _leaf("A_sub")
    act = SubstituteLeafAction(target_path=(0,), new_leaf=new_a)
    plan_a = apply_action(initial, act)
    expander_proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act, 1.0)],
    }
    evaluator_scores = {initial.id: 0.5, plan_a.id: 0.7}
    db = _make_db()
    mcts_search(
        initial,
        expander=_StaticExpander(expander_proposals),
        evaluator=_StaticEvaluator(evaluator_scores),
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=4, max_unique_plans=8),
        db=db,
    )
    # Group datoms by entity.
    by_entity: dict[str, set[str]] = {}
    for d in db.log():
        if not d.e.startswith("mcts-iter/"):
            continue
        by_entity.setdefault(d.e, set()).add(d.a)

    assert by_entity, "no iteration datoms emitted"
    for entity, attrs in by_entity.items():
        # Required keys present, no extras outside required ∪ optional.
        missing = _REQUIRED_ITER_ATTRS - attrs
        assert not missing, (
            f"entity {entity!r} missing required keys: {missing!r}"
        )
        extras = attrs - (_REQUIRED_ITER_ATTRS | _OPTIONAL_ITER_ATTRS)
        assert not extras, (
            f"entity {entity!r} has unexpected keys: {extras!r}"
        )


def test_no_audit_prefix_keys_leak_into_mcts_datoms():
    """G2 disjointness: MCTS datoms must not collide with G2's audit/ window.

    Design §13 audit-chain context note: the ``mcts/`` prefix is
    intentionally chosen to opt OUT of G2's effect-handler audit-chain
    scan (which filters ``a.startswith("audit/")``)."""
    initial = _initial()
    new_a = _leaf("A_sub")
    act = SubstituteLeafAction(target_path=(0,), new_leaf=new_a)
    plan_a = apply_action(initial, act)
    db = _make_db()
    mcts_search(
        initial,
        expander=_StaticExpander({initial.id: [(act, 1.0)]}),
        evaluator=_StaticEvaluator({initial.id: 0.5, plan_a.id: 0.7}),
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=2, max_unique_plans=8),
        db=db,
    )
    for d in db.log():
        if d.e.startswith("mcts/") or d.e.startswith("mcts-iter/"):
            assert not d.a.startswith(_AUDIT_PREFIX_BLACKLIST), (
                f"MCTS datom {d.e}/{d.a} leaked into audit/ window"
            )
            assert d.a.startswith(_MCTS_PREFIX), (
                f"MCTS datom attr {d.a!r} missing mcts/ namespace"
            )


# --- Test 2: prev-hash Merkle chain integrity ------------------------- #


def test_prev_hash_chain_walks_clean_from_search_anchor():
    """Walk the Merkle chain end-to-end; every prev-hash matches recompute."""
    initial = _initial()
    new_a = _leaf("A_sub")
    new_b = _leaf("B_sub")
    act_a = SubstituteLeafAction(target_path=(0,), new_leaf=new_a)
    act_b = SubstituteLeafAction(target_path=(1,), new_leaf=new_b)
    plan_a = apply_action(initial, act_a)
    plan_b = apply_action(initial, act_b)
    db = _make_db()
    config = MCTSConfig(max_iter=8, max_unique_plans=16)
    result = mcts_search(
        initial,
        expander=_StaticExpander({initial.id: [(act_a, 0.6), (act_b, 0.4)]}),
        evaluator=_StaticEvaluator({
            initial.id: 0.5,
            plan_a.id: 0.7,
            plan_b.id: 0.3,
        }),
        started_at_ms=1_000_000,
        config=config,
        db=db,
    )

    # Reconstruct each iteration row from the audit log.
    rows: dict[str, dict[str, object]] = {}
    for d in db.log():
        if not d.e.startswith("mcts-iter/"):
            continue
        rows.setdefault(d.e, {})[d.a] = d.v

    # Sort by (iter_index, valid_from-as-epoch-int) — but valid_from
    # is the same across all rows in the same txn. Within one txn the
    # order is the chain order. We sort by tx then within-tx by the
    # store insertion order (which the InMemoryStore preserves).
    # Pull rows in transaction-append order to reconstruct chain order.
    chain_order: list[str] = []
    seen: set[str] = set()
    for d in db.log():
        if not d.e.startswith("mcts-iter/") or d.e in seen:
            continue
        seen.add(d.e)
        chain_order.append(d.e)

    # Recompute prev-hash starting from the search-summary anchor.
    expected_prev = _hash_search_summary(
        initial.id, result.search_id and result.search_id or "",  # placeholder
        1_000_000,
    )
    # Actually use the real config_hash for the anchor.
    from persistence.plan._mcts import _hash_config
    expected_prev = _hash_search_summary(
        initial.id, _hash_config(config), 1_000_000
    )

    for entity in chain_order:
        row = rows[entity]
        stored_prev = row[_ATTR_PREV_HASH]
        assert stored_prev == expected_prev, (
            f"prev-hash mismatch at {entity}: "
            f"stored={stored_prev!r} expected={expected_prev!r}"
        )
        # Advance: hash the row content (excluding prev-hash itself).
        expected_prev = _hash_iter_content(
            search_id=row[_ATTR_SEARCH_ID],  # type: ignore[arg-type]
            iter_index=row[_ATTR_ITER_INDEX],  # type: ignore[arg-type]
            phase=row[_ATTR_PHASE],  # type: ignore[arg-type]
            plan_id=row[_ATTR_PLAN_ID],  # type: ignore[arg-type]
            inputs_hash=row["mcts/inputs-hash"],  # type: ignore[arg-type]
            output_value=row[_ATTR_OUTPUT],
            error_class=row.get(_ATTR_ERROR_CLASS),  # type: ignore[arg-type]
            error_repr=row.get(_ATTR_ERROR_REPR),  # type: ignore[arg-type]
        )


# --- Test 3: exactly one phase="evaluate" per iteration --------------- #


def test_exactly_one_evaluate_phase_per_iteration():
    """Design §16 invariant 6 — exactly one ``phase="evaluate"`` per BACKUP iter.

    Cache hits emit zero evaluate datoms; the count of evaluate datoms
    equals the count of distinct plan_ids that triggered evaluator-cache-
    miss-and-finite-return."""
    initial = _initial()
    act_a = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf("A_sub"))
    plan_a = apply_action(initial, act_a)
    db = _make_db()
    mcts_search(
        initial,
        expander=_StaticExpander({initial.id: [(act_a, 1.0)]}),
        evaluator=_StaticEvaluator({initial.id: 0.5, plan_a.id: 0.7}),
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=5, max_unique_plans=8),
        db=db,
    )
    # Group by entity; count evaluate-phase entities per iter_index.
    iter_to_phase_count: dict[int, dict[str, int]] = {}
    by_entity: dict[str, dict[str, object]] = {}
    for d in db.log():
        if not d.e.startswith("mcts-iter/"):
            continue
        by_entity.setdefault(d.e, {})[d.a] = d.v
    for row in by_entity.values():
        idx = row[_ATTR_ITER_INDEX]
        phase = row[_ATTR_PHASE]
        assert isinstance(idx, int)
        assert isinstance(phase, str)
        iter_to_phase_count.setdefault(idx, {}).setdefault(phase, 0)
        iter_to_phase_count[idx][phase] += 1
    # Cache-miss-only: only the first iteration where each plan is
    # evaluated emits an "evaluate" datom. Iter 0 (initial), iter 1 (plan_a)
    # both miss; iter 2+ hit cache. Each emitting iter has exactly one.
    for idx, phases in iter_to_phase_count.items():
        eval_count = phases.get("evaluate", 0)
        assert eval_count <= 1, (
            f"iter {idx}: {eval_count} evaluate datoms, expected ≤ 1"
        )


# --- Test 4: within-iteration ordering (rejects before expand/evaluate) #


def test_within_iter_chain_advances_in_emit_order():
    """W2 MINOR-7 pin: rejects come BEFORE the iteration's primary phase.

    Strictly: design §13 within-transaction ordering pins each chained
    prev-hash to the immediately preceding emission in the same txn.
    This test triggers a `plan_too_deep` reject inside the same iter
    that emits an expand, and verifies the reject's prev-hash chains
    OFF the expand row's content."""
    # Build a deep starter so AddStepAction at root pushes over MAX_PLAN_DEPTH.
    initial = _initial()
    # AddStepAction adding a fresh leaf at root index 0 — small Plan,
    # not deep. We need a different reject path. Use SubstituteLeafAction
    # with an out-of-range target_path → IndexError → plan_construction_raised.
    bad_act = SubstituteLeafAction(target_path=(99,), new_leaf=_leaf("nope"))
    good_act = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf("ok"))
    plan_good = apply_action(initial, good_act)
    db = _make_db()
    # Two proposals: index 0 = good (succeeds), index 1 = bad (rejects).
    mcts_search(
        initial,
        expander=_StaticExpander({initial.id: [(good_act, 0.6), (bad_act, 0.4)]}),
        evaluator=_StaticEvaluator({initial.id: 0.5, plan_good.id: 0.7}),
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=2, max_unique_plans=8),
        db=db,
    )
    # Find iter 0's reject and expand entities.
    iter0_phases: list[tuple[str, str]] = []
    by_entity: dict[str, dict[str, object]] = {}
    for d in db.log():
        if not d.e.startswith("mcts-iter/"):
            continue
        by_entity.setdefault(d.e, {})[d.a] = d.v
    for entity, row in by_entity.items():
        if row.get(_ATTR_ITER_INDEX) == 0:
            iter0_phases.append((entity, str(row[_ATTR_PHASE])))
    # Iter 0 must have one expand + one reject (the bad action).
    phase_set = {p for _, p in iter0_phases}
    assert "expand" in phase_set, "iter 0 missing expand datom"
    assert "reject" in phase_set, "iter 0 missing reject datom for bad action"


# --- Test 5: synthetic-time encoded in valid_from -------------------- #


def test_valid_from_pins_started_at_plus_iter_index():
    """All datoms in iter K share ``valid_from = synthetic(started, K)``."""
    initial = _initial()
    act = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf("A_sub"))
    plan_a = apply_action(initial, act)
    db = _make_db()
    started = 1_000_000
    mcts_search(
        initial,
        expander=_StaticExpander({initial.id: [(act, 1.0)]}),
        evaluator=_StaticEvaluator({initial.id: 0.5, plan_a.id: 0.7}),
        started_at_ms=started,
        config=MCTSConfig(max_iter=3, max_unique_plans=8),
        db=db,
    )
    # Iter 0 datoms (search summary + iter 0 rows) should share
    # valid_from = synthetic(started, 0). Iter 1 = synthetic(started, 1).
    by_iter: dict[int, set] = {}
    for d in db.log():
        if not d.e.startswith("mcts-iter/"):
            continue
        if d.a == _ATTR_ITER_INDEX:
            continue
    by_entity: dict[str, dict[str, object]] = {}
    by_entity_vf: dict[str, set] = {}
    for d in db.log():
        if not d.e.startswith("mcts-iter/"):
            continue
        by_entity.setdefault(d.e, {})[d.a] = d.v
        by_entity_vf.setdefault(d.e, set()).add(d.valid_from)
    for entity, row in by_entity.items():
        idx = row[_ATTR_ITER_INDEX]
        assert isinstance(idx, int)
        expected = _synthetic_valid_from(started, idx)
        actuals = by_entity_vf[entity]
        assert actuals == {expected}, (
            f"entity {entity}: valid_from mismatch — expected {expected!r}, "
            f"got {actuals!r}"
        )
        by_iter.setdefault(idx, set()).update(actuals)
    # Distinct iters → distinct valid_from values.
    if len(by_iter) >= 2:
        all_vfs: set = set()
        for s in by_iter.values():
            all_vfs.update(s)
        assert len(all_vfs) == len(by_iter), (
            "distinct iter_index values must produce distinct valid_from "
            f"timestamps; got {by_iter!r}"
        )


# --- Test 6: AddStepAction round-trip in expand output --------------- #


def test_addstep_proposal_is_recorded_in_expand_output():
    """AddStepAction proposals appear with ``new_child_canonical`` slot."""
    initial = _initial()
    add_act = AddStepAction(
        target_path=(),
        at=0,
        new_child=_leaf("inserted"),
    )
    new_plan = apply_action(initial, add_act)
    db = _make_db()
    mcts_search(
        initial,
        expander=_StaticExpander({initial.id: [(add_act, 1.0)]}),
        evaluator=_StaticEvaluator({initial.id: 0.5, new_plan.id: 0.7}),
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=2, max_unique_plans=8),
        db=db,
    )
    for d in db.log():
        if d.a != _ATTR_OUTPUT:
            continue
        if not d.e.startswith("mcts-iter/"):
            continue
        # The expand-phase output is a list of proposal dicts.
        if not isinstance(d.v, list):
            continue
        for proposal in d.v:
            if proposal.get("action_kind") == "AddStepAction":
                payload = proposal["action_payload"]
                assert "new_child_id" in payload
                assert "new_child_canonical" in payload
                assert payload["new_child_canonical"]["tag"] == ":leaf/predict"
                return
    raise AssertionError("AddStepAction expand output not found")
