"""Phase 2.1a — subprocess-driven CLI smoke tests.

Per design § 5.1. Verifies the externally observable invocation
surface (`python -m persistence.coder`):

- Exit code 1 on first stub raise (CoderStubNotImplemented).
- Stderr banner contains "persistence-coder skeleton: Phase 2.2a".
- `--db-path` omission emits in-memory warning to stderr.
- `--task` is required by argparse.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}


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
    # Phase 2.2a T4: _observe no longer stubs; first stub is _should_escalate_branch.
    assert "Phase 2.3b" in result.stderr
    assert "decision.kind == 'branch'" in result.stderr


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
