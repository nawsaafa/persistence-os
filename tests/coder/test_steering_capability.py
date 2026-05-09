"""G6 — Phase 2.3d LD-3 (R0-fold B5): cap shape representable + per-op denial."""
from __future__ import annotations

import pytest

from persistence.coder import Coder, _CoderSteeringSession
from persistence.effect.handlers import make_callable_llm_handler
from persistence.sdk import Substrate


def _done_call_fn():
    def call_fn(*, model, messages, tools=None, temperature=None, max_tokens=None):
        return {
            "tool_calls": [{
                "input": {
                    "kind": "act",
                    "confidence": 0.9,
                    "payload": {"done": True},
                },
            }],
            "text": "",
        }
    return call_fn


def test_capability_op_coder_representable():
    """B5 schema-level: Capability(op='coder', qualifier=...) round-trips."""
    from persistence.repl._caps import Capability
    cap_read = Capability(op="coder", qualifier="read")
    cap_write = Capability(op="coder", qualifier="write")
    cap_any = Capability(op="coder", qualifier="any")
    assert cap_read.op == "coder"
    assert cap_read.qualifier == "read"
    assert cap_write.qualifier == "write"
    assert cap_any.qualifier == "any"


def test_capability_unknown_qualifier_raises():
    from persistence.repl._caps import Capability, UnknownCapability
    with pytest.raises(UnknownCapability):
        Capability(op="coder", qualifier="lol")


@pytest.mark.parametrize(
    "op_name,required",
    [
        ("pause", ("coder", "read")),
        ("resume", ("coder", "read")),
        ("snapshot", ("coder", "read")),
        ("branch", ("coder", "write")),
        ("fold", ("coder", "write")),
        ("commit", ("coder", "write")),
    ],
)
def test_op_requires_capability(op_name, required):
    """G6 per-op denial. context_at omitted only because it needs a `t` arg —
    covered separately."""
    from persistence.repl._caps import CapabilitySet
    from persistence.repl._ws import _OpError
    from persistence.repl._protocol import ERR_CAPABILITY_DENIED

    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        # Empty cap set — no coder caps at all
        empty_caps = CapabilitySet(caps=frozenset())
        session = _CoderSteeringSession(coder=coder, cap_set=empty_caps)

        with pytest.raises(_OpError) as exc_info:
            method = getattr(session, op_name)
            if op_name == "branch":
                method({"directive": "x"})
            elif op_name == "fold":
                method(probe=lambda db: 1)
            elif op_name == "commit":
                method("parent")
            else:
                method()

        assert exc_info.value.code == ERR_CAPABILITY_DENIED
