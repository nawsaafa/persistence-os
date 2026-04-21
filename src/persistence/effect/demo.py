"""BankabilityAI demo — canonical stack from spec §3.

Run with::

    PYTHONPATH=src python3 -m persistence.effect.demo

The stack assembled here is exactly the one described in the spec:

    audit → policy → dry-run → cache → retry → rate-limit → raw

…plus auxiliary handlers (clock, random, sleep) at the very bottom so
replay is deterministic. Output shows:

- Policy allow / deny / deny-silently / require-approval decisions,
- Retry recovery from a flaky mock vendor,
- Cache hits bypassing retry + rate-limit,
- A Merkle-chained audit log with each entry's prev-hash pointer.
"""
from __future__ import annotations

from persistence.effect.handlers.audit import (
    AuditEntry,
    audit_entry_to_datom,
    make_audit_handler,
    verify_chain,
)
from persistence.effect.handlers.cache import make_cache_handler
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.handlers.dry_run import make_dry_run_handler
from persistence.effect.handlers.policy import (
    ApprovalRequired,
    PolicyDenied,
    make_policy_handler,
)
from persistence.effect.handlers.rate_limit import make_rate_limit_handler
from persistence.effect.handlers.raw import (
    TransientError,
    make_flaky_llm_handler,
    make_random_handler,
)
from persistence.effect.handlers.retry import make_retry_handler
from persistence.effect.runtime import Handler, Runtime, perform, with_runtime


# ---------------------------------------------------------------------------
# Policy — mirrors the three rules from spec §4 that apply to :llm/call.
# ---------------------------------------------------------------------------


BANKABILITY_POLICY = {
    "policy/id": "bankability-v3",
    "rules": [
        # r1-regulator-audit: deny :decide without regulator-facing tag AND rationale.
        {
            "id": "r4-decision-needs-rationale",
            "when": [":op=", "decide"],
            "require": [":non-empty?", [":args", "rationale"]],
            "on-fail": "require-approval",
        },
        # r3-no-prod-writes-in-dry-run
        {
            "id": "r3-no-prod-writes-in-dry-run",
            "when": [
                ":and",
                [":mode=", "dry-run"],
                [":op-in", ["tool/call", "emit-artifact"]],
                [":matches?", [":args", "name"], r"^(stripe|supabase-prod|binance)"],
            ],
            "on-fail": "deny-silently",
        },
        # Custom: ban disallowed models on :llm/call.
        {
            "id": "banned-model",
            "when": [
                ":and",
                [":op=", "llm/call"],
                [":matches?", [":args", "model"], r"^(gpt-3\.5|gpt-2)"],
            ],
            "on-fail": "deny",
        },
    ],
}


# ---------------------------------------------------------------------------
# Sleep handler — records sleeps instead of actually sleeping (demo clarity).
# ---------------------------------------------------------------------------


def make_recording_sleep_handler(record: list) -> Handler:
    def clause(args, k, ctx):
        record.append(args["ms"])
        return None

    return Handler(name="sleep", wraps={"sleep"}, clauses={"sleep": clause})


# ---------------------------------------------------------------------------
# Stack builder — spec §3 BankabilityAI order.
# ---------------------------------------------------------------------------


def _make_decide_handler() -> Handler:
    """Trivial raw handler for :decide — picks options[0] if any."""

    def clause(args, k, ctx):
        options = args.get("options", [])
        return {"choice": options[0] if options else None, "confidence": 0.8}

    return Handler(name="raw-decide", wraps={"decide"}, clauses={"decide": clause})


def _make_tool_handler() -> Handler:
    def clause(args, k, ctx):
        return {"result": f"tool-{args['name']}-called", "error": None}

    return Handler(name="raw-tool", wraps={"tool/call"}, clauses={"tool/call": clause})


def _make_emit_handler() -> Handler:
    def clause(args, k, ctx):
        return {"uri": f"s3://demo/{args['kind']}/{args['path']}"}

    return Handler(
        name="raw-emit", wraps={"emit-artifact"}, clauses={"emit-artifact": clause}
    )


def build_stack(
    entries: list[AuditEntry],
    sleeps: list[int],
    *,
    mode: str = "live",
) -> Runtime:
    raw = make_flaky_llm_handler(fail_every=4)  # every 4th call raises TransientError
    raw_decide = _make_decide_handler()
    raw_tool = _make_tool_handler()
    raw_emit = _make_emit_handler()
    rate_limit = make_rate_limit_handler(
        wraps={"llm/call"}, capacity=3, refill_per_sec=2.0, initial_tokens=3
    )
    retry = make_retry_handler(
        wraps={"llm/call"}, max_attempts=3, base_backoff_ms=50, jitter_ms=10
    )
    cache = make_cache_handler(wraps={"llm/call"})
    dry = make_dry_run_handler(
        mode=mode, wraps={"tool/call", "emit-artifact"}, mocks={}
    )
    policy = make_policy_handler(
        BANKABILITY_POLICY,
        wraps={"llm/call", "tool/call", "decide", "emit-artifact"},
        mode=mode,
    )
    audit = make_audit_handler(
        entries,
        wraps={"llm/call", "tool/call", "decide", "emit-artifact"},
    )
    clock = make_fixed_clock_handler(ts=1_712_000_000.0)
    rng = make_random_handler(seed=42)
    sleep_h = make_recording_sleep_handler(sleeps)
    # Innermost first in the list; outermost (audit) last.
    return Runtime(
        [
            raw,
            raw_decide,
            raw_tool,
            raw_emit,
            rate_limit,
            retry,
            cache,
            dry,
            policy,
            audit,
            clock,
            rng,
            sleep_h,
        ]
    )


# ---------------------------------------------------------------------------
# Demo script
# ---------------------------------------------------------------------------


def _hr(label: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {label}")
    print("=" * 72)


def main() -> int:
    entries: list[AuditEntry] = []
    sleeps: list[int] = []
    rt = build_stack(entries, sleeps)

    _hr("1. Successful :llm/call (raw echoes content)")
    with with_runtime(rt):
        out = perform(
            "llm/call", model="claude-opus-4-7", messages=[{"role": "user", "content": "hi"}]
        )
    print(f"   result: {out}")

    _hr("2. Same args hit the cache (raw not called, retry/rate-limit skipped)")
    with with_runtime(rt):
        out = perform(
            "llm/call", model="claude-opus-4-7", messages=[{"role": "user", "content": "hi"}]
        )
    print(f"   result: {out}  (identical to #1)")

    _hr("3. A different message; vendor fails on 4th underlying call — retry recovers")
    # We already consumed 1 raw call (call #1; call #2 was cached). Need to get
    # the raw call counter to hit #4. Call 3 passes, call 4 fails and retries.
    with with_runtime(rt):
        for i in range(3):
            out = perform(
                "llm/call",
                model="claude-opus-4-7",
                messages=[{"role": "user", "content": f"msg-{i}"}],
            )
            print(f"   call {i}: {out['text']}")
    print(f"   sleeps recorded (retry backoffs): {sleeps}")

    _hr("4. Banned model → PolicyDenied raised, still audited (intent logging)")
    try:
        with with_runtime(rt):
            perform(
                "llm/call",
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": "leak"}],
            )
    except PolicyDenied as exc:
        print(f"   DENIED: {exc}")

    _hr("5. :decide without rationale → require-approval, no approval_fn → error")
    try:
        with with_runtime(rt):
            perform("decide", question="ship it?", options=["yes", "no"])
    except ApprovalRequired as exc:
        print(f"   APPROVAL REQUIRED: {exc}")

    _hr("6. :decide WITH rationale → allow")
    with with_runtime(rt):
        out = perform(
            "decide",
            question="ship it?",
            options=["yes", "no"],
            rationale="ARIS 4-reviewer signature present",
        )
    print(f"   result: {out}")

    _hr("7. Switch to dry-run mode; :tool/call to stripe → denied silently")
    entries_dry: list[AuditEntry] = []
    sleeps_dry: list[int] = []
    rt_dry = build_stack(entries_dry, sleeps_dry, mode="dry-run")
    with with_runtime(rt_dry):
        out = perform("tool/call", name="stripe.charge", input={"amount": 100})
    print(f"   result (denied-silently sentinel): {out}")

    _hr("8. Merkle audit chain — each entry's prev_hash == prior id")
    print(f"   entries recorded: {len(entries)}")
    for i, e in enumerate(entries):
        preview_prev = (e.prev_hash or "-")[:18]
        print(
            f"   [{i}] op={e.op:<12} verdict={e.verdict:<7} "
            f"id={e.id[:18]}… prev={preview_prev}…"
        )
    chain_ok = verify_chain(entries)
    print(f"   verify_chain → {chain_ok}")

    _hr("9. Datom view of entry[0] (matches Fact spec §1 8-tuple)")
    if entries:
        datom = audit_entry_to_datom(entries[0])
        # Print a compact view
        for key in (
            "datom/e",
            "datom/a",
            "datom/tx",
            "datom/tx-time",
            "datom/valid-from",
            "datom/valid-to",
            "datom/op",
            "datom/invalidated-by",
        ):
            print(f"   {key:<22} = {datom[key]!r}")
        print(f"   datom/v             = {datom['datom/v']!r}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
