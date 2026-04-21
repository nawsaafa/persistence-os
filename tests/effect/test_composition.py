"""Full-stack composition tests — the BankabilityAI handler chain.

Stack (outer→inner):
    audit → policy → dry-run → cache → retry → rate-limit → raw

Checked properties:
- End-to-end call succeeds and produces an audit trail.
- Policy deny halts execution and is still captured by audit (intent logging).
- Retry recovers transparently; upstream sees success.
- Cache hit bypasses retry + rate-limit entirely.
- Proposition 2 (well-formedness) holds.
"""
import pytest

from persistence.effect.handlers.audit import AuditEntry, make_audit_handler
from persistence.effect.handlers.cache import make_cache_handler
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.handlers.dry_run import make_dry_run_handler
from persistence.effect.handlers.policy import PolicyDenied, make_policy_handler
from persistence.effect.handlers.rate_limit import make_rate_limit_handler
from persistence.effect.handlers.raw import (
    TransientError,
    make_echo_llm_handler,
    make_random_handler,
)
from persistence.effect.handlers.retry import make_retry_handler
from persistence.effect.runtime import Handler, Runtime, perform, with_runtime


def _sleep_record(record: list):
    def clause(args, k, ctx):
        record.append(args["ms"])
        return None

    return Handler(name="sleep", wraps={"sleep"}, clauses={"sleep": clause})


def _bankability_stack(entries, *, policy_doc, mode="live", raw_clause=None, clock_ts=1_712_000_000):
    """Assemble outer→inner: audit → policy → dry-run → cache → retry → rate-limit → raw.

    Returns (Runtime, sleeps-list).
    """
    sleeps: list = []
    base_ops = {"llm/call"}
    raw = (
        Handler(name="raw", wraps=base_ops, clauses={"llm/call": raw_clause})
        if raw_clause is not None
        else make_echo_llm_handler()
    )
    rate_limit = make_rate_limit_handler(
        wraps=base_ops, capacity=5, refill_per_sec=10.0, initial_tokens=5
    )
    retry = make_retry_handler(wraps=base_ops, max_attempts=3, base_backoff_ms=50, jitter_ms=0)
    cache = make_cache_handler(wraps=base_ops)
    dry = make_dry_run_handler(mode=mode, wraps=base_ops, mocks={})
    policy = make_policy_handler(policy_doc, wraps=base_ops, mode=mode)
    audit = make_audit_handler(entries, wraps=base_ops)
    clock = make_fixed_clock_handler(ts=clock_ts)
    rng = make_random_handler(seed=42)
    sleep_h = _sleep_record(sleeps)
    # Order matters: innermost first in the list.
    rt = Runtime([raw, rate_limit, retry, cache, dry, policy, audit, clock, rng, sleep_h])
    return rt, sleeps


# ---------- smoke ----------


PERMISSIVE_POLICY = {"policy/id": "permissive", "rules": []}


def test_full_stack_smoke_call_succeeds():
    entries: list[AuditEntry] = []
    rt, _ = _bankability_stack(entries, policy_doc=PERMISSIVE_POLICY)
    with with_runtime(rt):
        out = perform("llm/call", model="m", messages=[{"role": "user", "content": "hello"}])
    assert out["text"] == "echo:hello"
    assert len(entries) == 1
    assert entries[0].verdict == "ok"


# ---------- policy deny ----------


BAN_POLICY = {
    "policy/id": "no-llm",
    "rules": [{"id": "deny-llm", "when": [":op=", "llm/call"], "on-fail": "deny"}],
}


def test_policy_denied_halts_and_still_audited():
    entries: list[AuditEntry] = []
    rt, _ = _bankability_stack(entries, policy_doc=BAN_POLICY)
    with with_runtime(rt), pytest.raises(PolicyDenied):
        perform("llm/call", model="m", messages=[{"role": "user", "content": "x"}])
    # Audit is outermost; it must still see the attempt.
    assert len(entries) == 1
    assert entries[0].verdict == "error"
    assert "PolicyDenied" in (entries[0].error or "")


# ---------- retry recovery ----------


def test_retry_recovers_from_transient_failure_and_audit_sees_one_success():
    attempts = {"n": 0}

    def flaky(args, k, ctx):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise TransientError("vendor 503")
        return {"text": "ok-after-retry", "usage": {"tokens": 5}}

    entries: list[AuditEntry] = []
    rt, sleeps = _bankability_stack(entries, policy_doc=PERMISSIVE_POLICY, raw_clause=flaky)
    with with_runtime(rt):
        out = perform("llm/call", model="m", messages=[{"role": "user", "content": "a"}])
    assert out["text"] == "ok-after-retry"
    # Audit sits above retry, so it only sees ONE call (the successful one).
    assert len(entries) == 1
    assert entries[0].verdict == "ok"
    # Retry slept once between the two attempts.
    assert len(sleeps) == 1


# ---------- cache hit bypasses retry + rate-limit ----------


def test_cache_hit_bypasses_everything_below():
    calls = {"n": 0}

    def raw(args, k, ctx):
        calls["n"] += 1
        return {"text": "raw", "usage": {"tokens": 1}}

    entries: list[AuditEntry] = []
    rt, sleeps = _bankability_stack(entries, policy_doc=PERMISSIVE_POLICY, raw_clause=raw)
    args_payload = {"model": "m", "messages": [{"role": "user", "content": "same"}]}
    with with_runtime(rt):
        perform("llm/call", **args_payload)
        perform("llm/call", **args_payload)
    assert calls["n"] == 1  # raw only called once
    # Both calls still get an audit entry — audit is above the cache.
    assert len(entries) == 2
    assert all(e.verdict == "ok" for e in entries)


# ---------- well-formedness ----------


def test_full_stack_is_well_formed_over_the_catalog_of_ops_it_covers():
    entries: list[AuditEntry] = []
    rt, _ = _bankability_stack(entries, policy_doc=PERMISSIVE_POLICY)
    # The ops that this stack covers (not the full 15-op catalog — the stack
    # is purpose-built for llm/call + the auxiliary ops used by handlers).
    catalog = {"llm/call", "clock/now", "random", "sleep"}
    assert rt.is_well_formed(catalog), f"uncovered: {rt.uncovered_ops(catalog)}"


def test_audit_prev_hash_chain_intact_across_full_stack():
    """Gate 3: the last entry's prev_hash correctly references the penultimate's id."""
    entries: list[AuditEntry] = []
    rt, _ = _bankability_stack(entries, policy_doc=PERMISSIVE_POLICY)
    with with_runtime(rt):
        perform("llm/call", model="m", messages=[{"role": "user", "content": "a"}])
        perform("llm/call", model="m", messages=[{"role": "user", "content": "b"}])
        perform("llm/call", model="m", messages=[{"role": "user", "content": "c"}])
    assert len(entries) == 3
    assert entries[-1].prev_hash == entries[-2].id
    from persistence.effect.handlers.audit import verify_chain
    assert verify_chain(entries) is True
