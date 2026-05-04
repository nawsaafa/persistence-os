"""Phase 2.1b — provider translation contract (G5).

Parametric: each handler factory, fed a deterministic vendor-shaped
fake response, produces a catalog-shape dict with identical structural
keys ({text, tool_calls, usage, fingerprint}). The catalog OpSpec is
the single source of truth across providers.
"""
from __future__ import annotations

import pytest


CATALOG_KEYS = {"text", "tool_calls", "usage", "fingerprint"}


def _build_callable_handler():
    """Mode 3 fake: caller-supplied call_fn returns a catalog dict directly."""
    from persistence.effect.handlers import make_callable_llm_handler

    def fake_call_fn(model, messages, tools=None, **_):
        return {
            "text": "ok",
            "tool_calls": [{"id": "tu", "name": "emit_decision",
                            "input": {"kind": "act", "confidence": 0.9, "payload": {}}}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "fingerprint": "fp-callable",
        }

    return make_callable_llm_handler(call_fn=fake_call_fn)


def _build_anthropic_handler(monkeypatch):
    """Mode 1 fake: patch anthropic.Anthropic to return a fake Message."""
    pytest.importorskip("anthropic")
    from persistence.effect.handlers import make_anthropic_llm_handler

    class _B:
        def __init__(self, type_, **kw):
            self.type = type_
            for k, v in kw.items():
                setattr(self, k, v)

    class _U:
        input_tokens = 1
        output_tokens = 1

    class _M:
        id = "fp-anthropic"
        content = [
            _B("text", text="ok"),
            _B("tool_use", id="tu", name="emit_decision",
               input={"kind": "act", "confidence": 0.9, "payload": {}}),
        ]
        usage = _U()

    class _Msgs:
        def create(self, **_): return _M()

    monkeypatch.setattr("anthropic.Anthropic",
                        lambda **_: type("X", (), {"messages": _Msgs()})())
    return make_anthropic_llm_handler(api_key="dummy")


def _build_claude_code_handler(monkeypatch):
    """Mode 2 fake: inject a fake query into ctx that returns a catalog-shaped dict."""
    pytest.importorskip("claude_agent_sdk")
    from persistence.effect.handlers import make_claude_code_llm_handler

    h = make_claude_code_llm_handler()
    h.ctx["query"] = lambda **_: {
        "text": "ok",
        "tool_calls": [{"id": "tu", "name": "emit_decision",
                        "input": {"kind": "act", "confidence": 0.9, "payload": {}}}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "fingerprint": "fp-claude-code",
    }
    return h


@pytest.mark.parametrize("name,builder", [
    ("callable", lambda mp: _build_callable_handler()),
    ("anthropic", _build_anthropic_handler),
    ("claude-code", _build_claude_code_handler),
])
def test_provider_handler_returns_catalog_keys(name, builder, monkeypatch):
    h = builder(monkeypatch)
    result = h.clauses[":llm/call"](
        {"model": "m", "messages": [{"role": "user", "content": "hi"}], "tools": []},
        lambda _: None,
        h.ctx,
    )
    assert set(result.keys()) >= CATALOG_KEYS, (
        f"{name} handler missing catalog keys: {CATALOG_KEYS - set(result.keys())}"
    )


@pytest.mark.parametrize("name,builder", [
    ("callable", lambda mp: _build_callable_handler()),
    ("anthropic", _build_anthropic_handler),
    ("claude-code", _build_claude_code_handler),
])
def test_provider_handler_tool_call_input_is_emit_decision_shape(name, builder, monkeypatch):
    h = builder(monkeypatch)
    result = h.clauses[":llm/call"](
        {"model": "m", "messages": [{"role": "user", "content": "hi"}], "tools": []},
        lambda _: None,
        h.ctx,
    )
    if not result["tool_calls"]:
        pytest.skip(f"{name} handler returned no tool_calls — text-fallback path")
    tc = result["tool_calls"][0]
    assert tc["name"] == "emit_decision"
    assert {"kind", "confidence", "payload"} <= set(tc["input"].keys())
