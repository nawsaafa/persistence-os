"""B8 — ``phase="expand"`` output payload schema (R2 round-3 followup).

Pins the inner ``mcts/output`` payload shape on every ``phase="expand"``
iteration row. This is the W2 M4 closure: a future regression that
removes ``new_leaf_canonical`` / ``new_child_canonical`` while leaving
``new_leaf_id`` / ``new_child_id`` in place would silently break Prop 6
(replay reconstructibility) — the hashes alone are one-way, so a Node
synthesized fresh by an LLMExpander would be unrecoverable from the
audit log alone.

Test catches that regression by checking BOTH ``_id`` AND ``_canonical``
are present and round-trip via ``Node.from_canonical(...).id == _id``.
"""
from __future__ import annotations

from collections.abc import Sequence

from persistence.fact.db import DB
from persistence.fact.store import InMemoryStore
from persistence.plan import (
    Action,
    AddStepAction,
    ComposeWithSkillAction,
    MCTSConfig,
    Node,
    SubstituteLeafAction,
    apply_action,
    mcts_search,
)
from persistence.plan._mcts import _PRIOR_TOL, _StaticEvaluator, _StaticExpander
from persistence.plan._mcts_datoms import (
    _ATTR_OUTPUT,
    _ATTR_PHASE,
    _node_from_canonical,
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


def _scan_expand_outputs(db: DB) -> list[list[dict]]:
    """Collect every ``phase="expand"`` ``mcts/output`` payload in order."""
    by_entity: dict[str, dict[str, object]] = {}
    for d in db.log():
        if not d.e.startswith("mcts-iter/"):
            continue
        by_entity.setdefault(d.e, {})[d.a] = d.v
    out: list[list[dict]] = []
    for row in by_entity.values():
        if row.get(_ATTR_PHASE) == "expand":
            payload = row[_ATTR_OUTPUT]
            assert isinstance(payload, list), (
                f"expand output is {type(payload).__name__}, expected list"
            )
            out.append(payload)
    return out


# --- Test 1: SubstituteLeafAction proposal carries canonical Node bytes ---


def test_substitute_leaf_proposal_round_trips_canonical_to_id():
    """``new_leaf_canonical`` rebuilds the exact Node whose id matches.

    Load-bearing for Prop 6: the Node bytes survive in the audit log.
    """
    initial = _initial()
    new_leaf = Node(
        tag=":leaf/predict",
        attrs={"prompt": "summarize the document", "k": 5},
    )
    act = SubstituteLeafAction(target_path=(0,), new_leaf=new_leaf)
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
    payloads = _scan_expand_outputs(db)
    assert payloads, "no expand-phase outputs emitted"
    found = False
    for payload in payloads:
        for proposal in payload:
            if proposal["action_kind"] != "SubstituteLeafAction":
                continue
            assert "action_hash" in proposal
            assert "action_payload" in proposal
            assert "prior" in proposal
            inner = proposal["action_payload"]
            assert "target_path" in inner
            assert "new_leaf_id" in inner
            assert "new_leaf_canonical" in inner, (
                "SubstituteLeafAction missing new_leaf_canonical (W2 M4 regression)"
            )
            # Round-trip: rebuild Node and assert id matches.
            reconstructed = _node_from_canonical(inner["new_leaf_canonical"])
            assert reconstructed.id == inner["new_leaf_id"], (
                f"new_leaf_canonical hash mismatch: "
                f"reconstructed={reconstructed.id!r} stored={inner['new_leaf_id']!r}"
            )
            assert reconstructed.id == new_leaf.id
            found = True
    assert found, "no SubstituteLeafAction proposal in expand outputs"


# --- Test 2: AddStepAction proposal carries canonical Node bytes ----- #


def test_add_step_proposal_round_trips_canonical_to_id():
    """``new_child_canonical`` rebuilds the exact Node whose id matches."""
    initial = _initial()
    new_child = Node(
        tag=":seq",
        attrs={},
        children=(_leaf("retrieve"),),
    )
    act = AddStepAction(target_path=(), at=1, new_child=new_child)
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
    payloads = _scan_expand_outputs(db)
    assert payloads, "no expand-phase outputs emitted"
    found = False
    for payload in payloads:
        for proposal in payload:
            if proposal["action_kind"] != "AddStepAction":
                continue
            inner = proposal["action_payload"]
            assert "target_path" in inner
            assert "at" in inner
            assert "new_child_id" in inner
            assert "new_child_canonical" in inner, (
                "AddStepAction missing new_child_canonical (W2 M4 regression)"
            )
            reconstructed = _node_from_canonical(inner["new_child_canonical"])
            assert reconstructed.id == inner["new_child_id"], (
                f"new_child_canonical hash mismatch: "
                f"reconstructed={reconstructed.id!r} stored={inner['new_child_id']!r}"
            )
            assert reconstructed.id == new_child.id
            found = True
    assert found, "no AddStepAction proposal in expand outputs"


# --- Test 3: ComposeWithSkillAction has skill_id, NO _canonical ----- #


def test_compose_with_skill_proposal_records_only_skill_id():
    """Compose proposals record ``skill_id`` only — no canonical Node.

    Design §13: skill plan recoverable via SkillLibrary on replay.
    Skill_id is itself content-addressed.
    """
    # We don't need the apply to succeed (no skill library wired); the
    # action will reject with reason="skill_not_registered". The expand
    # row still records the proposal payload BEFORE the application.
    initial = _initial()
    compose_act = ComposeWithSkillAction(
        target_path=(0,), skill_id="skill/abcdef0123456789"
    )
    db = _make_db()
    expander_proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(compose_act, 1.0)],
    }
    mcts_search(
        initial,
        expander=_StaticExpander(expander_proposals),
        evaluator=_StaticEvaluator({initial.id: 0.5}),
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=2, max_unique_plans=8),
        db=db,
    )
    payloads = _scan_expand_outputs(db)
    assert payloads, "no expand-phase outputs emitted"
    found = False
    for payload in payloads:
        for proposal in payload:
            if proposal["action_kind"] != "ComposeWithSkillAction":
                continue
            inner = proposal["action_payload"]
            assert "target_path" in inner
            assert "skill_id" in inner
            assert inner["skill_id"] == "skill/abcdef0123456789"
            # Strict NO `_canonical` slot.
            assert "new_leaf_canonical" not in inner
            assert "new_child_canonical" not in inner
            found = True
    assert found, "no ComposeWithSkillAction proposal in expand outputs"


# --- Test 4: prior sum invariant within ± _PRIOR_TOL of 1.0 -------- #


def test_proposal_priors_sum_to_one_within_tolerance():
    """Each expand-phase proposal list sums to 1.0 ± ``_PRIOR_TOL``."""
    initial = _initial()
    act_a = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf("A_sub"))
    act_b = SubstituteLeafAction(target_path=(1,), new_leaf=_leaf("B_sub"))
    plan_a = apply_action(initial, act_a)
    plan_b = apply_action(initial, act_b)
    db = _make_db()
    mcts_search(
        initial,
        expander=_StaticExpander({initial.id: [(act_a, 0.7), (act_b, 0.3)]}),
        evaluator=_StaticEvaluator({
            initial.id: 0.5,
            plan_a.id: 0.7,
            plan_b.id: 0.3,
        }),
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=4, max_unique_plans=8),
        db=db,
    )
    payloads = _scan_expand_outputs(db)
    # Skip empty payloads — terminal-node signal (unknown plan_id under
    # the static expander returns ``()``); the prior-sum invariant only
    # binds non-empty proposal lists (design §8 / _PRIOR_TOL).
    non_empty = [p for p in payloads if p]
    assert non_empty, "no non-empty expand-phase outputs"
    for payload in non_empty:
        prior_sum = sum(float(p["prior"]) for p in payload)
        assert abs(prior_sum - 1.0) < _PRIOR_TOL, (
            f"prior sum {prior_sum!r} drifts beyond _PRIOR_TOL"
        )


# --- Test 5: action_kind ∈ closed set ------------------------------- #


def test_action_kind_field_is_one_of_three_closed_names():
    """Schema invariant: ``action_kind`` is one of the 3 known names."""
    initial = _initial()
    act = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf("A_sub"))
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
    closed = {
        "SubstituteLeafAction", "AddStepAction", "ComposeWithSkillAction"
    }
    for payload in _scan_expand_outputs(db):
        for proposal in payload:
            assert proposal["action_kind"] in closed, (
                f"unexpected action_kind {proposal['action_kind']!r}"
            )
