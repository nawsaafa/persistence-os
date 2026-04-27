"""EffectIntent envelope + thread-local guard."""
import pytest

from persistence.txn.intents import (
    EffectIntent,
    is_in_dosync,
    set_dosync_guard,
    clear_dosync_guard,
)


def test_effect_intent_captures_op_and_kwargs():
    intent = EffectIntent(op=":llm/call", kwargs={"prompt": "hello"})
    assert intent.op == ":llm/call"
    assert intent.kwargs == {"prompt": "hello"}


def test_effect_intent_is_frozen():
    intent = EffectIntent(op=":llm/call", kwargs={"prompt": "hello"})
    with pytest.raises((AttributeError, TypeError)):
        intent.op = ":other"


def test_dosync_guard_initially_off():
    assert is_in_dosync() is False


def test_dosync_guard_set_and_clear():
    token = set_dosync_guard()
    assert is_in_dosync() is True
    clear_dosync_guard(token)
    assert is_in_dosync() is False
