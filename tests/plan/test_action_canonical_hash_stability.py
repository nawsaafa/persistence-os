"""B1 — `_action_hash` round-trip stability across the three Action kinds.

Covers design §5 / §17 ADR-2: action canonical hash recipe routes
through ``Node.id`` (the canonical-form helper from ``_ast.py``) for
nested Node fields rather than ``dataclasses.asdict`` recursion. Two
structurally equal actions hash identically; mutating any structural
field changes the hash; the recipe is deterministic across N=200
Hypothesis examples.
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

from persistence.plan import (
    AddStepAction,
    ComposeWithSkillAction,
    Node,
    SubstituteLeafAction,
)
from persistence.plan._mcts import _action_hash


# --- Fixtures ------------------------------------------------------------- #


def _leaf(tag: str = ":leaf/predict", prompt: str = "x") -> Node:
    """Minimal leaf Node — used as new_leaf / new_child payload."""
    return Node(tag=tag, attrs={"prompt": prompt})


# --- SubstituteLeafAction stability -------------------------------------- #


def test_substitute_leaf_same_content_same_hash():
    """Two structurally-equal SubstituteLeafActions hash identically."""
    a = SubstituteLeafAction(target_path=(0, 1), new_leaf=_leaf())
    b = SubstituteLeafAction(target_path=(0, 1), new_leaf=_leaf())
    assert _action_hash(a) == _action_hash(b)


def test_substitute_leaf_path_change_changes_hash():
    """Different target_path → different hash."""
    a = SubstituteLeafAction(target_path=(0, 1), new_leaf=_leaf())
    b = SubstituteLeafAction(target_path=(0, 2), new_leaf=_leaf())
    assert _action_hash(a) != _action_hash(b)


def test_substitute_leaf_node_attr_change_changes_hash():
    """Mutating the nested Node's attrs changes Node.id, hence the action hash."""
    a = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf(prompt="x"))
    b = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf(prompt="y"))
    assert _action_hash(a) != _action_hash(b)


# --- AddStepAction stability --------------------------------------------- #


def test_add_step_same_content_same_hash():
    """Two structurally-equal AddStepActions hash identically."""
    a = AddStepAction(target_path=(0,), at=1, new_child=_leaf())
    b = AddStepAction(target_path=(0,), at=1, new_child=_leaf())
    assert _action_hash(a) == _action_hash(b)


def test_add_step_at_change_changes_hash():
    """Different insertion index → different hash."""
    a = AddStepAction(target_path=(0,), at=0, new_child=_leaf())
    b = AddStepAction(target_path=(0,), at=1, new_child=_leaf())
    assert _action_hash(a) != _action_hash(b)


def test_add_step_child_change_changes_hash():
    """Different nested Node content → different hash."""
    a = AddStepAction(target_path=(0,), at=0, new_child=_leaf(prompt="x"))
    b = AddStepAction(target_path=(0,), at=0, new_child=_leaf(prompt="y"))
    assert _action_hash(a) != _action_hash(b)


# --- ComposeWithSkillAction stability ----------------------------------- #


def test_compose_with_skill_same_content_same_hash():
    """Two structurally-equal ComposeWithSkillActions hash identically."""
    a = ComposeWithSkillAction(target_path=(0,), skill_id="skill/abc")
    b = ComposeWithSkillAction(target_path=(0,), skill_id="skill/abc")
    assert _action_hash(a) == _action_hash(b)


def test_compose_with_skill_id_change_changes_hash():
    """Different skill_id → different hash."""
    a = ComposeWithSkillAction(target_path=(0,), skill_id="skill/abc")
    b = ComposeWithSkillAction(target_path=(0,), skill_id="skill/def")
    assert _action_hash(a) != _action_hash(b)


# --- Cross-kind discrimination ------------------------------------------ #


def test_kinds_with_same_target_path_hash_distinctly():
    """The kind discriminator is part of the canonical payload."""
    sub = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf())
    add = AddStepAction(target_path=(0,), at=0, new_child=_leaf())
    compose = ComposeWithSkillAction(target_path=(0,), skill_id="skill/x")
    hashes = {_action_hash(sub), _action_hash(add), _action_hash(compose)}
    assert len(hashes) == 3


def test_substitute_leaf_attrs_dict_insertion_order_irrelevant():
    """Canonical-JSON sorts keys; attrs dict insertion order does not affect hash."""
    leaf_a = Node(tag=":leaf/op", attrs={"alpha": 1, "beta": 2})
    leaf_b = Node(tag=":leaf/op", attrs={"beta": 2, "alpha": 1})
    a = SubstituteLeafAction(target_path=(0,), new_leaf=leaf_a)
    b = SubstituteLeafAction(target_path=(0,), new_leaf=leaf_b)
    assert _action_hash(a) == _action_hash(b)


# --- Hypothesis property: determinism over N=200 examples --------------- #


_PATH_STRAT = st.lists(st.integers(min_value=0, max_value=8), max_size=5).map(tuple)


@given(
    target_path=_PATH_STRAT,
    prompt=st.text(min_size=0, max_size=12),
)
@settings(max_examples=200, deadline=None)
def test_substitute_leaf_hash_is_deterministic_property(
    target_path: tuple[int, ...], prompt: str
) -> None:
    """Constructing the same SubstituteLeafAction twice yields the same hash."""
    leaf_a = Node(tag=":leaf/op", attrs={"prompt": prompt})
    leaf_b = Node(tag=":leaf/op", attrs={"prompt": prompt})
    a = SubstituteLeafAction(target_path=target_path, new_leaf=leaf_a)
    b = SubstituteLeafAction(target_path=target_path, new_leaf=leaf_b)
    assert _action_hash(a) == _action_hash(b)


@given(
    target_path=_PATH_STRAT,
    at=st.integers(min_value=0, max_value=8),
    prompt=st.text(min_size=0, max_size=12),
)
@settings(max_examples=200, deadline=None)
def test_add_step_hash_is_deterministic_property(
    target_path: tuple[int, ...], at: int, prompt: str
) -> None:
    """AddStepAction hash determinism property."""
    child_a = Node(tag=":leaf/op", attrs={"prompt": prompt})
    child_b = Node(tag=":leaf/op", attrs={"prompt": prompt})
    a = AddStepAction(target_path=target_path, at=at, new_child=child_a)
    b = AddStepAction(target_path=target_path, at=at, new_child=child_b)
    assert _action_hash(a) == _action_hash(b)


@given(
    target_path=_PATH_STRAT,
    skill_id=st.text(min_size=1, max_size=24),
)
@settings(max_examples=200, deadline=None)
def test_compose_with_skill_hash_is_deterministic_property(
    target_path: tuple[int, ...], skill_id: str
) -> None:
    """ComposeWithSkillAction hash determinism property."""
    a = ComposeWithSkillAction(target_path=target_path, skill_id=skill_id)
    b = ComposeWithSkillAction(target_path=target_path, skill_id=skill_id)
    assert _action_hash(a) == _action_hash(b)
