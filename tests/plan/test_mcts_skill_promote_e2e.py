"""B9 — load-bearing skill-library 4-gate end-to-end (design §12 ADR-9).

Composition under test:

    mcts_search → promote (G1+G2+G3+G4-stub) → SkillLibrary.register

Pinned via the public ``mcts_promote`` surface. After a successful run:

* ``MCTSPromotionResult.promotion`` is a :class:`PromotionRecord` with
  all four gates passed.
* ``MCTSPromotionResult.skill_id`` is in the library (cross-instance
  visible).
* MCTS provenance datoms (``mcts/search`` summary + ``mcts/iteration``
  rows) are in the db.
* G2 audit-chain datoms (``audit/...`` from the seeded chain) are in
  the db, distinct from the MCTS namespace.

The test mirrors A8's ``test_v0_6_e2e_parse_optimize_promote_register_lookup``
pattern, swapping ``optimize()`` for ``mcts_search()``.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from persistence.effect.handlers.audit import (
    AuditEntry,
    audit_entry_to_datom,
    make_audit_handler,
)
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.handlers.raw import make_echo_llm_handler
from persistence.effect.runtime import Runtime, perform, with_runtime
from persistence.fact.datom import Datom
from persistence.fact.db import DB
from persistence.fact.store import InMemoryStore
from persistence.plan import (
    Action,
    MCTSConfig,
    MCTSPromotionResult,
    Node,
    PromotionRecord,
    SkillLibrary,
    SubstituteLeafAction,
    mcts_promote,
)
from persistence.plan._mcts import _StaticEvaluator, _StaticExpander
from persistence.replay.trajectory import Fact, Trajectory


# --- Audit-chain helpers (mirror A6 / A8 fixtures) ---------------------- #


def _frozen_clock(start_ms: int = 1_900_000_000_000):
    counter = [start_ms]

    def _now() -> datetime:
        ts = datetime.fromtimestamp(counter[0] / 1000, tz=timezone.utc)
        counter[0] += 1
        return ts

    return _now


def _record_audit_chain(*, n_entries: int) -> list[AuditEntry]:
    entries: list[AuditEntry] = []
    audit = make_audit_handler(entries, policy_id="bankability-v3")
    raw = make_echo_llm_handler()
    clock = make_fixed_clock_handler(ts=1_712_000_000.0)
    rt = Runtime([raw, clock, audit])
    with with_runtime(rt):
        for i in range(n_entries):
            perform(
                ":llm/call",
                model="m",
                messages=[{"role": "user", "content": f"msg-{i}"}],
            )
    return entries


def _seed_audit_datoms(db: DB, entries: list[AuditEntry]) -> None:
    for i, e in enumerate(entries, start=1):
        wire = audit_entry_to_datom(e)
        datom = Datom(
            e=wire[":datom/e"],
            a=wire[":datom/a"].lstrip(":"),
            v=wire[":datom/v"],
            tx=i,
            tx_time=wire[":datom/tx-time"],
            valid_from=wire[":datom/valid-from"],
            valid_to=wire[":datom/valid-to"],
            op="assert",
            provenance=wire[":datom/provenance"],
        )
        db.store.append([datom])


def _g2_pass_db() -> DB:
    """A DB whose seeded audit chain spans the standard G2 window AND has
    a deterministic clock for SkillLibrary's later transact()."""
    db = DB(InMemoryStore(), clock=_frozen_clock())
    entries = _record_audit_chain(n_entries=3)
    _seed_audit_datoms(db, entries)
    return db


_FULL_AUDIT_WINDOW: tuple[int, int] = (0, 9_999_999_999_999)


# --- ReplayEngine stub (G1 always passes) ------------------------------- #


@dataclass
class _ByteIdenticalReplayStub:
    def replay(self, plan: Node, trajectory: Trajectory) -> Trajectory:
        return trajectory

    def compare(self, a: Trajectory, b: Trajectory) -> dict:
        return {"divergence_step": None, "pnl_delta": 0.0}


def _make_trajectory(seed: int) -> Trajectory:
    fact = Fact(
        step=0,
        t=0.0,
        state={"step": 0},
        obs={"q": f"q-{seed}"},
        llm_in={"prompt": "p"},
        llm_out={"text": f"a-{seed}"},
        action={"kind": "answer", "value": f"a-{seed}"},
    )
    return Trajectory(
        agent="test",
        seeds={"llm": seed, "tool": 0, "env": 0},
        status="completed",
        facts=[fact],
        outcome={"pnl": 0.0, "balance": 0.0},
    )


def _ten_trajectories() -> list[Trajectory]:
    return [_make_trajectory(i) for i in range(10)]


# --- Plan AST + search fixtures ----------------------------------------- #


def _leaf(prompt: str = "x") -> Node:
    return Node(tag=":leaf/predict", attrs={"prompt": prompt})


def _initial_plan() -> Node:
    return Node(
        tag=":plan/seq",
        attrs={"v": 1},
        children=(_leaf("A"), _leaf("B")),
    )


# --- The load-bearing E2E test ----------------------------------------- #


def test_mcts_promote_runs_search_promote_register_end_to_end():
    """Full closed-loop integration: search wins → 4-gate passes →
    SkillLibrary contains the new skill, audit chain visible.
    """
    initial = _initial_plan()
    new_a = _leaf("A_sub")
    act = SubstituteLeafAction(target_path=(0,), new_leaf=new_a)
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act, 1.0)],
    }

    # Score the would-be winner higher than the initial so the search
    # gives us a meaningful child winner (also keeps the test
    # specification crisp).
    expander = _StaticExpander(proposals)
    evaluator = _StaticEvaluator(
        {initial.id: 0.5},
        on_unknown="zero",
    )

    db = _g2_pass_db()
    library = SkillLibrary(db)

    result = mcts_promote(
        initial,
        expander=expander,
        evaluator=evaluator,
        started_at_ms=1_900_000_000_001,
        db=db,
        skill_library=library,
        held_out_trajectories=_ten_trajectories(),
        replay_engine=_ByteIdenticalReplayStub(),
        audit_window=_FULL_AUDIT_WINDOW,
        optimized_score=0.90,
        baseline_score=0.80,
        promoted_at_ms=1_700_000_000_000,
        registered_at_ms=1_700_000_000_000,
        config=MCTSConfig(max_iter=4, max_unique_plans=8),
    )

    # Composition shape: bundles search + promotion + skill_id +
    # winner_plan reference.
    assert isinstance(result, MCTSPromotionResult)
    assert isinstance(result.promotion, PromotionRecord)

    # All four gates passed.
    record = result.promotion
    assert record.g1_replay_byte_identity is True
    assert record.g1_held_out_count == 10
    assert record.g2_audit_chain_verified is True
    assert record.g3_score_delta > 0
    assert record.g4_approver == "stub"
    assert record.g4_approved is True
    assert record.candidate_plan_id == result.winner_plan.id

    # SkillLibrary now contains the new skill.
    assert result.skill_id == "skill/" + result.winner_plan.id[:16]
    # In-process lookup returns the registered (plan, record) pair
    # byte-identically (same posture as A8's step 6).
    looked_up = library.lookup(result.skill_id)
    assert looked_up is not None
    plan_back, record_back = looked_up
    assert plan_back.id == result.winner_plan.id
    assert record_back is record
    # Cross-instance ``list_skills`` enumerates from the fact store
    # alone — verifies the registration datoms are persisted, not just
    # cached (A8 step 7 pin).
    fresh_lib = SkillLibrary(db)
    assert result.skill_id in fresh_lib.list_skills()

    # MCTS provenance datoms in the db.
    log = list(db.log())
    mcts_search_summary = [
        d for d in log if d.e == result.search.search_id
    ]
    assert mcts_search_summary, "no mcts/search summary datoms in db"
    mcts_iter_rows = [
        d for d in log if d.e.startswith("mcts-iter/")
    ]
    assert mcts_iter_rows, "no mcts-iter rows in db"

    # G2 audit-chain datoms ALSO present and distinct from MCTS
    # namespace (mcts/ vs audit/ — design §13 R2.M2 closure).
    audit_rows = [d for d in log if d.a.startswith("audit/")]
    assert audit_rows, "no audit-chain datoms (G2 window empty)"
    mcts_attrs = {d.a for d in log if d.e.startswith("mcts")}
    assert not any(a.startswith("audit/") for a in mcts_attrs), (
        "MCTS attrs leaked into audit/ namespace — G2 disjointness broken"
    )


def test_mcts_promote_re_register_idempotent():
    """A second ``mcts_promote`` over the same DB+skill_library returns
    the same ``skill_id`` and emits zero NEW skill datoms (idempotent)."""
    initial = _initial_plan()
    act = SubstituteLeafAction(target_path=(0,), new_leaf=_leaf("A_sub"))
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act, 1.0)],
    }
    expander = _StaticExpander(proposals)
    evaluator = _StaticEvaluator({initial.id: 0.5})

    db = _g2_pass_db()
    library = SkillLibrary(db)

    common_kwargs: dict = {
        "expander": expander,
        "evaluator": evaluator,
        "db": db,
        "skill_library": library,
        "held_out_trajectories": _ten_trajectories(),
        "replay_engine": _ByteIdenticalReplayStub(),
        "audit_window": _FULL_AUDIT_WINDOW,
        "optimized_score": 0.90,
        "baseline_score": 0.80,
        "config": MCTSConfig(max_iter=2, max_unique_plans=8),
    }
    first = mcts_promote(
        initial,
        started_at_ms=1_900_000_000_001,
        promoted_at_ms=1_700_000_000_000,
        registered_at_ms=1_700_000_000_000,
        **common_kwargs,
    )
    skill_datoms_after_first = [
        d for d in db.log()
        if d.e == first.skill_id and d.a in {
            "skill/plan", "skill/promotion-record", "skill/registered-at",
        }
    ]

    # Second run with a different started_at_ms / promoted_at_ms — the
    # winner Plan AST is byte-identical (same fixtures) → same skill_id
    # → register() is a no-op cross-instance.
    second = mcts_promote(
        initial,
        started_at_ms=1_900_000_000_002,
        promoted_at_ms=1_700_000_000_001,
        registered_at_ms=1_700_000_000_002,
        **common_kwargs,
    )
    skill_datoms_after_second = [
        d for d in db.log()
        if d.e == first.skill_id and d.a in {
            "skill/plan", "skill/promotion-record", "skill/registered-at",
        }
    ]
    assert second.skill_id == first.skill_id
    assert skill_datoms_after_first == skill_datoms_after_second
