"""B3 — ``MCTSNode`` + ``MCTSEdge`` surface contract.

Covers design §4 / §17 ADR-1: search-tree nodes are keyed by Plan AST
``Node.id`` (transposition table); edge counters are mutated in BACKUP
so neither dataclass is ``frozen=True``. ``slots=True`` keeps the
footprint small under thousands of plans.

The full search loop is exercised end-to-end by B6 (determinism +
visit-conservation tests). This module is surface-only: defaults,
mutability, the ``q_value`` zero-division guard, and the
``slots=True`` ban on attribute injection.
"""
from __future__ import annotations

import pytest

from persistence.plan import (
    AddStepAction,
    MCTSEdge,
    MCTSNode,
    Node,
    SubstituteLeafAction,
)
from persistence.plan._mcts import _action_hash


# --- Fixtures ------------------------------------------------------------- #


def _leaf(tag: str = ":leaf/predict", prompt: str = "x") -> Node:
    """Minimal leaf Node used as new_leaf / new_child payload."""
    return Node(tag=tag, attrs={"prompt": prompt})


@pytest.fixture
def substitute_action() -> SubstituteLeafAction:
    """A canonical ``SubstituteLeafAction`` for edge construction."""
    return SubstituteLeafAction(target_path=(0,), new_leaf=_leaf(":leaf/b"))


@pytest.fixture
def add_step_action() -> AddStepAction:
    """A canonical ``AddStepAction`` for edge construction."""
    return AddStepAction(target_path=(), at=0, new_child=_leaf(":leaf/c"))


# --- MCTSNode defaults --------------------------------------------------- #


def test_mcts_node_default_construction():
    """``MCTSNode(plan_id=...)`` carries the documented defaults (design §4)."""
    n = MCTSNode(plan_id="abc")
    assert n.plan_id == "abc"
    assert n.visits == 0
    assert n.total_value == 0.0
    assert n.children == {}
    assert n.is_terminal is False


def test_mcts_node_q_value_zero_visits_returns_zero():
    """``q_value`` returns ``0.0`` (not raise / not NaN) when ``visits == 0``."""
    n = MCTSNode(plan_id="abc")
    assert n.q_value == 0.0


def test_mcts_node_q_value_computes_mean():
    """``q_value`` returns ``total_value / visits`` for ``visits > 0`` (design §4)."""
    n = MCTSNode(plan_id="abc", visits=2, total_value=10.0)
    assert n.q_value == 5.0


def test_mcts_node_visits_and_total_value_are_mutable():
    """Visits + total_value are written by BACKUP (design §16) — NOT frozen."""
    n = MCTSNode(plan_id="abc")
    n.visits += 1
    n.total_value += 0.7
    assert n.visits == 1
    assert n.total_value == pytest.approx(0.7)


def test_mcts_node_children_dict_membership_is_mutable(substitute_action):
    """``children`` dict-membership grows in EXPAND (design §16) — NOT frozen."""
    n = MCTSNode(plan_id="abc")
    h = _action_hash(substitute_action)
    edge = MCTSEdge(
        action_hash=h,
        action=substitute_action,
        child_plan_id="def",
        prior=0.5,
    )
    n.children[h] = edge
    assert n.children[h] is edge


def test_mcts_node_is_terminal_is_mutable():
    """``is_terminal`` is flipped True when EXPAND yields zero proposals (design §16)."""
    n = MCTSNode(plan_id="abc")
    n.is_terminal = True
    assert n.is_terminal is True


def test_mcts_node_default_factory_is_per_instance():
    """Two ``MCTSNode``s do NOT share the same ``children`` dict identity."""
    a = MCTSNode(plan_id="a")
    b = MCTSNode(plan_id="b")
    assert a.children is not b.children


def test_mcts_node_slots_rejects_attribute_injection():
    """``slots=True`` bans ad-hoc attributes (Python 3.14: AttributeError)."""
    n = MCTSNode(plan_id="abc")
    with pytest.raises((AttributeError, TypeError)):
        n.spurious_attr = 1  # pyright: ignore[reportAttributeAccessIssue]


# --- MCTSEdge defaults --------------------------------------------------- #


def test_mcts_edge_default_construction(substitute_action):
    """``MCTSEdge`` carries the documented defaults (design §4)."""
    h = _action_hash(substitute_action)
    e = MCTSEdge(
        action_hash=h,
        action=substitute_action,
        child_plan_id="def",
        prior=0.5,
    )
    assert e.action_hash == h
    assert e.action is substitute_action
    assert e.child_plan_id == "def"
    assert e.prior == 0.5
    assert e.visits_through_edge == 0
    assert e.total_value_through_edge == 0.0


def test_mcts_edge_visits_and_total_value_through_edge_are_mutable(
    add_step_action,
):
    """``visits_through_edge`` + ``total_value_through_edge`` mutate in BACKUP
    (design §16, ADR pin) — NOT frozen."""
    h = _action_hash(add_step_action)
    e = MCTSEdge(
        action_hash=h,
        action=add_step_action,
        child_plan_id="def",
        prior=0.25,
    )
    e.visits_through_edge += 1
    e.total_value_through_edge += 0.9
    assert e.visits_through_edge == 1
    assert e.total_value_through_edge == pytest.approx(0.9)


def test_mcts_edge_slots_rejects_attribute_injection(substitute_action):
    """``slots=True`` bans ad-hoc attributes on ``MCTSEdge`` too."""
    e = MCTSEdge(
        action_hash=_action_hash(substitute_action),
        action=substitute_action,
        child_plan_id="def",
        prior=0.5,
    )
    with pytest.raises((AttributeError, TypeError)):
        e.spurious_attr = 1  # pyright: ignore[reportAttributeAccessIssue]


# --- Equality (default dataclass eq=True) -------------------------------- #


def test_mcts_node_default_eq_compares_by_value():
    """``@dataclass(slots=True)`` keeps ``eq=True`` by default; two nodes with
    identical fields are value-equal. (Not required by the design contract;
    transposition keys by ``plan_id``, not by node identity. This pins the
    Python default so a future ``eq=False`` flip is intentional.)"""
    a = MCTSNode(plan_id="x")
    b = MCTSNode(plan_id="x")
    assert a == b
    assert a is not b


def test_mcts_node_eq_distinguishes_plan_id():
    """Different plan ids → not equal (design §4 transposition keying)."""
    assert MCTSNode(plan_id="x") != MCTSNode(plan_id="y")
