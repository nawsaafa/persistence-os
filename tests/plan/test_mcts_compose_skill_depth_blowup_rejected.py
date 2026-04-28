"""B1 — `ComposeWithSkillAction` two-layer depth guard (design §6).

Two layers, both raising ``PlanDepthExceeded``:

* Pre-check: skill plan depth > MAX_PLAN_DEPTH // 2 = 16. Cheap rejection
  before any Node construction (skills nested ~16 deep would blow up the
  next iteration regardless of where they're composed).
* Post-check: result plan depth > MAX_PLAN_DEPTH = 32. Catches the case
  where a thin skill is composed deep inside a tall candidate.

Plus the happy path: a thin skill composed at a shallow path is accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from persistence.fact.db import DB
from persistence.fact.store import InMemoryStore
from persistence.plan import (
    ComposeWithSkillAction,
    MAX_PLAN_DEPTH,
    Node,
    PlanDepthExceeded,
    SkillLibrary,
    apply_action,
)


# --- Fixtures ------------------------------------------------------------- #


def _frozen_clock(start_ms: int = 1_000_000):
    counter = [start_ms]

    def _now() -> datetime:
        ts = datetime.fromtimestamp(counter[0] / 1000, tz=timezone.utc)
        counter[0] += 1
        return ts

    return _now


def _make_lib() -> SkillLibrary:
    return SkillLibrary(DB(InMemoryStore(), clock=_frozen_clock()))


@dataclass(frozen=True, slots=True)
class _StubPromotionRecord:
    promotion_id: str


def _chain(depth: int, *, leaf_tag: str = ":leaf/x") -> Node:
    """Return a single-child chain of given total depth (depth >= 1)."""
    if depth < 1:
        raise ValueError("depth must be >= 1")
    node = Node(tag=leaf_tag, attrs={"prompt": "x"})
    for i in range(depth - 1):
        node = Node(tag=":seq", attrs={"step": str(i)}, children=(node,))
    return node


def _deep_path(depth: int) -> tuple[int, ...]:
    """Return (0,) * (depth - 1) — the path to the leaf in a chain of given depth."""
    return (0,) * (depth - 1)


def _register_skill(plan: Node, lib: SkillLibrary, *, pid: str = "p-1") -> str:
    return lib.register(
        plan,
        _StubPromotionRecord(promotion_id=pid),
        registered_at_ms=1234,
    )


# --- Pre-check: skill plan deeper than MAX_PLAN_DEPTH // 2 -------------- #


def test_skill_plan_depth_exceeds_half_is_rejected_pre_check():
    """A skill plan of depth 18 (> 32 // 2 = 16) is rejected before construction."""
    half = MAX_PLAN_DEPTH // 2
    skill_plan = _chain(half + 2)  # depth 18
    assert _verify_depth(skill_plan, half + 2)

    lib = _make_lib()
    skill_id = _register_skill(skill_plan, lib)

    base = Node(tag=":seq", children=(Node(tag=":leaf/x", attrs={"prompt": "x"}),))
    action = ComposeWithSkillAction(target_path=(0,), skill_id=skill_id)
    with pytest.raises(PlanDepthExceeded, match="MAX_PLAN_DEPTH//2"):
        apply_action(base, action, skill_library=lib)


def test_skill_plan_at_half_boundary_passes_pre_check():
    """A skill plan of depth 16 (== MAX_PLAN_DEPTH // 2) passes the pre-check."""
    half = MAX_PLAN_DEPTH // 2
    skill_plan = _chain(half)  # depth 16

    lib = _make_lib()
    skill_id = _register_skill(skill_plan, lib)

    base = Node(tag=":seq", children=(Node(tag=":leaf/x", attrs={"prompt": "x"}),))
    action = ComposeWithSkillAction(target_path=(0,), skill_id=skill_id)
    # Compose at target_path=(0,) → new structure depth 1 + (16 grafting a
    # depth-1 leaf as first child) = 1 + max(16, 2) = 17 < 32. Should succeed.
    result = apply_action(base, action, skill_library=lib)
    assert _plan_depth_ext(result) <= MAX_PLAN_DEPTH


# --- Post-check: result deeper than MAX_PLAN_DEPTH ---------------------- #


def test_thin_skill_composed_at_deep_target_blows_post_check():
    """A depth-4 skill composed deep in a depth-30 candidate exceeds MAX_PLAN_DEPTH."""
    # Tall candidate: chain of depth 30 (root at depth 1, leaf at depth 30).
    candidate = _chain(30)
    # Thin skill: depth 4.
    skill_plan = _chain(4)

    lib = _make_lib()
    skill_id = _register_skill(skill_plan, lib)

    # target_path = (0,) * 29 → reaches the leaf at depth 30.
    target_path = _deep_path(30)
    action = ComposeWithSkillAction(target_path=target_path, skill_id=skill_id)
    # Replacing the depth-30 leaf with the depth-4 skill (which grafts the
    # original leaf as its first child) → resulting structure rooted at
    # depth 30 has depth max(4, 1 + 1) = 4. Total depth = 29 + 4 = 33 > 32.
    with pytest.raises(PlanDepthExceeded, match=f"> MAX_PLAN_DEPTH={MAX_PLAN_DEPTH}"):
        apply_action(candidate, action, skill_library=lib)


# --- Happy path: thin skill at shallow target ---------------------------- #


def test_thin_skill_at_shallow_target_passes_both_checks():
    """A depth-4 skill composed at a shallow target stays within MAX_PLAN_DEPTH."""
    candidate = _chain(10)  # depth 10
    skill_plan = _chain(4)  # depth 4

    lib = _make_lib()
    skill_id = _register_skill(skill_plan, lib)

    # target_path = (0,) * 5 → at depth 6. Subtree there has remaining depth 5.
    # New structure at depth 6: max(4, 1 + 5) = 6. Total depth = 5 + 6 = 11.
    target_path = (0,) * 5
    action = ComposeWithSkillAction(target_path=target_path, skill_id=skill_id)
    result = apply_action(candidate, action, skill_library=lib)
    assert _plan_depth_ext(result) <= MAX_PLAN_DEPTH


# --- Local depth helpers (test-local; mirrors module-private _plan_depth) #


def _plan_depth_ext(plan: Node) -> int:
    """Test-local copy of `_plan_depth` so tests don't depend on a private symbol."""
    if not plan.children:
        return 1
    return 1 + max(_plan_depth_ext(c) for c in plan.children)


def _verify_depth(plan: Node, expected: int) -> bool:
    """Assert the chain fixture really has the depth we asked for."""
    return _plan_depth_ext(plan) == expected
