"""Dry-run handler — short-circuits stateful ops with mocked returns.

Activated by ``mode="dry-run"``. In dry-run mode, any op listed in ``mocks``
returns its mock value without calling ``k``. Ops in ``allow_live`` pass
through normally (useful for read-only ops you want to keep alive during
paper-trading).
"""
from __future__ import annotations

from typing import Any, Callable, Iterable


from persistence.effect.runtime import Handler


# Mock can be a plain value or ``(args) -> value``.
Mock = Any


def make_dry_run_handler(
    *,
    mode: str = "live",
    wraps: Iterable[str] = ("tool/call", "emit-artifact", "net/fetch"),
    mocks: dict[str, Mock] | None = None,
    allow_live: set[str] | None = None,
) -> Handler:
    """Return a dry-run handler.

    Parameters
    ----------
    mode
        ``"dry-run"`` to short-circuit; anything else (``"live"``,
        ``"replay"``, etc.) passes through.
    wraps
        Ops this handler might intercept.
    mocks
        ``{op: mock}`` — value returned in dry-run mode. If ``mock`` is
        callable it is invoked with the ``args`` dict.
    allow_live
        Ops that pass through even in dry-run mode.
    """
    mocks = dict(mocks or {})
    allow_live = set(allow_live or set())

    def make_op_clause(op_name: str):
        def clause(args, k, ctx):
            if ctx["mode"] != "dry-run" or op_name in ctx["allow_live"]:
                return k(args)
            if op_name not in ctx["mocks"]:
                # No mock configured — pass through. Avoids silent blocks.
                return k(args)
            mock = ctx["mocks"][op_name]
            if callable(mock):
                return mock(args)
            return mock

        return clause

    clauses = {op: make_op_clause(op) for op in wraps}
    return Handler(
        name="dry-run",
        wraps=set(wraps),
        clauses=clauses,
        ctx={
            "mode": mode,
            "mocks": mocks,
            "allow_live": allow_live,
        },
    )
