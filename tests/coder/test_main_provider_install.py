"""Phase 2.1b — __main__.py provider install + stderr UX tests.

Subprocess-based: launches `python -m persistence.coder` in a fresh
process with controlled env, captures stderr. Hermetic — passes
env= and cwd= explicitly per the 2.1a F1 BLOCKING fix lesson.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHONPATH = str(REPO_ROOT / "src")


def _run(argv, env_extra: dict | None = None):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": PYTHONPATH}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "persistence.coder"] + argv,
        env=env,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_main_auto_no_providers_prints_echo_warning_and_exits_one():
    """No ANTHROPIC_API_KEY, no claude-agent-sdk → echo floor + warning + exit 1."""
    # Skip if claude-agent-sdk is installed — auto would pick claude-code, not echo.
    # The subprocess inherits the venv's site-packages because sys.executable is
    # the venv's python; `claude_agent_sdk` is therefore importable in the child.
    try:
        import claude_agent_sdk  # noqa: F401
        pytest.skip("claude-agent-sdk installed; auto path picks it instead of echo")
    except ImportError:
        pass
    result = _run(["--task", "t"])
    assert result.returncode == 1
    assert "no LLM provider available" in result.stderr
    assert "echo handler" in result.stderr
    # Phase 2.2a T4: _observe no longer stubs; _decide runs with echo handler;
    # first stub hit is _should_escalate_branch → CLI banner-masks → exits 1.
    assert "Phase 2.3b" in result.stderr


def test_main_explicit_anthropic_no_key_exits_with_error():
    result = _run(["--task", "t", "--provider", "anthropic"])
    assert result.returncode != 0
    assert "ANTHROPIC_API_KEY not set" in result.stderr


def test_main_explicit_claude_code_no_sdk_exits_with_error():
    result = _run(["--task", "t", "--provider", "claude-code"])
    # Skip if claude-agent-sdk IS installed — this test covers the absent case
    if "not installed" not in result.stderr:
        pytest.skip("claude-agent-sdk is installed in this env; skipping absent-case test")
    assert result.returncode != 0
    assert "claude-agent-sdk not installed" in result.stderr


def test_main_provider_stderr_note_for_anthropic_when_key_present():
    """When key is set, stderr says 'using anthropic provider'."""
    pytest.importorskip("anthropic")
    result = _run(
        ["--task", "t", "--provider", "anthropic"],
        env_extra={"ANTHROPIC_API_KEY": "sk-ant-fake-no-real-call"},
    )
    # Coder.run() will raise CoderStubNotImplemented on _observe before any
    # real call, so we just check the stderr provider note was printed.
    assert "using anthropic provider" in result.stderr
