"""Raw effect.perform() inside dosync guard raises EffectInIoBlock."""
import pytest

from persistence.effect.runtime import (
    Handler,
    Runtime,
    perform,
    with_runtime,
)
from persistence.txn import EffectInIoBlock
from persistence.txn.intents import set_dosync_guard, clear_dosync_guard


def _identity_handler() -> Handler:
    return Handler(
        name="identity",
        wraps={":noop"},
        clauses={":noop": lambda args, k, ctx: args.get("value", None)},
    )


def test_perform_outside_dosync_works():
    rt = Runtime(handlers=[_identity_handler()])
    with with_runtime(rt):
        result = perform(":noop", value=42)
    assert result == 42


def test_perform_inside_dosync_guard_raises_EffectInIoBlock():
    rt = Runtime(handlers=[_identity_handler()])
    token = set_dosync_guard()
    try:
        with with_runtime(rt):
            with pytest.raises(EffectInIoBlock):
                perform(":noop", value=42)
    finally:
        clear_dosync_guard(token)
