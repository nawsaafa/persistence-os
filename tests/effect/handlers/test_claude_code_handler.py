"""Phase 2.1b — Mode 2 Claude Code handler tests (design § 3.3).

The exact translation depends on claude-agent-sdk's API surface, which
was verified during impl via context7 + direct package inspection.

Findings:
  - ``query`` is async-gen (``async def`` returning ``AsyncIterator``);
    handler clause wraps with ``asyncio.run``.
  - ``prompt`` is a single string (or AsyncIterable[dict]); NO native
    ``messages=`` kwarg — handler flattens messages → prompt string.
  - ``tools`` on ``ClaudeAgentOptions`` is a list of CLI tool names
    (Read/Edit/etc.), NOT Anthropic-style tool-use schemas, so
    tool-use degrades to text-fallback (LD3, parsed by tier-2 in
    Phase 3 ``_prompt.py``).
  - Stream yields ``UserMessage | AssistantMessage | SystemMessage |
    ResultMessage | ...``. ``AssistantMessage.content`` is a list of
    ``TextBlock | ToolUseBlock | ...``. ``ResultMessage`` carries
    final ``usage`` and ``session_id``.

This test file pins the catalog wire-shape contract; the handler's
internals translate the SDK stream into that shape.
"""
from __future__ import annotations

import pytest

claude_agent_sdk = pytest.importorskip("claude_agent_sdk")  # noqa: F841


def test_make_claude_code_llm_handler_returns_catalog_shape(monkeypatch):
    """Patch the SDK entry-point in ctx to return a fake response;
    assert the handler clause produces catalog wire shape (text /
    tool_calls / usage / fingerprint keys present).

    This test uses the test seam — ``ctx['query']`` returns a
    catalog-shaped dict directly (bypasses SDK stream translation).
    The dedicated translation test below covers the real SDK shape.
    """
    from persistence.effect.handlers import make_claude_code_llm_handler

    h = make_claude_code_llm_handler()
    # Inject a fake call_fn into ctx via direct override (test seam):
    h.ctx["query"] = lambda **kw: _fake_catalog_response()
    result = h.clauses[":llm/call"](
        {"model": "claude-opus-4-7",
         "messages": [{"role": "user", "content": "hi"}],
         "tools": []},
        lambda _: None,
        h.ctx,
    )
    assert "text" in result
    assert "tool_calls" in result
    assert "usage" in result
    assert "fingerprint" in result


def _fake_catalog_response():
    """Catalog-shaped seam — exercised only by test_1 via ctx['query']
    override. Lets the contract test run without the SDK's async stream.
    """
    return {"text": "ok", "tool_calls": [], "usage": {}, "fingerprint": "fp"}


def test_make_claude_code_llm_handler_default_name_and_wraps():
    from persistence.effect.handlers import make_claude_code_llm_handler

    h = make_claude_code_llm_handler()
    assert h.name == "claude-code-llm"
    assert h.wraps == {":llm/call"}


def test_make_claude_code_llm_handler_raises_friendly_error_when_sdk_absent(monkeypatch):
    """If claude-agent-sdk is unavailable, RuntimeError (not bare ImportError)."""
    import sys
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
    if "persistence.effect.handlers.claude_code" in sys.modules:
        del sys.modules["persistence.effect.handlers.claude_code"]
    # Re-import through the public surface
    from persistence.effect.handlers import make_claude_code_llm_handler

    with pytest.raises(RuntimeError, match="claude-agent-sdk not installed"):
        make_claude_code_llm_handler()


def test_make_claude_code_llm_handler_translates_sdk_stream(monkeypatch):
    """Real SDK shape: ``query`` is an async-gen yielding messages.

    Patch ctx['query'] to be an async-gen that yields:
      - AssistantMessage with TextBlock + ToolUseBlock content
      - ResultMessage with terminal usage + session_id

    Assert the handler:
      - asyncio.run-wraps the async iteration
      - aggregates all AssistantMessage TextBlock.text into result['text']
      - extracts ToolUseBlock id/name/input into result['tool_calls']
      - pulls usage from ResultMessage (or AssistantMessage)
      - uses AssistantMessage.message_id (or ResultMessage.session_id) as fingerprint
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
    )

    from persistence.effect.handlers import make_claude_code_llm_handler

    asst = AssistantMessage(
        content=[
            TextBlock(text="hello "),
            ToolUseBlock(id="tu_42", name="emit_decision",
                         input={"kind": "act", "confidence": 0.8, "payload": {}}),
            TextBlock(text="world"),
        ],
        model="claude-opus-4-7",
        parent_tool_use_id=None,
        error=None,
        usage={"input_tokens": 11, "output_tokens": 13},
        message_id="msg_cc_001",
        stop_reason="end_turn",
        session_id="sess_abc",
        uuid="u-1",
    )
    res = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=80,
        is_error=False,
        num_turns=1,
        session_id="sess_abc",
        stop_reason="end_turn",
        total_cost_usd=0.0,
        usage={"input_tokens": 11, "output_tokens": 13},
        result="ok",
        structured_output=None,
        model_usage=None,
        permission_denials=None,
        errors=None,
        uuid="u-2",
    )

    async def _fake_query(**_kw):
        yield asst
        yield res

    h = make_claude_code_llm_handler()
    h.ctx["query"] = _fake_query

    result = h.clauses[":llm/call"](
        {"model": "claude-opus-4-7",
         "messages": [{"role": "user", "content": "hi"}],
         "tools": []},
        lambda _: None,
        h.ctx,
    )

    assert result["text"] == "hello world"
    assert result["tool_calls"] == [{
        "id": "tu_42",
        "name": "emit_decision",
        "input": {"kind": "act", "confidence": 0.8, "payload": {}},
    }]
    assert result["usage"] == {"input_tokens": 11, "output_tokens": 13}
    # fingerprint prefers the message_id; falls back to session_id.
    assert result["fingerprint"] in ("msg_cc_001", "sess_abc")
    assert result["fingerprint"] == "msg_cc_001"
