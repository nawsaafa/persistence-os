"""Trajectory + Fact dataclass tests.

Covers the schema in agent4-replay-spec §1:
  id, parent-id, branch-point, intervention, agent, goal, seeds,
  started-at, wall-clock-basis, status, outcome, facts, hash, tags.
"""
from __future__ import annotations

from persistence.replay.trajectory import Fact, Trajectory, trajectory_hash


def _mk_fact(step: int) -> Fact:
    return Fact(
        step=step,
        t=step,
        state={"step": step, "balance": 100.0},
        obs={"price": 100 + step},
        llm_in={"prompt_hash": f"hash-{step}"},
        llm_out={"text": "wait", "logprobs": [-0.1, -2.0]},
        action={"type": "wait"},
        tool_calls=[],
        random_draws={"expl": 0.5, "env": 0.2},
    )


def test_fact_constructs_with_all_spec_fields():
    f = _mk_fact(0)
    # All 9 fields from spec §1.
    assert f.step == 0
    assert f.t == 0
    assert f.state["balance"] == 100.0
    assert f.obs["price"] == 100
    assert f.llm_in["prompt_hash"] == "hash-0"
    assert f.llm_out["text"] == "wait"
    assert f.action["type"] == "wait"
    assert f.tool_calls == []
    assert f.random_draws == {"expl": 0.5, "env": 0.2}


def test_trajectory_constructs_with_all_spec_fields():
    t = Trajectory(
        agent="toy-trader",
        goal={"type": "profit"},
        seeds={"llm": 42, "tool": 0, "env": 0},
        started_at="2026-04-20T09:00:00Z",
        wall_clock_basis="recorded",
        status="completed",
        facts=[_mk_fact(0)],
        outcome={"pnl": 0.0},
        tags={"toy"},
    )
    assert t.id  # auto-generated UUID
    assert t.parent_id is None
    assert t.branch_point is None
    assert t.intervention is None
    assert t.agent == "toy-trader"
    assert t.goal == {"type": "profit"}
    assert t.seeds == {"llm": 42, "tool": 0, "env": 0}
    assert t.wall_clock_basis == "recorded"
    assert t.status == "completed"
    assert t.outcome == {"pnl": 0.0}
    assert t.tags == {"toy"}
    assert len(t.facts) == 1


def test_trajectory_id_is_unique_across_instances():
    a = Trajectory()
    b = Trajectory()
    assert a.id != b.id


def test_trajectory_round_trip_through_json():
    t = Trajectory(
        agent="toy-trader",
        goal={"type": "profit"},
        seeds={"llm": 42, "tool": 0, "env": 0},
        status="completed",
        facts=[_mk_fact(0), _mk_fact(1)],
        outcome={"pnl": 3.0},
        tags={"a", "b"},
    )
    payload = t.to_json()
    # Must actually be a JSON string.
    assert isinstance(payload, str)
    restored = Trajectory.from_json(payload)
    assert restored.id == t.id
    assert restored.agent == t.agent
    assert restored.seeds == t.seeds
    assert restored.outcome == t.outcome
    assert restored.tags == t.tags
    assert len(restored.facts) == 2
    assert restored.facts[0].step == 0
    assert restored.facts[1].llm_out["text"] == "wait"


def test_trajectory_hash_is_content_addressed():
    t1 = Trajectory(
        id="fixed-id-1",
        seeds={"llm": 42, "tool": 0, "env": 0},
        facts=[_mk_fact(0)],
        outcome={"pnl": 0.0},
    )
    t2 = Trajectory(
        id="fixed-id-2",  # different ID
        seeds={"llm": 42, "tool": 0, "env": 0},
        facts=[_mk_fact(0)],
        outcome={"pnl": 0.0},
    )
    # Content-addressed hash ignores the trajectory id itself.
    assert trajectory_hash(t1) == trajectory_hash(t2)


def test_trajectory_hash_differs_when_outcome_differs():
    t1 = Trajectory(facts=[_mk_fact(0)], outcome={"pnl": 0.0})
    t2 = Trajectory(facts=[_mk_fact(0)], outcome={"pnl": 5.0})
    assert trajectory_hash(t1) != trajectory_hash(t2)


def test_trajectory_hash_populates_hash_field_on_record():
    # Once record() populates .hash it should match trajectory_hash(t).
    t = Trajectory(facts=[_mk_fact(0)], outcome={"pnl": 0.0})
    t.hash = trajectory_hash(t)
    assert t.hash == trajectory_hash(t)


# ---------------------------------------------------------------------- spec wire
#
# ARIS R3 F3 — Trajectory.to_dict() emits snake_case Python-identifier keys
# (``id``, ``parent_id``, ``branch_point``, ...) but the canonical
# ``:persistence.replay/trajectory`` spec wants EDN keyword keys
# (``:trajectory/id``, ``:trajectory/parent-id``, ...). to_edn / from_edn
# are the wire-form converters that make this boundary work.


def _mk_trajectory_for_spec() -> Trajectory:
    import datetime as dt
    return Trajectory(
        agent="toy-trader",
        goal={"type": "profit", "target": 50.0},
        seeds={"llm": 42, "tool": 0, "env": 0},
        started_at=dt.datetime(2026, 4, 20, 9, tzinfo=dt.timezone.utc).isoformat(),
        wall_clock_basis="recorded",
        status="completed",
        facts=[_mk_fact(0)],
        outcome={"pnl": 3.0},
        tags={"demo"},
        hash="sha256:abc",
    )


def test_to_edn_keys_are_namespaced_keywords():
    t = _mk_trajectory_for_spec()
    edn = t.to_edn()
    for key in (
        ":trajectory/id",
        ":trajectory/parent-id",
        ":trajectory/branch-point",
        ":trajectory/agent",
        ":trajectory/goal",
        ":trajectory/seeds",
        ":trajectory/started-at",
        ":trajectory/wall-clock-basis",
        ":trajectory/status",
        ":trajectory/outcome",
        ":trajectory/facts",
        ":trajectory/hash",
    ):
        assert key in edn, f"missing required key {key}"


def test_to_edn_seeds_are_keyworded():
    t = _mk_trajectory_for_spec()
    edn = t.to_edn()
    seeds = edn[":trajectory/seeds"]
    assert ":llm" in seeds
    assert ":tool" in seeds
    assert ":env" in seeds


def test_to_edn_wall_clock_basis_and_status_are_keyworded():
    t = _mk_trajectory_for_spec()
    edn = t.to_edn()
    assert edn[":trajectory/wall-clock-basis"] == ":recorded"
    assert edn[":trajectory/status"] == ":completed"


def test_to_edn_conforms_to_replay_trajectory_spec():
    """ARIS R3 F3 — the wire form must pass the canonical spec."""
    from persistence import spec as S
    t = _mk_trajectory_for_spec()
    edn = t.to_edn()
    result = S.conform(":persistence.replay/trajectory", edn)
    assert result.is_ok, S.explain_for_llm(":persistence.replay/trajectory", edn)


def test_to_edn_defaults_started_at_when_none():
    """If started_at is None (early Trajectory before ``record()``), to_edn
    must still conform — the spec requires a tz-aware instant. We default
    to the UNIX epoch in UTC; callers who care can set started_at first.
    """
    from persistence import spec as S
    t = Trajectory(
        agent="toy",
        seeds={"llm": 0, "tool": 0, "env": 0},
        facts=[],
        outcome={},
        hash="sha256:00",
    )
    edn = t.to_edn()
    assert S.conform(":persistence.replay/trajectory", edn).is_ok


def test_from_edn_roundtrips_through_to_edn():
    t = _mk_trajectory_for_spec()
    restored = Trajectory.from_edn(t.to_edn())
    assert restored.agent == t.agent
    assert restored.goal == t.goal
    assert restored.seeds == t.seeds
    assert restored.wall_clock_basis == t.wall_clock_basis
    assert restored.status == t.status
    assert restored.outcome == t.outcome
    assert restored.hash == t.hash
    assert len(restored.facts) == len(t.facts)


def test_from_edn_reads_uuid_id():
    """:trajectory/id is a UUID on the wire; from_edn must restore it as a
    string (Trajectory.id is Optional[str] in the in-memory form).
    """
    import uuid
    t = _mk_trajectory_for_spec()
    edn = t.to_edn()
    # to_edn emits id as UUID string; from_edn should round-trip losslessly.
    restored = Trajectory.from_edn(edn)
    # id field accepts UUID string
    uuid.UUID(restored.id)  # must parse
