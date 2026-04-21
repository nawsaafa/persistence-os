"""Runtime concurrency — ContextVar isolation across asyncio tasks (R2 F4).

Paper §4.2 and the Runtime module docstring claim:

    "No hidden globals across threads: the active runtime lives in a
     ContextVar, and each Runtime owns its handler list plus its own mask set"

The active-runtime ContextVar *is* set, but ``Runtime._masks`` is per-instance
mutable state. If two concurrent asyncio tasks share a Runtime (the natural
multi-tenant deployment) each ``with mask(...)`` pushes onto the same list,
so one task's mask can leak into the other's dispatch during the overlap
window.

These tests exercise that exact scenario — spawning concurrent tasks against
one shared Runtime — and prove each task sees only its own mask stack.
"""
from __future__ import annotations

import asyncio

import pytest

from persistence.effect.runtime import (
    Handler,
    Runtime,
    mask,
    perform,
    with_runtime,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _stack_for_tests():
    """Two handlers: 'raw' always responds, 'audit' wraps raw and records.

    audit is the outermost and records ``('A',)`` or ``('B',)`` into calls
    depending on the args. The test uses mask('audit') inside some tasks.
    """
    calls: list = []

    def audit(args, k, ctx):
        calls.append(("audit", args.get("who")))
        return k(args)

    def raw(args, k, ctx):
        return {"who": args.get("who")}

    rt = Runtime([
        Handler(name="raw", wraps={"x"}, clauses={"x": raw}),
        Handler(name="audit", wraps={"x"}, clauses={"x": audit}),
    ])
    return rt, calls


# ---------------------------------------------------------------------------
# The load-bearing test: concurrent masks on a shared Runtime must not bleed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contextvar_isolates_runtime_state_across_asyncio_tasks():
    """R2 F4 (CONCURRENCY): 10 concurrent tasks share one Runtime; half mask
    'audit', half don't. Each task must see only its own view of the mask
    stack — the masked half record zero audit entries, the unmasked half
    record exactly one per call.

    With the original implementation (Runtime._masks = plain list), masks
    pushed by one task would be visible to others interleaved by the event
    loop, so the masked/unmasked accounting would drift. With _masks backed
    by a ContextVar the two task groups must stay cleanly separated.
    """
    rt, calls = _stack_for_tests()

    async def masked_worker(name: str) -> dict:
        # Each task yields control at least once inside the mask block so
        # the event loop can interleave with other tasks mid-mask.
        with with_runtime(rt):
            with mask("audit"):
                await asyncio.sleep(0)  # force interleave
                return perform("x", who=name)

    async def unmasked_worker(name: str) -> dict:
        with with_runtime(rt):
            await asyncio.sleep(0)  # force interleave
            return perform("x", who=name)

    # 5 masked + 5 unmasked tasks, alternating, run to completion in parallel.
    masked_names = [f"m{i}" for i in range(5)]
    unmasked_names = [f"u{i}" for i in range(5)]
    tasks = [masked_worker(n) for n in masked_names] + [
        unmasked_worker(n) for n in unmasked_names
    ]
    results = await asyncio.gather(*tasks)

    # Every task got its own value back (raw responded regardless).
    assert {r["who"] for r in results} == set(masked_names + unmasked_names)

    # CRITICAL: masked tasks MUST have bypassed audit; unmasked MUST have hit it.
    audited = [who for (kind, who) in calls if kind == "audit"]
    # No masked task's 'who' should appear in the audit trace.
    for mname in masked_names:
        assert mname not in audited, (
            f"mask leaked: task {mname!r} was masked but audit fired for it; "
            "Runtime._masks is shared state, not ContextVar-scoped"
        )
    # Every unmasked task's 'who' MUST appear exactly once.
    for uname in unmasked_names:
        assert audited.count(uname) == 1, (
            f"mask leaked: task {uname!r} was NOT masked but audit did not "
            f"fire exactly once ({audited.count(uname)}x); "
            "Runtime._masks is shared state, not ContextVar-scoped"
        )


@pytest.mark.asyncio
async def test_mask_scope_does_not_bleed_after_task_completes():
    """After a task's mask block exits, other tasks must see the prior state.

    Regression: an earlier prototype popped from a shared list, so a task
    that awaited mid-mask could have its mask popped by another task's
    exit, causing the remaining body to see the wrong state.
    """
    rt, calls = _stack_for_tests()

    async def first_masks_then_awaits():
        with with_runtime(rt):
            with mask("audit"):
                await asyncio.sleep(0.01)
                return perform("x", who="first")

    async def second_unmasks():
        with with_runtime(rt):
            # Give first a head start so its mask is pushed.
            await asyncio.sleep(0)
            return perform("x", who="second")

    results = await asyncio.gather(first_masks_then_awaits(), second_unmasks())
    audited = [who for (kind, who) in calls if kind == "audit"]
    # 'first' was masked → absent; 'second' was not → present.
    assert "first" not in audited
    assert audited.count("second") == 1
    assert {r["who"] for r in results} == {"first", "second"}
