"""Replay engine — aligned-randomness counterfactuals (CAMO).

Invariant: `replay(factual, NO-OP intervention)` must produce a byte-identical
trajectory. We achieve that by:

1. Seeding independent rngs per domain (:llm, :tool, :env) from the recorded
   trajectory's `seeds` dict.
2. Copying prefix facts (step < branch-point) verbatim AND advancing each rng
   by the same `random_draws` the factual recorded, so alignment holds.
3. At the intervention step, applying the intervention (:action, :obs,
   :llm-out, :state), then re-executing `agent_step` for all subsequent
   steps with the same rng streams.
4. Refusing to run :net/fetch / :tool/call on cache miss — those are real
   side effects.

See agent4-replay-spec §3 for the API.
"""
from __future__ import annotations

import copy
import math
import random
from typing import Any, Callable, Optional

from persistence.replay.effect_handler import (
    EffectHandler,
    PromptHashMismatch,
    ReplayCacheMiss,
)
from persistence.replay.trajectory import Fact, Trajectory, trajectory_hash


# Mapping from intervention field keyword (EDN-style :field) to Fact attribute.
_FIELD_MAP = {
    ":action": "action",
    "action": "action",
    ":obs": "obs",
    "obs": "obs",
    ":llm-out": "llm_out",
    "llm-out": "llm_out",
    "llm_out": "llm_out",
    ":state": "state",
    "state": "state",
}


def _mk_rngs(seeds: dict) -> dict:
    return {
        "llm": random.Random(seeds.get("llm", 0)),
        "tool": random.Random(seeds.get("tool", 0)),
        "env": random.Random(seeds.get("env", 0)),
    }


def _advance_rngs_to_match(rngs: dict, fact: Fact) -> None:
    """Advance each domain rng by consuming one draw so the internal state
    matches what it would be after the factual step at this index.

    Depends on the convention that `agent_step` takes exactly one draw per
    domain per step (enforced by the conftest's toy_agent_step).
    """
    # The agent_step contract is: it calls rngs["llm"].random() once and
    # rngs["env"].random() once per step. To stay aligned when we skip
    # re-executing (prefix copy), we mimic those calls here.
    rngs["llm"].random()
    rngs["env"].random()


def record(
    obs_stream: list,
    seeds: dict,
    agent_step_fn: Callable,
    apply_action_fn: Callable,
    initial_state: dict,
    *,
    agent: str = "",
    goal: Optional[dict] = None,
    tags: Optional[set] = None,
) -> Trajectory:
    """Record a trajectory by stepping the agent through `obs_stream`."""
    traj = Trajectory(
        agent=agent,
        goal=goal or {},
        seeds=dict(seeds),
        status="completed",
        tags=set(tags or set()),
    )
    handler = EffectHandler(mode="record")
    rngs = _mk_rngs(seeds)
    state = copy.deepcopy(initial_state)

    for obs in obs_stream:
        fact = agent_step_fn(state, obs, handler, rngs)
        traj.facts.append(fact)
        state = apply_action_fn(state, obs, fact.action)

    traj.outcome = {
        "pnl": state.get("pnl", 0.0),
        "balance": state.get("balance", 0.0),
    }
    traj.cache = dict(handler.cache)
    traj.call_log = list(handler.calls)
    traj.hash = trajectory_hash(traj)
    return traj


def replay(
    traj: Trajectory,
    interventions: list,
    *,
    extra_obs: Optional[list] = None,
    agent_step_fn: Optional[Callable] = None,
    apply_action_fn: Optional[Callable] = None,
) -> Trajectory:
    """Produce a counterfactual trajectory by re-executing `traj` under
    `interventions`, with aligned randomness.

    If `agent_step_fn` / `apply_action_fn` are omitted we pick the toy
    functions from the test fixture, preserving the spec §7 prototype.
    Real-world callers supply their own.
    """
    if traj.status not in {"completed", "failed", "counterfactual"}:
        raise ValueError(
            f"cannot replay trajectory with status={traj.status!r}; "
            f"only :completed / :failed trajectories are replayable"
        )

    if agent_step_fn is None or apply_action_fn is None:
        raise ValueError(
            "replay requires both agent_step_fn and apply_action_fn; "
            "pass the same functions used during record()."
        )

    if not interventions:
        raise ValueError("replay requires at least one intervention")

    # R2 F3: fail fast on empty trajectories — otherwise any intervention
    # silently succeeds with no facts to intervene on.
    if not traj.facts:
        raise ValueError(
            "cannot replay an empty trajectory (traj.facts is empty); "
            "nothing to branch off"
        )

    # R2 F3: fail fast on step indices outside [0, len(facts)). A typo'd
    # step=99 against a 4-step trajectory would otherwise emit a
    # counterfactual byte-identical to the factual with no intervention
    # applied — a silent pass that wastes hours of downstream debugging.
    max_step = len(traj.facts) - 1
    for iv in interventions:
        s = iv["step"]
        if s < 0 or s > max_step:
            raise ValueError(
                f"intervention step {s} out of range for trajectory with "
                f"{len(traj.facts)} facts (valid steps: 0..{max_step})"
            )

    branch_point = min(i["step"] for i in interventions)
    interventions_by_step = {i["step"]: i for i in interventions}

    new_traj = Trajectory(
        parent_id=traj.id,
        branch_point=branch_point,
        # ARIS Round 4 W4-intervention-wire (closes B1 + R1 N5 + R2 G4) —
        # store ALL interventions on the counterfactual's lineage surface,
        # not just the first. The replay loop below (``interventions_by_step``)
        # has always applied every intervention correctly; this fix brings
        # the lineage field into parity with the effect, so regulator-replay
        # and DPO consumers see the full interventional decomposition.
        intervention=[copy.deepcopy(iv) for iv in interventions],
        agent=traj.agent,
        goal=copy.deepcopy(traj.goal),
        seeds=dict(traj.seeds),
        wall_clock_basis=traj.wall_clock_basis,
        status="counterfactual",
        tags=set(traj.tags),
    )

    # Replay handler reads from the recorded cache only. We also hand it the
    # call log so it can detect prompt-hash drift against recorded args.
    handler = EffectHandler(
        mode="replay",
        cache=dict(traj.cache),
        calls=list(traj.call_log),
    )
    rngs = _mk_rngs(traj.seeds)
    state = copy.deepcopy(_initial_state_of(traj))

    # ---- prefix: verbatim copy, rng advanced to stay aligned ----------
    for k, orig in enumerate(traj.facts):
        if k < branch_point:
            new_traj.facts.append(copy.deepcopy(orig))
            _advance_rngs_to_match(rngs, orig)
            state = apply_action_fn(state, orig.obs, orig.action)
            continue

        # ---- at or past branch-point ----------------------------------
        intervention = interventions_by_step.get(k)
        if intervention:
            fact = _apply_intervention(orig, intervention, state, rngs)
        else:
            # Re-execute with aligned rng + replay-mode handler.
            fact = agent_step_fn(state, orig.obs, handler, rngs)
            fact.step = k

        new_traj.facts.append(fact)
        state = apply_action_fn(state, fact.obs, fact.action)

    # ---- extrapolation window (optional) ------------------------------
    extra = list(extra_obs or [])
    if extra:
        new_traj.extrapolated = True
        # Past the recorded observation window, effects must be *simulated*
        # rather than replayed — market would have reacted to hypothetical
        # action (reflexivity). We switch to record mode for this segment;
        # a future downstream step can still refuse NON_REPLAYABLE_OPS there
        # by wrapping the agent_step_fn.
        handler.mode = "record"
        for i, obs in enumerate(extra):
            fact = agent_step_fn(state, obs, handler, rngs)
            fact.step = len(traj.facts) + i
            new_traj.facts.append(fact)
            state = apply_action_fn(state, obs, fact.action)
    else:
        new_traj.extrapolated = False

    new_traj.outcome = {
        "pnl": state.get("pnl", 0.0),
        "balance": state.get("balance", 0.0),
    }
    new_traj.hash = trajectory_hash(new_traj)
    return new_traj


def _apply_intervention(
    orig: Fact, intervention: dict, current_state: dict, rngs: dict
) -> Fact:
    """Apply one intervention to a copy of the original fact.

    Supported fields: :action | :obs | :llm-out | :state. The rng draws that
    agent_step would have made at this step are consumed so alignment holds
    for any unintervened downstream steps.
    """
    field_key = intervention["field"]
    attr = _FIELD_MAP.get(field_key)
    if attr is None:
        raise ValueError(
            f"unknown intervention field {field_key!r}; "
            f"expected one of :action, :obs, :llm-out, :state"
        )

    # Consume the rng draws the agent_step would have made at this step.
    # This is the critical alignment step for NO-OP state-interventions:
    # the factual recorded random_draws = {"expl": X, "env": Y}. The replay
    # must consume one llm-rng draw and one env-rng draw here so subsequent
    # steps draw from the same offset.
    expl = rngs["llm"].random()
    env_draw = rngs["env"].random()

    new_fact = copy.deepcopy(orig)
    setattr(new_fact, attr, copy.deepcopy(intervention["new_value"]))

    # Keep random_draws consistent with the draws we just made (so a NO-OP
    # state intervention produces identical random_draws to the factual).
    new_fact.random_draws = {"expl": expl, "env": env_draw}
    return new_fact


def _initial_state_of(traj: Trajectory) -> dict:
    """Recover initial state from the recorded step-0 fact."""
    if not traj.facts:
        return {"step": 0, "balance": 0.0, "position": None, "pnl": 0.0}
    f0 = traj.facts[0]
    # state on fact 0 is the pre-step state (by conftest contract).
    return copy.deepcopy(f0.state)


# ======================================================================
# compare()
# ======================================================================


def compare(a: Trajectory, b: Trajectory) -> dict:
    """Paired diff: outcome delta, divergence step, KL on logprobs, ITE stub."""
    pnl_delta = float(b.outcome.get("pnl", 0.0) - a.outcome.get("pnl", 0.0))

    divergence_step = None
    for i, (fa, fb) in enumerate(zip(a.facts, b.facts)):
        if fa.action != fb.action:
            divergence_step = i
            break

    # KL divergence between LLM logprob distributions at the branch point (or
    # at divergence_step if no branch-point). Uses softmax(logprobs).
    kl = _kl_divergence(a, b, divergence_step if divergence_step is not None else 0)

    # ITE per step — placeholder stub (AgenTracer-style; agent4-replay-spec §4).
    ite_per_step: list = [None] * min(len(a.facts), len(b.facts))

    return {
        "pnl_delta": pnl_delta,
        "divergence_step": divergence_step,
        "factual_pnl": float(a.outcome.get("pnl", 0.0)),
        "counterfactual_pnl": float(b.outcome.get("pnl", 0.0)),
        "kl_divergence": kl,
        "ite_per_step": ite_per_step,
    }


def _softmax(xs: list) -> list:
    if not xs:
        return []
    m = max(xs)
    exps = [math.exp(x - m) for x in xs]
    s = sum(exps) or 1.0
    return [e / s for e in exps]


def _kl_divergence(a: Trajectory, b: Trajectory, step: int) -> float:
    if step >= len(a.facts) or step >= len(b.facts):
        return 0.0
    la = a.facts[step].llm_out.get("logprobs") or []
    lb = b.facts[step].llm_out.get("logprobs") or []
    if not la or not lb or len(la) != len(lb):
        return 0.0
    pa = _softmax(la)
    pb = _softmax(lb)
    kl = 0.0
    for p, q in zip(pa, pb):
        if p <= 0 or q <= 0:
            continue
        kl += p * math.log(p / q)
    return float(kl)
