"""Clock handler — the ONE place wall-clock reads are allowed.

All other handler code must perform ``:clock/now`` instead of calling
``time.time()``. This makes replay deterministic: in replay mode the clock
handler is swapped for one that returns pre-recorded timestamps.
"""
from __future__ import annotations

import time
from typing import Callable, Iterable

from persistence.effect.runtime import Handler


def make_system_clock_handler(
    now: Callable[[], float] | None = None,
    wraps: Iterable[str] = ("clock/now",),
) -> Handler:
    """Return a handler that answers :clock/now with the system clock.

    ``now`` defaults to ``time.time`` (seconds since epoch as a float). This
    factory is the single authorized caller of ``time.time()`` in the whole
    module — grep for ``time.time()`` to verify.
    """
    actual_now = now or time.time

    def clock_now(args, k, ctx):
        return {"ts": actual_now()}

    return Handler(
        name="clock",
        wraps=set(wraps),
        clauses={"clock/now": clock_now},
    )


def make_fixed_clock_handler(
    ts: float | int = 0,
    wraps: Iterable[str] = ("clock/now",),
) -> Handler:
    """Return a handler that answers :clock/now with a fixed timestamp.

    Used in tests and in replay.
    """

    def clock_now(args, k, ctx):
        return {"ts": ctx["ts"]}

    return Handler(
        name="clock",
        wraps=set(wraps),
        clauses={"clock/now": clock_now},
        ctx={"ts": ts},
    )


def make_replay_clock_handler(
    timestamps: list[float],
    wraps: Iterable[str] = ("clock/now",),
) -> Handler:
    """Return a handler that answers :clock/now from a pre-recorded list.

    Pops the front of ``timestamps`` on each call; raises if exhausted.
    """

    def clock_now(args, k, ctx):
        tss: list[float] = ctx["timestamps"]
        if not tss:
            raise RuntimeError("replay clock exhausted")
        return {"ts": tss.pop(0)}

    return Handler(
        name="clock",
        wraps=set(wraps),
        clauses={"clock/now": clock_now},
        ctx={"timestamps": list(timestamps)},
    )
