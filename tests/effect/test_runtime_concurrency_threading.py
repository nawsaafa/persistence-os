"""Threading variant of the ContextVar concurrency test (ARIS R3 G3).

The asyncio variant (``test_runtime_concurrency.py``) proves that one
Runtime shared across interleaved asyncio tasks keeps each task's mask
stack cleanly separated — `Runtime._masks` lives behind a ContextVar
that follows the asyncio context scope.

A ContextVar's behaviour under ``threading.Thread`` is different: each
new thread starts with a fresh, empty copy of the context (since
Python 3.7, ``copy_context()`` is NOT called for you at thread
spawn — each thread explicitly begins with the default value of every
ContextVar). So the invariant we want here is a documented, *tested*
contract: threads do not inherit the spawning thread's mask stack, and
one thread's mask does not bleed into another thread's.

Pairs with ``P-concurrency`` (SQLiteStore.allocate_and_append) as the
other half of the threading-isolation story.
"""
from __future__ import annotations

import threading
from collections import Counter

from persistence.effect.runtime import (
    Handler,
    Runtime,
    mask,
    perform,
    with_runtime,
)


def _stack_for_tests():
    """Two handlers — 'raw' always responds, 'audit' wraps raw and
    records per-call who."""
    calls: list = []
    calls_lock = threading.Lock()

    def audit(args, k, ctx):
        with calls_lock:
            calls.append(("audit", args.get("who")))
        return k(args)

    def raw(args, k, ctx):
        return {"who": args.get("who")}

    rt = Runtime([
        Handler(name="raw", wraps={"x"}, clauses={"x": raw}),
        Handler(name="audit", wraps={"x"}, clauses={"x": audit}),
    ])
    return rt, calls


def test_thread_starts_with_empty_mask_stack():
    """A new thread must NOT inherit its spawner's mask stack.

    This is the load-bearing contract for multi-worker WSGI/gunicorn
    deployments: each worker thread reads ``Runtime._masks`` fresh.
    """
    rt, calls = _stack_for_tests()

    # Parent thread masks audit.
    with with_runtime(rt):
        with mask("audit"):
            # Child thread — must NOT see the parent's mask.
            child_results: list = []

            def child():
                with with_runtime(rt):
                    # The child has a fresh ContextVar for _masks, so audit
                    # should still intercept its call.
                    child_results.append(perform("x", who="child"))

            t = threading.Thread(target=child)
            t.start()
            t.join(timeout=5)

            # Parent's own call — masked.
            parent_result = perform("x", who="parent")

    audited = [who for (kind, who) in calls if kind == "audit"]
    assert "parent" not in audited, (
        "parent's mask failed — audit fired for 'parent' even though we "
        "were inside mask('audit')"
    )
    assert "child" in audited, (
        "child thread inherited parent's mask — ContextVar isolation broken. "
        "Each thread must start with a fresh mask stack."
    )
    assert len(child_results) == 1
    assert parent_result["who"] == "parent"


def test_concurrent_threads_do_not_share_mask_state():
    """N threads: half mask audit inside their own scope, half don't.
    Masked ones must see zero audit hits; unmasked ones see exactly one.
    """
    rt, calls = _stack_for_tests()

    n_each = 10
    barrier = threading.Barrier(n_each * 2)

    def masked_worker(name: str) -> None:
        with with_runtime(rt):
            with mask("audit"):
                barrier.wait(timeout=10)
                perform("x", who=name)

    def unmasked_worker(name: str) -> None:
        with with_runtime(rt):
            barrier.wait(timeout=10)
            perform("x", who=name)

    masked_names = [f"m{i}" for i in range(n_each)]
    unmasked_names = [f"u{i}" for i in range(n_each)]
    threads: list[threading.Thread] = []
    for n in masked_names:
        threads.append(threading.Thread(target=masked_worker, args=(n,)))
    for n in unmasked_names:
        threads.append(threading.Thread(target=unmasked_worker, args=(n,)))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    audited = [who for (kind, who) in calls if kind == "audit"]
    # Every masked name absent.
    for mname in masked_names:
        assert mname not in audited, (
            f"mask leaked across threads: audit fired for {mname!r} "
            "even though its own thread was inside mask('audit')"
        )
    # Every unmasked name exactly once.
    counts = Counter(audited)
    for uname in unmasked_names:
        assert counts[uname] == 1, (
            f"unmasked thread {uname!r} saw audit {counts[uname]} times "
            "(expected exactly 1) — mask state bled across threads"
        )
