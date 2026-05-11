"""Phase 2.1a / 2.4b.1 — subprocess-driven CLI smoke tests.

Verifies the externally observable invocation surface
(`python -m persistence.coder`):

- Phase 2.4b.1 LD-1 G1: no-provider invocation exits non-zero with a
  single-line stderr banner and NO raw Python traceback. The banner
  substring is exact-match (R0-fold I4 — no "or similar" wording).
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
    """Phase 2.4b.1 LD-1 G1 — narrow banner-mask in echo mode.

    With no provider available, the auto-detect path falls through to the
    echo handler. Echo's deterministic ``{"text": "echo:..."}`` response
    cannot drive a real agent loop, so ``Coder.run()`` eventually raises
    ``ValueError``. LD-1 narrows the banner-mask to ``provider_name ==
    "echo"`` so this case exits 1 with a single-line stderr message
    instead of a raw Python traceback (which is what users saw pre-2.4b.1).

    Falsifier (manual probe): delete the ``except ValueError`` block in
    ``__main__.py`` → the traceback re-appears → the
    ``"Traceback ... not in stderr"`` assertion fails.
    """
    result = subprocess.run(
        [sys.executable, "-m", "persistence.coder", "--task", "hello"],
        capture_output=True,
        text=True,
        timeout=10,
        env=ENV,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1
    # G1 core invariant: no raw Python traceback in stderr.
    assert "Traceback (most recent call last):" not in result.stderr
    # G1 exact banner substring (R0-fold I4 — no "or similar" wording).
    assert (
        "persistence-coder: echo handler can't drive a real agent loop"
        in result.stderr
    )
    assert (
        "Set ANTHROPIC_API_KEY or sign in to Claude Code, then re-run."
        in result.stderr
    )
    # Pre-loop warnings from _build_substrate_and_handlers must still print.
    assert "warning: no --db-path" in result.stderr
    assert "warning: no LLM provider available" in result.stderr
    assert "echo handler" in result.stderr


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
