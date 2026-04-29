"""B4 — ``_StaticExpander`` lookup, truncation, and ``on_unknown`` branches.

Pinned signature so subsequent B-series fixtures (B5/B6/B-INT) can wire
deterministic proposals uniformly. The full prior-sum-to-1.0 enforcement
lives in B6's MCTS loop; this module is surface-only and only covers
the test-stub semantics described in design §8.
"""
from __future__ import annotations

import pytest

from persistence.plan import (
    Expander,
    Node,
    SubstituteLeafAction,
)
from persistence.plan._mcts import _StaticExpander


# --- Fixtures ------------------------------------------------------------ #


def _leaf(tag: str = ":leaf/predict", prompt: str = "x") -> Node:
    """Minimal leaf Node for test plans."""
    return Node(tag=tag, attrs={"prompt": prompt})


@pytest.fixture
def plan_a() -> Node:
    """Pinned plan A — root of the proposals dict."""
    return Node(tag=":plan/a", attrs={"k": 1}, children=(_leaf(),))


@pytest.fixture
def plan_b() -> Node:
    """Pinned plan B — used to probe unknown-plan-id behaviour."""
    return Node(tag=":plan/b", attrs={"k": 2}, children=(_leaf(),))


@pytest.fixture
def action_alpha() -> SubstituteLeafAction:
    return SubstituteLeafAction(target_path=(0,), new_leaf=_leaf(":leaf/alpha"))


@pytest.fixture
def action_beta() -> SubstituteLeafAction:
    return SubstituteLeafAction(target_path=(0,), new_leaf=_leaf(":leaf/beta"))


# --- Protocol satisfaction ----------------------------------------------- #


def test_static_expander_satisfies_expander_protocol(plan_a, action_alpha):
    """``_StaticExpander`` is structurally an ``Expander`` (``@runtime_checkable``)."""
    expander = _StaticExpander({plan_a.id: [(action_alpha, 1.0)]})
    assert isinstance(expander, Expander)


# --- Known-plan lookup --------------------------------------------------- #


def test_propose_returns_pinned_proposals_for_known_plan(
    plan_a, action_alpha, action_beta
):
    """Lookup by ``plan.id`` returns the pinned proposal list (truncated to k)."""
    expander = _StaticExpander(
        {plan_a.id: [(action_alpha, 0.6), (action_beta, 0.4)]},
    )
    result = expander.propose(plan_a, k=2)
    assert len(result) == 2
    assert result[0] == (action_alpha, 0.6)
    assert result[1] == (action_beta, 0.4)


def test_propose_returns_a_concrete_sequence_not_a_generator(
    plan_a, action_alpha
):
    """Return value is a Sequence (tuple); supports ``len`` without consuming."""
    expander = _StaticExpander({plan_a.id: [(action_alpha, 1.0)]})
    result = expander.propose(plan_a, k=4)
    # Generators have no __len__; calling len() forces materialisation.
    # The Sequence contract (design §8) demands re-iterable, so we
    # double-check: len() works AND the result can be indexed twice.
    assert len(result) == 1
    assert result[0] == (action_alpha, 1.0)
    assert result[0] == (action_alpha, 1.0)  # second access is allowed


# --- Unknown-plan branches ----------------------------------------------- #


def test_propose_unknown_plan_default_returns_empty_tuple(plan_a, plan_b, action_alpha):
    """Default ``on_unknown="empty"``: unknown ``plan.id`` -> ``()``."""
    expander = _StaticExpander({plan_a.id: [(action_alpha, 1.0)]})
    assert expander.propose(plan_b, k=2) == ()


def test_propose_unknown_plan_with_on_unknown_raise_raises_keyerror(
    plan_a, plan_b, action_alpha
):
    """``on_unknown="raise"``: unknown ``plan.id`` -> ``KeyError`` carrying the id."""
    expander = _StaticExpander(
        {plan_a.id: [(action_alpha, 1.0)]},
        on_unknown="raise",
    )
    with pytest.raises(KeyError) as excinfo:
        expander.propose(plan_b, k=2)
    # KeyError args[0] is the unknown plan_id (helps callers debug).
    assert excinfo.value.args[0] == plan_b.id


# --- Truncation --------------------------------------------------------- #


def test_propose_truncates_to_k_when_proposals_exceed_k(plan_a):
    """``k=2`` over a 5-proposal list returns the first 2 (head-slice)."""
    actions = [
        SubstituteLeafAction(target_path=(0,), new_leaf=_leaf(f":leaf/{i}"))
        for i in range(5)
    ]
    pinned = [(a, 0.2) for a in actions]
    expander = _StaticExpander({plan_a.id: pinned})
    result = expander.propose(plan_a, k=2)
    assert len(result) == 2
    assert result[0] == (actions[0], 0.2)
    assert result[1] == (actions[1], 0.2)


def test_propose_with_k_larger_than_pinned_returns_all_pinned(
    plan_a, action_alpha, action_beta
):
    """``k=10`` over a 2-proposal list returns the 2 (slice is clamped)."""
    expander = _StaticExpander(
        {plan_a.id: [(action_alpha, 0.5), (action_beta, 0.5)]},
    )
    result = expander.propose(plan_a, k=10)
    assert len(result) == 2


# --- Sequence-input compatibility --------------------------------------- #


def test_propose_accepts_tuple_or_list_proposals(plan_a, action_alpha):
    """``proposals`` values may be ``tuple`` or ``list`` (both are Sequences)."""
    as_tuple = _StaticExpander({plan_a.id: ((action_alpha, 1.0),)})
    as_list = _StaticExpander({plan_a.id: [(action_alpha, 1.0)]})
    assert as_tuple.propose(plan_a, k=1) == as_list.propose(plan_a, k=1)
