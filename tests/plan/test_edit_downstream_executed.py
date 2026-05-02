"""Phase 2.0e (#175): Hypothesis property test for delete_step's
downstream-execution check.

Validates the iff invariant across random plans + random execution
states at ``@max_examples=200``, mirroring the project's standard
property-test discipline (Phase 2.0c-ext precedent at
``tests/store/test_fold_byte_identity.py``).

Property under test
-------------------

For any plan ``P`` (random shape, depth ≤ 3, branching ≤ 3) and any
subset ``E`` of node-ids drawn from ``P``, deleting a step ``s`` whose
node-id is in ``P``::

    delete_step(P, s, tx=tx)  # with tx.completed_step_ids = E

raises :class:`PlanEditDownstreamExecuted` **iff** the subtree rooted at
``s`` contains at least one node id in ``E``. The "iff" is the
load-bearing invariant: the substrate must not raise spuriously
(false-positive) and must not allow silent execution-record loss
(false-negative).

Design alignment
----------------

* Design § 4.1 line 285 — ``delete_step`` "only allowed if no downstream
  step has executed".
* Phase 2.0e impl plan — delegates to ``_first_executed_in_subtree``;
  this property exercises the iff contract independent of impl detail.
"""
from __future__ import annotations

import pytest

import hypothesis.strategies as st
from hypothesis import HealthCheck, given, settings

from persistence.fact.db import DB
from persistence.plan import Node
from persistence.plan._edit import delete_step
from persistence.plan._errors import PlanEditDownstreamExecuted, StepIdNotFound


# ---------------------------------------------------------------------------
# Strategies — mirror tests/plan/test_edit.py PLAN_KINDS but exclude
# :code / :branch (their leaf forms raise UnimplementedNodeKindError on
# walk; orthogonal to the property under test).
# ---------------------------------------------------------------------------

PLAN_KINDS = (
    ":seq", ":par", ":choice", ":loop", ":race", ":let", ":case",
    ":tool-call", ":llm-call", ":checkpoint",
    ":reflect", ":verify", ":call-skill", ":ref",
)

_attr_key_strat = st.from_regex(r"[a-z][a-z0-9_-]{0,5}", fullmatch=True).filter(
    lambda k: k != "id"
)


def _scalar_strat() -> st.SearchStrategy:
    return st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-(10**4), max_value=10**4),
        st.text(
            alphabet=st.characters(
                min_codepoint=0x20,
                max_codepoint=0x7e,
                blacklist_characters='":\\',
            ),
            min_size=0,
            max_size=8,
        ),
    )


def _attrs_dict_strat() -> st.SearchStrategy:
    return st.dictionaries(
        keys=_attr_key_strat,
        values=_scalar_strat(),
        max_size=2,
    )


@st.composite
def plan_node_strat(draw, max_depth: int = 3) -> Node:
    """Build a random Plan AST with bounded depth + breadth.

    Bounded so that random selection of ``target`` and ``completed``
    yields meaningful coverage of both raise/no-raise paths within
    @max_examples=200.
    """
    tag = draw(st.sampled_from(PLAN_KINDS))
    attrs = draw(_attrs_dict_strat())
    if max_depth <= 0:
        children: tuple[Node, ...] = ()
    else:
        children = tuple(
            draw(
                st.lists(
                    plan_node_strat(max_depth=max_depth - 1),
                    max_size=3,
                )
            )
        )
    return Node(tag=tag, attrs=attrs, children=children)


def _all_nodes(plan: Node) -> list[Node]:
    out = [plan]
    for c in plan.children:
        out.extend(_all_nodes(c))
    return out


def _find_first_with_id(plan: Node, target_id: str) -> Node | None:
    for n in _all_nodes(plan):
        if n.id == target_id:
            return n
    return None


def _subtree_ids(subtree: Node) -> set[str]:
    """Pre-order DFS collection of every Node.id under (and including)
    ``subtree``. Used to compute the expected ``raise`` outcome
    independently of the impl helper."""
    ids = {subtree.id}
    for c in subtree.children:
        ids |= _subtree_ids(c)
    return ids


# ---------------------------------------------------------------------------
# Audit-stack autouse fixture (mirrors tests/plan/test_edit.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _audit_stack_for_downstream_property():
    """Install the canonical audit stack so :plan/edit intent replay
    does not raise AuditStackMissing on the no-raise path.

    See ``tests/plan/test_edit.py::_audit_stack_for_edit_tests`` for
    the same pattern; Phase 2.0d W1 (M2) requires an active runtime
    when ``:plan/edit`` intents are queued.
    """
    from persistence.effect import canonical_audit_stack, with_runtime

    rt = canonical_audit_stack(entries=[])
    with with_runtime(rt):
        yield


# ---------------------------------------------------------------------------
# Hypothesis property test — iff invariant
# ---------------------------------------------------------------------------


@given(
    plan=plan_node_strat(max_depth=3),
    target_seed=st.integers(min_value=0, max_value=2**32 - 1),
    completed_seed=st.integers(min_value=0, max_value=2**32 - 1),
)
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
def test_delete_step_raises_iff_target_subtree_has_executed_step(
    plan: Node,
    target_seed: int,
    completed_seed: int,
) -> None:
    """For any plan ``P`` and any subset ``E`` of executed step ids,
    ``delete_step(P, s, tx)`` with ``tx.completed_step_ids = E`` raises
    :class:`PlanEditDownstreamExecuted` **iff** the subtree rooted at
    ``s`` shares at least one node id with ``E``.

    The "iff" is the load-bearing contract: no false-positives, no
    false-negatives. False-positive would block legitimate plan
    revision; false-negative would silently lose an execution record
    (the failure mode the substrate must prevent — design § 4.1).
    """
    import random

    # Collect deletable (non-root) ids. Root is excluded — delete_step
    # on the root raises StepIdNotFound by design (no parent slot to
    # splice from), unrelated to the downstream-check property.
    nodes_excluding_root = [n for n in _all_nodes(plan) if n is not plan]
    if not nodes_excluding_root:
        # Plan has no non-root nodes; downstream-check has no deletable
        # target to exercise. Exit silently — Hypothesis will redraw.
        return

    target_rng = random.Random(target_seed)
    completed_rng = random.Random(completed_seed)

    # Choose a non-root node as the deletion target. Use the actual
    # Node object so we can compute the canonical subtree-ids set
    # (handles the duplicate-content case symmetrically — multiple
    # nodes may share an id, but their subtrees may differ; the
    # impl resolves to first-occurrence-in-DFS, so we mirror that
    # by walking the plan to find the FIRST node whose id matches
    # the chosen target's id).
    target_node = target_rng.choice(nodes_excluding_root)
    target_id = target_node.id
    matched_subtree = _find_first_with_id(plan, target_id)
    assert matched_subtree is not None  # Sanity — target id is in plan
    expected_subtree_ids = _subtree_ids(matched_subtree)

    # Random subset of all_ids serves as the "executed" set.
    all_ids = [n.id for n in _all_nodes(plan)]
    k = completed_rng.randint(0, len(all_ids))
    completed_ids = set(completed_rng.sample(all_ids, k=k))

    # Expected outcome by definition of the iff contract.
    expected_raise = bool(expected_subtree_ids & completed_ids)

    db = DB()
    if expected_raise:
        with pytest.raises(PlanEditDownstreamExecuted):
            with db.dosync() as tx:
                tx.completed_step_ids = completed_ids
                delete_step(plan, target_id, tx=tx)
    else:
        # Must not raise PlanEditDownstreamExecuted (and must not raise
        # StepIdNotFound — target is in plan by construction).
        # NOTE: deleting a step changes the parent's content (its
        # children tuple shrinks), which propagates new content-
        # addresses up the spine to the new root — that is the
        # designed-for behavior of immutable content-addressed AST,
        # not a violation. The iff contract is the property under
        # test; we only assert that delete_step returned a Node and
        # did not raise PlanEditDownstreamExecuted.
        new_plan: Node | None = None
        with db.dosync() as tx:
            tx.completed_step_ids = completed_ids
            new_plan = delete_step(plan, target_id, tx=tx)
        assert new_plan is not None
        assert isinstance(new_plan, Node)
