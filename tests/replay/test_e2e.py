"""End-to-end integration:
  record → intervene → replay → compare → extract_dpo_pair → assert shape
and
  record → gen_regression_test → exec → assert pass

These are the full verification-gate flows from the workstream spec.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from persistence.replay.dpo import extract_dpo_pair
from persistence.replay.engine import compare, record
from persistence.replay.regression import gen_regression_test


def test_full_dpo_flow(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    # 1. record a factual trajectory.
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)

    # 2. intervene: wait at step 1.
    cf = toy_replay(
        factual,
        [{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
    )

    # 3. compare.
    diff = compare(factual, cf)
    assert diff["divergence_step"] == 1
    assert diff["pnl_delta"] == pytest.approx(cf.outcome["pnl"] - factual.outcome["pnl"])

    # 4. extract DPO pair.
    pair = extract_dpo_pair(factual, cf, threshold=0.0)
    assert pair is not None
    assert set(pair.keys()) >= {"prompt", "chosen", "rejected", "margin"}

    # Sanity: the chosen action corresponded to the higher-pnl branch.
    if cf.outcome["pnl"] > factual.outcome["pnl"]:
        assert pair["chosen"] == cf.facts[1].llm_out
    else:
        assert pair["chosen"] == factual.facts[1].llm_out


def test_full_regression_test_flow(
    tmp_path: Path,
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply
):
    # 1. record a golden trajectory.
    golden = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)

    # 2. generate a pytest file from it.
    assertion = (
        "lambda traj: traj.outcome['pnl'] == %r and traj.outcome['balance'] == %r"
        % (golden.outcome["pnl"], golden.outcome["balance"])
    )
    source = gen_regression_test(golden, assertion, test_name="test_golden_pnl")

    # 3. parses as valid Python.
    ast.parse(source)

    # 4. runs green when executed under pytest.
    test_file = tmp_path / "test_generated_e2e.py"
    test_file.write_text(source)

    src_dir = Path(__file__).resolve().parent.parent.parent / "src"
    env_path = f"{src_dir}"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-v"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": env_path},
    )
    assert result.returncode == 0, (
        f"generated regression test failed:\n{result.stdout}\n{result.stderr}"
    )
