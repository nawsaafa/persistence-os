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
import warnings
from dataclasses import dataclass, field

import pytest

from persistence.effect.handlers.audit import (
    AuditEntry,
    audit_entry_to_datom,
    datom_to_audit_entry,
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
    ReplayEngine,
    _datom_to_wire_for_audit,
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
    seen: list[int] = field(default_factory=list)

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


def test_g1_empty_trajectory_list_returns_false_with_warning() -> None:
    """Empty list → False **and** emits a distinguishable ``UserWarning``.

    Per impl plan §2.A6 ("Empty trajectory list → False (and assert
    error message)"). The empty-list branch is distinct from the
    ``< min_count`` branch (which is silent) so a caller can tell
    "wiring is wrong, no corpus loaded" apart from "candidate failed
    coverage". Both still return False — gates return bool, A7's
    ``promote()`` raises ``GateFailure``.
    """
    plan = _candidate_plan()
    engine = _ByteIdenticalReplayStub()
    with pytest.warns(UserWarning, match="empty held_out_trajectories"):
        result = gate_g1_replay_byte_identity(
            plan,
            held_out_trajectories=[],
            replay_engine=engine,
            min_count=10,
        )
    assert result is False


def test_g1_below_min_count_does_not_warn() -> None:
    """Below-threshold (but non-empty) corpus is silent — only the
    empty-list branch emits the warning. Locks the distinguishability
    invariant from the test above."""
    plan = _candidate_plan()
    trajectories = [_make_trajectory(i) for i in range(3)]  # < 10
    engine = _ByteIdenticalReplayStub()
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        result = gate_g1_replay_byte_identity(
            plan,
            held_out_trajectories=trajectories,
            replay_engine=engine,
            min_count=10,
        )
    assert result is False
    # Specifically: no UserWarning matching the empty-list message.
    empty_list_warnings = [
        w for w in recorded
        if issubclass(w.category, UserWarning)
        and "empty held_out_trajectories" in str(w.message)
    ]
    assert empty_list_warnings == []


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
    # Short-circuit measurement: with the divergence at index 0, the
    # gate must NOT replay the remaining 9 trajectories. Converts the
    # "may stop early" docstring claim into a hard pin so a future
    # change to parallel/eager evaluation surfaces here.
    assert len(engine.seen) == 1


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


def test_g2_empty_db_returns_false_with_warning() -> None:
    """Zero audit datoms → False with UserWarning (R2 fix-pass W1.C).

    Vacuous truth is not accepted at a correctness gate: a DB with no
    audit datoms (or a miscomputed window that excludes everything)
    must surface as a wiring bug, not silently pass G2. Symmetric with
    G1's empty-corpus handling.
    """
    db = _make_db()  # no audit datoms at all
    with pytest.warns(UserWarning, match="empty audit window"):
        result = gate_g2_audit_chain(
            db,
            audit_window_start=0,
            audit_window_end=9_999_999_999_999,
        )
    assert result is False


def test_g2_window_excludes_all_audit_entries_returns_false_with_warning() -> None:
    """Window that excludes every audit entry → False with UserWarning.

    With every audit entry's ``recorded_at`` ≈ 1_712_000_000 (the fixed
    clock used by ``_record_audit_chain``), a window ending strictly
    before that instant excludes all entries and the gate must fail
    with a UserWarning rather than vacuously pass (R2 fix-pass W1.C).
    """
    entries = _record_audit_chain(n_entries=3)
    db = _make_db()
    _seed_audit_datoms(db, entries)
    # Window that ends well before the audit instants.
    with pytest.warns(UserWarning, match="empty audit window"):
        result = gate_g2_audit_chain(
            db,
            audit_window_start=0,
            audit_window_end=1_000_000_000,  # < 1_712_000_000
        )
    assert result is False


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


def test_replay_engine_protocol_is_runtime_checkable() -> None:
    """``ReplayEngine`` is decorated ``@runtime_checkable`` so callers
    can guard wiring with ``isinstance`` at boundaries (Stream F's
    adapter, Module 7 REPL's promote command). Both stubs above must
    satisfy the protocol structurally."""
    assert isinstance(_ByteIdenticalReplayStub(), ReplayEngine)
    assert isinstance(_DivergentOnIndexReplayStub(divergent_idx=0), ReplayEngine)
    # And a plainly non-conforming object must NOT satisfy.
    assert not isinstance("not a replay engine", ReplayEngine)
    assert not isinstance(object(), ReplayEngine)


def test_datom_to_wire_for_audit_round_trips_audit_entry() -> None:
    """Drift-pin: the splice in ``_datom_to_wire_for_audit`` mirrors
    Module 2's ``audit_entry_to_datom`` contract. If Module 2 changes
    where it stores the entry id (currently
    ``provenance[':signature']``), this round-trip breaks loudly here
    instead of producing silent G2 false-negatives in production.

    Round-trip:
        AuditEntry → audit_entry_to_datom → Datom → _datom_to_wire_for_audit
        → datom_to_audit_entry → AuditEntry'
    must preserve every field used by ``verify_chain``.
    """
    entries = _record_audit_chain(n_entries=1)
    db = _make_db()
    _seed_audit_datoms(db, entries)
    # There is exactly one audit datom in the store; pull it back.
    audit_datoms = [d for d in db.log() if d.a.startswith("audit/")]
    assert len(audit_datoms) == 1
    wire = _datom_to_wire_for_audit(audit_datoms[0])
    reconstructed = datom_to_audit_entry(wire)
    original = entries[0]

    # Every field that contributes to the content hash (i.e. anything
    # ``verify_chain`` depends on) must match.
    assert reconstructed.id == original.id
    assert reconstructed.prev_hash == original.prev_hash
    assert reconstructed.op == original.op
    assert reconstructed.args_hash == original.args_hash
    assert reconstructed.verdict == original.verdict
    assert reconstructed.latency_ms == original.latency_ms
    assert reconstructed.recorded_at == original.recorded_at
    assert reconstructed.policy_id == original.policy_id
    assert reconstructed.handler_chain == original.handler_chain
    assert reconstructed.principal == original.principal


# --- R2 fix-pass W1.A — G1 missing-key fail-closed ----------------------- #


def test_g1_compare_missing_divergence_step_key_raises_typeerror() -> None:
    """If ``compare()`` returns a dict without ``divergence_step``, the
    gate must raise ``TypeError`` rather than silently treat the missing
    key as byte-identity (R2 fix-pass W1.A).

    Without the fix, ``diff.get(_DIVERGENCE_KEY)`` returns ``None`` for
    a malformed compare result, and the gate would pass G1 vacuously —
    that's a fail-open hole if a Stream F adapter or test stub returns
    a wrong-shape dict. Strict-key access surfaces the wiring bug.
    """
    plan = _candidate_plan()
    trajectories = [_make_trajectory(i) for i in range(10)]

    @dataclass
    class _MalformedCompareStub:
        def replay(self, p: Node, t: Trajectory) -> Trajectory:
            return t

        def compare(self, a: Trajectory, b: Trajectory) -> dict:
            # No ``divergence_step`` key at all — a wiring bug.
            return {"pnl_delta": 0.0, "kl_divergence": 0.0}

    engine = _MalformedCompareStub()
    with pytest.raises(TypeError, match="divergence_step"):
        gate_g1_replay_byte_identity(
            plan,
            held_out_trajectories=trajectories,
            replay_engine=engine,
            min_count=10,
        )


# --- R2 fix-pass W1.F-1 — :signature required in audit provenance -------- #


def test_datom_to_wire_for_audit_missing_signature_raises_valueerror() -> None:
    """An audit ``Datom`` whose ``provenance`` lacks ``:signature`` must
    surface a typed ``ValueError`` rather than silently fall back to
    ``datom.tx`` (R2 fix-pass W1.F-1).

    The fallback would produce a wire dict whose ``:datom/tx`` is an
    int instead of a content hash, and the downstream
    ``datom_to_audit_entry`` reconstruction would either fail with an
    opaque error or yield an AuditEntry whose ``id`` doesn't match the
    canonical content hash — silently breaking G2 with no clear pointer
    to the wiring bug. The fix requires ``:signature`` and points at
    the wiring directly.
    """
    entries = _record_audit_chain(n_entries=1)
    db = _make_db()
    _seed_audit_datoms(db, entries)
    audit_datoms = [d for d in db.log() if d.a.startswith("audit/")]
    assert len(audit_datoms) == 1
    original = audit_datoms[0]
    # Strip the ``:signature`` from provenance — the inverse of what
    # ``audit_entry_to_datom`` writes. Cast back to Provenance since
    # Datom's ``provenance`` field is typed against the TypedDict; a
    # bare dict comprehension widens the type.
    from persistence.fact.datom import Provenance  # local import; test-only
    from typing import cast

    stripped_provenance = cast(
        Provenance,
        {k: v for k, v in original.provenance.items() if k != ":signature"},
    )
    tampered = Datom(
        e=original.e,
        a=original.a,
        v=original.v,
        tx=original.tx,
        tx_time=original.tx_time,
        valid_from=original.valid_from,
        valid_to=original.valid_to,
        op=original.op,
        provenance=stripped_provenance,
        invalidated_by=original.invalidated_by,
    )
    with pytest.raises(ValueError, match=":signature"):
        _datom_to_wire_for_audit(tampered)
