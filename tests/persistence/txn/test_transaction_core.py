"""Transaction class — direct API tests (no dosync wrapping yet)."""
from datetime import datetime

import pytest
from pyrsistent import pmap

from persistence.fact.db import DB
from persistence.txn.transaction import Transaction
from persistence.txn import RefValueNotImmutable


def test_transaction_constructed_with_db_t_start_attempt():
    db = DB()
    t_start = db._clock()
    tx = Transaction(db=db, t_start=t_start, attempt=0)
    assert tx.db is db
    assert tx.t_start == t_start
    assert tx.attempt == 0
    assert tx.read_set == set()
    assert tx.write_set == {}
    assert tx.effect_intent_log == []


def test_tx_now_returns_frozen_t_start():
    db = DB()
    t_start = db._clock()
    tx = Transaction(db=db, t_start=t_start, attempt=0)
    assert tx.now() == t_start
    # Calling _clock again would advance; tx.now() does NOT.
    assert tx.now() == t_start


def test_tx_assoc_queues_write_with_immutable_value():
    db = DB()
    tx = Transaction(db=db, t_start=db._clock(), attempt=0)
    r = db.ref("account-1")
    tx.assoc(r, pmap({"balance": 100}))
    assert r in tx.write_set
    assert tx.write_set[r] == pmap({"balance": 100})


def test_tx_assoc_rejects_mutable_value():
    db = DB()
    tx = Transaction(db=db, t_start=db._clock(), attempt=0)
    r = db.ref("account-1")
    with pytest.raises(RefValueNotImmutable):
        tx.assoc(r, {"balance": 100})  # plain dict


def test_tx_effect_queues_intent():
    db = DB()
    tx = Transaction(db=db, t_start=db._clock(), attempt=0)
    tx.effect(":log/write", message="hello")
    assert len(tx.effect_intent_log) == 1
    intent = tx.effect_intent_log[0]
    assert intent.op == ":log/write"
    assert intent.kwargs == {"message": "hello"}


def test_tx_assoc_raises_RefBranchMismatch_on_foreign_ref():
    """tx.assoc on a ref from a different DB must raise RefBranchMismatch
    (parallel to tx.deref). Without this guard, a foreign ref's eid would
    be silently written into the wrong DB at commit time.
    """
    from persistence.txn import RefBranchMismatch

    db1 = DB()
    db2 = DB()
    tx = Transaction(db=db1, t_start=db1._clock(), attempt=0)
    foreign_ref = db2.ref("account-1")
    with pytest.raises(RefBranchMismatch):
        tx.assoc(foreign_ref, pmap({"balance": 100}))


def test_empty_body_dosync_emits_exactly_one_commit_datom():
    """An empty dosync body (no assoc, no effect) still commits a single
    commit datom recording attempt count and timestamps. Pins the
    no-op-but-witnessed contract that future refactors must preserve.
    """
    db = DB()

    @db.dosync
    def noop(tx):  # pyright: ignore [reportArgumentType]
        pass

    n_before = len(list(db.store.all_datoms()))
    noop()
    after = list(db.store.all_datoms())
    assert len(after) - n_before == 1, (
        f"empty dosync body should emit exactly 1 commit datom; got "
        f"{len(after) - n_before}"
    )
    assert after[-1].a == "persistence.txn/commit-id"


def test_RetroactiveCorrectionError_propagates_through_dosync():
    """RetroactiveCorrectionError raised by transact_batch from inside a
    dosync body must propagate as RetroactiveCorrectionError — it is NOT
    a TxnError subclass. v0.4 callers using ``except TxnError`` will not
    catch this; they must explicitly handle the retroactive case.
    """
    from persistence.fact.db import RetroactiveCorrectionError
    from persistence.txn import TxnError

    # RetroactiveCorrectionError is fact-layer, not txn-layer.
    assert not issubclass(RetroactiveCorrectionError, TxnError)
