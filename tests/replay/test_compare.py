"""compare(a, b) — divergence step, pnl delta, KL divergence, ITE placeholder."""
from __future__ import annotations

from persistence.replay.engine import compare, record


def test_compare_identical_trajectories_has_no_divergence(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply
):
    a = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    b = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    result = compare(a, b)
    assert result["divergence_step"] is None
    assert result["pnl_delta"] == 0.0


def test_compare_divergence_step_is_first_differing_action(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    cf = toy_replay(
        factual,
        [{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
    )
    result = compare(factual, cf)
    assert result["divergence_step"] == 1


def test_compare_pnl_delta_signs_correctly(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    cf = toy_replay(
        factual,
        [{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
    )
    result = compare(factual, cf)
    # b.outcome.pnl - a.outcome.pnl
    assert result["pnl_delta"] == cf.outcome["pnl"] - factual.outcome["pnl"]


def test_compare_has_kl_divergence_between_llm_logprobs(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    cf = toy_replay(
        factual,
        [{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
    )
    result = compare(factual, cf)
    # KL divergence must be present and numeric.
    assert "kl_divergence" in result
    assert isinstance(result["kl_divergence"], float)
    assert result["kl_divergence"] >= 0.0


def test_compare_has_ite_per_step_placeholder(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    cf = toy_replay(
        factual,
        [{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
    )
    result = compare(factual, cf)
    # Placeholder stub from agent4-replay-spec §4 — a list per step, currently None.
    assert "ite_per_step" in result
    assert isinstance(result["ite_per_step"], list)
    assert len(result["ite_per_step"]) == min(len(factual.facts), len(cf.facts))
