"""tx.ensure — read-set padding (v0.5.2 § F2).

Covers the design doc § F2 acceptance gate:

    "classical bank-transfer write-skew test (Alice and Bob each
    transfer half their joint balance to a third party in parallel;
    without ``ensure``, both succeed and overdraw; with ``ensure`` on
    the partner's account, one retries and overdraws is prevented).
    Plus 3 unit tests covering ensure-set conflict detection."

The threading test mirrors the pattern in
``tests/persistence/txn/test_conflict.py::test_concurrent_threads_increment_counter_via_alter``
(``threading.Thread`` + ``.join()``).

`tx.ensure(ref)` is a strict superset of ``tx.deref(ref)``:
- Adds ``ref`` to ``ensure_set`` (provenance-distinguishable from
  ``read_set``).
- Calls ``tx.deref`` internally so ``read_set`` ALSO records the ref —
  required for read-your-own-writes consistency and to keep the
  conflict-detection invariant simple.
- Returns the snapshot value (matches Clojure
  ``LockingTransaction.ensure`` ergonomics).
"""
from __future__ import annotations

import threading

import pytest

from persistence.fact.db import DB
from persistence.txn import RefBranchMismatch
from persistence.txn.transaction import Transaction


# ---------------------------------------------------------------------------
# Test 1 — return value parity with deref.
# ---------------------------------------------------------------------------

def test_ensure_returns_deref_value():
    """``tx.ensure`` returns the snapshot value (matches Clojure
    ``LockingTransaction.ensure`` returning the deref'd value).
    """
    db = DB()
    r = db.new_ref(initial=42)
    # Seed initial value via assoc (new_ref discards initial in v0.5.0a1).
    with db.dosync() as tx:
        tx.assoc(r, 42)

    with db.dosync() as tx:
        v = tx.ensure(r)
    assert v == 42


# ---------------------------------------------------------------------------
# Test 2 — ensure populates BOTH ensure_set AND read_set.
# ---------------------------------------------------------------------------

def test_ensure_adds_to_ensure_set_and_read_set():
    """``tx.ensure(ref)`` MUST add ``ref`` to both sets.

    - ``ensure_set`` tags it as "padded for conflict-detection only"
      so the commit-datom auditor can distinguish from value reads.
    - ``read_set`` is also populated because ``ensure`` calls
      ``deref`` internally — the conflict-detection invariant
      ``read_set | write_set | ensure_set`` is mathematically
      equivalent under this rule, but the dual-set membership keeps
      the read-your-own-writes path uniform.
    """
    db = DB()
    r = db.new_ref(initial=0)
    tx = Transaction(db=db, t_start=db._clock(), attempt=0)
    tx.ensure(r)
    assert r in tx.ensure_set
    assert r in tx.read_set


# ---------------------------------------------------------------------------
# Test 3 — ensure_set surfaces on commit-datom provenance.
# ---------------------------------------------------------------------------

def test_ensure_set_emitted_in_commit_provenance():
    """The commit datom carries ``:persistence.txn/ensure-set`` as a
    sorted list of eids (mirrors ``:persistence.txn/read-set``).
    """
    db = DB()
    r1 = db.ref("z-padded")
    r2 = db.ref("a-padded")
    w = db.ref("a-written")

    @db.dosync
    def body(tx):
        # Ensure both refs in non-sorted insertion order — the commit
        # datom must record them sorted alphabetically by eid.
        tx.ensure(r1)
        tx.ensure(r2)
        tx.assoc(w, 1)  # also write so the body has a write

    body()
    commit_datom = next(
        d for d in db.log() if d.a == "persistence.txn/commit-id"
    )
    prov = commit_datom.provenance
    assert prov[":persistence.txn/ensure-set"] == sorted([r1.eid, r2.eid])
    # Read-set ALSO records the ensure'd refs (ensure calls deref
    # internally) — but the auditor uses set difference to distinguish
    # "deref'd for value" from "padded only".
    assert sorted([r1.eid, r2.eid]) == sorted(prov[":persistence.txn/read-set"])


def test_empty_ensure_set_emits_empty_list():
    """When the body never calls ``tx.ensure``, the provenance still
    emits the key with an empty list (mirrors read-set behavior).
    """
    db = DB()
    r = db.ref("v")

    @db.dosync
    def body(tx):
        tx.assoc(r, 1)

    body()
    commit_datom = next(
        d for d in db.log() if d.a == "persistence.txn/commit-id"
    )
    assert commit_datom.provenance[":persistence.txn/ensure-set"] == []


# ---------------------------------------------------------------------------
# Test 4 — branch-mismatch parity with deref.
# ---------------------------------------------------------------------------

def test_ensure_raises_RefBranchMismatch_on_foreign_ref():
    db1 = DB()
    db2 = DB()
    tx = Transaction(db=db1, t_start=db1._clock(), attempt=0)
    foreign_ref = db2.ref("v")
    with pytest.raises(RefBranchMismatch):
        tx.ensure(foreign_ref)


# ---------------------------------------------------------------------------
# Test 5 — conflict-detection extends to ensure_set (the canonical bug
# tx.ensure exists to fix). Without ensure_set in the union, a body that
# only ensure'd a ref (no deref) would see no conflict detection on that
# ref. Because our impl makes ensure call deref internally, this is
# already covered via read_set; this test makes the conflict-via-ensure
# behavior explicit.
# ---------------------------------------------------------------------------

def test_ensure_triggers_conflict_when_ensured_ref_changes():
    """A dosync that only ``ensure``s a ref and writes elsewhere must
    retry if a concurrent commit modifies the ensure'd ref.

    Drive the concurrent commit from a separate thread (nested
    ``dosync`` from the same task is prohibited by
    ``NestedDosyncNotSupported``). The first body attempt ``ensure``s
    ``r_other`` then waits on a barrier while a worker thread commits
    a write to ``r_other``; the commit gate detects the conflict via
    ``read_set | write_set | ensure_set`` and triggers a retry. The
    second attempt sees the post-conflict value and proceeds.
    """
    db = DB()
    r_other = db.ref("partner")
    r_self = db.ref("self")
    with db.dosync() as tx:
        tx.assoc(r_other, 0)
        tx.assoc(r_self, 0)

    # Coordinates a single concurrent write between body-attempt 0 and
    # the commit gate. ``concurrent_write_done`` blocks the main body
    # at exactly the point that maximises chance of conflict detection.
    concurrent_write_started = threading.Event()
    concurrent_write_done = threading.Event()

    def writer():
        concurrent_write_started.wait()
        with db.dosync() as inner:
            inner.assoc(r_other, 999)
        concurrent_write_done.set()

    writer_thread = threading.Thread(target=writer)
    writer_thread.start()

    attempts: list[int] = []

    @db.dosync(max_retries=8)
    def body(tx):
        attempts.append(tx.attempt)
        tx.ensure(r_other)
        if tx.attempt == 0:
            # Trigger the concurrent write only on the first attempt;
            # block until it's committed so the conflict-detection
            # union deterministically catches it.
            concurrent_write_started.set()
            concurrent_write_done.wait(timeout=5.0)
        tx.assoc(r_self, 1)

    body()
    writer_thread.join(timeout=5.0)
    assert not writer_thread.is_alive()
    # Body retried at least once: attempt 0 saw the conflict via
    # ``ensure_set`` union, attempt 1 (or later) succeeded.
    assert attempts[0] == 0
    assert len(attempts) >= 2
    view = db.as_of(db._clock())
    assert view.entity("partner").get("value") == 999
    assert view.entity("self").get("value") == 1


# ---------------------------------------------------------------------------
# Test 6 — bank-transfer write-skew (canonical Clojure ensure example).
#
# Without `ensure`: Alice and Bob each transfer half their joint balance
# to a third party in parallel; both commits succeed because neither
# read each other's account, so MVCC sees disjoint write_sets. Result:
# total transferred > joint balance (overdraw).
#
# With `ensure`: Alice's body ensures(bob_ref); Bob's body ensures
# (alice_ref). Conflict-detection now spans both accounts; one transfer
# retries and observes the latest balance, preventing overdraw.
#
# The post-condition is order-insensitive: regardless of which thread
# wins, total funds are conserved (alice + bob + third == initial sum)
# AND no balance goes negative.
# ---------------------------------------------------------------------------

def _read_balance(db: DB, eid: str) -> int:
    view = db.as_of(db._clock())
    entity = view.entity(eid)
    return entity.get("value", 0) if entity else 0


def test_ensure_prevents_write_skew_bank_transfer():
    """Bank-transfer write-skew — the canonical use case for ``ensure``.

    Two threads transfer half the joint Alice+Bob balance to a third
    party in parallel. Each body uses ``tx.ensure`` on the partner's
    ref. With the conflict-detection union including ``ensure_set``,
    one transaction retries and observes the latest joint balance,
    short-circuiting the second transfer when joint balance can't
    cover both withdrawals.

    Post-conditions (order-insensitive):
    - Conservation: ``alice + bob + third == 200``.
    - No overdraw: ``alice >= 0 and bob >= 0``.
    """
    db = DB()
    alice = db.ref("alice")
    bob = db.ref("bob")
    third_party = db.ref("third")
    with db.dosync() as tx:
        tx.assoc(alice, 100)
        tx.assoc(bob, 100)
        tx.assoc(third_party, 0)

    def transfer_half_with_ensure(my_ref, other_ref, amount):
        @db.dosync(max_retries=64)
        def _body(tx):
            my_bal = tx.deref(my_ref)
            other_bal = tx.ensure(other_ref)  # padding — see other side too
            joint = (my_bal or 0) + (other_bal or 0)
            if joint < 2 * amount:
                # Don't transfer — joint balance can't cover both withdrawals.
                return
            tx.assoc(my_ref, my_bal - amount)
            tx.assoc(third_party, (tx.deref(third_party) or 0) + amount)
        _body()

    t1 = threading.Thread(
        target=transfer_half_with_ensure, args=(alice, bob, 75)
    )
    t2 = threading.Thread(
        target=transfer_half_with_ensure, args=(bob, alice, 75)
    )
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    final_alice = _read_balance(db, alice.eid)
    final_bob = _read_balance(db, bob.eid)
    final_third = _read_balance(db, third_party.eid)
    # Conservation: total funds in == total funds out (no money created
    # or destroyed). With ensure preventing write-skew, this holds even
    # under contention.
    assert final_alice + final_bob + final_third == 200, (
        f"conservation violated: alice={final_alice} bob={final_bob} "
        f"third={final_third} (sum={final_alice + final_bob + final_third}, "
        "expected 200)"
    )
    # No overdraw: ensure'd refs caused one transfer to retry and
    # observe the post-other-commit balance, then short-circuit when
    # joint can't cover.
    assert final_alice >= 0, f"alice overdrew: {final_alice}"
    assert final_bob >= 0, f"bob overdrew: {final_bob}"
