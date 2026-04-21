"""Module 4 — counterfactual trajectory + replay engine.

Entry points:
    record(obs_stream, seeds, agent_step_fn) -> Trajectory
    replay(trajectory, interventions, mode=...) -> Trajectory
    compare(a, b) -> dict
    extract_dpo_pair(factual, counterfactual, threshold) -> Optional[dict]
    gen_regression_test(trajectory, assertion) -> str
"""

from persistence.replay.trajectory import Fact, Trajectory
from persistence.replay.effect_handler import (
    EffectHandler,
    NON_REPLAYABLE_OPS,
    PROMPT_HASH_OPS,
    PromptHashMismatch,
    RefusedInReplay,
    ReplayCacheMiss,
    make_replay_handler,
)
from persistence.replay.engine import record, replay, compare
from persistence.replay.dpo import extract_dpo_pair
from persistence.replay.regression import gen_regression_test

__all__ = [
    "Fact",
    "Trajectory",
    "EffectHandler",
    "NON_REPLAYABLE_OPS",
    "PROMPT_HASH_OPS",
    "PromptHashMismatch",
    "RefusedInReplay",
    "ReplayCacheMiss",
    "make_replay_handler",
    "record",
    "replay",
    "compare",
    "extract_dpo_pair",
    "gen_regression_test",
]
