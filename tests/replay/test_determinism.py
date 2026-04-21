"""THE critical test: aligned-randomness determinism.

Replay with a NO-OP intervention must produce a byte-identical trajectory.
If this fails, we have a leak of non-determinism and the whole DPO /
regression-test story falls over.
"""
from __future__ import annotations

from persistence.replay.engine import record, replay
from persistence.replay.trajectory import trajectory_hash


def _noop_intervention(traj, step):
    """Replay an unchanged :state at the given step — a true no-op."""
    original = traj.facts[step]
    return [{"step": step, "field": "state", "new_value": dict(original.state)}]


def test_noop_intervention_produces_byte_identical_trajectory(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(
        obs_stream=toy_obs_stream,
        seeds=toy_seeds,
        agent_step_fn=toy_agent,
        apply_action_fn=toy_apply,
        initial_state=toy_initial_state,
    )

    cf = toy_replay(factual, _noop_intervention(factual, step=1))

    # Same number of facts.
    assert len(cf.facts) == len(factual.facts)
    # Per-step byte equality: every field on every Fact must match.
    for a, b in zip(factual.facts, cf.facts):
        assert a.step == b.step
        assert a.t == b.t
        assert a.state == b.state
        assert a.obs == b.obs
        assert a.llm_in == b.llm_in
        assert a.llm_out == b.llm_out
        assert a.action == b.action
        assert a.tool_calls == b.tool_calls
        assert a.random_draws == b.random_draws
    # Outcomes identical.
    assert cf.outcome["pnl"] == factual.outcome["pnl"]
    assert cf.outcome["balance"] == factual.outcome["balance"]
    # Content-addressed hash identical.
    assert trajectory_hash(cf) == trajectory_hash(factual)


def test_two_independent_records_with_same_seed_are_byte_identical(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply
):
    """Same seed, same obs stream ⇒ identical trajectory."""
    a = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    b = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    assert trajectory_hash(a) == trajectory_hash(b)


def test_different_seeds_produce_different_trajectories(
    toy_obs_stream, toy_initial_state, toy_agent, toy_apply
):
    a = record(
        toy_obs_stream, {"llm": 1, "tool": 0, "env": 0},
        toy_agent, toy_apply, toy_initial_state,
    )
    b = record(
        toy_obs_stream, {"llm": 2, "tool": 0, "env": 0},
        toy_agent, toy_apply, toy_initial_state,
    )
    # They might share some steps but not all — at minimum, random_draws["expl"]
    # differs because the seed differs.
    assert a.facts[0].random_draws["expl"] != b.facts[0].random_draws["expl"]


def test_seeds_are_per_domain_independent(
    toy_obs_stream, toy_initial_state, toy_agent, toy_apply
):
    """Changing the :env seed must NOT change llm-domain draws, and vice versa."""
    base = record(
        toy_obs_stream, {"llm": 100, "tool": 0, "env": 200},
        toy_agent, toy_apply, toy_initial_state,
    )
    only_env_changed = record(
        toy_obs_stream, {"llm": 100, "tool": 0, "env": 999},
        toy_agent, toy_apply, toy_initial_state,
    )
    # :llm draws are identical because :llm seed didn't change.
    for a, b in zip(base.facts, only_env_changed.facts):
        assert a.random_draws["expl"] == b.random_draws["expl"], (
            "llm-domain rng leaked into env-domain — seeds not independent"
        )
    # :env draws differ because :env seed changed.
    assert any(
        a.random_draws["env"] != b.random_draws["env"]
        for a, b in zip(base.facts, only_env_changed.facts)
    )
