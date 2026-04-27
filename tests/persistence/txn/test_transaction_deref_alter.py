"""tx.deref (snapshot read) + tx.alter (read-then-write)."""
import pytest
from pyrsistent import pmap

from persistence.fact.db import DB
from persistence.txn.transaction import Transaction
from persistence.txn import RefBranchMismatch, RefValueNotImmutable


def test_deref_returns_None_for_unwritten_entity():
    db = DB()
    tx = Transaction(db=db, t_start=db._clock(), attempt=0)
    r = db.ref("never-written")
    assert tx.deref(r) is None


def test_deref_adds_ref_to_read_set():
    db = DB()
    tx = Transaction(db=db, t_start=db._clock(), attempt=0)
    r = db.ref("account-1")
    tx.deref(r)
    assert r in tx.read_set


def test_deref_returns_pending_write_if_already_assoc():
    db = DB()
    tx = Transaction(db=db, t_start=db._clock(), attempt=0)
    r = db.ref("account-1")
    tx.assoc(r, pmap({"balance": 100}))
    # In-body deref should see the in-flight write (read-your-own-writes).
    assert tx.deref(r) == pmap({"balance": 100})


def test_deref_raises_RefBranchMismatch_on_foreign_ref():
    db1 = DB()
    db2 = DB()
    tx = Transaction(db=db1, t_start=db1._clock(), attempt=0)
    foreign_ref = db2.ref("account-1")
    with pytest.raises(RefBranchMismatch):
        tx.deref(foreign_ref)


def test_alter_reads_then_writes_returns_new_value():
    db = DB()
    tx = Transaction(db=db, t_start=db._clock(), attempt=0)
    r = db.ref("counter-1")
    tx.assoc(r, 5)  # initial value
    new_v = tx.alter(r, lambda v, n: v + n, 3)
    assert new_v == 8
    assert tx.write_set[r] == 8
    # alter should add ref to read_set as well
    assert r in tx.read_set


def test_alter_on_unwritten_passes_None_to_fn():
    db = DB()
    tx = Transaction(db=db, t_start=db._clock(), attempt=0)
    r = db.ref("counter-1")
    new_v = tx.alter(r, lambda v: 100 if v is None else v + 1)
    assert new_v == 100


def test_alter_rejects_mutable_result():
    db = DB()
    tx = Transaction(db=db, t_start=db._clock(), attempt=0)
    r = db.ref("counter-1")
    with pytest.raises(RefValueNotImmutable):
        tx.alter(r, lambda v: {"mutable": True})  # fn returns dict
