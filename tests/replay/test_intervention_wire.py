"""ARIS Round 4 W4-intervention-wire — multi-step intervention audit lineage
+ keyword-keyed ``:trajectory/intervention`` wire boundary.

Round 3 surfaced two coupled defects on :class:`Trajectory.intervention`:

- **B1** — ``engine.replay`` assigned ``intervention=copy.deepcopy(interventions[0])``,
  collapsing a multi-step intervention list to the first entry on the audit
  lineage surface. Downstream consumers (DPO readers, regulator-replay) would
  see a partial intervention story.
- **R1 N5 / R3 N2** — the engine emitted bare-string intervention keys
  (``"step"``, ``"field"``, ``"new_value"``) while the spec
  ``:persistence.replay/intervention`` required keyword form. Every real
  counterfactual emitted by ``engine.replay`` failed ``Trajectory.to_edn``.

These tests assert the post-fix invariants:

1. ``Trajectory.intervention`` is a list of dicts (one entry per submitted
   intervention) — written by the engine from the full list, not just the
   first element.
2. ``Trajectory.to_edn()`` keywordifies each intervention at the wire
   boundary and the self-conform gate passes for real replay output.
3. ``Trajectory.from_edn(to_edn(t))`` round-trips to an equivalent
   ``Trajectory`` with the same intervention list (strings on the Python
   side, keywords on the wire side).
"""
from __future__ import annotations

import copy

import pytest

from persistence import spec as S
from persistence.replay.engine import record, replay
from persistence.replay.trajectory import Fact, Trajectory


# ---------------------------------------------------------------------------
# Shared tiny agent — intentionally minimal. The toy_* fixtures in conftest
# have a whole trading scenario; we only need enough agent shape to drive
# record → replay on a 4-step trajectory so cf.to_edn is under real traffic.
# ---------------------------------------------------------------------------


def _tiny_agent(state, obs, handler, rngs):
    """A deterministic-when-seeded agent step; routes all non-determinism
    through rngs and handler so the engine can replay it.
    """
    prompt_hash = f"p{state.get('step', 0):03d}"

    def _call_llm():
        return {"text": "hold", "model": "mock"}

    expl = rngs["llm"].random()
    env = rngs["env"].random()
    llm_out = handler.call(
        op=":llm/call",
        args={"prompt_hash": prompt_hash, "model": "mock"},
        fn=_call_llm,
    )
    return Fact(
        step=state.get("step", 0),
        t=state.get("step", 0),
        state=dict(state),
        obs=dict(obs),
        llm_in={"prompt_hash": prompt_hash, "model": "mock"},
        llm_out=dict(llm_out),
        action={"type": "hold"},
        random_draws={"expl": expl, "env": env},
    )


def _tiny_apply(state, obs, action):
    new = dict(state)
    new["step"] = new.get("step", 0) + 1
    return new


def _factual() -> Trajectory:
    obs_stream = [{"price": i, "regime": "trend"} for i in range(4)]
    seeds = {"llm": 1, "tool": 7, "env": 13}
    return record(obs_stream, seeds, _tiny_agent, _tiny_apply, {"step": 0})


# ---------------------------------------------------------------------------
# 1. Trajectory.intervention is a list, one entry per intervention
# ---------------------------------------------------------------------------


class TestTrajectoryInterventionIsList:
    """B1 closure — the engine must store every intervention on the
    counterfactual's lineage surface, not just the first.
    """

    def test_three_interventions_produce_length_three_list(self):
        factual = _factual()
        ivs = [
            {"step": 0, "field": "action", "new_value": {"type": "wait"}},
            {"step": 1, "field": "obs", "new_value": {"price": 999, "regime": "chop"}},
            {"step": 2, "field": "action", "new_value": {"type": "hold"}},
        ]
        cf = replay(
            factual,
            interventions=ivs,
            agent_step_fn=_tiny_agent,
            apply_action_fn=_tiny_apply,
        )
        assert isinstance(cf.intervention, list), (
            "Trajectory.intervention must be a list after W4-intervention-wire"
        )
        assert len(cf.intervention) == 3, (
            f"expected 3 intervention entries, got {len(cf.intervention)}"
        )
        # Python-side keys remain bare strings (the wire layer keywordifies).
        assert cf.intervention[0]["step"] == 0
        assert cf.intervention[1]["step"] == 1
        assert cf.intervention[2]["step"] == 2

    def test_single_intervention_still_produces_length_one_list(self):
        """The type change is uniform — a single intervention is a 1-list,
        not a bare dict.
        """
        factual = _factual()
        cf = replay(
            factual,
            interventions=[{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
            agent_step_fn=_tiny_agent,
            apply_action_fn=_tiny_apply,
        )
        assert isinstance(cf.intervention, list)
        assert len(cf.intervention) == 1
        assert cf.intervention[0]["field"] == "action"

    def test_engine_deepcopies_so_caller_mutation_is_safe(self):
        """Mutating the submitted interventions after replay must not mutate
        the trajectory's stored lineage.
        """
        factual = _factual()
        ivs = [{"step": 1, "field": "action", "new_value": {"type": "wait"}}]
        cf = replay(
            factual,
            interventions=ivs,
            agent_step_fn=_tiny_agent,
            apply_action_fn=_tiny_apply,
        )
        ivs[0]["new_value"]["type"] = "MUTATED"
        assert cf.intervention[0]["new_value"]["type"] == "wait"


# ---------------------------------------------------------------------------
# 2. Trajectory.to_edn conforms on real replay output (R1 N5 closure)
# ---------------------------------------------------------------------------


class TestTrajectoryToEdnOnReplayOutput:
    """R1 N5 closure — the self-conform gate on Trajectory.to_edn must pass
    for a counterfactual produced by the real engine, not just for synthetic
    pre-keywordified inputs.
    """

    def test_to_edn_conforms_on_single_intervention_replay(self):
        """This test WILL FAIL on main before W4-intervention-wire lands —
        the engine emits bare-string keys (``"step"``, ``"field"``,
        ``"new_value"``), the spec requires keyword form.
        """
        factual = _factual()
        cf = replay(
            factual,
            interventions=[{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
            agent_step_fn=_tiny_agent,
            apply_action_fn=_tiny_apply,
        )
        # to_edn self-conforms internally; if this does not raise, the
        # spec gate accepted the keywordified intervention wire form.
        edn = cf.to_edn()
        # Belt-and-braces: explicitly conform a second time at the caller.
        result = S.conform(":persistence.replay/trajectory", edn)
        assert result.is_ok, (
            f"Trajectory.to_edn on replay output failed spec conform: {result}"
        )

    def test_to_edn_conforms_on_multi_intervention_replay(self):
        """Three interventions — the wire layer must keywordify each
        individual intervention inside the list."""
        factual = _factual()
        ivs = [
            {"step": 0, "field": "action", "new_value": {"type": "wait"}},
            {"step": 2, "field": "obs", "new_value": {"price": 999, "regime": "chop"}},
            {"step": 3, "field": "action", "new_value": {"type": "hold"}},
        ]
        cf = replay(
            factual,
            interventions=ivs,
            agent_step_fn=_tiny_agent,
            apply_action_fn=_tiny_apply,
        )
        edn = cf.to_edn()
        assert S.conform(":persistence.replay/trajectory", edn).is_ok

    def test_to_edn_intervention_slot_is_seq_of_keyword_keyed_dicts(self):
        """Explicit shape check — the wire form of the intervention slot
        is a list of dicts whose keys all start with ':' (EDN keywords)
        and whose ``:field`` value is itself a keyword.
        """
        factual = _factual()
        cf = replay(
            factual,
            interventions=[
                {"step": 1, "field": "action", "new_value": {"type": "wait"}},
                {"step": 2, "field": "obs", "new_value": {"price": 7}},
            ],
            agent_step_fn=_tiny_agent,
            apply_action_fn=_tiny_apply,
        )
        edn = cf.to_edn()
        wire_ivs = edn[":trajectory/intervention"]
        assert isinstance(wire_ivs, list), (
            f":trajectory/intervention must be a list, got {type(wire_ivs).__name__}"
        )
        assert len(wire_ivs) == 2
        for wire_iv in wire_ivs:
            assert set(wire_iv.keys()) == {":step", ":field", ":new-value"}, (
                f"expected EDN-keyword keys, got {sorted(wire_iv.keys())}"
            )
            # :field is itself a keyword on the wire (":action" or ":obs").
            assert isinstance(wire_iv[":field"], str)
            assert wire_iv[":field"].startswith(":"), wire_iv[":field"]


# ---------------------------------------------------------------------------
# 3. Trajectory.from_edn de-keywordifies symmetrically
# ---------------------------------------------------------------------------


class TestTrajectoryFromEdnInverse:
    """``from_edn(to_edn(t)).intervention`` must equal the original Python-side
    form (bare-string keys, bare-string field values), so the wire boundary
    is a pure encoding layer.
    """

    def test_round_trip_preserves_multi_intervention_list(self):
        factual = _factual()
        ivs = [
            {"step": 0, "field": "action", "new_value": {"type": "wait"}},
            {"step": 2, "field": "obs", "new_value": {"price": 999}},
        ]
        cf = replay(
            factual,
            interventions=copy.deepcopy(ivs),
            agent_step_fn=_tiny_agent,
            apply_action_fn=_tiny_apply,
        )
        wire = cf.to_edn()
        restored = Trajectory.from_edn(wire)
        # Bare-string form is restored on the Python side.
        assert isinstance(restored.intervention, list)
        assert len(restored.intervention) == 2
        for got, expected in zip(restored.intervention, ivs):
            assert set(got.keys()) == {"step", "field", "new_value"}, (
                f"expected bare-string keys, got {sorted(got.keys())}"
            )
            assert got["step"] == expected["step"]
            assert got["field"] == expected["field"]
            assert got["new_value"] == expected["new_value"]


# ---------------------------------------------------------------------------
# 4. Synthetic constructor also accepts list-shape + keywordifies on to_edn
# ---------------------------------------------------------------------------


def test_trajectory_constructed_with_list_intervention_serialises():
    """A Trajectory built directly (not via the engine) with a list of
    bare-string-keyed interventions must serialise cleanly.
    """
    t = Trajectory(
        agent="test",
        seeds={"llm": 1, "tool": 2, "env": 3},
        intervention=[
            {"step": 0, "field": "action", "new_value": {"type": "wait"}},
        ],
        hash="sha256:ff",
    )
    edn = t.to_edn()
    assert S.conform(":persistence.replay/trajectory", edn).is_ok


def test_trajectory_with_no_intervention_serialises_without_slot():
    """A Trajectory with ``intervention=None`` (the default) must omit the
    :trajectory/intervention slot entirely — it's optional in the spec.
    """
    t = Trajectory(
        agent="test",
        seeds={"llm": 1, "tool": 2, "env": 3},
        hash="sha256:aa",
    )
    edn = t.to_edn()
    assert ":trajectory/intervention" not in edn
    assert S.conform(":persistence.replay/trajectory", edn).is_ok
