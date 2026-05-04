"""persistence.effect.handlers — the handler library.

Each handler is a factory returning a :class:`persistence.effect.runtime.Handler`.
Per spec §9, handlers carry their own ctx (no hidden globals) and route all
non-determinism through effects (:clock/now, :random).
"""
from persistence.effect.handlers.callable import make_callable_llm_handler
from persistence.effect.handlers.anthropic import make_anthropic_llm_handler
from persistence.effect.handlers.claude_code import make_claude_code_llm_handler

__all__ = [
    "make_callable_llm_handler",
    "make_anthropic_llm_handler",
    "make_claude_code_llm_handler",
]
