"""Rate-limit handler — thread-safe token bucket.

The clock is read via ``:clock/now``; sleeps go through ``:sleep``. No
hidden globals — each handler instance carries its own bucket state in
``ctx``. The per-instance ``_lock`` is a ``threading.Lock`` so concurrent
callers never double-spend tokens.
"""
from __future__ import annotations

import threading
from typing import Iterable

from persistence.effect.runtime import Handler, perform


def make_rate_limit_handler(
    *,
    wraps: Iterable[str] = (":llm/call",),
    capacity: float = 2.0,
    refill_per_sec: float = 1.0,
    initial_tokens: float | None = None,
) -> Handler:
    """Token-bucket rate limiter.

    - ``capacity`` — maximum bucket size.
    - ``refill_per_sec`` — tokens added per second.
    - ``initial_tokens`` — starting tokens (defaults to ``capacity``).

    On each call: refill based on elapsed time since last check, then if
    tokens < 1 perform ``:sleep`` for the needed duration; decrement 1 token;
    call ``k``.
    """
    if initial_tokens is None:
        initial_tokens = capacity
    lock = threading.Lock()

    def make_op_clause(op_name: str):
        def clause(args, k, ctx):
            now = perform(":clock/now")["ts"]
            sleep_ms: int = 0
            with ctx["_lock"]:
                last = ctx["last"]
                if last is None:
                    # First call: no elapsed time credited; start the clock.
                    elapsed = 0.0
                else:
                    elapsed = max(0.0, now - last)
                ctx["tokens"] = min(
                    ctx["capacity"], ctx["tokens"] + elapsed * ctx["refill_per_sec"]
                )
                ctx["last"] = now
                if ctx["tokens"] < 1.0:
                    needed = 1.0 - ctx["tokens"]
                    if ctx["refill_per_sec"] <= 0:
                        raise RuntimeError("rate-limit: refill_per_sec must be > 0")
                    sleep_s = needed / ctx["refill_per_sec"]
                    sleep_ms = max(1, int(round(sleep_s * 1000)))
                    # After sleeping, we will have exactly `needed` more tokens,
                    # taking us to 1.0; then we decrement to 0.
                    ctx["tokens"] = 0.0
                else:
                    ctx["tokens"] -= 1.0
            if sleep_ms > 0:
                perform(":sleep", ms=sleep_ms)
            return k(args)

        return clause

    clauses = {op: make_op_clause(op) for op in wraps}
    return Handler(
        name="rate-limit",
        wraps=set(wraps),
        clauses=clauses,
        ctx={
            "capacity": float(capacity),
            "refill_per_sec": float(refill_per_sec),
            "tokens": float(initial_tokens),
            "last": None,  # set on first clock read; prevents spurious refill from last=0 epoch
            "_lock": lock,
        },
    )
