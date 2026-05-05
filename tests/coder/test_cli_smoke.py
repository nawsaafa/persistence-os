"""Phase 2.1a — subprocess-driven CLI smoke tests.

Per design § 5.1. Verifies the externally observable invocation
surface (`python -m persistence.coder`):

- Exit code 1 on first stub raise (CoderStubNotImplemented).
- Stderr banner contains "persistence-coder skeleton: Phase 2.2a".
- `--db-path` omission emits in-memory warning to stderr.
- `--task` is required by argparse.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}

# Phase 2.2a follow-up: T6's run() widening means _observe is now filled
# and the loop reaches _decide → :llm/call. Auto-detect picks claude-code
# when claude-agent-sdk is importable; that provider attempts a real
# LLM call and times out. Skip these subprocess smokes in that env (the
# coder unit tests still cover the gate logic). Same precedent as the
# 2.1b skip-if-installed pattern. Re-enable once the CLI exposes an
# explicit --provider=echo choice.
_CLAUDE_CODE_AVAILABLE = importlib.util.find_spec("claude_agent_sdk") is not None
_SKIP_REASON = (
    "claude-agent-sdk installed → auto-detect picks claude-code provider, "
    "which would attempt a real LLM call instead of falling through to a "
    "stub raise. Skipped per the 2.1b precedent; covered by unit tests."
)


@pytest.mark.skipif(_CLAUDE_CODE_AVAILABLE, reason=_SKIP_REASON)
def test_cli_runs_skeleton_and_emits_banner_on_first_stub() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "persistence.coder", "--task", "hello"],
        capture_output=True,
        text=True,
        timeout=10,
        env=ENV,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1
    assert "persistence-coder skeleton" in result.stderr
    # Phase 2.2a T6: _should_escalate_* filled; first stub hit is _escalate_branch.
    assert "Phase 2.3b" in result.stderr
    assert "s.plan.mcts_search" in result.stderr


@pytest.mark.skipif(_CLAUDE_CODE_AVAILABLE, reason=_SKIP_REASON)
def test_cli_warns_when_db_path_omitted() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "persistence.coder", "--task", "hi"],
        capture_output=True,
        text=True,
        timeout=10,
        env=ENV,
        cwd=str(REPO_ROOT),
    )
    assert "warning: no --db-path" in result.stderr
    assert "in-memory substrate" in result.stderr


def test_cli_rejects_missing_task() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "persistence.coder"],
        capture_output=True,
        text=True,
        timeout=10,
        env=ENV,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode != 0
    assert "--task" in result.stderr  # argparse "required" message
