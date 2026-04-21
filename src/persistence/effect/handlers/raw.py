"""Raw / mocked handlers used as the bottom of the stack in tests and demo.

Never talks to a real LLM or network — pure Python echoes and scripted returns.
"""
from __future__ import annotations

from typing import Iterable

from persistence.effect.runtime import Handler


class TransientError(Exception):
    """Raised by the raw handler to model a retriable vendor failure."""


def make_echo_llm_handler(
    wraps: Iterable[str] = (":llm/call",),
    usage_tokens: int = 12,
) -> Handler:
    """Handler that returns ``{"text": "echo:<last-message-content>", "usage": ...}``.

    Used as the terminus of the stack in most tests. Deterministic —
    no randomness, no network.
    """

    def clause(args, k, ctx):
        messages = args.get("messages", [])
        content = messages[-1].get("content", "") if messages else ""
        return {
            "text": f"echo:{content}",
            "usage": {"tokens": ctx["usage_tokens"]},
            "fingerprint": f"mock:{args.get('model', 'unknown')}",
        }

    return Handler(
        name="raw-echo",
        wraps=set(wraps),
        clauses={":llm/call": clause},
        ctx={"usage_tokens": usage_tokens},
    )


def make_flaky_llm_handler(
    fail_every: int = 4,
    wraps: Iterable[str] = (":llm/call",),
) -> Handler:
    """Fails every Nth call with a :class:`TransientError` — models vendor 503s.

    Count lives in ctx so the handler instance carries its own state
    (no hidden globals, per spec §9).
    """

    def clause(args, k, ctx):
        ctx["n"] += 1
        if ctx["n"] % ctx["fail_every"] == 0:
            raise TransientError(f"vendor 503 on call {ctx['n']}")
        messages = args.get("messages", [])
        content = messages[-1].get("content", "") if messages else ""
        return {"text": f"echo:{content}", "usage": {"tokens": 12}}

    return Handler(
        name="raw-flaky",
        wraps=set(wraps),
        clauses={":llm/call": clause},
        ctx={"n": 0, "fail_every": fail_every},
    )


def make_scripted_tool_handler(
    scripts: dict[str, object],
    wraps: Iterable[str] = (":tool/call",),
) -> Handler:
    """Handler that returns ``scripts[name]`` on :tool/call for name ``name``.

    Unknown tool names return ``{"error": "unknown-tool"}``.
    """

    def clause(args, k, ctx):
        name = args["name"]
        if name in ctx["scripts"]:
            return {"result": ctx["scripts"][name], "error": None}
        return {"result": None, "error": "unknown-tool"}

    return Handler(
        name="raw-tools",
        wraps=set(wraps),
        clauses={":tool/call": clause},
        ctx={"scripts": dict(scripts)},
    )


def make_random_handler(
    seed: int = 0xC001FEE,
    wraps: Iterable[str] = (":random",),
) -> Handler:
    """Deterministic PRNG routed through :random. Uses random.Random seeded once.

    This is the ONLY authorized use of Python's random module inside the
    handler library. All other handlers must perform :random.
    """
    import random as _random

    def clause(args, k, ctx):
        rng: _random.Random = ctx["rng"]
        kind = args["kind"]
        params = args.get("params", {})
        if kind == "uniform":
            return {"value": rng.uniform(params.get("low", 0.0), params.get("high", 1.0))}
        if kind == "gaussian":
            return {"value": rng.gauss(params.get("mu", 0.0), params.get("sigma", 1.0))}
        if kind == "jitter":
            hi = params.get("max", 0.1)
            return {"value": rng.uniform(0.0, hi)}
        raise ValueError(f"unknown random kind: {kind}")

    return Handler(
        name=":random",
        wraps=set(wraps),
        clauses={":random": clause},
        ctx={"rng": _random.Random(seed)},
    )
