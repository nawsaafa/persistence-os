"""A7 — G3 score-delta + G4 stub + ``promote()`` orchestration tests.

Covers all 7 cases pinned by
``docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md`` §2.A7 lines
557–566 plus the defensive invariants the parent task specifies:

1. All 4 gates pass → ``PromotionRecord`` returned with
   ``g4_approver == "stub"``.
2. G1 fail → ``GateFailure`` raised, message indicates G1, partial
   record reachable from the exception.
3. G2 fail → ``GateFailure`` raised, message indicates G2.
4. G3 fail (``optimized - baseline < epsilon``) → ``GateFailure``
   raised, message indicates G3.
5. Custom ``g4_fn`` returning ``{"approved": False}`` →
   ``GateFailure("G4 not approved")`` raised.
6. Determinism: same inputs → same ``promotion_id``.
7. Stub-marker assertion: ``record.g4_approver == "stub"`` is
   detectable (Stream G integration test scans for this before v1.0.0).

Defensive pins additional to the seven:

- ``PromotionRecord`` is frozen + slots — field assignment raises.
- ``promotion_id`` is 64-char lowercase hex (sha256 hexdigest shape).
- ``gate_g3_score_delta`` returns False at ``< epsilon`` and True at
  ``>= epsilon`` (both branches pinned).
- ``PromotionRecord`` satisfies ``_PromotionRecordLike`` (cross-task
  drift pin to A5's structural protocol).
- ``gate_g4_stub()`` returns the exact dict shape the impl plan pins.

Engine-stub conventions reuse the A6 test stubs verbatim (one
``ByteIdenticalReplayStub`` + one ``DivergentOnIndexReplayStub``);
audit-chain seed reuses A6's ``_record_audit_chain`` /
``_seed_audit_datoms`` pattern.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pytest

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
from persistence.plan._errors import GateFailure
from persistence.plan._promotion import (
    PromotionRecord,
    gate_g3_score_delta,
    gate_g4_stub,
    promote,
)
from persistence.plan._skill_library import _PromotionRecordLike
from persistence.replay.trajectory import Fact, Trajectory


# --- Fixtures (mirrors A6 test fixtures) ----------------------------------- #


def _make_db() -> DB:
    return DB(InMemoryStore())


def _candidate_plan() -> Node:
    return Node(tag=":llm-call", attrs={"signature": "q -> a"})


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


@dataclass
class _ByteIdenticalReplayStub:
    """ReplayEngine that always replays byte-identical (G1 pass)."""

    def replay(self, plan: Node, trajectory: Trajectory) -> Trajectory:
        return trajectory

    def compare(self, a: Trajectory, b: Trajectory) -> dict:
        return {"divergence_step": None, "pnl_delta": 0.0}


@dataclass
class _DivergentReplayStub:
    """ReplayEngine that always diverges (G1 fail)."""

    seen: list[int] = field(default_factory=list)

    def replay(self, plan: Node, trajectory: Trajectory) -> Trajectory:
        self.seen.append(trajectory.seeds.get("llm", 0))
        mutated = Trajectory.from_dict(trajectory.to_dict())
        mutated.facts[0].action = {"kind": "answer", "value": "DIVERGED"}
        return mutated

    def compare(self, a: Trajectory, b: Trajectory) -> dict:
        return {"divergence_step": 0, "pnl_delta": 0.0}


def _record_audit_chain(*, n_entries: int) -> list[AuditEntry]:
    """Run the audit handler over n_entries calls; return the entries.

    Mirrors the A6 fixture so G2-pass fixtures use the production shape.
    """
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
    """Append the entries to db.store as audit/... datoms (preserves
    the audit handler's recorded_at, unlike DB.transact)."""
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
    """A DB whose audit chain spans the standard G2 window (verifies)."""
    db = _make_db()
    entries = _record_audit_chain(n_entries=3)
    _seed_audit_datoms(db, entries)
    return db


def _g2_fail_db() -> DB:
    """A DB whose audit chain is tampered (G2 fails)."""
    db = _make_db()
    entries = _record_audit_chain(n_entries=3)
    # Tamper: keep the OLD id so canonical content drifts from id.
    middle = entries[1]
    entries[1] = AuditEntry(
        id=middle.id,
        prev_hash=middle.prev_hash,
        op=middle.op,
        args_hash="sha256:" + "0" * 64,
        verdict=middle.verdict,
        latency_ms=middle.latency_ms,
        recorded_at=middle.recorded_at,
        result_hash=middle.result_hash,
        error=middle.error,
        policy_id=middle.policy_id,
        handler_chain=middle.handler_chain,
        principal=middle.principal,
        run_id=middle.run_id,
        parent=middle.parent,
        txn_commit=middle.txn_commit,
    )
    _seed_audit_datoms(db, entries)
    return db


_FULL_AUDIT_WINDOW: tuple[int, int] = (0, 9_999_999_999_999)


def _ten_trajectories() -> list[Trajectory]:
    return [_make_trajectory(i) for i in range(10)]


# --- gate_g3_score_delta --------------------------------------------------- #


def test_g3_optimized_above_threshold_returns_true() -> None:
    """``optimized - baseline >= epsilon`` → True (the value claim).

    Use values whose binary-float subtraction lands well clear of the
    ``0.05`` threshold (``0.90 - 0.80`` ≈ ``0.0999…``) so the test
    doesn't accidentally pin IEEE-754 rounding to the gate contract.
    """
    assert gate_g3_score_delta(0.90, 0.80, epsilon=0.05) is True


def test_g3_optimized_strictly_below_threshold_returns_false() -> None:
    """``optimized - baseline < epsilon`` → False (no claim of value)."""
    assert gate_g3_score_delta(0.83, 0.80, epsilon=0.05) is False


def test_g3_optimized_exactly_at_threshold_returns_true() -> None:
    """Boundary: ``optimized - baseline == epsilon`` accepts.

    Pin ``>=`` not ``>`` so a contributor flipping the comparison
    surfaces here. Use exact integer-valued floats (``2.0 - 1.0``)
    against an exact epsilon (``1.0``) so IEEE-754 rounding doesn't
    confound the boundary.
    """
    assert gate_g3_score_delta(2.0, 1.0, epsilon=1.0) is True


def test_g3_default_epsilon_is_zero_point_zero_five() -> None:
    """Drift-pin: impl plan §2.A7 pins ``epsilon = 0.05`` default."""
    import inspect

    sig = inspect.signature(gate_g3_score_delta)
    assert sig.parameters["epsilon"].default == 0.05


def test_g3_negative_delta_returns_false() -> None:
    """Optimized worse than baseline → False (sanity)."""
    assert gate_g3_score_delta(0.70, 0.80, epsilon=0.05) is False


def test_g3_ieee754_boundary_is_exact_not_within_tolerance() -> None:
    """Pin the contract: G3 uses exact ``>=`` against ``epsilon``, NOT a
    within-tolerance compare. ``0.85 - 0.80`` evaluates to
    ``0.04999999999999993`` under IEEE-754, which is ``< 0.05`` exactly.

    A future contributor adding ``math.isclose`` or a tolerance-based
    compare would silently flip this case to True; the test locks the
    current semantic so the change surfaces here. Caller-level rounding
    (e.g. quantize to 4 decimals before passing) is the right place to
    handle near-boundary values, NOT inside the gate.
    """
    # Sanity: confirm the IEEE-754 expectation.
    assert (0.85 - 0.80) < 0.05
    # And the gate honors the exact comparison.
    assert gate_g3_score_delta(0.85, 0.80, epsilon=0.05) is False


# --- gate_g4_stub ---------------------------------------------------------- #


def test_g4_stub_returns_exact_dict_shape() -> None:
    """Pin the stub's contract verbatim — Stream D's REPL replacement
    must return the same keys."""
    out = gate_g4_stub()
    assert out == {
        "approved": True,
        "approver": "stub",
        "rationale": "Stream A stub — Stream D replaces",
    }


# --- PromotionRecord shape ------------------------------------------------- #


def test_promotion_record_is_frozen() -> None:
    """``frozen=True`` — field assignment must raise.

    Combined with ``slots=True`` this raises ``AttributeError`` (frozen)
    or ``FrozenInstanceError`` (a subclass of AttributeError) per the
    dataclasses module contract; either is fine.
    """
    record = PromotionRecord(
        candidate_plan_id="abcd1234",
        g1_replay_byte_identity=True,
        g1_held_out_count=10,
        g2_audit_chain_verified=True,
        g3_score_delta=0.07,
        g3_score_threshold=0.05,
        g4_approver="stub",
        g4_approved=True,
        g4_rationale="ok",
        promoted_at=1_700_000_000_000,
        promotion_id="0" * 64,
    )
    with pytest.raises(AttributeError):
        record.g4_approver = "tampered"  # type: ignore[misc]


def test_promotion_record_satisfies_promotion_record_like_protocol() -> None:
    """Drift pin to A5: A7's record must satisfy A5's structural Protocol.

    A5 stubs into ``_StubPromotionRecord(promotion_id: str)``;
    SkillLibrary's ``register()`` only reads ``promotion_id``. A7's
    full PromotionRecord MUST be a drop-in.
    """
    record = PromotionRecord(
        candidate_plan_id="abcd1234",
        g1_replay_byte_identity=True,
        g1_held_out_count=10,
        g2_audit_chain_verified=True,
        g3_score_delta=0.07,
        g3_score_threshold=0.05,
        g4_approver="stub",
        g4_approved=True,
        g4_rationale="ok",
        promoted_at=1_700_000_000_000,
        promotion_id="0" * 64,
    )
    # Structural check via runtime_checkable Protocol (A5 polish).
    assert isinstance(record, _PromotionRecordLike)


def test_promotion_id_is_64char_lowercase_hex() -> None:
    """``promotion_id`` is sha256 hexdigest — exactly 64 lowercase hex chars."""
    plan = _candidate_plan()
    record = promote(
        plan,
        db=_g2_pass_db(),
        optimized_score=0.90,
        baseline_score=0.80,
        held_out_trajectories=_ten_trajectories(),
        replay_engine=_ByteIdenticalReplayStub(),
        audit_window=_FULL_AUDIT_WINDOW,
        promoted_at_ms=1_700_000_000_000,
    )
    assert re.fullmatch(r"[0-9a-f]{64}", record.promotion_id)


# --- promote() — case 1: all gates pass ------------------------------------ #


def test_promote_all_gates_pass_returns_record_with_stub_approver() -> None:
    """Case 1 + Case 7: ``g4_approver == "stub"`` (Stream G marker)."""
    plan = _candidate_plan()
    record = promote(
        plan,
        db=_g2_pass_db(),
        optimized_score=0.90,
        baseline_score=0.80,
        held_out_trajectories=_ten_trajectories(),
        replay_engine=_ByteIdenticalReplayStub(),
        audit_window=_FULL_AUDIT_WINDOW,
        promoted_at_ms=1_700_000_000_000,
    )
    assert isinstance(record, PromotionRecord)
    # Stream G marker pin — DO NOT remove without bumping v1.0.0 gate.
    assert record.g4_approver == "stub"
    assert record.g4_approved is True
    # Every gate-result field reflects success.
    assert record.g1_replay_byte_identity is True
    assert record.g1_held_out_count == 10
    assert record.g2_audit_chain_verified is True
    assert record.g3_score_delta == pytest.approx(0.10)
    assert record.g3_score_threshold == 0.05
    assert record.candidate_plan_id == plan.id
    assert record.promoted_at == 1_700_000_000_000


# --- promote() — case 2: G1 fails ------------------------------------------ #


def test_promote_empty_held_out_trajectories_raises_g1_failure() -> None:
    """Empty held-out corpus → ``GateFailure`` from G1.

    G1 emits a ``UserWarning`` for the empty-list branch (per A6 fix-pass
    pin) and returns False; ``promote()`` then raises GateFailure
    indicating G1. Pin both signals together so the orchestrator cannot
    silently swallow either: the warning AND the GateFailure are part
    of the contract.
    """
    plan = _candidate_plan()
    with pytest.warns(UserWarning, match="empty held_out_trajectories"):
        with pytest.raises(GateFailure) as excinfo:
            promote(
                plan,
                db=_g2_pass_db(),
                optimized_score=0.90,
                baseline_score=0.80,
                held_out_trajectories=[],
                replay_engine=_ByteIdenticalReplayStub(),
                audit_window=_FULL_AUDIT_WINDOW,
                promoted_at_ms=1_700_000_000_000,
            )
    assert "G1" in str(excinfo.value)
    partial = getattr(excinfo.value, "partial_record", None)
    assert partial is not None
    # G1 failed under coverage gate; downstream gates not run.
    assert partial.g1_replay_byte_identity is False
    assert partial.g1_held_out_count == 0


def test_promote_g1_fail_raises_with_partial_record() -> None:
    """Case 2: G1 fail → ``GateFailure`` indicates G1; partial record
    has ``g1_replay_byte_identity is False``."""
    plan = _candidate_plan()
    with pytest.raises(GateFailure) as excinfo:
        promote(
            plan,
            db=_g2_pass_db(),
            optimized_score=0.90,
            baseline_score=0.80,
            held_out_trajectories=_ten_trajectories(),
            replay_engine=_DivergentReplayStub(),
            audit_window=_FULL_AUDIT_WINDOW,
            promoted_at_ms=1_700_000_000_000,
        )
    assert "G1" in str(excinfo.value)
    partial = getattr(excinfo.value, "partial_record", None)
    assert partial is not None
    assert isinstance(partial, PromotionRecord)
    assert partial.g1_replay_byte_identity is False


# --- promote() — case 3: G2 fails ------------------------------------------ #


def test_promote_g2_fail_raises_with_partial_record() -> None:
    """Case 3: G2 fail → ``GateFailure`` indicates G2.

    With G1 → G2 → G3 → G4 ordering, G1 must have passed first; the
    partial record reflects that. (If a contributor flips to G3 → G1 →
    G2 → G4 ordering, they should update both this and the next test.)
    """
    plan = _candidate_plan()
    with pytest.raises(GateFailure) as excinfo:
        promote(
            plan,
            db=_g2_fail_db(),
            optimized_score=0.90,
            baseline_score=0.80,
            held_out_trajectories=_ten_trajectories(),
            replay_engine=_ByteIdenticalReplayStub(),
            audit_window=_FULL_AUDIT_WINDOW,
            promoted_at_ms=1_700_000_000_000,
        )
    assert "G2" in str(excinfo.value)
    partial = getattr(excinfo.value, "partial_record", None)
    assert partial is not None
    assert partial.g1_replay_byte_identity is True  # G1 ran and passed
    assert partial.g2_audit_chain_verified is False


# --- promote() — case 4: G3 fails ------------------------------------------ #


def test_promote_g3_fail_raises_with_partial_record() -> None:
    """Case 4: G3 fail → ``GateFailure`` indicates G3."""
    plan = _candidate_plan()
    with pytest.raises(GateFailure) as excinfo:
        promote(
            plan,
            db=_g2_pass_db(),
            optimized_score=0.81,  # delta 0.01 < epsilon 0.05
            baseline_score=0.80,
            held_out_trajectories=_ten_trajectories(),
            replay_engine=_ByteIdenticalReplayStub(),
            audit_window=_FULL_AUDIT_WINDOW,
            promoted_at_ms=1_700_000_000_000,
        )
    assert "G3" in str(excinfo.value)
    partial = getattr(excinfo.value, "partial_record", None)
    assert partial is not None
    assert partial.g1_replay_byte_identity is True
    assert partial.g2_audit_chain_verified is True
    # G3 ran and recorded the actual delta even though it failed.
    assert partial.g3_score_delta == pytest.approx(0.01)


# --- promote() — case 5: G4 returns approved=False ------------------------- #


def test_promote_g4_not_approved_raises() -> None:
    """Case 5: custom ``g4_fn`` returning ``approved=False`` →
    ``GateFailure("G4 not approved")``."""
    plan = _candidate_plan()

    def g4_reject() -> dict:
        return {
            "approved": False,
            "approver": "human-operator",
            "rationale": "needs more eval",
        }

    with pytest.raises(GateFailure) as excinfo:
        promote(
            plan,
            db=_g2_pass_db(),
            optimized_score=0.90,
            baseline_score=0.80,
            held_out_trajectories=_ten_trajectories(),
            replay_engine=_ByteIdenticalReplayStub(),
            audit_window=_FULL_AUDIT_WINDOW,
            promoted_at_ms=1_700_000_000_000,
            g4_fn=g4_reject,
        )
    assert "G4" in str(excinfo.value)
    partial = getattr(excinfo.value, "partial_record", None)
    assert partial is not None
    assert partial.g4_approved is False
    # Custom approver name flowed through into the partial record.
    assert partial.g4_approver == "human-operator"


# --- R2 fix-pass W1.B — G4 isinstance(bool) check ------------------------ #


def test_promote_g4_approved_non_bool_raises_typeerror() -> None:
    """A custom ``g4_fn`` returning ``approved`` that's not a strict
    ``bool`` must raise ``TypeError`` (R2 fix-pass W1.B).

    Without the fix, ``bool(g4_result["approved"])`` would silently
    coerce truthy non-bools (e.g. the string ``"False"``, a non-empty
    dict) into ``True`` — promoting a candidate whose g4_fn surface
    doesn't even agree on the contract. The strict ``isinstance``
    check surfaces the wiring bug at the Stream D / operator-token
    boundary.
    """
    plan = _candidate_plan()

    def g4_string_false() -> dict:
        # The string "False" is truthy in Python; ``bool("False")``
        # returns True. This is exactly the silent-approval hole the
        # fix closes.
        return {
            "approved": "False",
            "approver": "buggy-stub",
            "rationale": "wrong type",
        }

    with pytest.raises(TypeError, match="bool"):
        promote(
            plan,
            db=_g2_pass_db(),
            optimized_score=0.90,
            baseline_score=0.80,
            held_out_trajectories=_ten_trajectories(),
            replay_engine=_ByteIdenticalReplayStub(),
            audit_window=_FULL_AUDIT_WINDOW,
            promoted_at_ms=1_700_000_000_000,
            g4_fn=g4_string_false,
        )


# --- promote() — case 6: determinism --------------------------------------- #


def test_promote_same_inputs_same_promotion_id() -> None:
    """Case 6: identical inputs → identical ``promotion_id``."""
    plan = _candidate_plan()
    common = dict(
        optimized_score=0.90,
        baseline_score=0.80,
        held_out_trajectories=_ten_trajectories(),
        replay_engine=_ByteIdenticalReplayStub(),
        audit_window=_FULL_AUDIT_WINDOW,
        promoted_at_ms=1_700_000_000_000,
    )
    record_a = promote(plan, db=_g2_pass_db(), **common)  # type: ignore[arg-type]
    record_b = promote(plan, db=_g2_pass_db(), **common)  # type: ignore[arg-type]
    assert record_a.promotion_id == record_b.promotion_id


def test_promote_different_promoted_at_ms_changes_promotion_id() -> None:
    """Drift pin: ``promoted_at_ms`` is part of the hash payload — two
    runs with different timestamps must produce different ids."""
    plan = _candidate_plan()
    common = dict(
        optimized_score=0.90,
        baseline_score=0.80,
        held_out_trajectories=_ten_trajectories(),
        replay_engine=_ByteIdenticalReplayStub(),
        audit_window=_FULL_AUDIT_WINDOW,
    )
    record_a = promote(plan, db=_g2_pass_db(), promoted_at_ms=1, **common)  # type: ignore[arg-type]
    record_b = promote(plan, db=_g2_pass_db(), promoted_at_ms=2, **common)  # type: ignore[arg-type]
    assert record_a.promotion_id != record_b.promotion_id


# --- promote() — exit gate: pure orchestration ---------------------------- #


def test_promote_does_not_mutate_db_log() -> None:
    """A7 exit gate: ``promote()`` is pure orchestration — no new datoms.

    A8's integration test is the ``SkillLibrary.register()`` site that
    writes datoms; the orchestrator itself only reads.
    """
    db = _g2_pass_db()
    log_before = list(db.log())
    promote(
        _candidate_plan(),
        db=db,
        optimized_score=0.90,
        baseline_score=0.80,
        held_out_trajectories=_ten_trajectories(),
        replay_engine=_ByteIdenticalReplayStub(),
        audit_window=_FULL_AUDIT_WINDOW,
        promoted_at_ms=1_700_000_000_000,
    )
    log_after = list(db.log())
    assert log_before == log_after
