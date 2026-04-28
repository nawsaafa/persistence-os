"""A8 — v0.6.0a1 Plan execution end-to-end integration test.

Exercises the entire v0.6.0a1 public surface — parse → optimize →
promote → register → lookup — through ``persistence.plan``'s top-level
``__init__`` only. Closes Stream A's implementation tier; ARIS
(A-FINAL.1) and the CHANGELOG / tag (A-FINAL.2) come after.

The 8-step scenario this test pins (per
``docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md`` §2.A8):

1. **Parse** a hand-written EDN plan (a single ``:seq`` containing two
   ``:llm-call`` nodes with ``:signature`` attrs).
2. **Mock** dispatcher with a deterministic ``:llm-call`` handler so
   metric scoring is reproducible across runs.
3. **Mock** DSPy + MIPROv2 via ``sys.modules`` (same pattern as
   ``tests/plan/test_optimize_e2e.py``) so ``optimize()`` returns a
   tweaked plan with all six provenance keys pinned on every node.
4. **Run G1+G2+G3+G4-stub** via ``promote()`` — all four gates pass.
5. **Register** the optimized plan in ``SkillLibrary`` and capture the
   ``skill_id``.
6. **Lookup** by ``skill_id`` → Plan AST + PromotionRecord round-trip.
7. **Audit reconstruction**: ``db.as_of(t_after_register)`` produces a
   snapshot whose seeded SkillLibrary lists the registered skill.
8. **Re-register** the same plan → idempotent (same ``skill_id``, no
   extra datoms).

Mocking strategy notes (judgment calls):

* DSPy is mocked via ``monkeypatch.setitem(sys.modules, "dspy", ...)``
  (same shape as A4's ``mock_dspy`` fixture). The optimize() function
  lazy-imports inside its body so ``sys.modules`` injection is enough.
* ``ReplayEngine`` is satisfied with a tiny structural stub (the
  Protocol is ``@runtime_checkable``). A6's ``_ByteIdenticalReplayStub``
  is the precedent.
* The G2-pass DB is built via the production audit-handler stack
  (``make_audit_handler`` + ``audit_entry_to_datom``) — mirrors A6 +
  A7's ``_g2_pass_db()``. Inlined here rather than imported because
  A8's job is to verify the public-surface integration, not to chain
  into other test files' helpers.

No production code changes — A8 is a single new test file. The 8-step
happy-path lives in :func:`test_v0_6_e2e_parse_optimize_promote_register_lookup`;
two supporting tests (idempotent re-register; ``as_of`` snapshot
reconstruction) cover step 7 and step 8 in isolation so a regression
that breaks just one of them surfaces with a tight test name.
"""
from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

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
from persistence.plan import (
    Dispatcher,
    ExecutionResult,
    MetricRef,
    Node,
    PromotionRecord,
    ReplayEngine,
    SkillLibrary,
    optimize,
    parse,
    promote,
    register_metric,
    unregister_metric,
    walk,
)
from persistence.replay.trajectory import Fact, Trajectory


# --- DSPy + MIPROv2 mock fixture (mirrors test_optimize_e2e.py) ---------- #


@pytest.fixture
def mock_dspy(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """Install a MagicMock at ``sys.modules['dspy']`` and ``dspy.teleprompt``.

    ``optimize()`` lazy-imports ``dspy`` + ``MIPROv2`` inside the
    function body, so the mock must be in place BEFORE the call site.
    """
    mock = MagicMock(name="dspy")

    class _StubModule:
        """Minimal subclassable stand-in for ``dspy.Module``.

        ``MagicMock`` cannot be subclassed directly, but the forward
        adapter (``_plan_to_dspy_module``) defines a subclass of
        ``dspy.Module`` to wrap the dispatched plan. A plain Python
        class with a no-arg ``__init__`` is enough.
        """

        def __init__(self) -> None:
            return None

    mock.Module = _StubModule

    teleprompt_mock = MagicMock(name="dspy.teleprompt")
    mock.teleprompt = teleprompt_mock
    monkeypatch.setitem(sys.modules, "dspy", mock)
    monkeypatch.setitem(sys.modules, "dspy.teleprompt", teleprompt_mock)
    yield mock


@pytest.fixture
def mock_miprov2_compile(mock_dspy: MagicMock) -> MagicMock:
    """Wire ``MIPROv2(...).compile(...)`` to return a stub optimized module.

    The stub mimics what DSPy returns post-compile: a ``Module`` instance
    whose predictors carry tuned signature strings. The inverse adapter
    reads the signatures off the optimized module to rebuild the Plan
    AST with provenance pinned.
    """
    optimized_module = MagicMock(name="OptimizedModule")
    sub0 = MagicMock(name="Predict_tuned_0")
    sub0.signature = "q -> a [tuned]"
    sub1 = MagicMock(name="Predict_tuned_1")
    sub1.signature = "a -> summary [tuned]"
    optimized_module.llm_call_0 = sub0
    optimized_module.llm_call_1 = sub1
    optimized_module.predictors = MagicMock(return_value=[sub0, sub1])
    optimized_module.named_predictors = MagicMock(
        return_value=[("llm_call_0", sub0), ("llm_call_1", sub1)]
    )

    miprov2_class = MagicMock(name="MIPROv2_class")
    miprov2_instance = MagicMock(name="MIPROv2_instance")
    miprov2_instance.compile = MagicMock(return_value=optimized_module)
    miprov2_class.return_value = miprov2_instance

    mock_dspy.teleprompt.MIPROv2 = miprov2_class
    return miprov2_class


# --- Metric registry fixture --------------------------------------------- #


_METRIC_REF: MetricRef = MetricRef(id="test-v0-6-int-e2e", version="v1")


@pytest.fixture
def registered_metric() -> Iterator[MetricRef]:
    """Register a deterministic metric; clean up on teardown.

    Returns a non-zero score whenever the result has at least one
    leaf — proxy for "the plan ran"; same monotonicity property used in
    A4's e2e test.
    """

    def _metric(result: ExecutionResult, expected: dict) -> float:
        if result.status != "ok":
            return 0.0
        return 0.5 + 0.5 * (1 if result.leaf_results else 0)

    register_metric(_METRIC_REF, _metric, replace=True)
    try:
        yield _METRIC_REF
    finally:
        try:
            unregister_metric(_METRIC_REF)
        except Exception:
            # Idempotent teardown — never mask a real failure with a
            # teardown crash.
            pass


# --- Replay-engine stub (G1 pass) ---------------------------------------- #


@dataclass
class _ByteIdenticalReplayStub:
    """ReplayEngine that always replays byte-identical → G1 always passes."""

    def replay(self, plan: Node, trajectory: Trajectory) -> Trajectory:
        return trajectory

    def compare(self, a: Trajectory, b: Trajectory) -> dict:
        return {"divergence_step": None, "pnl_delta": 0.0}


def test_replay_engine_protocol_satisfied_by_stub() -> None:
    """Cross-task drift pin: A6 made ``ReplayEngine`` ``@runtime_checkable``.
    Mirror the A6 fix-pass precedent so this integration test surfaces a
    shape break in the Protocol before the e2e tests would (which would
    fail with a less-readable AttributeError deep inside ``promote()``).
    """
    assert isinstance(_ByteIdenticalReplayStub(), ReplayEngine)
    # And a plainly non-conforming object must NOT satisfy.
    assert not isinstance(object(), ReplayEngine)


# --- Trajectory + audit-chain helpers ------------------------------------ #


def _make_trajectory(seed: int) -> Trajectory:
    """One-step trajectory; seed varies the LLM rng for distinguishability."""
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


def _record_audit_chain(*, n_entries: int) -> list[AuditEntry]:
    """Run the audit handler over ``n_entries`` calls; return entries."""
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
    """Append entries to ``db.store`` as ``audit/...`` datoms.

    Direct ``db.store.append`` (rather than ``db.transact``) preserves
    the audit handler's recorded_at, which the G2 gate filters on.
    """
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


def _frozen_clock(start_ms: int):
    """Clock callable that advances 1 ms per call (matches A5 test fixture)."""
    counter = [start_ms]

    def _now() -> datetime:
        ts = datetime.fromtimestamp(counter[0] / 1000, tz=timezone.utc)
        counter[0] += 1
        return ts

    return _now


def _g2_pass_db(*, start_ms: int = 1_900_000_000_000) -> DB:
    """A DB whose audit chain spans a G2 window AND has a deterministic
    clock for the SkillLibrary's ``skill/...`` datoms.

    ``start_ms`` is set safely above the audit instants
    (``1_712_000_000`` seconds × 1000 = ``1.712e12`` ms) so that the
    SkillLibrary's later transact() emits tx_time strictly after every
    audit datom — keeping audit-window math monotonic.
    """
    db = DB(InMemoryStore(), clock=_frozen_clock(start_ms))
    entries = _record_audit_chain(n_entries=3)
    _seed_audit_datoms(db, entries)
    return db


_FULL_AUDIT_WINDOW: tuple[int, int] = (0, 9_999_999_999_999)


# --- The EDN under test -------------------------------------------------- #


_EDN_PLAN: str = (
    '[:seq {} '
    '[:llm-call {:signature "q -> a"}] '
    '[:llm-call {:signature "a -> summary"}]'
    ']'
)


# --- Step 1-8 happy path ------------------------------------------------- #


def test_v0_6_e2e_parse_optimize_promote_register_lookup(
    mock_dspy: MagicMock,
    mock_miprov2_compile: MagicMock,
    registered_metric: MetricRef,
) -> None:
    """End-to-end: parse → optimize → promote → register → lookup.

    Pins all 8 steps from impl plan §2.A8 in a single test so the
    integration works as a coherent whole. Per-step assertions document
    what the surface guarantees for downstream consumers.
    """
    # Step 1: parse a hand-written EDN plan.
    baseline_plan = parse(_EDN_PLAN)
    assert baseline_plan.tag == ":seq"
    assert len(baseline_plan.children) == 2
    assert all(c.tag == ":llm-call" for c in baseline_plan.children)
    baseline_id = baseline_plan.id

    # Step 2: dispatcher with a deterministic ``:llm-call`` handler.
    # The metric scores off ExecutionResult shape, not handler output;
    # the dispatcher only needs to be wired so optimize()'s baseline
    # scoring does not dispatch into an empty registry.
    dispatcher = Dispatcher()
    dispatcher.register(":llm-call", lambda node, env: "ok")

    # Step 3: optimize() — DSPy + MIPROv2 mocked above.
    optimized = optimize(
        baseline_plan,
        training_set=[
            {"inputs": {"q": "hello"}, "expected": "world"},
            {"inputs": {"q": "foo"}, "expected": "bar"},
        ],
        metric=registered_metric,
        dispatcher=dispatcher,
    )

    # Provenance round-trip — every node carries the six provenance keys.
    expected_keys = {
        "plan/optimizer",
        "plan/optimizer-call",
        "plan/baseline",
        "plan/training-set-hash",
        "plan/metric-id",
        "plan/metric-version",
    }
    visited: list[Node] = []
    walk(optimized.plan, visitor=lambda n, _: visited.append(n))
    assert len(visited) >= 3  # :seq + 2 :llm-call
    for node in visited:
        for key in expected_keys:
            assert key in node.attrs, (
                f"Node {node.tag!r} missing provenance key {key!r}"
            )
    assert optimized.baseline_plan_id == baseline_id

    # Step 4: promote() — G1+G2+G3+G4-stub all pass.
    db = _g2_pass_db()
    record = promote(
        optimized.plan,
        db=db,
        optimized_score=0.90,
        baseline_score=0.80,
        held_out_trajectories=_ten_trajectories(),
        replay_engine=_ByteIdenticalReplayStub(),
        audit_window=_FULL_AUDIT_WINDOW,
        promoted_at_ms=1_700_000_000_000,
    )
    assert isinstance(record, PromotionRecord)
    assert record.g1_replay_byte_identity is True
    assert record.g1_held_out_count == 10
    assert record.g2_audit_chain_verified is True
    assert record.g3_score_delta == pytest.approx(0.10)
    assert record.g4_approver == "stub"  # Stream G marker pin
    assert record.g4_approved is True
    assert record.candidate_plan_id == optimized.plan.id

    # Step 5: register in SkillLibrary; capture skill_id.
    library = SkillLibrary(db)
    log_before_register = list(db.log())
    skill_id = library.register(
        optimized.plan, record, registered_at_ms=1_700_000_000_000
    )
    log_after_register = list(db.log())
    assert skill_id == "skill/" + optimized.plan.id[:16]
    # register() emits exactly 3 datoms (skill/plan + skill/promotion-record
    # + skill/registered-at).
    assert len(log_after_register) - len(log_before_register) == 3

    # Step 6: lookup by skill_id → Plan AST + PromotionRecord round-trip.
    looked_up = library.lookup(skill_id)
    assert looked_up is not None
    plan_back, record_back = looked_up
    # Byte-identity: the in-memory cache returns the original references.
    assert plan_back is optimized.plan
    assert record_back is record
    # Defensive: the looked-up record's promotion_id matches what
    # promote() returned.
    assert record_back.promotion_id == record.promotion_id

    # Step 7: as_of() snapshot reconstruction.
    # The frozen clock advances 1 ms per transact; register() uses 1
    # transact (3 datoms share one tx_id), so ``current_clock`` is past
    # the registration. Pulling a snapshot at that instant must include
    # the skill in a fresh SkillLibrary's list_skills().
    snapshot_at = db._clock()
    snapshot = db.as_of(snapshot_at)
    snapshot_store = InMemoryStore()
    snapshot_store.append(list(snapshot.datoms))
    snapshot_db = DB(snapshot_store)
    snapshot_lib = SkillLibrary(snapshot_db)
    visible = snapshot_lib.list_skills()
    assert skill_id in visible

    # Step 8: re-register the same plan → idempotent.
    log_before_reregister = list(db.log())
    skill_id_again = library.register(
        optimized.plan, record, registered_at_ms=1_700_000_000_001
    )
    log_after_reregister = list(db.log())
    # Same skill_id; zero new datoms.
    assert skill_id_again == skill_id
    assert log_before_reregister == log_after_reregister


# --- Supporting test 1 — idempotent re-register (step 8 in isolation) -- #


def test_re_register_optimized_plan_is_idempotent(
    mock_dspy: MagicMock,
    mock_miprov2_compile: MagicMock,
    registered_metric: MetricRef,
) -> None:
    """A fresh SkillLibrary instance over the same DB does NOT double-write.

    Pins the cross-instance idempotency claim end-to-end through the
    full v0.6.0a1 surface — covers the slow-path log-scan branch in
    ``SkillLibrary.register`` for the integration scenario, not just
    the unit test in ``test_skill_library.py``.
    """
    baseline_plan = parse(_EDN_PLAN)
    dispatcher = Dispatcher()
    dispatcher.register(":llm-call", lambda node, env: "ok")
    optimized = optimize(
        baseline_plan,
        training_set=[{"inputs": {"q": "x"}, "expected": "y"}],
        metric=registered_metric,
        dispatcher=dispatcher,
    )
    db = _g2_pass_db()
    record = promote(
        optimized.plan,
        db=db,
        optimized_score=0.90,
        baseline_score=0.80,
        held_out_trajectories=_ten_trajectories(),
        replay_engine=_ByteIdenticalReplayStub(),
        audit_window=_FULL_AUDIT_WINDOW,
        promoted_at_ms=1_700_000_000_000,
    )

    lib_a = SkillLibrary(db)
    sid_a = lib_a.register(
        optimized.plan, record, registered_at_ms=1_700_000_000_000
    )
    log_after_a = list(db.log())

    # Fresh library over the same DB — empty cache, must hit the
    # fact-store probe.
    lib_b = SkillLibrary(db)
    sid_b = lib_b.register(
        optimized.plan, record, registered_at_ms=1_700_000_000_999
    )
    log_after_b = list(db.log())

    assert sid_a == sid_b
    # Zero new datoms emitted by lib_b.
    assert log_after_a == log_after_b


# --- Supporting test 2 — as_of() reconstruction (step 7 in isolation) -- #


def test_as_of_snapshot_reconstructs_only_pre_snapshot_skills(
    mock_dspy: MagicMock,
    mock_miprov2_compile: MagicMock,
    registered_metric: MetricRef,
) -> None:
    """A snapshot taken between two registrations sees only the first.

    Pins the bitemporal contract through the full Plan-execution
    surface: a SkillLibrary backed by ``db.as_of(t)`` returns only the
    skills whose ``skill/plan`` datom has ``tx_time <= t``. Without
    this guarantee, audit reconstruction (the design's "regulator
    replay" anchor) is meaningless.
    """
    # Two distinct plans → two distinct skill_ids (different content
    # hash).
    edn_plan_a = '[:seq {} [:llm-call {:signature "q -> a"}]]'
    edn_plan_b = '[:seq {} [:llm-call {:signature "q -> b"}]]'
    plan_a = parse(edn_plan_a)
    plan_b = parse(edn_plan_b)

    dispatcher = Dispatcher()
    dispatcher.register(":llm-call", lambda node, env: "ok")
    optimized_a = optimize(
        plan_a,
        training_set=[{"inputs": {"q": "x"}, "expected": "y"}],
        metric=registered_metric,
        dispatcher=dispatcher,
    )
    optimized_b = optimize(
        plan_b,
        training_set=[{"inputs": {"q": "p"}, "expected": "q"}],
        metric=registered_metric,
        dispatcher=dispatcher,
    )

    db = _g2_pass_db()
    record_a = promote(
        optimized_a.plan,
        db=db,
        optimized_score=0.90,
        baseline_score=0.80,
        held_out_trajectories=_ten_trajectories(),
        replay_engine=_ByteIdenticalReplayStub(),
        audit_window=_FULL_AUDIT_WINDOW,
        promoted_at_ms=1_700_000_000_000,
    )
    record_b = promote(
        optimized_b.plan,
        db=db,
        optimized_score=0.90,
        baseline_score=0.80,
        held_out_trajectories=_ten_trajectories(),
        replay_engine=_ByteIdenticalReplayStub(),
        audit_window=_FULL_AUDIT_WINDOW,
        promoted_at_ms=1_700_000_000_000,
    )

    library = SkillLibrary(db)
    sid_a = library.register(
        optimized_a.plan, record_a, registered_at_ms=1_700_000_000_000
    )
    # Snapshot AFTER plan A registration but BEFORE plan B registration.
    # The frozen clock advances 1 ms per transact; we add 500 µs to
    # land strictly between the two transacts.
    t_between = db._clock() + timedelta(microseconds=500)
    sid_b = library.register(
        optimized_b.plan, record_b, registered_at_ms=1_700_000_000_001
    )
    assert sid_a != sid_b  # different content → different skill_ids

    snapshot = db.as_of(t_between)
    snapshot_store = InMemoryStore()
    snapshot_store.append(list(snapshot.datoms))
    snapshot_db = DB(snapshot_store)
    snapshot_lib = SkillLibrary(snapshot_db)
    visible = snapshot_lib.list_skills()

    assert sid_a in visible
    assert sid_b not in visible
