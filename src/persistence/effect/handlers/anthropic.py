"""Phase 2.1b — Mode 1 Anthropic API :llm/call handler.

Routes :llm/call through the official ``anthropic`` Python SDK.
Lazy import — ``pip install persistence-os`` does NOT drag the SDK
unless this factory is called. Friendly RuntimeError (not bare
ImportError) when the SDK is absent.

Vendor → catalog translation per design § 3.3 / verified against
anthropic>=0.40 Messages API:
  - response.content is a list of blocks; .type ∈ {"text", "tool_use"}
  - text blocks: .text
  - tool_use blocks: .id, .name, .input
  - response.usage.input_tokens / .output_tokens
  - response.id is the fingerprint
"""
from __future__ import annotations

from typing import Any

from persistence.effect.runtime import Handler


def make_anthropic_llm_handler(
    *,
    api_key: str | None = None,
    name: str = "anthropic-llm",
) -> Handler:
    """Build a ``:llm/call`` handler that calls the Anthropic Messages API.

    ``api_key`` defaults to ``None`` so the SDK reads ``ANTHROPIC_API_KEY``
    from the environment. Pass an explicit key to override.

    Raises ``RuntimeError`` (not bare ``ImportError``) if the
    ``anthropic`` SDK is not installed.
    """
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "anthropic SDK not installed. "
            "Install with: pip install 'anthropic>=0.40,<1.0'"
        ) from exc

    client = anthropic.Anthropic(api_key=api_key)

    def clause(args, k, ctx):
        resp = ctx["client"].messages.create(
            model=args["model"],
            messages=args["messages"],
            tools=args.get("tools", []) or [],
            temperature=args.get("temperature", 1.0),
            max_tokens=args.get("max_tokens", 4096),
        )
        text = ""
        tool_calls: list[dict[str, Any]] = []
        for block in resp.content:
            if block.type == "text":
                text = block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
        return {
            "text": text,
            "tool_calls": tool_calls,
            "usage": {
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            },
            "fingerprint": resp.id,
        }

    return Handler(
        name=name,
        wraps={":llm/call"},
        clauses={":llm/call": clause},
        ctx={"client": client},
    )
