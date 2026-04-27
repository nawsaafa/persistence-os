"""Per-ref attribute spec — v0.5.1 (rev O / N3).

``Ref.spec_attr`` (default ``"value"``) names which write-spec the ref's
writes are validated against AND which entity-attribute name the value
lives under. The default preserves v0.5.0a1 behavior bit-for-bit; explicit
``spec_attr="..."`` lets two refs to the same eid validate against
different specs and write to different attributes on the same entity.

Equality and hashing are over ``(eid, db_id)`` — ``spec_attr`` is a
label, not part of identity.
"""
from __future__ import annotations

import pytest

from persistence.fact.db import DB
from persistence.spec import int_, register, registered_keys, str_
from persistence.spec._registry import SpecError, _REGISTRY
from persistence.txn import Ref


# ---------------------------------------------------------------------------
# spec-registry isolation: each test that registers ad-hoc keys cleans up
# after itself so we don't leak into the rest of the suite.
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_spec_registry():
    """Snapshot _REGISTRY, yield, restore. Avoids cross-test contamination."""
    snapshot = dict(_REGISTRY)
    try:
        yield
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(snapshot)


# ---------------------------------------------------------------------------
# 1. default spec_attr is "value"
# ---------------------------------------------------------------------------

def test_ref_default_spec_attr_is_value():
    db = DB()
    r = db.ref("e1")
    assert r.spec_attr == "value"
    # also via direct construction
    r2 = Ref(eid="e2", db_id=r.db_id)
    assert r2.spec_attr == "value"


# ---------------------------------------------------------------------------
# 2. explicit spec_attr threads through
# ---------------------------------------------------------------------------

def test_ref_with_explicit_spec_attr():
    db = DB()
    r_db = db.ref("e1", spec_attr="balance")
    assert r_db.spec_attr == "balance"

    r_new = db.new_ref(spec_attr="balance")
    assert r_new.spec_attr == "balance"

    # direct construction still works
    r_direct = Ref(eid="e1", db_id=r_db.db_id, spec_attr="account/balance")
    assert r_direct.spec_attr == "account/balance"


# ---------------------------------------------------------------------------
# 3. eq/hash invariant — spec_attr does NOT participate
# ---------------------------------------------------------------------------

def test_two_refs_same_eid_different_spec_attrs_are_equal():
    db = DB()
    r_default = db.ref("e1")
    r_balance = db.ref("e1", spec_attr="balance")
    r_name = db.ref("e1", spec_attr="name")

    # equality: identity is (eid, db_id) only.
    assert r_default == r_balance
    assert r_balance == r_name
    assert r_default == r_name

    # hash: matches equality.
    assert hash(r_default) == hash(r_balance) == hash(r_name)

    # set / dict membership: all three collapse to one bucket.
    bucket = {r_default, r_balance, r_name}
    assert len(bucket) == 1

    # but spec_attr labels are still observable on each instance.
    assert r_default.spec_attr == "value"
    assert r_balance.spec_attr == "balance"
    assert r_name.spec_attr == "name"


# ---------------------------------------------------------------------------
# 4. per-ref spec validation fires the correct spec
# ---------------------------------------------------------------------------

def test_per_ref_spec_validation_fires_correct_spec(clean_spec_registry):
    """Register int spec under 'balance' and str spec under 'name'.

    A balance-ref accepts an int and rejects a str; a name-ref accepts a
    str and rejects an int. Demonstrates that ``_spec_validate_writes``
    routes per-ref now (vs the v0.5.0a1 single-global-key behavior).
    """
    register("balance", int_())
    register("name", str_())
    assert "balance" in registered_keys()
    assert "name" in registered_keys()

    db = DB()
    r_balance = db.ref("acct-1", spec_attr="balance")
    r_name = db.ref("acct-1", spec_attr="name")  # same eid, different attr

    # int into balance OK
    @db.dosync
    def write_balance_ok(tx):
        tx.assoc(r_balance, 100)

    write_balance_ok()  # no SpecError

    # str into balance — rejected by balance's int_() spec
    @db.dosync
    def write_balance_bad(tx):
        tx.assoc(r_balance, "not-an-int")

    with pytest.raises(SpecError):
        write_balance_bad()

    # str into name OK
    @db.dosync
    def write_name_ok(tx):
        tx.assoc(r_name, "alice")

    write_name_ok()

    # int into name — rejected by name's str_() spec
    @db.dosync
    def write_name_bad(tx):
        tx.assoc(r_name, 999)

    with pytest.raises(SpecError):
        write_name_bad()


# ---------------------------------------------------------------------------
# 5. invalid spec_attr shape raises ValueError
# ---------------------------------------------------------------------------

def test_invalid_spec_attr_shape_raises():
    """Reject leading colon, whitespace, empty string, leading punctuation.

    The Datom store strips a leading ``":"`` from ``a`` field, so a
    ``spec_attr`` like ``":colon"`` would silently land as ``"colon"``
    on the wire — break that reflection by rejecting at Ref construction
    time. Whitespace and empty string have no valid EDN keyword
    interpretation. Leading slash / dot / dash violate EDN keyword name
    shape (must start with alphanum).
    """
    db = DB()
    bad_shapes = [
        ":colon",          # leading colon — Datom strips it; reject up front
        "bad name",        # whitespace
        "",                # empty
        "/leading-slash",  # leading slash (not alphanum)
        ".dot-first",      # leading dot
        "-dash-first",     # leading dash
        "name\twith\ttab", # tab inside
    ]
    for bad in bad_shapes:
        with pytest.raises(ValueError, match="spec_attr"):
            db.ref("e1", spec_attr=bad)
        with pytest.raises(ValueError, match="spec_attr"):
            db.new_ref(spec_attr=bad)
        with pytest.raises(ValueError, match="spec_attr"):
            Ref(eid="e1", db_id="db-X", spec_attr=bad)


# ---------------------------------------------------------------------------
# 6. tx.deref reads from the spec_attr-named attribute
# ---------------------------------------------------------------------------

def test_tx_deref_reads_from_spec_attr_attribute():
    """Write through a balance-ref in one dosync; in a fresh dosync,
    construct a new balance-ref with the same eid and confirm the
    snapshot read returns the prior write. Also confirm a default-attr
    ref to the same eid reads ``None`` (different attribute name).
    """
    db = DB()
    eid = "acct-42"

    @db.dosync
    def initialize(tx):
        tx.assoc(db.ref(eid, spec_attr="balance"), 250)

    initialize()

    # Fresh dosync — fresh transaction, fresh ref.
    seen: dict = {}

    @db.dosync
    def read_back(tx):
        r_balance_fresh = db.ref(eid, spec_attr="balance")
        seen["balance"] = tx.deref(r_balance_fresh)
        # default-attr ref to same eid: different attribute, no datom.
        r_default = db.ref(eid)
        seen["default"] = tx.deref(r_default)

    read_back()
    assert seen["balance"] == 250
    assert seen["default"] is None
