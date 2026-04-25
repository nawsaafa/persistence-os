"""Phase A — smoke test that effect.Runtime can use plan.Dispatcher.

The runtime continues to expose its existing perform/Handler API; Dispatcher
is a NEW seam. This test confirms the integration contract: a handler
registered on a Dispatcher can call into Runtime.perform without circular
imports or context conflicts.

DONE_WITH_CONCERNS deviation from plan verbatim test code:
  The plan's test used ``class RecordingHandler(EffectHandler): op = ':raw'; def handle(...)``
  which would fail because ``effect.runtime.Handler`` is a ``@dataclass`` with fields
  ``(name, wraps, clauses, ctx)`` — NOT a base class with overridable ``handle()``.
  Adapted to use the actual dataclass constructor API:
    Handler(name=..., wraps={":raw"}, clauses={":raw": clause_fn})
  where clause_fn signature is (args, k, ctx) -> result (matching Handler.invoke).
  The TEST INTENT is identical: a Dispatcher handler delegates to Runtime.perform
  and the call lands end-to-end.
"""
from __future__ import annotations

import pytest


def test_dispatcher_can_invoke_runtime_perform():
    """A Dispatcher handler that calls runtime.perform works end-to-end."""
    from persistence.effect.runtime import Handler, Runtime
    from persistence.plan import Dispatcher, Node

    seen: list[str] = []

    # Build an effect handler that records :raw ops.
    # Handler is a @dataclass: (name, wraps, clauses, ctx).
    # Clause signature: (args, k, ctx) -> result.
    def raw_clause(args: dict, k, ctx: dict):
        seen.append(args.get("payload", ""))
        return {"ok": True}

    effect_handler = Handler(
        name="recorder",
        wraps={":raw"},
        clauses={":raw": raw_clause},
    )
    rt = Runtime(handlers=[effect_handler])

    # Dispatcher handler delegates to the effect runtime
    d = Dispatcher()
    d.register(
        ":record",
        lambda node, env: rt.perform(":raw", {"payload": node.attrs.get("msg", "")}),
    )

    plan = Node(
        tag=":seq",
        children=(
            Node(tag=":record", attrs={"msg": "first"}),
            Node(tag=":record", attrs={"msg": "second"}),
        ),
    )
    d.dispatch(plan, env={})

    assert seen == ["first", "second"]
