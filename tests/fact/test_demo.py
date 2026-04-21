"""Smoke test for ``python -m persistence.fact.demo``.

The demo reproduces the agent1-fact-spec §8 WACC counterfactual for project
P-042. Verifying it runs cleanly + prints the expected factual, historical,
and counterfactual values is a conductor-track verification gate.
"""

from __future__ import annotations

import subprocess
import sys


def test_demo_reproduces_spec_section_8_output():
    result = subprocess.run(
        [sys.executable, "-m", "persistence.fact.demo"],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PYTHONPATH": "src",
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        },
    )
    assert result.returncode == 0, (
        f"demo exited {result.returncode}. stderr:\n{result.stderr}"
    )
    out = result.stdout
    # Factual
    assert "Now:" in out
    assert "0.091" in out
    # Historical
    assert "April 15:" in out
    assert "0.087" in out
    # Counterfactual
    assert "Branch:" in out
    assert "0.095" in out
