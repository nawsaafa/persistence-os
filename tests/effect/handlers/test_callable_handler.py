"""Phase 2.1b — Mode 3 callable handler unit tests (design § 3.3 + LD2)."""
from __future__ import annotations

from persistence.effect.handlers import make_callable_llm_handler


def test_callable_handler_passes_args_through():
    received = {}

    def call_fn(model, messages, tools=None, temperature=None, max_tokens=None):
        received["model"] = model
        received["messages"] = messages
        received["tools"] = tools
        return {"text": "ok", "tool_calls": [], "usage": {"in": 1, "out": 1}, "fingerprint": "fp"}

    h = make_callable_llm_handler(call_fn=call_fn)
    result = h.clauses[":llm/call"](
        {"model": "m", "messages": [{"role": "user", "content": "hi"}], "tools": [{"name": "t"}]},
        lambda _: None,
        h.ctx,
    )
    assert received["model"] == "m"
    assert received["messages"] == [{"role": "user", "content": "hi"}]
    assert received["tools"] == [{"name": "t"}]
    assert result == {"text": "ok", "tool_calls": [], "usage": {"in": 1, "out": 1}, "fingerprint": "fp"}


def test_callable_handler_default_name_and_wraps():
    def call_fn(**_): return {"text": "", "tool_calls": [], "usage": {}, "fingerprint": ""}
    h = make_callable_llm_handler(call_fn=call_fn)
    assert h.name == "callable-llm"
    assert h.wraps == {":llm/call"}


def test_callable_handler_custom_name():
    def call_fn(**_): return {"text": "", "tool_calls": [], "usage": {}, "fingerprint": ""}
    h = make_callable_llm_handler(call_fn=call_fn, name="my-vendor")
    assert h.name == "my-vendor"


def test_callable_handler_optional_args_default_to_none():
    received = {}

    def call_fn(model, messages, tools=None, temperature=None, max_tokens=None):
        received.update({"tools": tools, "temperature": temperature, "max_tokens": max_tokens})
        return {"text": "", "tool_calls": [], "usage": {}, "fingerprint": ""}

    h = make_callable_llm_handler(call_fn=call_fn)
    h.clauses[":llm/call"]({"model": "m", "messages": []}, lambda _: None, h.ctx)
    assert received == {"tools": None, "temperature": None, "max_tokens": None}
