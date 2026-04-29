"""B1 — `apply_action` correctness across the three Action kinds.

Covers design §6 ADR-3: applicator is pure, structural, leaves untouched
subtrees object-identical, and produces only valid Nodes (Node.id
recomputes; tag-prefix and attr-key invariants hold).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from persistence.fact.db import DB
from persistence.fact.store import InMemoryStore
from persistence.plan import (
    AddStepAction,
    ComposeWithSkillAction,
    Node,
    SkillLibrary,
    SubstituteLeafAction,
    apply_action,
)


# --- Fixtures ------------------------------------------------------------- #


def _frozen_clock(start_ms: int = 1_000_000):
    """Deterministic 1-ms-tick clock for SkillLibrary tests."""
    counter = [start_ms]

    def _now() -> datetime:
        ts = datetime.fromtimestamp(counter[0] / 1000, tz=timezone.utc)
        counter[0] += 1
        return ts

    return _now


def _make_db() -> DB:
    return DB(InMemoryStore(), clock=_frozen_clock())


@dataclass(frozen=True, slots=True)
class _StubPromotionRecord:
    """Minimal promotion-record stub satisfying ``_PromotionRecordLike``."""

    promotion_id: str


def _three_leaf_seq() -> Node:
    """A three-leaf :seq plan: root → (leaf-a, leaf-b, leaf-c)."""
    return Node(
        tag=":seq",
        attrs={"name": "demo"},
        children=(
            Node(tag=":leaf/a", attrs={"prompt": "a"}),
            Node(tag=":leaf/b", attrs={"prompt": "b"}),
            Node(tag=":leaf/c", attrs={"prompt": "c"}),
        ),
    )


# --- SubstituteLeafAction ------------------------------------------------ #


def test_substitute_leaf_replaces_target_only():
    """Substitute swaps the target leaf; siblings are object-identical."""
    plan = _three_leaf_seq()
    new_leaf = Node(tag=":leaf/replaced", attrs={"prompt": "X"})
    action = SubstituteLeafAction(target_path=(1,), new_leaf=new_leaf)
    result = apply_action(plan, action)

    assert result.children[1] is new_leaf
    # Siblings preserved by reference (structural recursion).
    assert result.children[0] is plan.children[0]
    assert result.children[2] is plan.children[2]
    # Original plan unchanged.
    assert plan.children[1].tag == ":leaf/b"


def test_substitute_leaf_at_nested_path():
    """Substitute at depth ≥ 2 walks the path correctly."""
    plan = Node(
        tag=":seq",
        children=(
            Node(
                tag=":seq",
                children=(
                    Node(tag=":leaf/a", attrs={"prompt": "a"}),
                    Node(tag=":leaf/b", attrs={"prompt": "b"}),
                ),
            ),
        ),
    )
    new_leaf = Node(tag=":leaf/replaced", attrs={"prompt": "X"})
    action = SubstituteLeafAction(target_path=(0, 1), new_leaf=new_leaf)
    result = apply_action(plan, action)
    assert result.children[0].children[1] is new_leaf
    assert result.children[0].children[0] is plan.children[0].children[0]


def test_substitute_leaf_invalid_path_raises_index_error():
    """Out-of-range target_path raises IndexError."""
    plan = _three_leaf_seq()
    new_leaf = Node(tag=":leaf/replaced")
    action = SubstituteLeafAction(target_path=(99,), new_leaf=new_leaf)
    with pytest.raises(IndexError):
        apply_action(plan, action)


# --- AddStepAction ------------------------------------------------------- #


def test_add_step_appends_to_children():
    """AddStepAction with at == len(children) appends; prior children preserved."""
    plan = _three_leaf_seq()
    new_child = Node(tag=":leaf/d", attrs={"prompt": "d"})
    action = AddStepAction(target_path=(), at=3, new_child=new_child)
    result = apply_action(plan, action)
    assert len(result.children) == 4
    assert result.children[3] is new_child
    # Prior children preserved structurally.
    for i in range(3):
        assert result.children[i] is plan.children[i]


def test_add_step_inserts_at_index():
    """AddStepAction with at < len(children) inserts; sibling order preserved."""
    plan = _three_leaf_seq()
    new_child = Node(tag=":leaf/inserted", attrs={"prompt": "I"})
    action = AddStepAction(target_path=(), at=1, new_child=new_child)
    result = apply_action(plan, action)
    assert len(result.children) == 4
    assert result.children[0] is plan.children[0]
    assert result.children[1] is new_child
    assert result.children[2] is plan.children[1]
    assert result.children[3] is plan.children[2]


def test_add_step_rejects_negative_index():
    """Negative AddStepAction.at raises IndexError."""
    plan = _three_leaf_seq()
    action = AddStepAction(
        target_path=(), at=-1, new_child=Node(tag=":leaf/x")
    )
    with pytest.raises(IndexError):
        apply_action(plan, action)


def test_add_step_rejects_index_past_end():
    """AddStepAction.at > len(children) raises IndexError."""
    plan = _three_leaf_seq()
    action = AddStepAction(
        target_path=(), at=99, new_child=Node(tag=":leaf/x")
    )
    with pytest.raises(IndexError):
        apply_action(plan, action)


# --- ComposeWithSkillAction --------------------------------------------- #


def _register_skill(plan: Node, lib: SkillLibrary, promotion_id: str = "p-1") -> str:
    """Register ``plan`` and return the skill_id."""
    return lib.register(
        plan,
        _StubPromotionRecord(promotion_id=promotion_id),
        registered_at_ms=1234,
    )


def test_compose_with_skill_substitutes_subtree():
    """ComposeWithSkillAction wraps the subtree under the skill root."""
    db = _make_db()
    lib = SkillLibrary(db)
    skill_plan = Node(
        tag=":seq",
        attrs={"role": "wrapper"},
        children=(Node(tag=":leaf/post", attrs={"prompt": "post"}),),
    )
    skill_id = _register_skill(skill_plan, lib)

    base_plan = _three_leaf_seq()
    action = ComposeWithSkillAction(target_path=(0,), skill_id=skill_id)
    result = apply_action(base_plan, action, skill_library=lib)

    composed = result.children[0]
    assert composed.tag == skill_plan.tag
    # The original subtree at target_path becomes the first child of the
    # composed skill root (design §12 _apply_compose_with_skill).
    assert composed.children[0] is base_plan.children[0]
    # Then the skill's own children follow.
    assert composed.children[1].tag == ":leaf/post"


def test_compose_with_skill_without_library_raises():
    """ComposeWithSkillAction with skill_library=None raises ValueError."""
    plan = _three_leaf_seq()
    action = ComposeWithSkillAction(target_path=(0,), skill_id="skill/anything")
    with pytest.raises(ValueError, match="requires skill_library"):
        apply_action(plan, action, skill_library=None)


def test_compose_with_skill_unregistered_id_raises():
    """ComposeWithSkillAction with a skill_id absent from the library raises."""
    db = _make_db()
    lib = SkillLibrary(db)
    plan = _three_leaf_seq()
    action = ComposeWithSkillAction(
        target_path=(0,), skill_id="skill/0000000000000000"
    )
    with pytest.raises(ValueError, match="not registered"):
        apply_action(plan, action, skill_library=lib)


# --- Output validity ----------------------------------------------------- #


def test_substitute_leaf_result_is_valid_node():
    """Result Node's .id recomputes (no cached state); tag/attr invariants hold."""
    plan = _three_leaf_seq()
    new_leaf = Node(tag=":leaf/replaced", attrs={"prompt": "X"})
    result = apply_action(
        plan, SubstituteLeafAction(target_path=(1,), new_leaf=new_leaf)
    )
    # .id triggers Node canonical-form computation; bare attribute access
    # is enough to surface any structural error.
    assert isinstance(result.id, str)
    assert len(result.id) == 32
    assert result.tag.startswith(":")


def test_add_step_result_is_valid_node():
    """AddStep result re-canonicalizes via Node.id without raising."""
    plan = _three_leaf_seq()
    new_child = Node(tag=":leaf/d", attrs={"prompt": "d"})
    result = apply_action(
        plan, AddStepAction(target_path=(), at=3, new_child=new_child)
    )
    assert isinstance(result.id, str)
    assert len(result.id) == 32


def test_compose_with_skill_result_is_valid_node():
    """ComposeWithSkill result re-canonicalizes via Node.id without raising."""
    db = _make_db()
    lib = SkillLibrary(db)
    skill_plan = Node(
        tag=":seq",
        attrs={"role": "wrapper"},
        children=(Node(tag=":leaf/post", attrs={"prompt": "post"}),),
    )
    skill_id = _register_skill(skill_plan, lib)
    base_plan = _three_leaf_seq()
    action = ComposeWithSkillAction(target_path=(0,), skill_id=skill_id)
    result = apply_action(base_plan, action, skill_library=lib)
    assert isinstance(result.id, str)
    assert len(result.id) == 32
