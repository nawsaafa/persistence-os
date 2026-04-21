"""Retry handler — exponential backoff with jitter, fully determinized via effects.

Backoff is routed through ``:sleep``; jitter is routed through ``:random``
(kind ``"jitter"``). Neither ``time.sleep`` nor ``random.random()`` is
called directly — replay is deterministic.
"""
from __future__ import annotations

from typing import Iterable, Type

from persistence.effect.handlers.raw import TransientError
from persistence.effect.runtime import Handler, perform


def make_retry_handler(
    *,
    wraps: Iterable[str] = ("llm/call", "tool/call", "net/fetch"),
    max_attempts: int = 3,
    base_backoff_ms: int = 200,
    jitter_ms: int = 100,
    retryable: tuple[Type[BaseException], ...] = (TransientError,),
) -> Handler:
    """Return a retry handler.

    Backoff for attempt *i* (0-indexed):

        ``sleep_ms = base_backoff_ms * 2**i  +  jitter``

    where ``jitter`` comes from a ``:random`` effect of kind ``"jitter"`` with
    ``params={"max": jitter_ms}``. This means a replay handler that returns
    pre-recorded jitter samples makes retry timing bit-identical.
    """

    def make_op_clause(op_name: str):
        def clause(args, k, ctx):
            last_exc: BaseException | None = None
            for attempt in range(ctx["max_attempts"]):
                try:
                    return k(args)
                except ctx["retryable"] as exc:  # type: ignore[misc]
                    last_exc = exc
                    if attempt == ctx["max_attempts"] - 1:
                        raise
                    jitter = perform(
                        "random", kind="jitter", params={"max": ctx["jitter_ms"]}
                    )["value"]
                    sleep_ms = int(ctx["base_backoff_ms"] * (2 ** attempt) + jitter)
                    perform("sleep", ms=sleep_ms)
            # unreachable — either returned or raised
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("retry loop exited without result")

        return clause

    clauses = {op: make_op_clause(op) for op in wraps}
    return Handler(
        name="retry",
        wraps=set(wraps),
        clauses=clauses,
        ctx={
            "max_attempts": max_attempts,
            "base_backoff_ms": base_backoff_ms,
            "jitter_ms": jitter_ms,
            "retryable": retryable,
        },
    )
