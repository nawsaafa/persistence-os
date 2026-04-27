"""Ref dataclass — Phase A."""
from dataclasses import FrozenInstanceError

import pytest

from persistence.txn import Ref


def test_ref_constructed_with_eid_and_db_id():
    r = Ref(eid="account-1", db_id="db-A")
    assert r.eid == "account-1"
    assert r.db_id == "db-A"


def test_ref_equality_over_eid_and_db_id():
    r1 = Ref(eid="account-1", db_id="db-A")
    r2 = Ref(eid="account-1", db_id="db-A")
    r3 = Ref(eid="account-2", db_id="db-A")
    r4 = Ref(eid="account-1", db_id="db-B")
    assert r1 == r2
    assert r1 != r3
    assert r1 != r4   # different db_id = different ref


def test_ref_hashable():
    r1 = Ref(eid="account-1", db_id="db-A")
    r2 = Ref(eid="account-1", db_id="db-A")
    s = {r1, r2}
    assert len(s) == 1


def test_ref_immutable():
    r = Ref(eid="account-1", db_id="db-A")
    with pytest.raises(FrozenInstanceError):
        r.eid = "other"  # frozen dataclass refuses assignment
