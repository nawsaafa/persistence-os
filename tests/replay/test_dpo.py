"""DPO pair extraction — agent4-replay-spec §5."""
from __future__ import annotations

from persistence.replay.dpo import extract_dpo_pair
from persistence.replay.engine import record


def test_dpo_pair_shape_when_outcomes_diverge(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    cf = toy_replay(
        factual,
        [{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
    )
    # With outcomes that differ, pair must be emitted regardless of sign.
    pair = extract_dpo_pair(factual, cf, threshold=0.0)
    assert pair is not None
    assert set(pair.keys()) >= {"prompt", "chosen", "rejected", "margin"}
    # prompt is drawn from the llm_in at the branch point.
    assert pair["prompt"] == factual.facts[cf.branch_point].llm_in
    # margin magnitude equals |outcome delta|.
    assert pair["margin"] == abs(cf.outcome["pnl"] - factual.outcome["pnl"])


def test_dpo_picks_better_outcome_as_chosen(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    cf = toy_replay(
        factual,
        [{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
    )
    pair = extract_dpo_pair(factual, cf, threshold=0.0)
    assert pair is not None
    # Chosen must be the llm_out from whichever trajectory had higher pnl.
    winner = cf if cf.outcome["pnl"] > factual.outcome["pnl"] else factual
    loser = factual if winner is cf else cf
    assert pair["chosen"] == winner.facts[cf.branch_point].llm_out
    assert pair["rejected"] == loser.facts[cf.branch_point].llm_out


def test_dpo_returns_none_when_outcomes_within_threshold(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    # NO-OP replay: identical outcomes.
    noop_val = dict(factual.facts[1].state)
    cf = toy_replay(
        factual,
        [{"step": 1, "field": "state", "new_value": noop_val}],
    )
    pair = extract_dpo_pair(factual, cf, threshold=0.0)
    # Zero delta ⇒ no preference signal.
    assert pair is None


def test_dpo_returns_none_when_prefix_does_not_match(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    cf = toy_replay(
        factual,
        [{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
    )
    # Corrupt the prefix of cf to simulate a mismatched pair.
    cf.facts[0].action = {"type": "sell"}
    pair = extract_dpo_pair(factual, cf, threshold=0.0)
    assert pair is None
