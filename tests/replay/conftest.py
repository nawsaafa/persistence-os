"""Shared fixtures for replay module tests.

Exposes the same toy trading scenario described in agent4-replay-spec §7,
used across deterministic and counterfactual tests.
"""
from __future__ import annotations

import copy
import hashlib

import pytest


# ---------------------------------------------------------------------------
# Toy trading agent (matches spec §7 prototype, but instrumented to route ALL
# non-determinism through a per-domain rng + EffectHandler so the replay engine
# can reproduce it bit-for-bit).
# ---------------------------------------------------------------------------


def toy_agent_step(state, obs, handler, rngs):
    """A deterministic-when-seeded agent step.

    Uses `rngs["llm"]` for the exploration draw, `rngs["env"]` for a small
    environmental jitter. The LLM effect itself is cached through `handler`
    so replay mode returns the recorded text instead of re-running the mock.
    """
    from persistence.replay.trajectory import Fact

    prompt = f"price={obs['price']} regime={obs['regime']}"
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]

    expl = rngs["llm"].random()
    env_draw = rngs["env"].random()

    def _call_llm():
        # Deterministic "mock LLM": sell if holding and profitable, otherwise
        # buy on trend + exploration > 0.3, otherwise wait. Price/regime only.
        if state.get("position") is not None and obs["price"] > state["position"]:
            text = "sell"
        elif obs["regime"] == "trend" and expl > 0.3:
            text = "buy"
        else:
            text = "wait"
        return {
            "text": text,
            "logprobs": [-0.2, -1.8],
            "model": "mock-opus-4.7",
        }

    llm_out = handler.call(
        op=":llm/call",
        args={"prompt_hash": prompt_hash, "model": "mock-opus-4.7"},
        fn=_call_llm,
    )

    action = {"type": llm_out["text"]}
    return Fact(
        step=state["step"],
        t=state.get("t", state["step"]),
        state=copy.deepcopy(state),
        obs=copy.deepcopy(obs),
        llm_in={"prompt_hash": prompt_hash, "model": "mock-opus-4.7"},
        llm_out=copy.deepcopy(llm_out),
        action=action,
        tool_calls=[],
        random_draws={"expl": expl, "env": env_draw},
    )


def toy_apply_action(state, obs, action):
    new = copy.deepcopy(state)
    new["step"] += 1
    new["t"] = new["step"]
    if action["type"] == "buy" and new["position"] is None:
        new["position"] = obs["price"]
        new["balance"] -= obs["price"]
    elif action["type"] == "sell" and new.get("position") is not None:
        new["balance"] += obs["price"]
        new["pnl"] = obs["price"] - new["position"]
        new["position"] = None
    # "wait" is a no-op on state (besides step advance).
    return new


@pytest.fixture
def toy_obs_stream():
    return [
        {"price": 100, "regime": "chop"},
        {"price": 95, "regime": "trend"},
        {"price": 110, "regime": "trend"},
        {"price": 108, "regime": "chop"},
    ]


@pytest.fixture
def toy_seeds():
    # seed 1 on :llm yields expl > 0.3 at step 1, so the factual agent buys
    # at step 1 (regime=trend) — this makes counterfactual wait-vs-buy
    # comparisons actually diverge. The demo (spec §7) independently uses 42.
    return {"llm": 1, "tool": 7, "env": 13}


@pytest.fixture
def toy_initial_state():
    return {"step": 0, "t": 0, "balance": 400.0, "position": None, "pnl": 0.0}


@pytest.fixture
def toy_agent():
    return toy_agent_step


@pytest.fixture
def toy_apply():
    return toy_apply_action


@pytest.fixture
def toy_replay():
    """Helper — a replay function pre-wired with the toy agent + apply."""
    from persistence.replay.engine import replay

    def _replay(factual, interventions, **kwargs):
        return replay(
            factual,
            interventions,
            agent_step_fn=kwargs.pop("agent_step_fn", toy_agent_step),
            apply_action_fn=kwargs.pop("apply_action_fn", toy_apply_action),
            **kwargs,
        )

    return _replay
