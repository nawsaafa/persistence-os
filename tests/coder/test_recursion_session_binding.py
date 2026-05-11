"""Phase 2.3c.2 T4 smoke — DispatcherContext binding at coder iteration entry.

Verifies that ``Coder.run()`` wraps each iteration with a DispatcherContext
ContextVar binding so the audit middleware (T3 ``_audit_stack._make_dispatcher_handler``)
reads a live context for budget enforcement + cycle API.

Scope (T4):
- T4.1 ``current_dispatcher_context()`` returns a DispatcherContext during
       ``coder.run()``'s iteration body (specifically during ``:llm/call``
       dispatch).
- T4.2 ``:llm/call`` is registered as a leaf tag at ``REGISTERED_LEAF_TAGS``
       (per T4 enabler for skill-body :llm/call dispatch).

Out of scope: end-to-end budget enforcement (T3 G2 covers exhaustively at
the unit level — 26 cases) and end-to-end recursion + composition (T6 G4
LOAD-BEARING). T4 only verifies the BINDING seam is in place so T6 has a
working substrate to exercise.
"""

from __future__ import annotations

from typing import Any

from persistence.coder._planner import REGISTERED_LEAF_TAGS
from persistence.coder._recursion import (
    DispatcherContext,
    current_dispatcher_context,
)
from persistence.coder._session import Coder
from persistence.effect import with_runtime
from persistence.effect.handlers.callable import make_callable_llm_handler
from persistence.effect.handlers.clock import make_system_clock_handler
from persistence.effect.handlers.sys_now import make_sys_now_handler
from persistence.sdk import Substrate


def test_llm_call_in_registered_leaf_tags() -> None:
    """T4.2 — :llm/call is now a registered leaf tag (skill-body enabler)."""
    assert ":llm/call" in REGISTERED_LEAF_TAGS, (
        "Phase 2.3c.2 T4: :llm/call must be in REGISTERED_LEAF_TAGS so the "
        "planner-side adapter can dispatch it via s.effect.perform; routes "
        "through the audit middleware's DispatcherContext binding."
    )


def test_dispatcher_context_bound_during_iteration() -> None:
    """T4.1 — current_dispatcher_context() is non-None during :llm/call.

    With ``audit=False`` the canonical audit stack (incl. T3's dispatcher
    handler) is not installed, so budget enforcement does NOT fire. This
    test only checks the BINDING SEAM in ``Coder.run()`` — that the
    ContextVar is non-None at any point inside the iteration body. Budget
    enforcement coverage lives in T3 ``test_recursion_budget.py`` (G2,
    26 cases).
    """
    s = Substrate.open("memory", audit=False)
    captured: list[DispatcherContext | None] = []

    def call_fn(**kwargs: Any) -> dict:  # noqa: ARG001
        captured.append(current_dispatcher_context())
        return {
            "tool_calls": [
                {
                    "input": {
                        "kind": "act",
                        "confidence": 0.9,
                        "payload": {"done": True, "op": ":fs/read"},
                    }
                }
            ]
        }

    handler = make_callable_llm_handler(call_fn=call_fn)
    s.effect.install_handler(handler, position="bottom")
    # Phase 2.4b LD-1+LD-4 site 3: _session.py:_decide now performs
    # :sys/now for valid_from provenance; install the clock + sys_now
    # handlers so the audit=False custom stack covers the op.
    s.effect.install_handler(make_system_clock_handler(), position="bottom")
    s.effect.install_handler(make_sys_now_handler(), position="bottom")

    # Pre-condition: no DispatcherContext bound outside coder.run()
    assert current_dispatcher_context() is None

    coder = Coder(task="smoke", substrate=s, max_iters=1)
    # Phase 2.4b LD-1: make_sys_now_handler's clause nested-performs
    # :clock/now via the MODULE-LEVEL perform() which reads the active
    # runtime ContextVar. Under audit=True (Substrate.open default) the
    # facade sets _active automatically; under audit=False it does NOT,
    # so we wrap the coder.run() call in with_runtime(s._runtime).
    # Mirrors test_loop_replay.py:185 and test_recursion_composition_g4.py:285.
    with with_runtime(s._runtime):
        coder.run()

    assert len(captured) == 1, (
        f"expected 1 :llm/call captured; got {len(captured)}"
    )
    ctx = captured[0]
    assert ctx is not None, (
        "DispatcherContext was not bound during :llm/call dispatch — "
        "Coder.run() must wrap each iteration with dispatcher_context(...)"
    )
    assert isinstance(ctx, DispatcherContext)

    # Post-condition: ContextVar is unbound after run() returns (token reset)
    assert current_dispatcher_context() is None


