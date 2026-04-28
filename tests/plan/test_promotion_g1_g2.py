"""A6 — G1 replay byte-identity + G2 audit-chain promotion gates.

Internal helpers in ``persistence.plan._promotion`` (NOT yet exported via
``__init__.__all__`` — that's A7's job once the ``promote()`` orchestrator
lands). Tests pin the contract per
``docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md`` §2.A6 and the
design doc §7.

Engine wiring. The impl plan calls G1's wiring parameter ``replay_engine:
ReplayEngine``. ``persistence.replay`` exposes module-level functions
(``replay``, ``compare``) rather than a class, so A6 defines a structural
``ReplayEngine`` Protocol on the ``_promotion`` side — same decoupling
pattern A5 used for ``_PromotionRecordLike``. Tests inject a tiny stub
that drives the byte-identity decision deterministically; Stream F (the
real 50-trajectory regulator-replay corpus) will plug ``persistence.replay``
in directly.

G2 reuses :func:`persistence.effect.verify_chain` end-to-end. Tests build
``AuditEntry`` chains via the ``make_audit_handler`` factory (production
shape), then feed them into the DB by writing them as ``:audit/...``
datoms via :func:`persistence.effect.audit_entry_to_datom`.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

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
from persistence.plan import Node
from persistence.plan._promotion import (
    gate_g1_replay_byte_identity,
    gate_g2_audit_chain,
)
from persistence.replay.trajectory import Fact, Trajectory


# --- Fixtures -------------------------------------------------------------- #


def _make_db() -> DB:
    """Fresh in-memory DB; G2 doesn't need a deterministic clock — the
    audit datoms carry their own ``tx_time`` per the audit handler."""
    return DB(InMemoryStore())


def _candidate_plan() -> Node:
    """Trivial Plan AST. G1's contract is byte-identity of the replay
    output, not anything Plan-specific — any well-formed Node works."""
    return Node(tag=":llm-call", attrs={"signature": "q -> a"})


def _make_trajectory(seed: int) -> Trajectory:
    """One-step trajectory with deterministic content. The seed varies
    only the ``llm`` rng seed so two trajectories are distinguishable."""
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


# --- Test ReplayEngine stubs ---------------------------------------------- #


@dataclass
class _ByteIdenticalReplayStub:
    """ReplayEngine stub that always replays byte-identical.

    G1 calls ``engine.replay(plan, trajectory)`` then ``engine.compare(
    factual, counterfactual)``; this stub returns the same trajectory
    unchanged, so every comparison shows zero divergence.
    """

    def replay(self, plan: Node, trajectory: Trajectory) -> Trajectory:
        return trajectory

    def compare(self, a: Trajectory, b: Trajectory) -> dict:
        # divergence_step=None signals byte-identity in
        # persistence.replay.engine.compare's own convention.
        return {"divergence_step": None, "pnl_delta": 0.0}


@dataclass
class _DivergentOnIndexReplayStub:
    """ReplayEngine stub that diverges on the trajectory at ``divergent_idx``.

    Diverges by replacing the action on the first fact — one byte
    different is enough to fail G1.
    """

    divergent_idx: int
    seen: list[int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.seen = []

    def replay(self, plan: Node, trajectory: Trajectory) -> Trajectory:
        # Index in the held-out list is implicit; we use the trajectory's
        # llm seed as the discriminator (one-test-only convention).
        seed = trajectory.seeds.get("llm", 0)
        self.seen.append(seed)
        if seed == self.divergent_idx:
            # Mutate one byte: change action.
            mutated = Trajectory.from_dict(trajectory.to_dict())
            mutated.facts[0].action = {"kind": "answer", "value": "DIVERGED"}
            return mutated
        return trajectory

    def compare(self, a: Trajectory, b: Trajectory) -> dict:
        # Spot the divergence on the first fact's action.
        if a.facts and b.facts and a.facts[0].action != b.facts[0].action:
            return {"divergence_step": 0, "pnl_delta": 0.0}
        return {"divergence_step": None, "pnl_delta": 0.0}


# --- G1 tests -------------------------------------------------------------- #


def test_g1_all_trajectories_byte_identical_returns_true() -> None:
    """All ≥ ``min_count`` trajectories replay byte-identical → True."""
    plan = _candidate_plan()
    trajectories = [_make_trajectory(i) for i in range(10)]
    engine = _ByteIdenticalReplayStub()
    assert (
        gate_g1_replay_byte_identity(
            plan,
            held_out_trajectories=trajectories,
            replay_engine=engine,
            min_count=10,
        )
        is True
    )


def test_g1_one_trajectory_diverges_returns_false() -> None:
    """A single divergent byte across the corpus → False (one bad apple)."""
    plan = _candidate_plan()
    trajectories = [_make_trajectory(i) for i in range(10)]
    # The 5th trajectory (seed=5) diverges; the other nine match.
    engine = _DivergentOnIndexReplayStub(divergent_idx=5)
    assert (
        gate_g1_replay_byte_identity(
            plan,
            held_out_trajectories=trajectories,
            replay_engine=engine,
            min_count=10,
        )
        is False
    )


def test_g1_fewer_than_min_count_returns_false() -> None:
    """Fewer than ``min_count`` trajectories → False (insufficient coverage).

    Even when every trajectory in the short corpus is byte-identical, G1
    must fail — Prop 4 needs corpus coverage to claim replay-stability.
    """
    plan = _candidate_plan()
    trajectories = [_make_trajectory(i) for i in range(3)]
    engine = _ByteIdenticalReplayStub()
    assert (
        gate_g1_replay_byte_identity(
            plan,
            held_out_trajectories=trajectories,
            replay_engine=engine,
            min_count=10,
        )
        is False
    )


def test_g1_empty_trajectory_list_returns_false() -> None:
    """Empty list → False. The "<min_count" branch covers this; we
    explicitly pin the boundary so a future implementer can't accidentally
    short-circuit "no trajectories ⇒ vacuously true"."""
    plan = _candidate_plan()
    engine = _ByteIdenticalReplayStub()
    assert (
        gate_g1_replay_byte_identity(
            plan,
            held_out_trajectories=[],
            replay_engine=engine,
            min_count=10,
        )
        is False
    )


def test_g1_min_count_exactly_at_threshold_returns_true() -> None:
    """``len(trajectories) == min_count`` is the boundary — must accept."""
    plan = _candidate_plan()
    trajectories = [_make_trajectory(i) for i in range(10)]
    engine = _ByteIdenticalReplayStub()
    assert (
        gate_g1_replay_byte_identity(
            plan,
            held_out_trajectories=trajectories,
            replay_engine=engine,
            min_count=10,
        )
        is True
    )


def test_g1_short_circuits_on_first_divergence() -> None:
    """G1 may short-circuit but MUST iterate trajectories before deciding.

    The stub records every trajectory it replayed in ``seen``. If the
    first trajectory diverges, the gate may stop early; the test asserts
    we at least visited the divergent one. (A future contributor adding
    parallel replay would change this — pin the existing serial-iteration
    behaviour so the change is observable.)
    """
    plan = _candidate_plan()
    trajectories = [_make_trajectory(i) for i in range(10)]
    engine = _DivergentOnIndexReplayStub(divergent_idx=0)
    assert (
        gate_g1_replay_byte_identity(
            plan,
            held_out_trajectories=trajectories,
            replay_engine=engine,
            min_count=10,
        )
        is False
    )
    # The divergent trajectory must have been replayed (seen[0]).
    assert 0 in engine.seen


# --- G2 helpers ------------------------------------------------------------ #


def _record_audit_chain(*, n_entries: int) -> list[AuditEntry]:
    """Run the audit handler over ``n_entries`` ``:llm/call`` calls and
    return the recorded entries. This is the production shape — the
    factory's ``_canonicalise_content`` rules are exercised end-to-end.
    """
    entries: list[AuditEntry] = []
    audit = make_audit_handler(entries, policy_id="bankability-v3")
    raw = make_echo_llm_handler()
    # ``ts`` as float — int values would round-trip int → float through
    # ``datom_to_audit_entry`` (datetime.timestamp() returns float) and
    # the recomputed canonical hash would diverge from the stored id.
    # Production clocks (system, replay) emit floats; this fixture
    # matches that shape.
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
    """Write ``entries`` to ``db`` as ``audit/...`` datoms in place.

    Uses ``audit_entry_to_datom`` to produce the wire form, strips the
    leading ``:`` from ``:datom/a`` (``Datom.a`` is plain-string per
    ``datom.py:114``), and appends to the underlying store with each
    audit entry assigned a fresh tx id. A direct ``DB.transact`` would
    re-stamp ``tx_time`` from the DB's clock, which would lose the
    fidelity of the audit handler's own ``recorded_at``. The store-level
    append preserves the original audit instants — exactly what the gate
    filters on to reconstruct the chain in tx_time order.
    """
    for i, e in enumerate(entries, start=1):
        wire = audit_entry_to_datom(e)
        datom = Datom(
            e=wire[":datom/e"],
            a=wire[":datom/a"].lstrip(":"),  # Datom.a is bare (no leading colon)
            v=wire[":datom/v"],
            tx=i,
            tx_time=wire[":datom/tx-time"],
            valid_from=wire[":datom/valid-from"],
            valid_to=wire[":datom/valid-to"],
            op="assert",
            provenance=wire[":datom/provenance"],
        )
        db.store.append([datom])


# --- G2 tests -------------------------------------------------------------- #


def test_g2_clean_audit_chain_returns_true() -> None:
    """A clean Merkle chain over the window verifies → True."""
    entries = _record_audit_chain(n_entries=3)
    db = _make_db()
    _seed_audit_datoms(db, entries)
    # Window covers all entries: choose a tx_time range comfortably
    # spanning every entry's ``recorded_at``.
    start_ms = 0
    end_ms = 9_999_999_999_999
    assert (
        gate_g2_audit_chain(
            db,
            audit_window_start=start_ms,
            audit_window_end=end_ms,
        )
        is True
    )


def test_g2_tampered_entry_returns_false() -> None:
    """A tampered (id rewritten) entry breaks the chain → False."""
    entries = _record_audit_chain(n_entries=3)
    # Tamper with the middle entry's ``args_hash`` — recompute nothing,
    # so the stored ``id`` no longer matches the canonical content.
    tampered = AuditEntry(
        id=entries[1].id,  # keep the OLD id (drift from content)
        prev_hash=entries[1].prev_hash,
        op=entries[1].op,
        args_hash="sha256:" + "0" * 64,  # tampered content
        verdict=entries[1].verdict,
        latency_ms=entries[1].latency_ms,
        recorded_at=entries[1].recorded_at,
        result_hash=entries[1].result_hash,
        error=entries[1].error,
        policy_id=entries[1].policy_id,
        handler_chain=entries[1].handler_chain,
        principal=entries[1].principal,
        run_id=entries[1].run_id,
        parent=entries[1].parent,
        txn_commit=entries[1].txn_commit,
    )
    entries[1] = tampered

    db = _make_db()
    _seed_audit_datoms(db, entries)
    assert (
        gate_g2_audit_chain(
            db,
            audit_window_start=0,
            audit_window_end=9_999_999_999_999,
        )
        is False
    )


def test_g2_empty_window_returns_true_vacuously() -> None:
    """Zero entries in window → True (the empty chain is consistent)."""
    db = _make_db()  # no audit datoms at all
    assert (
        gate_g2_audit_chain(
            db,
            audit_window_start=0,
            audit_window_end=9_999_999_999_999,
        )
        is True
    )


def test_g2_window_excludes_out_of_range_entries() -> None:
    """Entries outside [start, end] are not considered.

    With every audit entry's ``recorded_at`` ≈ 1_712_000_000 (the fixed
    clock used by ``_record_audit_chain``), a window ending strictly
    before that instant must yield True (vacuously empty).
    """
    entries = _record_audit_chain(n_entries=3)
    db = _make_db()
    _seed_audit_datoms(db, entries)
    # Window that ends well before the audit instants.
    assert (
        gate_g2_audit_chain(
            db,
            audit_window_start=0,
            audit_window_end=1_000_000_000,  # < 1_712_000_000
        )
        is True
    )


def test_g2_ignores_non_audit_datoms() -> None:
    """Non-``:audit/...`` datoms in the window must not be treated as
    audit entries — the gate filters by attribute prefix."""
    entries = _record_audit_chain(n_entries=2)
    db = _make_db()
    _seed_audit_datoms(db, entries)
    # Add an unrelated datom in the same time window. It must be
    # ignored by the gate; the audit chain (just two entries) still
    # verifies cleanly.
    db = db.transact(
        [
            {
                "e": "skill/abc",
                "a": "skill/plan",
                "v": "0123",
                "valid_from": _dt.datetime(
                    1970, 1, 1, tzinfo=_dt.timezone.utc
                ),
            }
        ],
        provenance={"source": "test"},
    )
    assert (
        gate_g2_audit_chain(
            db,
            audit_window_start=0,
            audit_window_end=9_999_999_999_999,
        )
        is True
    )


# --- Defensive invariants ------------------------------------------------- #


def test_g1_passes_plan_and_trajectory_to_engine_replay() -> None:
    """The engine.replay call must receive (plan, trajectory) — pin
    the call shape so a future refactor doesn't silently drop the plan."""
    plan = _candidate_plan()
    trajectories = [_make_trajectory(i) for i in range(10)]

    received: list[tuple[Node, Trajectory]] = []

    @dataclass
    class _RecordingStub:
        def replay(self, p: Node, t: Trajectory) -> Trajectory:
            received.append((p, t))
            return t

        def compare(self, a: Trajectory, b: Trajectory) -> dict:
            return {"divergence_step": None, "pnl_delta": 0.0}

    engine = _RecordingStub()
    gate_g1_replay_byte_identity(
        plan,
        held_out_trajectories=trajectories,
        replay_engine=engine,
        min_count=10,
    )
    # Each trajectory was passed alongside the same plan reference.
    assert len(received) == len(trajectories)
    assert all(p is plan for p, _t in received)


def test_g1_min_count_default_is_ten() -> None:
    """The impl plan pins ``min_count`` default = 10. Cheap drift-pin
    so a contributor changing the default surfaces the test failure."""
    import inspect

    sig = inspect.signature(gate_g1_replay_byte_identity)
    assert sig.parameters["min_count"].default == 10
