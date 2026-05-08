"""Phase 2.3c.2 G2 — recursion-budget enforcement at the audit-stack middleware.

Covers test gate G2 from
``docs/plans/2026-05-08-phase-2.3c.2-recursion-composition-design.md`` §4:

  * G2.1 ``LLMRecursionBudgetExceeded(field="depth")`` raised when next_depth
         > MAX (parametrized MAX vs MAX+1 boundary)
  * G2.2 ``LLMRecursionBudgetExceeded(field="tokens")`` raised when token
         budget exceeded — Layer 1 (pre-call: ``token_count >= max``) AND
         Layer 2 (pre-call: estimated input would push over)
  * G2.3 ``LLMRecursionBudgetExceeded(field="requests")`` raised when
         request_count > MAX
  * G2.4 UNIFIED budget across recursion + composition (LD4) — sequential
         performs from a single ``coder.run()`` cycle all share ONE
         DispatcherContext counter
  * G2.5 Layer 4 post-call accounting: provider returns
         ``usage.total_tokens`` — ``ctx.token_count`` increments by that
  * G2.6 Layer 4 fallback: provider returns no ``usage.total_tokens`` —
         ``ctx.token_count`` increments by
         ``estimated_input + injected_max_tokens`` (conservative overcount)
  * G2.7 Layer 3 output-cap injection: ``args["max_tokens"]`` is set to
         ``min(existing, remaining)`` BEFORE ``k(args)`` is called

T3 wires DispatcherContext via a NEW dispatcher handler installed in
``canonical_audit_stack`` ABOVE the audit middleware. The dispatcher
handler is transparent (pass-through) when no DispatcherContext is bound;
it activates only inside a ``dispatcher_context(ctx)`` block.

Fixture pattern: ``canonical_audit_stack(entries)`` + bottom-of-stack
``make_echo_llm_handler()`` + ``with_runtime(rt): with dispatcher_context(ctx): perform(...)``.
The 2.3b T6 broken pattern (``s.effect.perform = scripted_fn`` direct
attribute assignment) is NOT used — that bypasses the runtime stack.
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
    dispatcher_context,
)
from persistence.effect import canonical_audit_stack, perform, with_runtime
from persistence.effect.handlers.audit import AuditEntry
from persistence.effect.handlers.raw import make_echo_llm_handler
from persistence.effect.runtime import Handler


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _build_runtime_with_provider(
    entries: list[AuditEntry] | None = None,
    *,
    usage_total_tokens: int | None = 12,
):
    """Build a Runtime with the canonical audit stack + a configurable
    bottom-of-stack ``:llm/call`` provider.

    ``usage_total_tokens``: when not None, the provider returns
    ``{"text": ..., "usage": {"total_tokens": N}, ...}``. When None, the
    provider returns the result without a ``total_tokens`` key — exercises
    Layer 4 fallback (G2.6).
    """
    if entries is None:
        entries = []
    rt = canonical_audit_stack(entries)

    def clause(args, k, ctx):
        messages = args.get("messages", [])
        content = messages[-1].get("content", "") if messages else ""
        result: dict = {
            "text": f"echo:{content}",
            "fingerprint": f"mock:{args.get('model', 'unknown')}",
            "max_tokens_seen": args.get("max_tokens"),
        }
        if usage_total_tokens is not None:
            result["usage"] = {"total_tokens": usage_total_tokens}
        else:
            # No usage at all — Layer 4 fallback path
            pass
        return result

    provider = Handler(
        name="raw-echo",
        wraps={":llm/call"},
        clauses={":llm/call": clause},
    )
    rt.handlers.insert(0, provider)
    return rt, entries


def _perform_call(content: str = "hi", *, model: str = "test-model") -> dict:
    """One round-trip ``:llm/call`` perform."""
    return perform(
        ":llm/call",
        model=model,
        messages=[{"role": "user", "content": content}],
    )


# ---------------------------------------------------------------------------
# G2.1 — Depth bound enforced inside middleware
# ---------------------------------------------------------------------------


def test_g2_1_depth_bound_no_dispatcher_no_enforcement() -> None:
    """No DispatcherContext bound -> middleware is transparent.

    Without ``dispatcher_context(ctx)``, the dispatcher handler must NOT
    enforce any bounds (pre-existing 2.1b callers must still work).
    """
    rt, entries = _build_runtime_with_provider()
    with with_runtime(rt):
        # Many sequential calls — without bound DispatcherContext, no
        # bound check ever fires. (Sequential means depth peaks at 1 per
        # call and the dispatcher handler has nothing to enforce against.)
        for _ in range(5):
            _perform_call("ping")
    assert len([e for e in entries if e.op == ":llm/call"]) == 5


@pytest.mark.parametrize(
    "max_depth, pre_bump, expect_raise",
    [
        (3, 0, False),  # fresh ctx, sequential -> no raise
        (3, 2, False),  # pre-bump 2 -> middleware bumps to 3 == MAX, OK
        (3, 3, True),   # pre-bump 3 -> middleware bumps to 4 > MAX -> raise
        (1, 1, True),   # pre-bump 1 -> middleware bumps to 2 > MAX=1 -> raise
    ],
)
def test_g2_1_depth_bound_with_low_budget(
    max_depth: int, pre_bump: int, expect_raise: bool
) -> None:
    """With a tight depth budget, simulate Python-stack-nested call.

    The middleware's ``enter_call`` increments ``ctx.depth`` post-fashion
    and raises if depth > max_depth. Sequential calls in this fixture
    don't stack (each call's exit decrements depth back to 0); to exercise
    the bound, we PRE-BUMP ``ctx.depth`` before the perform, simulating
    an outer call that's still on the Python stack.
    """
    budget = RecursionBudget(
        max_depth=max_depth,
        max_tokens=20_000,
        max_requests=10,
    )
    ctx = DispatcherContext(budget=budget)
    ctx.depth = pre_bump
    rt, _ = _build_runtime_with_provider()
    with with_runtime(rt), dispatcher_context(ctx):
        if expect_raise:
            with pytest.raises(LLMRecursionBudgetExceeded) as exc_info:
                _perform_call()
            assert exc_info.value.field == "depth"
            assert exc_info.value.limit == max_depth
            assert exc_info.value.observed == pre_bump + 1
        else:
            _perform_call()


def test_g2_1_depth_bound_max_allowed_when_no_pre_bump() -> None:
    """next_depth == MAX is allowed (post-increment > strict).

    Make MAX_LLM_CALL_DEPTH calls sequentially without pre-bump; depth
    peaks at 1 per call (sequential), which is well under MAX=3.
    """
    ctx = DispatcherContext()
    rt, entries = _build_runtime_with_provider()
    with with_runtime(rt), dispatcher_context(ctx):
        for _ in range(MAX_LLM_CALL_DEPTH):
            _perform_call()
    assert len([e for e in entries if e.op == ":llm/call"]) == MAX_LLM_CALL_DEPTH
    # Sequential calls — depth peaked at 1 per call, decremented on exit
    assert ctx.depth == 0
    assert ctx.request_count == MAX_LLM_CALL_DEPTH


# ---------------------------------------------------------------------------
# G2.3 — Request bound enforced inside middleware (cumulative)
# ---------------------------------------------------------------------------


def test_g2_3_request_bound_overflow_rejected() -> None:
    """request_count cumulative across calls; > max_requests raises.

    Use a tight budget (max_requests=2) so the 3rd call raises with
    field="requests".
    """
    budget = RecursionBudget(max_depth=10, max_tokens=20_000, max_requests=2)
    ctx = DispatcherContext(budget=budget)
    rt, entries = _build_runtime_with_provider()
    with with_runtime(rt), dispatcher_context(ctx):
        _perform_call("a")
        _perform_call("b")
        # 2nd call complete; ctx.request_count == 2 == max_requests (boundary OK)
        with pytest.raises(LLMRecursionBudgetExceeded) as exc_info:
            _perform_call("c")
        assert exc_info.value.field == "requests"
        assert exc_info.value.limit == 2
        assert exc_info.value.observed == 3
    # Only 2 calls succeeded; the 3rd raised before reaching the provider.
    assert len([e for e in entries if e.op == ":llm/call"]) == 2


@pytest.mark.parametrize(
    "max_requests, expect_raise_at_call",
    [
        (3, None),   # 3 sequential calls all succeed (boundary OK)
        (2, 3),      # 3rd call raises
        (1, 2),      # 2nd call raises
    ],
)
def test_g2_3_request_bound_parametrized(
    max_requests: int, expect_raise_at_call: int | None
) -> None:
    budget = RecursionBudget(
        max_depth=10, max_tokens=20_000, max_requests=max_requests
    )
    ctx = DispatcherContext(budget=budget)
    rt, _ = _build_runtime_with_provider()
    raised_at = None
    with with_runtime(rt), dispatcher_context(ctx):
        for n in range(1, 4):
            try:
                _perform_call(f"call-{n}")
            except LLMRecursionBudgetExceeded as e:
                assert e.field == "requests"
                raised_at = n
                break
    assert raised_at == expect_raise_at_call


# ---------------------------------------------------------------------------
# G2.2 — Token bound enforced (Layer 1 + Layer 2)
# ---------------------------------------------------------------------------


def test_g2_2_token_layer1_pre_call_zero_remaining() -> None:
    """Layer 1: ctx.token_count >= max_tokens BEFORE call -> raise.

    Pre-load ctx.token_count to max_tokens; next perform must raise BEFORE
    reaching the provider.
    """
    budget = RecursionBudget(max_depth=10, max_tokens=100, max_requests=10)
    ctx = DispatcherContext(budget=budget)
    ctx.token_count = 100  # exactly at budget — remaining = 0
    rt, entries = _build_runtime_with_provider()
    with with_runtime(rt), dispatcher_context(ctx):
        with pytest.raises(LLMRecursionBudgetExceeded) as exc_info:
            _perform_call()
        assert exc_info.value.field == "tokens"
        assert exc_info.value.limit == 100
        assert exc_info.value.observed == 100
    # No call landed
    assert len([e for e in entries if e.op == ":llm/call"]) == 0


def test_g2_2_token_layer2_estimated_input_overflow() -> None:
    """Layer 2: ctx.token_count + estimated_input > max_tokens -> raise.

    With max_tokens=100 and ctx.token_count=80, Layer 1 says remaining=20
    (allowed). Layer 2 estimates input tokens via
    ``len(json.dumps(messages)) // 4``; a long message pushes
    estimated_input > 20 -> raise.
    """
    budget = RecursionBudget(max_depth=10, max_tokens=100, max_requests=10)
    ctx = DispatcherContext(budget=budget)
    ctx.token_count = 80  # remaining=20
    rt, _ = _build_runtime_with_provider()
    long_content = "x" * 200  # json-dumped len > 200, // 4 > 50 -> > 20 remaining
    with with_runtime(rt), dispatcher_context(ctx):
        with pytest.raises(LLMRecursionBudgetExceeded) as exc_info:
            _perform_call(long_content)
        assert exc_info.value.field == "tokens"


def test_g2_2_token_layer1_remaining_just_below_threshold_allowed() -> None:
    """Layer 1: remaining > 0 + estimated_input small enough -> call proceeds."""
    budget = RecursionBudget(max_depth=10, max_tokens=100_000, max_requests=10)
    ctx = DispatcherContext(budget=budget)
    rt, entries = _build_runtime_with_provider()
    with with_runtime(rt), dispatcher_context(ctx):
        _perform_call("hi")
    assert len([e for e in entries if e.op == ":llm/call"]) == 1


# ---------------------------------------------------------------------------
# G2.7 — Layer 3 output-cap injection
# ---------------------------------------------------------------------------


def test_g2_7_layer3_max_tokens_injected_when_remaining_below_existing() -> None:
    """Layer 3: args["max_tokens"] set to min(existing, remaining) before k(args).

    Provider clause records args.get("max_tokens") as ``max_tokens_seen``.
    With small remaining budget + larger caller-provided max_tokens, the
    middleware should clamp to remaining.
    """
    budget = RecursionBudget(max_depth=10, max_tokens=500, max_requests=10)
    ctx = DispatcherContext(budget=budget)
    ctx.token_count = 400  # remaining = 100
    rt, _ = _build_runtime_with_provider()
    with with_runtime(rt), dispatcher_context(ctx):
        result = perform(
            ":llm/call",
            model="m",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=10_000,  # caller asked big
        )
    # Should have been clamped to remaining = 100
    assert result["max_tokens_seen"] == 100


def test_g2_7_layer3_max_tokens_injected_when_caller_did_not_supply() -> None:
    """Layer 3: args["max_tokens"] absent -> set to remaining."""
    budget = RecursionBudget(max_depth=10, max_tokens=500, max_requests=10)
    ctx = DispatcherContext(budget=budget)
    ctx.token_count = 200  # remaining = 300
    rt, _ = _build_runtime_with_provider()
    with with_runtime(rt), dispatcher_context(ctx):
        result = _perform_call()
    assert result["max_tokens_seen"] == 300


def test_g2_7_layer3_max_tokens_caller_smaller_kept() -> None:
    """Layer 3: when caller's max_tokens < remaining, keep caller's value."""
    budget = RecursionBudget(max_depth=10, max_tokens=10_000, max_requests=10)
    ctx = DispatcherContext(budget=budget)
    rt, _ = _build_runtime_with_provider()
    with with_runtime(rt), dispatcher_context(ctx):
        result = perform(
            ":llm/call",
            model="m",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=50,
        )
    # Caller's smaller value preserved
    assert result["max_tokens_seen"] == 50


# ---------------------------------------------------------------------------
# G2.5 — Layer 4 post-call accounting (provider supplies usage.total_tokens)
# ---------------------------------------------------------------------------


def test_g2_5_layer4_post_call_accounts_provider_usage() -> None:
    """ctx.token_count increments by result['usage']['total_tokens']."""
    budget = RecursionBudget(max_depth=10, max_tokens=100_000, max_requests=10)
    ctx = DispatcherContext(budget=budget)
    rt, _ = _build_runtime_with_provider(usage_total_tokens=42)
    with with_runtime(rt), dispatcher_context(ctx):
        _perform_call()
        assert ctx.token_count == 42
        _perform_call()
        assert ctx.token_count == 84  # cumulative


def test_g2_5_layer4_post_call_with_zero_usage() -> None:
    """Provider returns usage.total_tokens=0 -> token_count unchanged."""
    budget = RecursionBudget(max_depth=10, max_tokens=100_000, max_requests=10)
    ctx = DispatcherContext(budget=budget)
    rt, _ = _build_runtime_with_provider(usage_total_tokens=0)
    with with_runtime(rt), dispatcher_context(ctx):
        _perform_call()
    assert ctx.token_count == 0


# ---------------------------------------------------------------------------
# G2.6 — Layer 4 fallback (provider returns no usage)
# ---------------------------------------------------------------------------


def test_g2_6_layer4_fallback_no_usage_uses_estimate_plus_max_tokens() -> None:
    """No usage.total_tokens -> conservative-overcount substitute.

    Falls back to ``estimated_input + injected_max_tokens`` per design
    R1.1-fold NICE. estimated_input is the Layer 2 calculation
    (``len(json.dumps(messages)) // 4``); injected_max_tokens is the
    Layer 3 clamp value.
    """
    budget = RecursionBudget(max_depth=10, max_tokens=100_000, max_requests=10)
    ctx = DispatcherContext(budget=budget)
    rt, _ = _build_runtime_with_provider(usage_total_tokens=None)
    with with_runtime(rt), dispatcher_context(ctx):
        _perform_call("hi")
    # Some increment happened (conservative overcount); just assert > 0.
    assert ctx.token_count > 0


def test_g2_6_layer4_fallback_subsequent_call_sees_overcount() -> None:
    """Cumulative overcount across multiple no-usage calls.

    Caller-supplied ``max_tokens=20`` keeps the conservative overcount
    bounded so two calls both fit under the 100_000 budget. Without an
    explicit caller cap, the fallback would charge ``remaining`` (≈100k)
    on the FIRST call and exhaust the budget for the second — that
    behavior is correct (conservative-overcount is supposed to be
    pessimistic) but unhelpful for testing accumulation; the cap
    parametrizes a more realistic call shape.
    """
    budget = RecursionBudget(max_depth=10, max_tokens=100_000, max_requests=10)
    ctx = DispatcherContext(budget=budget)
    rt, _ = _build_runtime_with_provider(usage_total_tokens=None)
    with with_runtime(rt), dispatcher_context(ctx):
        perform(
            ":llm/call",
            model="m",
            messages=[{"role": "user", "content": "a"}],
            max_tokens=20,
        )
        first = ctx.token_count
        perform(
            ":llm/call",
            model="m",
            messages=[{"role": "user", "content": "b"}],
            max_tokens=20,
        )
        second = ctx.token_count
    assert first > 0
    assert second > first  # cumulative


# ---------------------------------------------------------------------------
# G2.4 — UNIFIED budget across recursion + composition (LD4)
# ---------------------------------------------------------------------------


def test_g2_4_unified_budget_single_dispatcher_context_across_calls() -> None:
    """Multiple sequential :llm/call performs (recursion + composition both
    feed the SAME counter under one bound DispatcherContext).

    Without composition primitive integration in T3, we approximate "unified
    budget" by asserting that N sequential calls under one bound ctx all
    contribute to the SAME request_count + token_count counters (vs separate
    counters per call).
    """
    budget = RecursionBudget(max_depth=10, max_tokens=100_000, max_requests=10)
    ctx = DispatcherContext(budget=budget)
    rt, entries = _build_runtime_with_provider(usage_total_tokens=10)
    with with_runtime(rt), dispatcher_context(ctx):
        for _ in range(5):
            _perform_call()
    assert ctx.request_count == 5
    # All 5 calls' usage tokens accumulated:
    assert ctx.token_count == 50
    # Audit chain integrity (LD4 unified counter does NOT break the chain):
    llm_entries = [e for e in entries if e.op == ":llm/call"]
    assert len(llm_entries) == 5
    for i in range(1, len(llm_entries)):
        assert llm_entries[i].prev_hash == llm_entries[i - 1].id


def test_g2_4_unified_budget_exhausts_with_real_call_count() -> None:
    """Tight unified budget exhausts after N calls regardless of which
    code-path drives them (recursion vs composition vs sequential)."""
    budget = RecursionBudget(max_depth=10, max_tokens=100_000, max_requests=3)
    ctx = DispatcherContext(budget=budget)
    rt, _ = _build_runtime_with_provider()
    with with_runtime(rt), dispatcher_context(ctx):
        _perform_call("a")
        _perform_call("b")
        _perform_call("c")  # 3rd call: request_count==3, boundary OK
        with pytest.raises(LLMRecursionBudgetExceeded) as exc_info:
            _perform_call("d")  # 4th call: request_count==4 > max=3 -> raise
        assert exc_info.value.field == "requests"


# ---------------------------------------------------------------------------
# Layer ordering invariant: bounds checked BEFORE provider invoked
# ---------------------------------------------------------------------------


def test_layer1_token_bound_raises_before_provider_invoked() -> None:
    """Layer 1 raises BEFORE k(args) is called -> no AuditEntry emitted.

    If Layer 1 fired only AFTER the call, an entry would land in the chain.
    """
    budget = RecursionBudget(max_depth=10, max_tokens=10, max_requests=10)
    ctx = DispatcherContext(budget=budget)
    ctx.token_count = 10  # at budget
    rt, entries = _build_runtime_with_provider()
    with with_runtime(rt), dispatcher_context(ctx):
        with pytest.raises(LLMRecursionBudgetExceeded):
            _perform_call()
    # No AuditEntry for the rejected call -> the audit middleware never
    # got past the dispatcher handler.
    assert len([e for e in entries if e.op == ":llm/call"]) == 0


def test_depth_bound_raises_before_provider_invoked() -> None:
    """Depth bound check fires before k(args) -> no AuditEntry.

    Pre-bump ctx.depth to MAX so the next enter pushes > MAX.
    """
    ctx = DispatcherContext()
    ctx.depth = MAX_LLM_CALL_DEPTH  # next enter -> MAX+1 > MAX
    rt, entries = _build_runtime_with_provider()
    with with_runtime(rt), dispatcher_context(ctx):
        with pytest.raises(LLMRecursionBudgetExceeded) as exc_info:
            _perform_call()
        assert exc_info.value.field == "depth"
    assert len([e for e in entries if e.op == ":llm/call"]) == 0


# ---------------------------------------------------------------------------
# Pass-through invariant: middleware transparent when no DispatcherContext
# ---------------------------------------------------------------------------


def test_no_dispatcher_context_does_not_inject_max_tokens() -> None:
    """Without bound DispatcherContext, the dispatcher handler is a no-op.

    Caller's max_tokens (or absence) is NOT modified by the middleware.
    """
    rt, _ = _build_runtime_with_provider()
    with with_runtime(rt):
        # No max_tokens supplied; provider sees None
        result = _perform_call()
    assert result["max_tokens_seen"] is None


def test_no_dispatcher_context_caller_max_tokens_passes_through() -> None:
    """Without bound DispatcherContext, caller's max_tokens passes through."""
    rt, _ = _build_runtime_with_provider()
    with with_runtime(rt):
        result = perform(
            ":llm/call",
            model="m",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=999,
        )
    assert result["max_tokens_seen"] == 999
