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


# ---------- R2 F3 — multi-step / out-of-range / empty trajectory ----------


def test_multi_step_simultaneous_interventions_produce_consistent_hash(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    """R2 F3: two interventions at different steps must both land AND the
    replay must be deterministic (same hash across two identical invocations).

    toy_obs_stream has 4 steps; intervene on step 1 (action) and step 3 (obs).

    ARIS Round 3 P-rigor-polish G4: the assertions now pin the exact
    shape of ``cf.branch_point`` and ``cf.intervention`` — not just the
    effect on the facts list. Before, the test passed even if replay
    mis-populated the lineage fields.
    """
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    assert len(factual.facts) == 4

    interventions = [
        {"step": 1, "field": "action", "new_value": {"type": "wait"}},
        {"step": 3, "field": "obs", "new_value": {"price": 999, "regime": "chop"}},
    ]
    cf_a = toy_replay(factual, interventions)
    cf_b = toy_replay(factual, interventions)

    # Both interventions landed.
    assert cf_a.facts[1].action == {"type": "wait"}
    assert cf_a.facts[3].obs == {"price": 999, "regime": "chop"}
    # And the replay is deterministic under fixed seeds.
    from persistence.replay.trajectory import trajectory_hash

    assert trajectory_hash(cf_a) == trajectory_hash(cf_b)
    # Step 0 is before every intervention — byte-identical to factual.
    assert cf_a.facts[0].random_draws == factual.facts[0].random_draws
    assert cf_a.facts[0].action == factual.facts[0].action

    # G4: branch_point pins to min(step) across the intervention list.
    # The counterfactual's lineage reports the *earliest* intervention
    # as the branch point, since everything at or after that step is
    # suffix — prior steps are verbatim prefix.
    expected_branch = min(i["step"] for i in interventions)
    assert cf_a.branch_point == expected_branch, (
        f"cf.branch_point expected {expected_branch} (min of intervention "
        f"steps {[i['step'] for i in interventions]}), got {cf_a.branch_point}"
    )
    assert cf_b.branch_point == expected_branch

    # G4: intervention shape assertions. The current Phase-1 engine
    # stores only the first intervention on ``Trajectory.intervention``
    # (see ``src/persistence/replay/engine.py:164`` and the
    # ``Optional[dict]`` type on ``Trajectory.intervention``). We pin
    # that shape exactly so a regression (e.g. storing the wrong one,
    # or None when interventions were supplied) fails loudly, and we
    # pin the submitted input list length separately.
    assert len(interventions) == 2, "submitted list shape precondition"
    assert isinstance(cf_a.intervention, dict), (
        "Phase 1 stores only the first intervention; multi-intervention "
        "list storage on Trajectory is a Phase 2 upgrade tracked as "
        "a surfaced-bug item in ARIS Round 3 WORKER-SUMMARY"
    )
    first = interventions[0]
    assert cf_a.intervention["step"] == first["step"]
    assert cf_a.intervention["field"] == first["field"]
    assert cf_a.intervention["new_value"] == first["new_value"]
    assert cf_a.intervention == cf_b.intervention, (
        "intervention record diverged across two deterministic replays"
    )


def test_replay_with_step_greater_than_trajectory_length_raises(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    """R2 F3: a typo'd step number must fail fast, not silently drop.

    toy_obs_stream has 4 steps (indices 0..3). step=99 is out of range;
    replay must raise ValueError rather than silently emit a counterfactual
    identical to the factual.
    """
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    with pytest.raises(ValueError, match="out of range"):
        toy_replay(
            factual,
            [{"step": 99, "field": "action", "new_value": {"type": "wait"}}],
        )


def test_replay_with_negative_step_raises(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    """R2 F3 (complement): negative step is equally nonsensical and must error."""
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    with pytest.raises(ValueError, match="out of range"):
        toy_replay(
            factual,
            [{"step": -1, "field": "action", "new_value": {"type": "wait"}}],
        )


def test_empty_trajectory_replay_raises(
    toy_agent, toy_apply
):
    """R2 F3: replay of a trajectory with zero facts must raise, not silently
    produce a counterfactual with no facts.
    """
    from persistence.replay.trajectory import Trajectory

    t = Trajectory(status="completed", facts=[], seeds={"llm": 0, "tool": 0, "env": 0})
    with pytest.raises(ValueError, match="empty"):
        replay(
            t,
            [{"step": 0, "field": "action", "new_value": {"type": "wait"}}],
            agent_step_fn=toy_agent,
            apply_action_fn=toy_apply,
        )
