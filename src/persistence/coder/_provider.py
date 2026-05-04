"""Phase 2.1b — provider auto-detection + explicit-provider validation.

The CLI's --provider auto path tries claude-code first (Max sub),
anthropic second (paid API), echo handler third (smoke-test floor).
Explicit --provider=<name> errors loudly via SystemExit if the chosen
provider isn't available.

R1 fix-pass F8: --provider=claude-code detection is importability ONLY
— no session-reachability probe at startup (no RPC, no key check). The
session handshake happens lazily on first :llm/call perform; if the
user is signed out the handler raises a clean RuntimeError on first
call which the CLI surfaces.
"""
from __future__ import annotations

import os

from persistence.effect.runtime import Handler

ProviderName = str  # "claude-code" | "anthropic" | "echo"


def detect_or_explicit(provider: str) -> tuple[Handler | None, ProviderName]:
    """Return ``(handler-to-install, provider_name)``.

    handler is ``None`` for the ``"echo"`` floor case (caller installs
    ``make_echo_llm_handler`` itself). Raises ``SystemExit`` (with a
    user-facing error message) on explicit-provider unavailability.
    """
    if provider in ("claude-code", "auto"):
        if _claude_code_available():
            from persistence.effect.handlers import make_claude_code_llm_handler
            return make_claude_code_llm_handler(), "claude-code"
        if provider == "claude-code":
            raise SystemExit(
                "error: --provider=claude-code but claude-agent-sdk not "
                "installed (pip install claude-agent-sdk). "
                "Note: signed-out state is detected lazily on first "
                "LLM call, not at startup."
            )

    if provider in ("anthropic", "auto"):
        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                from persistence.effect.handlers import make_anthropic_llm_handler
                return make_anthropic_llm_handler(), "anthropic"
            except RuntimeError as exc:
                if provider == "anthropic":
                    raise SystemExit(f"error: --provider=anthropic but {exc}") from exc
                # auto: fall through to echo floor
        elif provider == "anthropic":
            raise SystemExit(
                "error: --provider=anthropic but ANTHROPIC_API_KEY not set"
            )

    return None, "echo"


def _claude_code_available() -> bool:
    """Importability check ONLY — no session-reachability probe.

    First-call ``RuntimeError`` from the handler surfaces signed-out
    state with a "sign in to Claude Code" hint; the CLI surfaces that
    via the same exit path as any other handler exception.
    """
    try:
        import claude_agent_sdk  # noqa: F401
        return True
    except ImportError:
        return False
