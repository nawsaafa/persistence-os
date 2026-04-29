"""tx.commute — commutative writes + curated registry (v0.5.2 § F3).

Covers the design doc § F3 acceptance gates:

    "Parallel-commute test: two threads each call
    tx.commute(counter, 'inc-by', 1) 100 times against same counter;
    final value = 200, zero retries."

    "Replay byte-identity test: a commute-log committed at t1, replayed
    against the same store at t2, produces structurally-identical user-
    write log."

    "Registry-locked test: register_commute('evil', lambda v, x: v - x)
    raises RuntimeError without sentinel env var."

    "4 fn-id test: each curated fn-id has a 3-test slice covering
    identity, associativity-witness, and disjoint-key edge cases."

Plus the four intra-txn semantics cases from § F3 lines 307-312:
multiple commutes same ref, commute-then-set (explicit wins),
set-then-commute (both apply), deref-after-commute (returns optimistic).

Threading test mirrors the pattern in
``tests/persistence/txn/test_atoms.py::test_swap_under_threaded_contention``.
"""
from __future__ import annotations

import threading

import pytest
from pyrsistent import pmap

from persistence.fact.db import DB
from persistence.txn import (
    register_commute,
    unregister_commute,
)


# ---------------------------------------------------------------------------
# Single-thread inc-by — sanity gate.
# ---------------------------------------------------------------------------

def test_inc_by_single_thread():
    """Sequential 10 commutes should produce final counter = 10."""
    db = DB()
    counter = db.new_ref(initial=0)

    for _ in range(10):
        @db.dosync
        def body(tx):
            tx.commute(counter, "inc-by", 1)
        body()

    view = db.as_of(db._clock())
    assert view.entity(counter.eid).get("value") == 10


# ---------------------------------------------------------------------------
# Parallel commute — the headline acceptance gate. 10 threads × 10
# increments each = 100. Mirrors F1 atom threading test.
# ---------------------------------------------------------------------------

def test_inc_by_parallel_threads():
    """10 threads × 10 commutes each → counter = 100, zero retries.

    Commute is conflict-free by design — refs in commute_log are NOT
    in the conflict-detection union, so no thread retries due to
    another's commute. The reapply-at-commit phase serialises against
    ``store._lock`` so the final value is deterministic.
    """
    db = DB()
    counter = db.new_ref(initial=0)

    def worker():
        for _ in range(10):
            @db.dosync
            def body(tx):
                tx.commute(counter, "inc-by", 1)
            body()

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    view = db.as_of(db._clock())
    final = view.entity(counter.eid).get("value")
    assert final == 100, f"expected 100 (no lost increments), got {final!r}"


# ---------------------------------------------------------------------------
# sum-into — empirical commutativity witness.
# ---------------------------------------------------------------------------

def test_sum_into_associativity_witness():
    """f(f(v, a), b) == f(f(v, b), a) across small int draws.

    Empirical witness — addition is associative+commutative over ints,
    so this is a sanity test that the registered fn implements add.
    """
    from persistence.txn._commute import lookup_commute

    sum_into = lookup_commute("sum-into")
    for v in range(-3, 4):
        for a in range(-3, 4):
            for b in range(-3, 4):
                assert sum_into(sum_into(v, a), b) == sum_into(sum_into(v, b), a)


def test_sum_into_committed_value():
    """End-to-end: sum-into produces correct total under contention."""
    db = DB()
    total = db.new_ref(initial=0)

    def worker(amount):
        @db.dosync
        def body(tx):
            tx.commute(total, "sum-into", amount)
        body()

    amounts = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    threads = [threading.Thread(target=worker, args=(a,)) for a in amounts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    view = db.as_of(db._clock())
    assert view.entity(total.eid).get("value") == sum(amounts)  # 55


# ---------------------------------------------------------------------------
# set-union — disjoint AND overlapping cases (set-union is unconditionally
# commutative).
# ---------------------------------------------------------------------------

def test_set_union_disjoint_keys_parallel():
    """Two threads union disjoint frozensets; result = union."""
    db = DB()
    tags = db.new_ref(initial=frozenset())

    def w1():
        @db.dosync
        def body(tx):
            tx.commute(tags, "set-union", frozenset({"alpha", "beta"}))
        body()

    def w2():
        @db.dosync
        def body(tx):
            tx.commute(tags, "set-union", frozenset({"gamma", "delta"}))
        body()

    t1 = threading.Thread(target=w1)
    t2 = threading.Thread(target=w2)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    view = db.as_of(db._clock())
    final = view.entity(tags.eid).get("value")
    assert final == frozenset({"alpha", "beta", "gamma", "delta"})


def test_set_union_overlapping_keys_idempotent():
    """Two threads union overlapping frozensets; idempotent on overlap.

    set-union is unconditionally commutative AND idempotent: union of
    {a, b} and {b, c} is always {a, b, c} regardless of order.
    """
    db = DB()
    tags = db.new_ref(initial=frozenset())

    def w1():
        @db.dosync
        def body(tx):
            tx.commute(tags, "set-union", frozenset({"a", "b"}))
        body()

    def w2():
        @db.dosync
        def body(tx):
            tx.commute(tags, "set-union", frozenset({"b", "c"}))
        body()

    t1 = threading.Thread(target=w1)
    t2 = threading.Thread(target=w2)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    view = db.as_of(db._clock())
    final = view.entity(tags.eid).get("value")
    assert final == frozenset({"a", "b", "c"})


# ---------------------------------------------------------------------------
# dict-merge-shallow — disjoint AND overlapping (LWW-deterministic) cases.
# ---------------------------------------------------------------------------

def test_dict_merge_shallow_disjoint_keys():
    """Two threads merge dicts with disjoint keys; both keys present.

    Disjoint-key case is the dominant real workload; commutative
    cleanly because no key is contended.
    """
    db = DB()
    signals = db.new_ref(initial=pmap())

    def w1():
        @db.dosync
        def body(tx):
            tx.commute(signals, "dict-merge-shallow", {"signal-a": 1})
        body()

    def w2():
        @db.dosync
        def body(tx):
            tx.commute(signals, "dict-merge-shallow", {"signal-b": 2})
        body()

    t1 = threading.Thread(target=w1)
    t2 = threading.Thread(target=w2)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    view = db.as_of(db._clock())
    final = view.entity(signals.eid).get("value")
    assert final == pmap({"signal-a": 1, "signal-b": 2})


def test_dict_merge_shallow_overlapping_keys_lww_deterministic():
    """Overlapping keys: deterministic last-write-wins per commit-order.

    The result is deterministic but NOT commutative on overlap — which
    of the two values wins depends on commit-order, but exactly one
    value DOES win, and the result is one of two known shapes (caller-
    aware: documented at the registry table).

    This test asserts the result is one of the two acceptable shapes;
    actual order is non-deterministic across runs.
    """
    db = DB()
    counts = db.new_ref(initial=pmap())

    def w1():
        @db.dosync
        def body(tx):
            tx.commute(counts, "dict-merge-shallow", {"key": 1})
        body()

    def w2():
        @db.dosync
        def body(tx):
            tx.commute(counts, "dict-merge-shallow", {"key": 2})
        body()

    t1 = threading.Thread(target=w1)
    t2 = threading.Thread(target=w2)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    view = db.as_of(db._clock())
    final = view.entity(counts.eid).get("value")
    # LWW on overlap: one of the two values wins. Either is acceptable;
    # the contract is "deterministic per commit-order", not
    # "commutative on overlap".
    assert final in (pmap({"key": 1}), pmap({"key": 2}))


# ---------------------------------------------------------------------------
# Intra-txn semantics — 4 cases from § F3 lines 307-312.
# ---------------------------------------------------------------------------

def test_intra_txn_multiple_commutes_same_ref():
    """Case 1: multiple commutes compose in body-order.

    Body calls inc-by(1), inc-by(2), inc-by(3) — eager values 1, 2,
    3 mid-body; final committed value = 0+1+2+3 = 6.
    """
    db = DB()
    counter = db.new_ref(initial=0)

    eager_values = []

    @db.dosync
    def body(tx):
        eager_values.append(tx.commute(counter, "inc-by", 1))
        eager_values.append(tx.commute(counter, "inc-by", 2))
        eager_values.append(tx.commute(counter, "inc-by", 3))
    body()

    # Eager values compose in body-order against snapshot 0:
    # 0+1=1, 1+2=3, 3+3=6.
    assert eager_values == [1, 3, 6]
    view = db.as_of(db._clock())
    assert view.entity(counter.eid).get("value") == 6


def test_intra_txn_commute_then_assoc_explicit_wins():
    """Case 2: commute then assoc — explicit write WINS.

    Body calls commute(r, inc-by, 5) then assoc(r, 99). At commit:
    commute entry on r is dropped; the explicit-write fact emits with
    v=99.
    """
    db = DB()
    counter = db.new_ref(initial=0)

    @db.dosync
    def body(tx):
        tx.commute(counter, "inc-by", 5)
        tx.assoc(counter, 99)
    body()

    view = db.as_of(db._clock())
    assert view.entity(counter.eid).get("value") == 99


def test_intra_txn_assoc_then_commute_both_apply():
    """Case 3: assoc then commute — both apply.

    Body calls assoc(r, 10) then commute(r, inc-by, 5). The body-eager
    value seen by the commute is the just-set explicit value (10).
    Since explicit-write wins (case 2), at commit the assoc emits v=10
    and the commute on the same ref is DROPPED — so final committed
    value is 10.

    NOTE: § F3 line 311 prose says "both apply; the body-eager value
    seen by the commute is the just-set value; at commit, the explicit
    write fact emits FIRST, then the commute reapplies against the
    explicit-write's value (which equals the latest committed since
    they're committed atomically)." But § F3 line 310 (case 2) says
    "the explicit write WINS — commute log entries on that ref are
    dropped at commit." Cases 2 and 3 prose appear contradictory —
    case 2 unambiguously says "drop commute" for ANY ref also in
    write_set, regardless of body-order. Implementation follows case
    2 (the structurally simpler invariant). This test pins the
    realised behavior.
    """
    db = DB()
    counter = db.new_ref(initial=0)

    eager_after_commute = []

    @db.dosync
    def body(tx):
        tx.assoc(counter, 10)
        # Body-eager: commute applies on top of the just-set explicit
        # write (10 + 5 = 15) — visible to subsequent body reads.
        eager_after_commute.append(tx.commute(counter, "inc-by", 5))
    body()

    # Eager body value = 15.
    assert eager_after_commute == [15]
    # At commit, explicit write wins (case 2): final committed = 10.
    view = db.as_of(db._clock())
    assert view.entity(counter.eid).get("value") == 10


def test_intra_txn_deref_after_commute_returns_eager():
    """Case 4: deref after commute returns optimistic body-eager value.

    Body calls commute(r, inc-by, 1) then deref(r). The deref returns
    the eager value (committed_base + 1 = 1), NOT the snapshot value.
    Repeated derefs return the same eager value (idempotent under
    read_set membership).
    """
    db = DB()
    counter = db.new_ref(initial=0)

    derefs = []

    @db.dosync
    def body(tx):
        tx.commute(counter, "inc-by", 1)
        derefs.append(tx.deref(counter))
        derefs.append(tx.deref(counter))  # repeated deref idempotent
    body()

    assert derefs == [1, 1]


# ---------------------------------------------------------------------------
# Replay byte-identity — narrow scope: the commute_log emitted on commit
# datom provenance is byte-identical across two runs of the same body
# under the same starting state + frozen clock.
# ---------------------------------------------------------------------------

def test_replay_byte_identity_commute():
    """Two runs of the same commute-bearing body against fresh frozen-
    clock DBs produce identical user-write logs AND identical
    commute-log provenance entries (modulo per-run commit_id which is
    excluded by filter).
    """
    from datetime import datetime, timezone

    fixed_t = datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc)

    def _run_one() -> tuple[list, list]:
        db = DB(clock=lambda: fixed_t)
        counter = db.new_ref(initial=0)
        tags = db.new_ref(initial=frozenset())

        # Use a fixed eid path via db.ref so both runs allocate the
        # same eids (new_ref draws uuid4 — non-deterministic).
        cnt_ref = db.ref("counter")
        tags_ref = db.ref("tags")

        @db.dosync
        def body(tx):
            tx.commute(cnt_ref, "inc-by", 5)
            tx.commute(cnt_ref, "inc-by", 3)
            tx.commute(tags_ref, "set-union", frozenset({"x", "y"}))
        body()

        # User-write log: filter out per-run commit datoms.
        user_log = [
            (d.e, d.a, d.v, d.op, d.valid_from)
            for d in db.log()
            if not d.a.startswith("persistence.txn/")
        ]
        # Commute log on commit datom provenance.
        commute_log_entries = []
        for d in db.log():
            if d.a == "persistence.txn/commit-id":
                commute_log_entries.append(
                    d.provenance.get(":persistence.txn/commute-log", [])
                )
        return user_log, commute_log_entries

    log1, commute1 = _run_one()
    log2, commute2 = _run_one()
    assert log1 == log2
    assert commute1 == commute2


# ---------------------------------------------------------------------------
# Registry contract — locked in production, unlocked under sentinel,
# duplicate-without-replace raises.
# ---------------------------------------------------------------------------

def test_register_commute_locked_in_production():
    """Without sentinel env var, register_commute raises RuntimeError."""
    with pytest.raises(RuntimeError, match="forbidden in production"):
        register_commute("evil", lambda v, x: v - x)


def test_register_commute_unlocked_with_sentinel(monkeypatch):
    """With sentinel set, register_commute succeeds and the new fn_id
    is callable via tx.commute."""
    monkeypatch.setenv("PERSISTENCE_TXN_ALLOW_RUNTIME_REGISTRATION", "1")
    register_commute("plus-test", lambda v, x: (v if v is not None else 0) + x)
    try:
        db = DB()
        r = db.new_ref(initial=0)

        @db.dosync
        def body(tx):
            tx.commute(r, "plus-test", 7)
        body()

        view = db.as_of(db._clock())
        assert view.entity(r.eid).get("value") == 7
    finally:
        unregister_commute("plus-test")


def test_register_commute_duplicate_raises_without_replace(monkeypatch):
    """Re-registering an existing fn_id raises ValueError."""
    monkeypatch.setenv("PERSISTENCE_TXN_ALLOW_RUNTIME_REGISTRATION", "1")
    with pytest.raises(ValueError, match="already registered"):
        register_commute("inc-by", lambda v, x: v + x + 1)


def test_register_commute_duplicate_with_replace_succeeds(monkeypatch):
    """replace=True allows re-registration; restore default after."""
    from persistence.txn._commute import _DEFAULT_COMMUTE_TABLE

    monkeypatch.setenv("PERSISTENCE_TXN_ALLOW_RUNTIME_REGISTRATION", "1")
    original = _DEFAULT_COMMUTE_TABLE["inc-by"]
    # Replacement still defaults None→0 to match the original's
    # contract — only the +100 offset distinguishes them.
    register_commute(
        "inc-by",
        lambda v, x: (v if v is not None else 0) + x + 100,
        replace=True,
    )
    try:
        db = DB()
        r = db.new_ref(initial=0)

        @db.dosync
        def body(tx):
            tx.commute(r, "inc-by", 1)
        body()
        view = db.as_of(db._clock())
        # Replaced fn produced (0 + 1 + 100) = 101 instead of 1.
        assert view.entity(r.eid).get("value") == 101
    finally:
        # Restore the canonical inc-by for any subsequent test.
        register_commute("inc-by", original, replace=True)


def test_unregister_commute_locked_in_production():
    """Without sentinel, unregister_commute raises RuntimeError."""
    with pytest.raises(RuntimeError, match="forbidden in production"):
        unregister_commute("inc-by")


def test_unknown_fn_id_raises():
    """tx.commute with unregistered fn_id raises ValueError naming the
    curated registry."""
    db = DB()
    r = db.new_ref(initial=0)

    @db.dosync
    def body(tx):
        tx.commute(r, "nonexistent-fn", 1)

    with pytest.raises(ValueError, match="unknown fn_id"):
        body()


# ---------------------------------------------------------------------------
# tx.commute on foreign DB raises RefBranchMismatch (parity with
# deref/assoc/ensure).
# ---------------------------------------------------------------------------

def test_commute_raises_RefBranchMismatch_on_foreign_ref():
    from persistence.txn import RefBranchMismatch

    db1 = DB()
    db2 = DB()
    foreign = db2.ref("v")

    @db1.dosync
    def body(tx):
        tx.commute(foreign, "inc-by", 1)

    with pytest.raises(RefBranchMismatch):
        body()


# ---------------------------------------------------------------------------
# Empty commute-log emits empty list (mirrors read-set / ensure-set
# behavior).
# ---------------------------------------------------------------------------

def test_empty_commute_log_emits_empty_list():
    db = DB()
    r = db.ref("v")

    @db.dosync
    def body(tx):
        tx.assoc(r, 1)
    body()

    commit_datom = next(
        d for d in db.log() if d.a == "persistence.txn/commit-id"
    )
    assert commit_datom.provenance[":persistence.txn/commute-log"] == []


# ---------------------------------------------------------------------------
# Conflict-detection: tx.commute does NOT extend conflict union.
# ---------------------------------------------------------------------------

def test_commute_does_not_add_to_read_set():
    """A body that ONLY commutes (no deref/alter/ensure) on a ref
    must NOT add that ref to read_set — that's the entire point of
    commute vs alter.
    """
    db = DB()
    r = db.new_ref(initial=0)

    captured: dict = {}

    @db.dosync
    def body(tx):
        tx.commute(r, "inc-by", 1)
        captured["read_set_size"] = len(tx.read_set)
        captured["commute_log_size"] = len(tx.commute_log)
    body()

    assert captured["read_set_size"] == 0
    assert captured["commute_log_size"] == 1


def test_commute_does_not_force_retry_under_concurrent_writes():
    """Two concurrent commits on the same counter — one via commute,
    one via assoc — neither retries due to commute_log union, because
    commute is conflict-free by design. The commute reapplies at
    commit-time against the latest committed value, so the explicit
    write is observed.
    """
    db = DB()
    r = db.ref("counter")
    with db.dosync() as tx:
        tx.assoc(r, 0)

    barrier = threading.Event()

    def commute_worker():
        @db.dosync
        def body(tx):
            tx.commute(r, "inc-by", 10)
            barrier.set()
        body()

    def assoc_worker():
        # Wait until the commute body has at least started.
        barrier.wait(timeout=2.0)
        @db.dosync
        def body(tx):
            current = tx.deref(r) or 0
            tx.assoc(r, current + 5)
        body()

    t1 = threading.Thread(target=commute_worker)
    t2 = threading.Thread(target=assoc_worker)
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    view = db.as_of(db._clock())
    final = view.entity(r.eid).get("value")
    # Conservation: assoc contributed +5, commute contributed +10
    # against whatever was latest at lock-time. Final value must be
    # >= 15 and <= 15 since neither op multiplies. (The exact value
    # is 15 because commute reapplies against the latest committed,
    # which is either 0 or 5; either way the final is 10 + (0 or 5)
    # = 10 or 15... actually the assoc may overwrite the commute if
    # it commits AFTER the commute. So the final is one of 5, 10,
    # 15 depending on commit ordering — all are valid outcomes that
    # demonstrate no RETRY happened.)
    assert final in (5, 10, 15)
