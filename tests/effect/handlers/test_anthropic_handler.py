"""Phase 2.1b — Mode 1 Anthropic handler tests (design § 3.3).

Skip-marked when the anthropic SDK is absent — these are unit tests
that patch the SDK client; integration tests requiring real API keys
are gated separately by the missing-key skip below.
"""
from __future__ import annotations

import os

import pytest

anthropic = pytest.importorskip("anthropic")  # noqa: F841


def test_make_anthropic_llm_handler_translates_response_to_catalog_shape(monkeypatch):
    """Patch anthropic.Anthropic to return a fake Message; assert the
    handler clause produces catalog wire shape."""
    from persistence.effect.handlers import make_anthropic_llm_handler

    class _FakeBlock:
        def __init__(self, type_, **kw):
            self.type = type_
            for k, v in kw.items():
                setattr(self, k, v)

    class _FakeUsage:
        input_tokens = 5
        output_tokens = 7

    class _FakeMessage:
        id = "msg_test_001"
        content = [
            _FakeBlock("text", text="answer text"),
            _FakeBlock("tool_use", id="tu_001", name="emit_decision",
                       input={"kind": "act", "confidence": 0.9, "payload": {}}),
        ]
        usage = _FakeUsage()

    class _FakeMessages:
        def create(self, **kw):
            self.last_kw = kw
            return _FakeMessage()

    class _FakeClient:
        def __init__(self, **_): self.messages = _FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", _FakeClient)

    h = make_anthropic_llm_handler(api_key="dummy")
    result = h.clauses[":llm/call"](
        {"model": "claude-opus-4-7",
         "messages": [{"role": "user", "content": "hi"}],
         "tools": [{"name": "emit_decision"}]},
        lambda _: None,
        h.ctx,
    )

    assert result["text"] == "answer text"
    assert result["tool_calls"] == [{
        "id": "tu_001",
        "name": "emit_decision",
        "input": {"kind": "act", "confidence": 0.9, "payload": {}},
    }]
    assert result["usage"] == {"input_tokens": 5, "output_tokens": 7}
    assert result["fingerprint"] == "msg_test_001"


def test_make_anthropic_llm_handler_default_name_and_wraps(monkeypatch):
    monkeypatch.setattr("anthropic.Anthropic", lambda **_: type("X", (), {"messages": None})())
    from persistence.effect.handlers import make_anthropic_llm_handler

    h = make_anthropic_llm_handler(api_key="dummy")
    assert h.name == "anthropic-llm"
    assert h.wraps == {":llm/call"}


def test_make_anthropic_llm_handler_raises_friendly_error_when_sdk_absent(monkeypatch):
    """If anthropic is unavailable, RuntimeError (not bare ImportError)."""
    import sys
    monkeypatch.setitem(sys.modules, "anthropic", None)  # force ImportError on import
    # Force re-import inside factory by deleting any cached module
    if "persistence.effect.handlers.anthropic" in sys.modules:
        del sys.modules["persistence.effect.handlers.anthropic"]
    # Re-import the factory through the public surface — the lazy import
    # inside the factory body should raise RuntimeError.
    from persistence.effect.handlers import make_anthropic_llm_handler

    with pytest.raises(RuntimeError, match="anthropic SDK not installed"):
        make_anthropic_llm_handler(api_key="dummy")


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set (live integration test)",
)
def test_make_anthropic_llm_handler_real_call_returns_catalog_shape():
    """Tier-2 integration: real API call. Skipped without key."""
    from persistence.effect.handlers import make_anthropic_llm_handler

    h = make_anthropic_llm_handler()
    result = h.clauses[":llm/call"](
        {"model": "claude-haiku-4-5-20251001",
         "messages": [{"role": "user", "content": "Reply with just 'ok'."}],
         "max_tokens": 64},
        lambda _: None,
        h.ctx,
    )
    assert isinstance(result["text"], str)
    assert "input_tokens" in result["usage"]
    assert "output_tokens" in result["usage"]
    assert isinstance(result["fingerprint"], str) and result["fingerprint"]
