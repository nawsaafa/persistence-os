"""Phase 2.3c.2 G1 — DispatcherContext lifecycle.

Covers test gate G1 from
``docs/plans/2026-05-08-phase-2.3c.2-recursion-composition-design.md`` §4:

  * G1.1 enter_call / exit_call increment depth + request_count correctly
  * G1.2 ContextVar binding survives nested dispatch via
         ``dispatcher_context(ctx)`` context manager + token reset on exit
  * G1.3 Fresh DispatcherContext on outermost — initial state checked
  * G1.4 Bounds — enter_call raises ``LLMRecursionBudgetExceeded(field="depth")``
         when next_depth > ``MAX_LLM_CALL_DEPTH`` (parametrized boundary)
  * G1.5 Bounds — enter_call raises ``LLMRecursionBudgetExceeded(field="requests")``
         when request_count > ``MAX_RECURSIVE_REQUESTS`` (parametrized boundary)
  * G1.6 exit_call decrements depth; request_count is NOT decremented
         (cumulative across recursion tree per LD1)
  * G1.7 ``SkillCycleDetected`` is a subclass of ``_PlanCycleDetected``
         (issubclass + isinstance)
  * G1.8 ``LLMRecursionBudgetExceeded`` exposes field/limit/observed as
         attributes; bogus field rejected
  * G1.9 ContextVar token reset works across sequential
         ``dispatcher_context(...)`` invocations — no state bleed.

T1 scope is pure types + ContextVar plumbing + bounds-check helpers; the
4-layer token enforcement (Layers 1-4 per LD1) integrates with the actual
``:llm/call`` middleware in T3 and is NOT tested here.
"""
from __future__ import annotations

import pytest

from persistence.coder._recursion import (
    DispatcherContext,
    LLMRecursionBudgetExceeded,
    MAX_LLM_CALL_DEPTH,
    MAX_RECURSIVE_REQUESTS,
    MAX_RECURSIVE_TOKENS,
    RecursionBudget,
    SkillCycleDetected,
    current_dispatcher_context,
    dispatcher_context,
    enter_call,
    exit_call,
    pop_cycle,
    push_cycle,
)
from persistence.plan._mcts import _PlanCycleDetected


# ---------------------------------------------------------------------------
# Module-level constants pinned at design-doc values (LD1)
# ---------------------------------------------------------------------------


def test_module_constants_pin_design_values() -> None:
    """LD1 hard cap + soft caps match the design-frozen values."""
    assert MAX_LLM_CALL_DEPTH == 3
    assert MAX_RECURSIVE_TOKENS == 20_000
    assert MAX_RECURSIVE_REQUESTS == 10


def test_recursion_budget_default_matches_constants() -> None:
    """RecursionBudget() defaults match the module-level constants."""
    b = RecursionBudget()
    assert b.max_depth == MAX_LLM_CALL_DEPTH
    assert b.max_tokens == MAX_RECURSIVE_TOKENS
    assert b.max_requests == MAX_RECURSIVE_REQUESTS


def test_recursion_budget_is_frozen() -> None:
    """RecursionBudget is a frozen dataclass — overrides go via construction."""
    b = RecursionBudget(max_depth=5, max_tokens=100, max_requests=2)
    assert b.max_depth == 5
    assert b.max_tokens == 100
    assert b.max_requests == 2
    with pytest.raises((AttributeError, Exception)):
        # FrozenInstanceError or AttributeError depending on dataclass impl
        b.max_depth = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# G1.3 — Fresh DispatcherContext on outermost (initial state)
# ---------------------------------------------------------------------------


def test_g1_3_dispatcher_context_initial_state() -> None:
    """Outermost DispatcherContext starts at depth=0, tokens=0, requests=0."""
    ctx = DispatcherContext()
    assert ctx.depth == 0
    assert ctx.token_count == 0
    assert ctx.request_count == 0
    assert ctx.cycle_path == []
    assert ctx.parent_audit_entry_id is None
    assert isinstance(ctx.budget, RecursionBudget)
    assert ctx.budget.max_depth == MAX_LLM_CALL_DEPTH


def test_g1_3_dispatcher_context_cycle_path_is_independent() -> None:
    """default_factory=list — separate ctxs do not share the same list."""
    a = DispatcherContext()
    b = DispatcherContext()
    a.cycle_path.append("hash-a")
    assert b.cycle_path == []  # if shared default, would be ["hash-a"]


# ---------------------------------------------------------------------------
# G1.1 — enter_call/exit_call counter increments
# ---------------------------------------------------------------------------


def test_g1_1_enter_call_increments_depth_and_requests() -> None:
    """One enter_call: depth 0->1, request_count 0->1."""
    ctx = DispatcherContext()
    enter_call(ctx)
    assert ctx.depth == 1
    assert ctx.request_count == 1


def test_g1_1_enter_call_nested_increments() -> None:
    """Three nested enters: depth 0->1->2->3 (allowed under MAX=3)."""
    ctx = DispatcherContext()
    enter_call(ctx)
    enter_call(ctx)
    enter_call(ctx)
    assert ctx.depth == 3
    assert ctx.request_count == 3


# ---------------------------------------------------------------------------
# G1.6 — exit_call decrements depth; request_count is cumulative (NOT reset)
# ---------------------------------------------------------------------------


def test_g1_6_exit_call_decrements_depth() -> None:
    """enter then exit returns depth to 0; request_count stays at 1."""
    ctx = DispatcherContext()
    enter_call(ctx)
    assert ctx.depth == 1
    assert ctx.request_count == 1
    exit_call(ctx)
    assert ctx.depth == 0
    # LD1: request_count is cumulative across recursion tree — NOT decremented
    assert ctx.request_count == 1


def test_g1_6_exit_call_pop_unwinds_nested() -> None:
    """Push 2 levels, pop 2 — depth back to 0, request_count cumulative."""
    ctx = DispatcherContext()
    enter_call(ctx)
    enter_call(ctx)
    assert ctx.depth == 2
    exit_call(ctx)
    assert ctx.depth == 1
    exit_call(ctx)
    assert ctx.depth == 0
    assert ctx.request_count == 2  # cumulative, never decremented


# ---------------------------------------------------------------------------
# G1.4 — Depth bound: > strict, equality allowed (boundary test)
# ---------------------------------------------------------------------------


def test_g1_4_depth_boundary_max_allowed() -> None:
    """next_depth == MAX_LLM_CALL_DEPTH is allowed (post-increment > strict).

    With MAX=3, allowed depths are {1,2,3}. Three sequential enter_call
    calls drive depth to exactly 3 with no raise.
    """
    ctx = DispatcherContext()
    for _ in range(MAX_LLM_CALL_DEPTH):
        enter_call(ctx)
    assert ctx.depth == MAX_LLM_CALL_DEPTH


def test_g1_4_depth_boundary_overflow_rejected() -> None:
    """next_depth == MAX_LLM_CALL_DEPTH + 1 raises with field="depth"."""
    ctx = DispatcherContext()
    for _ in range(MAX_LLM_CALL_DEPTH):
        enter_call(ctx)
    # 4th enter -> depth=4 > MAX=3 -> raise
    with pytest.raises(LLMRecursionBudgetExceeded) as exc_info:
        enter_call(ctx)
    assert exc_info.value.field == "depth"
    assert exc_info.value.limit == MAX_LLM_CALL_DEPTH
    assert exc_info.value.observed == MAX_LLM_CALL_DEPTH + 1


@pytest.mark.parametrize(
    "next_depth, should_raise",
    [
        (MAX_LLM_CALL_DEPTH, False),       # boundary allowed
        (MAX_LLM_CALL_DEPTH + 1, True),    # boundary + 1 rejected
    ],
)
def test_g1_4_depth_boundary_parametrized(next_depth: int, should_raise: bool) -> None:
    """Parametrized G7-style depth boundary: MAX allowed, MAX+1 rejected."""
    ctx = DispatcherContext()
    # Drive depth to next_depth - 1 first, then attempt one more enter
    for _ in range(next_depth - 1):
        enter_call(ctx)
    if should_raise:
        with pytest.raises(LLMRecursionBudgetExceeded) as exc_info:
            enter_call(ctx)
        assert exc_info.value.field == "depth"
    else:
        enter_call(ctx)
        assert ctx.depth == next_depth


# ---------------------------------------------------------------------------
# G1.5 — Request bound: > strict, equality allowed (boundary test)
# ---------------------------------------------------------------------------


def test_g1_5_request_boundary_overflow_rejected() -> None:
    """request_count > MAX_RECURSIVE_REQUESTS raises with field="requests".

    Use a custom budget with a low request cap to drive overflow before
    the depth cap fires (since MAX_RECURSIVE_REQUESTS=10 > MAX_LLM_CALL_DEPTH=3
    in the default budget; we can't naturally hit it without unwind).

    Pattern: small budget + interleaved enter/exit so depth stays low.
    """
    budget = RecursionBudget(max_depth=10, max_tokens=20_000, max_requests=2)
    ctx = DispatcherContext(budget=budget)
    # 1st enter: requests 0->1, depth 0->1 (OK)
    enter_call(ctx)
    exit_call(ctx)  # depth back to 0; request_count stays 1
    # 2nd enter: requests 1->2, depth 0->1 (OK; 2 == max_requests, allowed)
    enter_call(ctx)
    exit_call(ctx)  # request_count stays 2
    assert ctx.request_count == 2
    # 3rd enter: requests 2->3, depth 0->1 — 3 > max_requests=2 -> RAISE
    with pytest.raises(LLMRecursionBudgetExceeded) as exc_info:
        enter_call(ctx)
    assert exc_info.value.field == "requests"
    assert exc_info.value.limit == 2
    assert exc_info.value.observed == 3


@pytest.mark.parametrize(
    "request_overflow",
    [False, True],
)
def test_g1_5_request_boundary_parametrized(request_overflow: bool) -> None:
    """request_count == max_requests allowed; +1 rejected."""
    budget = RecursionBudget(max_depth=10, max_tokens=20_000, max_requests=3)
    ctx = DispatcherContext(budget=budget)
    # Drive request_count to max_requests via interleaved enter/exit
    for _ in range(budget.max_requests):
        enter_call(ctx)
        exit_call(ctx)
    assert ctx.request_count == budget.max_requests
    if request_overflow:
        with pytest.raises(LLMRecursionBudgetExceeded) as exc_info:
            enter_call(ctx)
        assert exc_info.value.field == "requests"
    # No-op when request_overflow=False — the body above already
    # asserts the equality-boundary is allowed without raise.


# ---------------------------------------------------------------------------
# G1.8 — LLMRecursionBudgetExceeded constructor + attributes
# ---------------------------------------------------------------------------


def test_g1_8_exception_carries_attributes() -> None:
    """field/limit/observed are accessible as attributes."""
    e = LLMRecursionBudgetExceeded(field="depth", limit=3, observed=4)
    assert e.field == "depth"
    assert e.limit == 3
    assert e.observed == 4


def test_g1_8_exception_str_includes_all_three() -> None:
    """__str__ surfaces field/limit/observed for debug clarity."""
    e = LLMRecursionBudgetExceeded(field="tokens", limit=20_000, observed=20_500)
    s = str(e)
    assert "tokens" in s
    assert "20000" in s or "20_000" in s or "20,000" in s
    assert "20500" in s or "20_500" in s or "20,500" in s


@pytest.mark.parametrize("field", ["depth", "tokens", "requests"])
def test_g1_8_exception_accepts_valid_fields(field: str) -> None:
    """field must be one of {'depth','tokens','requests'}."""
    e = LLMRecursionBudgetExceeded(field=field, limit=1, observed=2)
    assert e.field == field


@pytest.mark.parametrize("bogus", ["", "depths", "DEPTH", "size", "memory", "x"])
def test_g1_8_exception_rejects_bogus_fields(bogus: str) -> None:
    """Constructor rejects unknown field strings."""
    with pytest.raises(ValueError):
        LLMRecursionBudgetExceeded(field=bogus, limit=1, observed=2)


# ---------------------------------------------------------------------------
# G1.7 — SkillCycleDetected subclass of _PlanCycleDetected
# ---------------------------------------------------------------------------


def test_g1_7_skill_cycle_detected_is_plan_cycle_subclass() -> None:
    """SkillCycleDetected inherits from _PlanCycleDetected (LD2)."""
    assert issubclass(SkillCycleDetected, _PlanCycleDetected)


def test_g1_7_skill_cycle_detected_isinstance() -> None:
    """An instance of SkillCycleDetected is also a _PlanCycleDetected."""
    e = SkillCycleDetected("skill A re-entered active path")
    assert isinstance(e, _PlanCycleDetected)
    assert isinstance(e, ValueError)  # _PlanCycleDetected itself is ValueError


def test_g1_7_skill_cycle_detected_constructor_signature() -> None:
    """SkillCycleDetected accepts a message arg (mirrors parent ValueError)."""
    msg = "skill A active in cycle_path"
    e = SkillCycleDetected(msg)
    assert msg in str(e)


# ---------------------------------------------------------------------------
# G1.2 — ContextVar binding via dispatcher_context() context manager
# ---------------------------------------------------------------------------


def test_g1_2_unbound_returns_none() -> None:
    """Outside any dispatcher_context() block, current_*() returns None."""
    assert current_dispatcher_context() is None


def test_g1_2_bind_via_context_manager() -> None:
    """Inside dispatcher_context(ctx), current_*() returns the bound ctx."""
    ctx = DispatcherContext()
    with dispatcher_context(ctx):
        assert current_dispatcher_context() is ctx


def test_g1_2_unbinds_on_exit() -> None:
    """After dispatcher_context() block exits, current_*() returns None again."""
    ctx = DispatcherContext()
    with dispatcher_context(ctx):
        pass
    assert current_dispatcher_context() is None


def test_g1_2_unbinds_on_exception() -> None:
    """Exception inside the block still resets the ContextVar token."""
    ctx = DispatcherContext()
    with pytest.raises(RuntimeError, match="boom"):
        with dispatcher_context(ctx):
            assert current_dispatcher_context() is ctx
            raise RuntimeError("boom")
    assert current_dispatcher_context() is None


# ---------------------------------------------------------------------------
# G1.9 — Sequential dispatcher_context() invocations don't bleed state
# ---------------------------------------------------------------------------


def test_g1_9_sequential_bind_unbind_no_bleed() -> None:
    """Two sequential dispatcher_context() blocks see distinct ctxs."""
    ctx_a = DispatcherContext()
    ctx_b = DispatcherContext()
    with dispatcher_context(ctx_a):
        assert current_dispatcher_context() is ctx_a
    # Between blocks: unbound
    assert current_dispatcher_context() is None
    with dispatcher_context(ctx_b):
        assert current_dispatcher_context() is ctx_b
        assert current_dispatcher_context() is not ctx_a
    assert current_dispatcher_context() is None


def test_g1_9_nested_bind_restores_outer() -> None:
    """Nested dispatcher_context blocks: inner binds ctx_b; on exit, ctx_a restored."""
    ctx_a = DispatcherContext()
    ctx_b = DispatcherContext()
    with dispatcher_context(ctx_a):
        assert current_dispatcher_context() is ctx_a
        with dispatcher_context(ctx_b):
            assert current_dispatcher_context() is ctx_b
        # Inner exited; outer ctx_a restored via token reset
        assert current_dispatcher_context() is ctx_a
    assert current_dispatcher_context() is None


# ---------------------------------------------------------------------------
# Token-count field present + cumulative semantics (LD1 placeholder for T3)
# ---------------------------------------------------------------------------


def test_token_count_field_initial_zero_and_mutable() -> None:
    """token_count field exists; T3 will populate via Layer 4 post-call accounting."""
    ctx = DispatcherContext()
    assert ctx.token_count == 0
    # Mutable dataclass — direct increment is the post-call accounting path
    ctx.token_count += 500
    assert ctx.token_count == 500


# ---------------------------------------------------------------------------
# T3-added — cycle_path push/pop API surface (LD2 Layer B)
# ---------------------------------------------------------------------------


def test_push_cycle_appends_to_cycle_path() -> None:
    """push_cycle adds the content_hash to ctx.cycle_path."""
    ctx = DispatcherContext()
    push_cycle(ctx, "sha256:hash-A")
    assert ctx.cycle_path == ["sha256:hash-A"]
    push_cycle(ctx, "sha256:hash-B")
    assert ctx.cycle_path == ["sha256:hash-A", "sha256:hash-B"]


def test_push_cycle_raises_on_active_duplicate() -> None:
    """push_cycle raises SkillCycleDetected when hash already in cycle_path."""
    ctx = DispatcherContext()
    push_cycle(ctx, "sha256:hash-A")
    push_cycle(ctx, "sha256:hash-B")
    with pytest.raises(SkillCycleDetected) as exc_info:
        push_cycle(ctx, "sha256:hash-A")  # already active
    assert "sha256:hash-A" in str(exc_info.value)
    # Original path unchanged after raise
    assert ctx.cycle_path == ["sha256:hash-A", "sha256:hash-B"]


def test_push_cycle_skill_cycle_detected_is_plan_cycle_subclass() -> None:
    """The raised exception is catchable as _PlanCycleDetected (LD2 type
    relation — search loop's existing except covers it)."""
    ctx = DispatcherContext()
    push_cycle(ctx, "sha256:hash-A")
    with pytest.raises(_PlanCycleDetected):
        push_cycle(ctx, "sha256:hash-A")


def test_pop_cycle_lifo_discipline() -> None:
    """pop_cycle removes the matching top-of-stack hash."""
    ctx = DispatcherContext()
    push_cycle(ctx, "sha256:hash-A")
    push_cycle(ctx, "sha256:hash-B")
    pop_cycle(ctx, "sha256:hash-B")
    assert ctx.cycle_path == ["sha256:hash-A"]
    pop_cycle(ctx, "sha256:hash-A")
    assert ctx.cycle_path == []


def test_pop_cycle_raises_on_mismatch() -> None:
    """pop_cycle raises RuntimeError on stack discipline violation."""
    ctx = DispatcherContext()
    push_cycle(ctx, "sha256:hash-A")
    with pytest.raises(RuntimeError, match="cycle_path stack discipline"):
        pop_cycle(ctx, "sha256:hash-WRONG")
    # cycle_path unchanged — pop did not occur
    assert ctx.cycle_path == ["sha256:hash-A"]


def test_pop_cycle_raises_on_empty_path() -> None:
    """pop_cycle on empty cycle_path is a stack discipline violation."""
    ctx = DispatcherContext()
    with pytest.raises(RuntimeError, match="cycle_path stack discipline"):
        pop_cycle(ctx, "sha256:hash-anything")


def test_push_pop_allows_reuse_after_unwind() -> None:
    """LD2 G6.3 sequential reuse: A → completes → A again is OK."""
    ctx = DispatcherContext()
    push_cycle(ctx, "sha256:hash-A")
    pop_cycle(ctx, "sha256:hash-A")
    # A is no longer in cycle_path; pushing again must NOT raise
    push_cycle(ctx, "sha256:hash-A")
    assert ctx.cycle_path == ["sha256:hash-A"]


def test_push_cycle_keys_full_content_hash_not_skill_id() -> None:
    """LD2 R0-fold B3 — keying is FULL plan.id (32-hex), not 16-hex skill_id slice.

    Two distinct skill_ids that share the same content_hash MUST be
    treated as the same cycle key. Here we simulate by using the same
    content_hash with different "skill_id-shaped" probes — push_cycle
    only sees the content_hash argument so the alias case is naturally
    covered.
    """
    ctx = DispatcherContext()
    full_hash = "sha256:" + "f" * 64
    push_cycle(ctx, full_hash)
    # A re-entry attempt with the SAME content_hash (regardless of
    # which skill_id alias requested it) MUST raise.
    with pytest.raises(SkillCycleDetected):
        push_cycle(ctx, full_hash)
