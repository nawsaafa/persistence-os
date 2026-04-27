"""Conflict detection + retry loop end-to-end tests."""
import pytest
from pyrsistent import pmap

from persistence.fact.db import DB
from persistence.txn import TxnRetryExhausted


def test_decorator_retries_on_conflict_and_eventually_commits():
    """Two transactions touch same ref. Sequential success-path test —
    a true conflict-and-retry test requires threads (covered below).
    """
    db = DB()
    r = db.ref("counter")
    # Seed initial value.
    with db.dosync() as tx:
        tx.assoc(r, 0)

    @db.dosync
    def t1(tx):
        v = tx.deref(r)
        tx.assoc(r, v + 10)

    t1()
    t1()
    # Both transactions ran sequentially; final counter = 20.
    view = db.as_of(db._clock())
    assert view.entity("counter").get("value") == 20


def test_concurrent_threads_increment_counter_via_alter():
    """Two threads both alter the same counter. Both eventually commit.
    Final value = sum of increments. Retries are invisible to caller.
    """
    import threading

    db = DB()
    r = db.ref("counter")
    with db.dosync() as tx:
        tx.assoc(r, 0)

    @db.dosync
    def increment(tx):
        tx.alter(r, lambda v, n=None: (v if v is not None else 0) + 1)

    threads = [threading.Thread(target=increment) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    view = db.as_of(db._clock())
    final = view.entity("counter").get("value")
    assert final == 10, f"expected 10 (no lost increments), got {final!r}"


def test_max_retries_zero_with_no_conflict_succeeds():
    db = DB()
    r = db.ref("v")

    @db.dosync(max_retries=0)
    def write(tx):
        tx.assoc(r, "value")

    write()
    view = db.as_of(db._clock())
    assert view.entity("v").get("value") == "value"


def test_dosync_with_deadline_succeeds_quickly():
    db = DB()
    r = db.ref("v")

    @db.dosync(deadline=1.0)
    def write(tx):
        tx.assoc(r, "value")

    write()
    view = db.as_of(db._clock())
    assert view.entity("v").get("value") == "value"
