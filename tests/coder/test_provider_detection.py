"""Phase 2.1b — provider auto-detection tests (G2.1b-c, design § 3.6)."""
from __future__ import annotations

import pytest


def _patch_claude_code(monkeypatch, available: bool):
    import sys
    if available:
        # Inject a stub module into sys.modules so import succeeds
        monkeypatch.setitem(sys.modules, "claude_agent_sdk", type(sys)("claude_agent_sdk"))
    else:
        monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)


# ---------- _claude_code_available (importability-only per F8) ----------


def test_claude_code_available_true_when_module_importable(monkeypatch):
    from persistence.coder import _provider
    _patch_claude_code(monkeypatch, available=True)
    assert _provider._claude_code_available() is True


def test_claude_code_available_false_when_module_absent(monkeypatch):
    from persistence.coder import _provider
    _patch_claude_code(monkeypatch, available=False)
    assert _provider._claude_code_available() is False


# ---------- detect_or_explicit auto matrix (G2.1b-c) ----------


def test_auto_prefers_claude_code_when_available(monkeypatch):
    from persistence.coder import _provider

    _patch_claude_code(monkeypatch, available=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    # Make make_claude_code_llm_handler return a sentinel to avoid SDK calls
    monkeypatch.setattr(
        "persistence.effect.handlers.make_claude_code_llm_handler",
        lambda **_: object(),
    )
    handler, name = _provider.detect_or_explicit("auto")
    assert name == "claude-code"
    assert handler is not None


def test_auto_falls_to_anthropic_when_claude_code_absent(monkeypatch):
    from persistence.coder import _provider

    _patch_claude_code(monkeypatch, available=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(
        "persistence.effect.handlers.make_anthropic_llm_handler",
        lambda **_: object(),
    )
    handler, name = _provider.detect_or_explicit("auto")
    assert name == "anthropic"
    assert handler is not None


def test_auto_falls_to_echo_when_neither_available(monkeypatch):
    from persistence.coder import _provider

    _patch_claude_code(monkeypatch, available=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    handler, name = _provider.detect_or_explicit("auto")
    assert name == "echo"
    assert handler is None


# ---------- explicit-provider error paths ----------


def test_explicit_claude_code_raises_when_sdk_absent(monkeypatch):
    from persistence.coder import _provider

    _patch_claude_code(monkeypatch, available=False)
    with pytest.raises(SystemExit, match="claude-agent-sdk not installed"):
        _provider.detect_or_explicit("claude-code")


def test_explicit_anthropic_raises_when_key_missing(monkeypatch):
    from persistence.coder import _provider

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY not set"):
        _provider.detect_or_explicit("anthropic")


def test_explicit_anthropic_raises_when_sdk_runtime_error(monkeypatch):
    """If anthropic SDK absent (factory raises RuntimeError), surface
    as SystemExit not unhandled exception."""
    from persistence.coder import _provider

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    def _raise(*_, **__): raise RuntimeError("anthropic SDK not installed (test)")
    monkeypatch.setattr(
        "persistence.effect.handlers.make_anthropic_llm_handler",
        _raise,
    )
    with pytest.raises(SystemExit, match="anthropic SDK not installed"):
        _provider.detect_or_explicit("anthropic")
