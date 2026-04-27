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
