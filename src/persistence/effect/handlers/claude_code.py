"""Phase 2.1b — Mode 2 Claude Code :llm/call handler.

Routes :llm/call through claude-agent-sdk so the host Claude Code
session's auth (Max subscription) flows through. Lazy import.

SDK surface verified via context7 + direct package inspection
(claude-agent-sdk-python @ 0.x):

  - Entry-point: ``query(*, prompt, options=None, transport=None) ->
    AsyncIterator[Message]``. ``query`` is an async-generator
    function — must be driven via ``asyncio.run`` + an ``async for``.
  - ``prompt`` accepts ``str`` or ``AsyncIterable[dict]``. There is
    NO native ``messages: list[dict]`` kwarg, so this handler flattens
    the catalog ``messages`` argument into a single prompt string with
    explicit role prefixes.
  - ``ClaudeAgentOptions.tools`` is a list of CLI tool names
    (Read/Edit/Bash/...), NOT Anthropic-style ``tool_use`` schemas.
    Tool-use therefore degrades to text-fallback (LD3 in design); the
    coder's tier-2 ``<decision>{json}</decision>`` parser
    (Phase 3 ``_prompt.py``) handles it.
  - The yielded stream contains ``AssistantMessage`` (with ``.content``
    list of ``TextBlock | ToolUseBlock | ...``, plus ``.message_id``
    and ``.usage``) and ``ResultMessage`` (terminal, with ``.usage``
    and ``.session_id``).

The catalog wire-shape contract from the design (text/tool_calls/
usage/fingerprint) is reconstructed by collecting all
``AssistantMessage`` TextBlocks (concatenated for ``text``) and
ToolUseBlocks (mapped to ``{id,name,input}``) and pulling final
``usage``+``fingerprint`` from the ResultMessage (or last
AssistantMessage as fallback).
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import asyncio
from typing import Any

from persistence.effect.runtime import Handler


def make_claude_code_llm_handler(*, name: str = "claude-code-llm") -> Handler:
    """Build a ``:llm/call`` handler that routes through claude-agent-sdk.

    Raises ``RuntimeError`` (not bare ``ImportError``) if
    ``claude-agent-sdk`` is not installed.
    """
    try:
        from claude_agent_sdk import query
    except ImportError as exc:
        raise RuntimeError(
            "claude-agent-sdk not installed. "
            "Install with: pip install claude-agent-sdk"
        ) from exc

    def clause(args, k, ctx):
        prompt = _messages_to_prompt(args["messages"])
        # Build options lazily so the test seam can return a non-SDK
        # object (catalog dict or fake async-gen) without instantiating
        # ClaudeAgentOptions.
        resp = ctx["query"](
            prompt=prompt,
            options=_make_options(args),
        )
        # Test seam: catalog-shaped dict short-circuit. Preserves the
        # contract test path that injects a plain dict.
        if isinstance(resp, dict) and "text" in resp:
            return {
                "text": resp.get("text", ""),
                "tool_calls": resp.get("tool_calls", []),
                "usage": resp.get("usage", {}),
                "fingerprint": resp.get("fingerprint", ""),
            }
        # Real SDK path (or async-gen fake): drive the async stream
        # synchronously inside this sync clause via asyncio.run.
        return asyncio.run(_aggregate_stream(resp))

    return Handler(
        name=name,
        wraps={":llm/call"},
        clauses={":llm/call": clause},
        ctx={"query": query},
    )


def _messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    """Flatten a messages list into a single prompt string.

    claude-agent-sdk's ``query`` does not accept structured messages —
    it takes a single prompt string (or an AsyncIterable of dicts for
    streaming mode, which we don't use here). Role boundaries are
    preserved with explicit prefixes so the LLM still sees turn
    structure.
    """
    parts: list[str] = []
    for m in messages:
        role = str(m.get("role", "user")).capitalize()
        parts.append(f"{role}: {m.get('content', '')}")
    return "\n\n".join(parts)


def _make_options(args: dict[str, Any]) -> Any:
    """Build a ``ClaudeAgentOptions`` from catalog args.

    Returns ``None`` if no SDK-relevant options are present, letting
    ``query`` use its own defaults.
    """
    try:
        from claude_agent_sdk import ClaudeAgentOptions
    except ImportError:
        return None
    model = args.get("model")
    if model is None:
        return ClaudeAgentOptions()
    return ClaudeAgentOptions(model=model)


async def _aggregate_stream(stream: Any) -> dict[str, Any]:
    """Aggregate an SDK async-gen of messages into the catalog wire shape.

    - Concatenates all ``AssistantMessage`` ``TextBlock.text`` into ``text``.
    - Maps each ``ToolUseBlock`` to ``{id, name, input}`` in ``tool_calls``.
    - Pulls ``usage`` from the terminal ``ResultMessage`` if present,
      else the last AssistantMessage's ``usage``.
    - Picks ``fingerprint`` as (in order of preference) the last
      AssistantMessage ``message_id``, the ResultMessage ``session_id``,
      or empty string.

    Block discrimination uses ``isinstance`` against the lazily-imported
    SDK classes as the primary path. If the SDK ever renames
    ``TextBlock`` / ``ToolUseBlock``, the ``isinstance`` check fails on
    real SDK instances and the ``__class__.__name__`` fallback also
    misses — surfacing the drift loudly (empty result + Task 6 cross-
    handler equivalence test failure) instead of silently dropping
    content. The name-string fallback is preserved ONLY for test fakes
    that are not real SDK instances (e.g. mocks bypassing dataclass
    field constraints).
    """
    try:
        from claude_agent_sdk import TextBlock, ToolUseBlock
    except ImportError:
        # SDK absent at runtime: no real instances possible, name-string
        # match on fakes is the only path.
        TextBlock = None  # type: ignore[assignment]
        ToolUseBlock = None  # type: ignore[assignment]

    text_chunks: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    fingerprint = ""
    last_message_id: str | None = None
    last_session_id: str | None = None

    async for msg in stream:
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            for block in content:
                # Primary: isinstance against real SDK classes. Fallback:
                # __class__.__name__ string match for test fakes that
                # aren't real SDK instances (mocks bypassing dataclass
                # field constraints). If the SDK ever renames TextBlock
                # / ToolUseBlock, isinstance fails on real instances AND
                # the name-string fallback also misses — block is
                # dropped, surfacing drift loudly via Task 6 cross-
                # handler equivalence + the empty result, instead of
                # silently passing.
                if TextBlock is not None and isinstance(block, TextBlock):
                    text_chunks.append(getattr(block, "text", ""))
                elif ToolUseBlock is not None and isinstance(block, ToolUseBlock):
                    tool_calls.append({
                        "id": getattr(block, "id", ""),
                        "name": getattr(block, "name", ""),
                        "input": getattr(block, "input", {}),
                    })
                else:
                    # Test-fake fallback: block is not a real SDK
                    # instance (or the SDK is absent). Match on class
                    # name so existing fakes that bypass dataclass
                    # constraints still flow.
                    btype = getattr(block, "__class__", type(None)).__name__
                    if btype == "TextBlock":
                        text_chunks.append(getattr(block, "text", ""))
                    elif btype == "ToolUseBlock":
                        tool_calls.append({
                            "id": getattr(block, "id", ""),
                            "name": getattr(block, "name", ""),
                            "input": getattr(block, "input", {}),
                        })
                    # Other block kinds (Thinking/ToolResult/ServerTool*)
                    # are not part of the catalog wire shape — drop.
        msg_usage = getattr(msg, "usage", None)
        if isinstance(msg_usage, dict) and msg_usage:
            usage = msg_usage
        mid = getattr(msg, "message_id", None)
        if isinstance(mid, str) and mid:
            last_message_id = mid
        sid = getattr(msg, "session_id", None)
        if isinstance(sid, str) and sid:
            last_session_id = sid

    fingerprint = last_message_id or last_session_id or ""
    return {
        "text": "".join(text_chunks),
        "tool_calls": tool_calls,
        "usage": usage,
        "fingerprint": fingerprint,
    }
