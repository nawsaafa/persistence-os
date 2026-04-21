"""CLI demo — reproduces agent4-replay-spec §7 exactly.

Records a 4-step toy trading trajectory, replays with an intervention at
step 1 (action=wait), prints factual/counterfactual/comparison.
"""
from __future__ import annotations

import copy
import hashlib

from persistence.replay.effect_handler import EffectHandler
from persistence.replay.engine import compare, record, replay
from persistence.replay.trajectory import Fact


# ---- toy trading agent (matches spec §7 prototype) ------------------------


def demo_agent_step(state, obs, handler: EffectHandler, rngs) -> Fact:
    prompt = f"price={obs['price']} regime={obs['regime']}"
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]

    expl = rngs["llm"].random()
    env_draw = rngs["env"].random()

    def _call_llm():
        if obs["regime"] == "trend" and expl > 0.3:
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


def demo_apply_action(state, obs, action) -> dict:
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
    return new


def main() -> None:
    obs_stream = [
        {"price": 100, "regime": "chop"},
        {"price": 95, "regime": "trend"},
        {"price": 110, "regime": "trend"},
        {"price": 108, "regime": "chop"},
    ]
    initial_state = {"step": 0, "t": 0, "balance": 400.0, "position": None, "pnl": 0.0}
    seeds = {"llm": 42, "tool": 0, "env": 0}

    factual = record(
        obs_stream=obs_stream,
        seeds=seeds,
        agent_step_fn=demo_agent_step,
        apply_action_fn=demo_apply_action,
        initial_state=initial_state,
        agent="toy-trader",
    )
    print("Factual:", factual.outcome)

    cf = replay(
        factual,
        [{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
        agent_step_fn=demo_agent_step,
        apply_action_fn=demo_apply_action,
    )
    print("Counterfactual:", cf.outcome)
    print("Comparison:", compare(factual, cf))


if __name__ == "__main__":
    main()
