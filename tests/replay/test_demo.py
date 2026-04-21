"""Demo must reproduce agent4-replay-spec §7 exactly."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_demo_runs_and_prints_factual_counterfactual_comparison():
    repo_root = Path(__file__).resolve().parent.parent.parent
    src_dir = repo_root / "src"
    env = {**os.environ, "PYTHONPATH": str(src_dir)}
    result = subprocess.run(
        [sys.executable, "-m", "persistence.replay.demo"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo_root),
    )
    assert result.returncode == 0, (
        f"demo failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # Spec §7 prints three lines: Factual, Counterfactual, Comparison.
    out = result.stdout
    assert "Factual:" in out
    assert "Counterfactual:" in out
    assert "Comparison:" in out
    # Comparison must include pnl_delta, divergence_step, factual_pnl,
    # counterfactual_pnl keys (from compare()).
    assert "pnl_delta" in out
    assert "divergence_step" in out
    assert "factual_pnl" in out
    assert "counterfactual_pnl" in out
