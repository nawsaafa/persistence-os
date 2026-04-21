"""Regression test generator — emit a pytest file from a golden trajectory.

The generated file, when executed, must replay the trajectory and assert the
supplied assertion holds — forming a self-contained regression harness.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from persistence.replay.engine import record
from persistence.replay.regression import gen_regression_test


def test_generated_file_parses_as_valid_python(
    tmp_path: Path,
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    assertion = 'lambda traj: traj.outcome["pnl"] == %r' % factual.outcome["pnl"]
    source = gen_regression_test(factual, assertion, test_name="test_golden")
    # Must parse as a Python module.
    ast.parse(source)
    # Must mention pytest and the trajectory's id.
    assert "def test_golden" in source
    assert factual.id in source


def test_generated_file_runs_green_against_golden_trajectory(
    tmp_path: Path,
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    assertion = 'lambda traj: traj.outcome["pnl"] == %r' % factual.outcome["pnl"]
    source = gen_regression_test(factual, assertion, test_name="test_golden_replay")

    # Write the generated file into a scratch dir under tmp_path and run pytest.
    test_file = tmp_path / "test_generated.py"
    test_file.write_text(source)

    # Point PYTHONPATH at our src/ so the generated file can import.
    src_dir = Path(__file__).resolve().parent.parent.parent / "src"
    env_path = f"{src_dir}:{Path(__file__).resolve().parent.parent}"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-v"],
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "PYTHONPATH": env_path,
        },
    )
    assert result.returncode == 0, (
        f"generated test failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_generated_file_fails_on_wrong_assertion(
    tmp_path: Path,
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    # Deliberately wrong assertion.
    bogus_value = factual.outcome["pnl"] + 99999.0
    assertion = "lambda traj: traj.outcome['pnl'] == %r" % bogus_value
    source = gen_regression_test(factual, assertion, test_name="test_should_fail")

    test_file = tmp_path / "test_generated_fail.py"
    test_file.write_text(source)

    src_dir = Path(__file__).resolve().parent.parent.parent / "src"
    env_path = f"{src_dir}:{Path(__file__).resolve().parent.parent}"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-v"],
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "PYTHONPATH": env_path,
        },
    )
    # Wrong assertion ⇒ pytest exits non-zero.
    assert result.returncode != 0
