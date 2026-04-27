"""MVCC conflict detection — query datoms since t_start for given entity-ids."""
from datetime import datetime, timezone, timedelta

from persistence.fact.db import DB
from persistence.txn.conflict import any_datoms_since


def test_no_conflict_when_no_writes_since_t_start():
    db = DB()
    t_start = db._clock()
    # No writes between t_start and now.
    assert any_datoms_since(db, t_start, entity_ids={"account-1"}) is False


def test_conflict_when_entity_written_after_t_start():
    db = DB()
    t_start = db._clock() - timedelta(seconds=1)
    db = db.transact([{
        "e": "account-1",
        "a": "balance",
        "v": 100,
        "valid_from": datetime.now(timezone.utc),
    }])
    assert any_datoms_since(db, t_start, entity_ids={"account-1"}) is True


def test_no_conflict_when_unrelated_entity_written():
    db = DB()
    t_start = db._clock() - timedelta(seconds=1)
    db = db.transact([{
        "e": "account-2",
        "a": "balance",
        "v": 100,
        "valid_from": datetime.now(timezone.utc),
    }])
    # Touched set doesn't include account-2.
    assert any_datoms_since(db, t_start, entity_ids={"account-1"}) is False


def test_empty_entity_ids_means_no_conflict():
    db = DB()
    t_start = db._clock()
    db = db.transact([{
        "e": "account-1",
        "a": "balance",
        "v": 100,
        "valid_from": datetime.now(timezone.utc),
    }])
    # Empty touched set short-circuits.
    assert any_datoms_since(db, t_start, entity_ids=set()) is False
