"""Replay engine — prefix alignment, intervention faithfulness, suffix re-exec."""
from __future__ import annotations

import pytest

from persistence.replay.engine import record, replay


def test_prefix_before_branch_point_is_equal_to_factual(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    cf = toy_replay(
        factual,
        [{"step": 2, "field": "action", "new_value": {"type": "wait"}}],
    )
    # Steps 0 and 1 are BEFORE the intervention — must be equal byte-for-byte.
    for k in range(2):
        a, b = factual.facts[k], cf.facts[k]
        assert a.state == b.state
        assert a.obs == b.obs
        assert a.llm_out == b.llm_out
        assert a.action == b.action
        assert a.random_draws == b.random_draws


def test_intervention_faithfulness_action_overridden_at_branch_point(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    cf = toy_replay(
        factual,
        [{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
    )
    assert cf.facts[1].action == {"type": "wait"}


def test_suffix_after_intervention_re_executes_with_new_state(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    # Force wait at step 1 — the factual buys at step 1 (regime=trend, expl>0.3).
    cf = toy_replay(
        factual,
        [{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
    )

    # Suffix facts (step >= 2) are re-executed, so their `state` reflects the
    # non-buy intervention (balance never decreased by $95).
    assert cf.facts[2].state["balance"] == pytest.approx(400.0)
    # And differs from factual where the buy at step 1 happened.
    assert factual.facts[2].state["balance"] != cf.facts[2].state["balance"]


def test_seed_alignment_rng_stream_matches_factual_for_noop(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    """NO-OP intervention preserves the rng stream across every step."""
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    step = 2
    noop = [{"step": step, "field": "state", "new_value": dict(factual.facts[step].state)}]
    cf = toy_replay(factual, noop)
    for a, b in zip(factual.facts, cf.facts):
        assert a.random_draws["expl"] == b.random_draws["expl"]
        assert a.random_draws["env"] == b.random_draws["env"]


def test_trajectory_parent_id_links_to_factual(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    cf = toy_replay(
        factual,
        [{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
    )
    assert cf.parent_id == factual.id
    assert cf.branch_point == 1
    assert cf.status == "counterfactual"


def test_replay_refuses_running_trajectory(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    factual.status = "running"
    with pytest.raises(ValueError, match="running"):
        toy_replay(factual, [{"step": 1, "field": "action", "new_value": {"type": "wait"}}])


def test_replay_accepts_completed_and_failed_status(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    # Both statuses are replayable.
    for status in ("completed", "failed"):
        factual.status = status
        cf = toy_replay(
            factual,
            [{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
        )
        assert cf.status == "counterfactual"


def test_replay_past_observation_window_marks_extrapolated(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    assert len(factual.facts) == 4

    # Extend replay with extra observations beyond the original window.
    extra_obs = [
        {"price": 105, "regime": "chop"},
        {"price": 99, "regime": "trend"},
    ]
    cf = toy_replay(
        factual,
        [{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
        extra_obs=extra_obs,
    )
    assert len(cf.facts) == 4 + len(extra_obs)
    assert cf.extrapolated is True
    # Unchanged replay within-window is NOT extrapolated.
    cf_within = toy_replay(
        factual,
        [{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
    )
    assert cf_within.extrapolated is False


def test_intervene_on_obs_replaces_observation_at_step(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    cf = toy_replay(
        factual,
        [{"step": 2, "field": "obs", "new_value": {"price": 200, "regime": "chop"}}],
    )
    assert cf.facts[2].obs == {"price": 200, "regime": "chop"}


def test_intervene_on_llm_out_replaces_llm_output(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    new_out = {"text": "sell", "logprobs": [-0.1, -5.0], "model": "mock-opus-4.7"}
    cf = toy_replay(
        factual,
        [{"step": 1, "field": "llm-out", "new_value": new_out}],
    )
    assert cf.facts[1].llm_out == new_out


def test_replay_requires_agent_step_fn():
    """Calling replay without agent_step_fn must raise a clear ValueError."""
    from persistence.replay.trajectory import Trajectory

    t = Trajectory(status="completed", facts=[])
    t.facts = []  # status check first; empty facts also defensible.
    with pytest.raises(ValueError, match="agent_step_fn"):
        replay(
            t,
            [{"step": 0, "field": "action", "new_value": {"type": "wait"}}],
        )
