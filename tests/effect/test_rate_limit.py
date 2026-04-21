"""Rate-limit handler tests — token bucket, thread-safe, clock-driven."""
import threading

import pytest

from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.handlers.raw import make_echo_llm_handler
from persistence.effect.handlers.rate_limit import make_rate_limit_handler
from persistence.effect.runtime import Handler, Runtime, perform, with_runtime


def _sleep_record(record):
    def clause(args, k, ctx):
        record.append(args["ms"])
        return None

    return Handler(name="sleep", wraps={"sleep"}, clauses={"sleep": clause})


def _advancing_clock(ts_seq: list[float]):
    """Clock that returns ``ts_seq.pop(0)`` each call."""

    def clause(args, k, ctx):
        return {"ts": ctx["seq"].pop(0)}

    return Handler(
        name="clock",
        wraps={"clock/now"},
        clauses={"clock/now": clause},
        ctx={"seq": list(ts_seq)},
    )


def test_under_capacity_passes_through_without_sleep():
    sleeps: list = []
    # 2 tokens capacity, refill 1/s.
    rl = make_rate_limit_handler(
        wraps={"llm/call"}, capacity=2, refill_per_sec=1.0, initial_tokens=2
    )
    raw = make_echo_llm_handler()
    clock = _advancing_clock([100.0, 100.0, 100.0, 100.0])  # two calls, 2 reads each
    rt = Runtime([raw, clock, _sleep_record(sleeps), rl])
    with with_runtime(rt):
        perform("llm/call", model="m", messages=[{"content": "a"}])
        perform("llm/call", model="m", messages=[{"content": "b"}])
    assert sleeps == []


def test_over_capacity_sleeps_for_next_refill():
    sleeps: list = []
    # Capacity=1, refill 1/s. Second call must sleep ~1000ms.
    rl = make_rate_limit_handler(
        wraps={"llm/call"}, capacity=1, refill_per_sec=1.0, initial_tokens=1
    )
    raw = make_echo_llm_handler()
    # Clock reads: call1=100.0 (enter) + 100.0 (refill check unused),
    # then for call2: 100.0 (still same second), then next refill-check 101.0.
    # Our handler reads clock once per call.
    clock = _advancing_clock([100.0, 100.0])
    rt = Runtime([raw, clock, _sleep_record(sleeps), rl])
    with with_runtime(rt):
        perform("llm/call", model="m", messages=[{"content": "a"}])
        perform("llm/call", model="m", messages=[{"content": "b"}])
    # 2nd call had 0 tokens → must sleep ~1000 ms.
    assert sleeps == [1000]


def test_refill_after_clock_advance():
    sleeps: list = []
    rl = make_rate_limit_handler(
        wraps={"llm/call"}, capacity=1, refill_per_sec=1.0, initial_tokens=0
    )
    raw = make_echo_llm_handler()
    # First call at t=100 → 0 tokens, sleep 1s. Second at t=102 → refilled, free.
    clock = _advancing_clock([100.0, 102.0])
    rt = Runtime([raw, clock, _sleep_record(sleeps), rl])
    with with_runtime(rt):
        perform("llm/call", model="m", messages=[{"content": "a"}])
        perform("llm/call", model="m", messages=[{"content": "b"}])
    # Call 1: 0 tokens → sleep 1000 ms
    # Call 2: refilled to cap → no sleep
    assert sleeps == [1000]


def test_thread_safety_basic_lock_contract():
    """Multiple threads through the same handler must never double-spend tokens.

    We run N threads each performing 1 call through a capacity-N bucket, and
    verify that the total tokens consumed equals N (bucket never went negative).
    """
    threads = 8
    rl = make_rate_limit_handler(
        wraps={"llm/call"}, capacity=threads, refill_per_sec=1000.0, initial_tokens=threads
    )
    raw = make_echo_llm_handler()
    # Every thread reads clock once; supply enough.
    clock_seq = [100.0] * (threads * 2)
    clock = _advancing_clock(clock_seq)
    sleeps: list = []
    rt = Runtime([raw, clock, _sleep_record(sleeps), rl])
    errors: list = []

    def worker():
        try:
            with with_runtime(rt):
                perform("llm/call", model="m", messages=[{"content": "x"}])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == []
    # Tokens should be 0 after N calls with N initial tokens.
    assert rl.ctx["tokens"] == pytest.approx(0.0, abs=1e-6)
