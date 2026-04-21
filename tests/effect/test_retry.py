"""Retry handler tests — exponential backoff with jitter, determinism via :sleep/:random."""
import pytest

from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.handlers.raw import TransientError, make_flaky_llm_handler
from persistence.effect.handlers.retry import make_retry_handler
from persistence.effect.runtime import Handler, Runtime, perform, with_runtime


def _mock_sleep_handler(record):
    """Record :sleep calls instead of actually sleeping."""

    def clause(args, k, ctx):
        record.append(args["ms"])
        return None

    return Handler(name=":sleep", wraps={":sleep"}, clauses={":sleep": clause})


def _mock_random_handler(values):
    """Return ``values.pop(0)`` for each :random call — deterministic jitter."""

    def clause(args, k, ctx):
        v = ctx["values"].pop(0)
        return {"value": v}

    return Handler(
        name=":random",
        wraps={":random"},
        clauses={":random": clause},
        ctx={"values": list(values)},
    )


def test_retry_succeeds_on_first_attempt_without_sleeping():
    sleeps: list[int] = []
    retry = make_retry_handler(wraps={":llm/call"}, max_attempts=3, base_backoff_ms=100)
    raw = make_flaky_llm_handler(fail_every=10)  # won't fail
    rt = Runtime([raw, _mock_sleep_handler(sleeps), _mock_random_handler([0.0]), retry])
    with with_runtime(rt):
        out = perform(":llm/call", model="m", messages=[{"content": "x"}])
    assert out["text"] == "echo:x"
    assert sleeps == []


def test_retry_retries_until_success_and_uses_exponential_backoff():
    sleeps: list[int] = []
    # Fail every 1st call for the first 2 attempts, succeed on 3rd.
    # Use a counter-based custom handler:
    counter = {"n": 0}

    def flaky(args, k, ctx):
        counter["n"] += 1
        if counter["n"] < 3:
            raise TransientError(f"fail {counter['n']}")
        return {"text": "ok"}

    raw = Handler(name="raw", wraps={":llm/call"}, clauses={":llm/call": flaky})
    retry = make_retry_handler(wraps={":llm/call"}, max_attempts=5, base_backoff_ms=100)
    # Jitter values = 0 so backoff is exactly base * 2^attempt
    rt = Runtime([
        raw,
        _mock_sleep_handler(sleeps),
        _mock_random_handler([0.0, 0.0, 0.0, 0.0]),
        retry,
    ])
    with with_runtime(rt):
        out = perform(":llm/call", model="m", messages=[{"content": "x"}])
    assert out == {"text": "ok"}
    # Two failures → two sleeps.
    # attempt 0 fails → sleep base*1 = 100
    # attempt 1 fails → sleep base*2 = 200
    assert sleeps == [100, 200]


def test_retry_applies_jitter_from_random_handler():
    sleeps: list[int] = []
    counter = {"n": 0}

    def flaky(args, k, ctx):
        counter["n"] += 1
        if counter["n"] < 2:
            raise TransientError("fail")
        return {"ok": True}

    raw = Handler(name="raw", wraps={":llm/call"}, clauses={":llm/call": flaky})
    retry = make_retry_handler(wraps={":llm/call"}, max_attempts=3, base_backoff_ms=100, jitter_ms=50)
    # jitter sample = 25 ms → sleep = 100 + 25
    rt = Runtime([
        raw,
        _mock_sleep_handler(sleeps),
        _mock_random_handler([25.0, 10.0]),
        retry,
    ])
    with with_runtime(rt):
        perform(":llm/call", model="m", messages=[{"content": "x"}])
    assert sleeps == [125]


def test_retry_gives_up_after_max_attempts_and_re_raises():
    sleeps: list[int] = []

    def always_fail(args, k, ctx):
        raise TransientError("nope")

    raw = Handler(name="raw", wraps={":llm/call"}, clauses={":llm/call": always_fail})
    retry = make_retry_handler(wraps={":llm/call"}, max_attempts=3, base_backoff_ms=100)
    rt = Runtime([
        raw,
        _mock_sleep_handler(sleeps),
        _mock_random_handler([0.0, 0.0, 0.0]),
        retry,
    ])
    with with_runtime(rt), pytest.raises(TransientError, match="nope"):
        perform(":llm/call", model="m", messages=[{"content": "x"}])
    # 2 backoffs between 3 attempts.
    assert len(sleeps) == 2


def test_retry_does_not_retry_non_transient_errors():
    """Only ``retryable`` exceptions trigger retry. Default = TransientError only."""
    sleeps: list[int] = []

    def permanent(args, k, ctx):
        raise ValueError("bug")

    raw = Handler(name="raw", wraps={":llm/call"}, clauses={":llm/call": permanent})
    retry = make_retry_handler(wraps={":llm/call"}, max_attempts=3, base_backoff_ms=100)
    rt = Runtime([
        raw,
        _mock_sleep_handler(sleeps),
        _mock_random_handler([0.0, 0.0]),
        retry,
    ])
    with with_runtime(rt), pytest.raises(ValueError, match="bug"):
        perform(":llm/call", model="m", messages=[{"content": "x"}])
    assert sleeps == []
