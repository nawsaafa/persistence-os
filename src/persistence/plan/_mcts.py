"""MCTS over the content-addressed Plan AST (v0.6.5; design §5, §6, ADR-11).

B1: Action ADT + canonical hash + pure ``apply_action`` + depth guard.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from persistence.plan._ast import Node
from persistence.plan._errors import PlanDepthExceeded

if TYPE_CHECKING:
    from persistence.plan._skill_library import SkillLibrary

__all__ = [
    "Action",
    "AddStepAction",
    "ComposeWithSkillAction",
    "MAX_PLAN_DEPTH",
    "SubstituteLeafAction",
    "apply_action",
]

#: Plan-depth ceiling enforced post-construction by ``apply_action``;
#: ``ComposeWithSkillAction`` additionally pre-checks against
#: ``MAX_PLAN_DEPTH // 2`` (design §6 two-layer guard).
MAX_PLAN_DEPTH: int = 32

#: Tags whose Nodes MUST be leaves; empty in v0.6.5 (forward-compat).
_LEAF_REQUIRED_TAGS: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class SubstituteLeafAction:
    """Replace the Node at ``target_path`` with ``new_leaf``."""
    target_path: tuple[int, ...]
    new_leaf: Node


@dataclass(frozen=True, slots=True)
class AddStepAction:
    """Insert ``new_child`` at index ``at`` under the Node at ``target_path``."""
    target_path: tuple[int, ...]
    at: int
    new_child: Node


@dataclass(frozen=True, slots=True)
class ComposeWithSkillAction:
    """Wrap the subtree at ``target_path`` inside the registered skill ``skill_id``."""
    target_path: tuple[int, ...]
    skill_id: str


#: Closed Action union; dispatch is ``isinstance``-strict (design §5).
Action = Union[SubstituteLeafAction, AddStepAction, ComposeWithSkillAction]


def _action_hash(action: Action) -> str:
    """Return sha256 hex of ``action``'s structural payload (design §5).

    Nested Nodes contribute via ``Node.id`` (the ``_ast.py`` canonical-
    form helper), NOT via ``dataclasses.asdict`` recursion."""
    path_list = list(action.target_path)
    if isinstance(action, SubstituteLeafAction):
        payload: dict[str, object] = {
            "kind": "SubstituteLeafAction",
            "target_path": path_list,
            "new_leaf_id": action.new_leaf.id,
        }
    elif isinstance(action, AddStepAction):
        payload = {
            "kind": "AddStepAction",
            "target_path": path_list,
            "at": action.at,
            "new_child_id": action.new_child.id,
        }
    elif isinstance(action, ComposeWithSkillAction):
        payload = {
            "kind": "ComposeWithSkillAction",
            "target_path": path_list,
            "skill_id": action.skill_id,
        }
    else:
        raise ValueError(f"unknown action kind: {type(action).__name__}")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _plan_depth(plan: Node) -> int:
    """Return depth of ``plan`` (leaf = 1; recursive max+1)."""
    if not plan.children:
        return 1
    return 1 + max(_plan_depth(c) for c in plan.children)


def _resolve_path(plan: Node, path: tuple[int, ...]) -> Node:
    """Walk ``plan`` along child indices in ``path``; raise ``IndexError`` on miss."""
    node = plan
    for i, idx in enumerate(path):
        if idx < 0 or idx >= len(node.children):
            raise IndexError(
                f"path {path!r} step {i} index {idx} out of range "
                f"(parent has {len(node.children)} children)"
            )
        node = node.children[idx]
    return node


def _replace_at_path(plan: Node, path: tuple[int, ...], replacement: Node) -> Node:
    """Return a new tree with ``path`` swapped for ``replacement`` (pure recursion)."""
    if not path:
        return replacement
    head, *rest = path
    if head < 0 or head >= len(plan.children):
        raise IndexError(
            f"path step index {head} out of range "
            f"(parent has {len(plan.children)} children)"
        )
    new_child = _replace_at_path(plan.children[head], tuple(rest), replacement)
    new_children = (*plan.children[:head], new_child, *plan.children[head + 1 :])
    return Node(tag=plan.tag, attrs=plan.attrs, children=new_children)


def _apply_add_step(plan: Node, action: AddStepAction) -> Node:
    """Insert ``action.new_child`` at ``action.at`` under ``action.target_path``."""
    parent = _resolve_path(plan, action.target_path)
    at = action.at
    if at < 0 or at > len(parent.children):
        raise IndexError(
            f"AddStepAction.at {at} out of range "
            f"(parent has {len(parent.children)} children)"
        )
    new_children = (*parent.children[:at], action.new_child, *parent.children[at:])
    new_parent = Node(tag=parent.tag, attrs=parent.attrs, children=new_children)
    return _replace_at_path(plan, action.target_path, new_parent)


def _apply_compose_with_skill(
    plan: Node,
    action: ComposeWithSkillAction,
    skill_library: "SkillLibrary | None",
) -> Node:
    """Wrap subtree at ``target_path`` inside the looked-up skill plan (design §6)."""
    if skill_library is None:
        raise ValueError("ComposeWithSkillAction requires skill_library")
    looked_up = skill_library.lookup(action.skill_id)
    if looked_up is None:
        raise ValueError(
            f"ComposeWithSkillAction: skill_id {action.skill_id!r} not registered"
        )
    skill_plan, _record = looked_up
    skill_depth = _plan_depth(skill_plan)
    if skill_depth > MAX_PLAN_DEPTH // 2:
        raise PlanDepthExceeded(
            f"ComposeWithSkillAction: skill {action.skill_id!r} plan "
            f"depth {skill_depth} > MAX_PLAN_DEPTH//2={MAX_PLAN_DEPTH // 2}"
        )
    subtree = _resolve_path(plan, action.target_path)
    new_skill_root = Node(
        tag=skill_plan.tag,
        attrs=skill_plan.attrs,
        children=(subtree, *skill_plan.children),
    )
    return _replace_at_path(plan, action.target_path, new_skill_root)


def apply_action(
    plan: Node,
    action: Action,
    *,
    skill_library: "SkillLibrary | None" = None,
) -> Node:
    """Return ``plan`` with ``action`` applied (pure; design §6).

    Dispatch is ``isinstance``-strict (third-party look-alikes raise
    ``ValueError``). Result depth is post-checked against
    ``MAX_PLAN_DEPTH``; overflow raises ``PlanDepthExceeded``."""
    if isinstance(action, SubstituteLeafAction):
        # _replace_at_path raises IndexError on invalid target_path.
        new_plan = _replace_at_path(plan, action.target_path, action.new_leaf)
    elif isinstance(action, AddStepAction):
        new_plan = _apply_add_step(plan, action)
    elif isinstance(action, ComposeWithSkillAction):
        new_plan = _apply_compose_with_skill(plan, action, skill_library)
    else:
        raise ValueError(f"unknown action kind: {type(action).__name__}")
    new_depth = _plan_depth(new_plan)
    if new_depth > MAX_PLAN_DEPTH:
        raise PlanDepthExceeded(
            f"apply_action produced a plan of depth {new_depth} "
            f"> MAX_PLAN_DEPTH={MAX_PLAN_DEPTH}"
        )
    return new_plan


# B2-B9 to follow
