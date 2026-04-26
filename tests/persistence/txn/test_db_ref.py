"""DB.ref() / DB.new_ref() — Phase A."""
import pytest
from pyrsistent import pmap

from persistence.fact.db import DB
from persistence.txn import Ref, RefValueNotImmutable


def test_db_ref_returns_Ref_with_eid_and_db_id():
    db = DB()
    r = db.ref("account-1")
    assert isinstance(r, Ref)
    assert r.eid == "account-1"
    # db_id is opaque but must be stable per DB instance
    assert r.db_id == db.ref("account-2").db_id


def test_db_ref_same_eid_returns_equal_ref():
    db = DB()
    r1 = db.ref("account-1")
    r2 = db.ref("account-1")
    assert r1 == r2
    assert hash(r1) == hash(r2)


def test_two_dbs_have_different_db_ids():
    db1 = DB()
    db2 = DB()
    r1 = db1.ref("account-1")
    r2 = db2.ref("account-1")
    assert r1 != r2  # different db_id


def test_new_ref_allocates_uuid7_eid():
    db = DB()
    r1 = db.new_ref()
    r2 = db.new_ref()
    assert r1 != r2
    assert isinstance(r1.eid, str)
    # UUID7 strings are 36 chars with 4 hyphens
    assert len(r1.eid) == 36
    assert r1.eid.count("-") == 4


def test_new_ref_with_initial_value_must_be_immutable():
    db = DB()
    # acceptable
    r = db.new_ref(initial=pmap({"balance": 100}))
    assert isinstance(r, Ref)


def test_new_ref_rejects_mutable_initial_value():
    db = DB()
    with pytest.raises(RefValueNotImmutable):
        db.new_ref(initial={"balance": 100})  # plain dict — rejected
    with pytest.raises(RefValueNotImmutable):
        db.new_ref(initial=[1, 2, 3])  # plain list — rejected
