"""Phase 2.1b — Mode 3 callable :llm/call handler primitive.

Library-only. Caller's ``call_fn`` is responsible for translating its
vendor's response into the catalog wire shape per
``OpSpec(":llm/call").returns``::

    {"text": str, "tool_calls": list[dict], "usage": dict, "fingerprint": str}

This is the provider-agnosticism primitive (LD2): persistence-coder is
NOT Claude-specific. Any host (Codex, Cursor, juba, custom Python apps,
Ollama / vLLM / local LLMs) can wire its LLM access by passing a
``call_fn`` that does the translation.

No new deps. Install via::

    s.effect.install_handler(make_callable_llm_handler(call_fn=my_fn),
                             position="bottom")
"""
from __future__ import annotations

from typing import Any, Callable

from persistence.effect.runtime import Handler

CallFn = Callable[..., dict[str, Any]]
"""Signature: ``(*, model, messages, tools=None, temperature=None,
max_tokens=None) -> catalog-shape dict``."""


def make_callable_llm_handler(
    call_fn: CallFn,
    *,
    name: str = "callable-llm",
) -> Handler:
    """Build a ``:llm/call`` handler whose clause delegates to ``call_fn``.

    ``call_fn`` is invoked with kwargs only (``model=``, ``messages=``,
    optional ``tools=`` / ``temperature=`` / ``max_tokens=``) and must
    return a catalog-shape dict. Any vendor translation is the caller's
    responsibility.
    """

    def clause(args, k, ctx):
        return ctx["call_fn"](
            model=args["model"],
            messages=args["messages"],
            tools=args.get("tools"),
            temperature=args.get("temperature"),
            max_tokens=args.get("max_tokens"),
        )

    return Handler(
        name=name,
        wraps={":llm/call"},
        clauses={":llm/call": clause},
        ctx={"call_fn": call_fn},
    )
