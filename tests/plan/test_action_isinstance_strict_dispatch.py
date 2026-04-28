"""B1 — `apply_action` dispatch is `isinstance`-strict, NOT duck-typed.

Covers design §5 / §17 ADR-2: third-party dataclasses with field names
matching one of the three Action kinds — but no inheritance — are
rejected with ``ValueError("unknown action kind: ...")``. Pin against
the temptation to switch to ``match`` / structural pattern matching.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from persistence.plan import Node, apply_action


def _plan() -> Node:
    return Node(
        tag=":seq",
        children=(Node(tag=":leaf/a", attrs={"prompt": "a"}),),
    )


@dataclass(frozen=True)
class _ThirdPartySubstituteLike:
    """Same field names as SubstituteLeafAction, but no nominal inheritance."""

    target_path: tuple[int, ...]
    new_leaf: Node


@dataclass(frozen=True)
class _ThirdPartyAddStepLike:
    """Same field names as AddStepAction, but no nominal inheritance."""

    target_path: tuple[int, ...]
    at: int
    new_child: Node


@dataclass(frozen=True)
class _ThirdPartyComposeLike:
    """Same field names as ComposeWithSkillAction, but no nominal inheritance."""

    target_path: tuple[int, ...]
    skill_id: str


def test_third_party_substitute_like_dataclass_is_rejected():
    """Same-shape dataclass routes through the else-branch ValueError."""
    plan = _plan()
    fake = _ThirdPartySubstituteLike(
        target_path=(0,), new_leaf=Node(tag=":leaf/x")
    )
    with pytest.raises(ValueError, match="unknown action kind"):
        apply_action(plan, fake)  # type: ignore[arg-type]


def test_third_party_add_step_like_dataclass_is_rejected():
    """Same-shape AddStep duck-type rejected at the dispatch boundary."""
    plan = _plan()
    fake = _ThirdPartyAddStepLike(
        target_path=(), at=0, new_child=Node(tag=":leaf/x")
    )
    with pytest.raises(ValueError, match="unknown action kind"):
        apply_action(plan, fake)  # type: ignore[arg-type]


def test_third_party_compose_like_dataclass_is_rejected():
    """Same-shape ComposeWithSkill duck-type rejected at the dispatch boundary."""
    plan = _plan()
    fake = _ThirdPartyComposeLike(target_path=(), skill_id="skill/x")
    with pytest.raises(ValueError, match="unknown action kind"):
        apply_action(plan, fake)  # type: ignore[arg-type]


def test_simple_namespace_duck_type_is_rejected():
    """SimpleNamespace with matching attrs → still rejected (no isinstance match)."""
    plan = _plan()
    fake = SimpleNamespace(target_path=(0,), new_leaf=Node(tag=":leaf/x"))
    with pytest.raises(ValueError, match="unknown action kind"):
        apply_action(plan, fake)  # type: ignore[arg-type]


def test_error_message_names_offending_class():
    """Error message includes the third-party class name for diagnostic clarity."""
    plan = _plan()
    fake = _ThirdPartySubstituteLike(
        target_path=(0,), new_leaf=Node(tag=":leaf/x")
    )
    with pytest.raises(ValueError, match="_ThirdPartySubstituteLike"):
        apply_action(plan, fake)  # type: ignore[arg-type]
