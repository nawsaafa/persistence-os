"""Atom — single-cell CAS over datom-refs (v0.5.2 § F1).

Covers the design doc § F1 acceptance gate:

    "5+ tests at tests/persistence/txn/test_atoms.py covering swap-
    success, swap-retry-under-contention, compare_and_set true/false
    paths, reset, and atom-inside-dosync prohibition."

Plus three additional tests:
- ``Atom``-vs-``Ref`` distinct types (atom_distinct_from_ref)
- ``db.atom`` rejects duplicate eid (atom_rejects_duplicate_eid)
- in-dosync prohibition uniformly across all 4 ops (atom_in_dosync_*)

Threading test mirrors the pattern in
``tests/persistence/txn/test_conflict.py::test_concurrent_threads_increment_counter_via_alter``
(``threading.Thread`` + ``.join()``).
"""
from __future__ import annotations

import threading

import pytest

from persistence.fact.db import DB
from persistence.txn import (
    Atom,
    AtomInDosyncProhibited,
    Ref,
)


# ---------------------------------------------------------------------------
# Single-thread swap correctness.
# ---------------------------------------------------------------------------

def test_swap_increments_under_no_contention():
    db = DB()
    counter = db.atom("counter", initial=0)
    assert counter.deref() == 0

    for _ in range(10):
        counter.swap(lambda v: v + 1)

    assert counter.deref() == 10


# ---------------------------------------------------------------------------
# Threaded swap — 10 threads × 10 increments each = 100 final.
# Mirrors v0.5.0a1 B7 threading test pattern.
# ---------------------------------------------------------------------------

def test_swap_under_threaded_contention():
    db = DB()
    counter = db.atom("counter", initial=0)

    def worker():
        for _ in range(10):
            counter.swap(lambda v: v + 1)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = counter.deref()
    assert final == 100, f"expected 100 (no lost increments), got {final!r}"


# ---------------------------------------------------------------------------
# compare_and_set — both true and false paths.
# ---------------------------------------------------------------------------

def test_compare_and_set_true_path():
    db = DB()
    counter = db.atom("counter", initial=0)

    ok = counter.compare_and_set(0, 5)
    assert ok is True
    assert counter.deref() == 5


def test_compare_and_set_false_path():
    db = DB()
    counter = db.atom("counter", initial=0)
    # Move it off 0 first.
    counter.swap(lambda v: 1)
    assert counter.deref() == 1

    # CAS expecting 0 should fail because current is 1.
    ok = counter.compare_and_set(0, 5)
    assert ok is False
    # Value should still be 1, not 5.
    assert counter.deref() == 1


# ---------------------------------------------------------------------------
# reset — unconditional overwrite.
# ---------------------------------------------------------------------------

def test_reset():
    db = DB()
    counter = db.atom("counter", initial=0)
    counter.swap(lambda v: v + 7)
    assert counter.deref() == 7

    rv = counter.reset(42)
    # reset returns the new value for chaining.
    assert rv == 42
    assert counter.deref() == 42


# ---------------------------------------------------------------------------
# Atom-in-dosync prohibition — uniformly across all 4 ops.
# ---------------------------------------------------------------------------

def test_atom_swap_in_dosync_raises():
    db = DB()
    counter = db.atom("counter", initial=0)

    @db.dosync
    def body(tx):
        counter.swap(lambda v: v + 1)

    with pytest.raises(AtomInDosyncProhibited):
        body()


def test_atom_deref_in_dosync_raises():
    db = DB()
    counter = db.atom("counter", initial=0)

    @db.dosync
    def body(tx):
        counter.deref()

    with pytest.raises(AtomInDosyncProhibited):
        body()


def test_atom_compare_and_set_in_dosync_raises():
    db = DB()
    counter = db.atom("counter", initial=0)

    @db.dosync
    def body(tx):
        counter.compare_and_set(0, 5)

    with pytest.raises(AtomInDosyncProhibited):
        body()


def test_atom_reset_in_dosync_raises():
    db = DB()
    counter = db.atom("counter", initial=0)

    @db.dosync
    def body(tx):
        counter.reset(99)

    with pytest.raises(AtomInDosyncProhibited):
        body()


# ---------------------------------------------------------------------------
# Atom is a distinct type from Ref — keeps swap! discoverable
# (per design doc § F1: "Distinct type from Ref to keep swap! discoverable").
# ---------------------------------------------------------------------------

def test_atom_distinct_from_ref():
    db = DB()
    atom = db.atom("entity-1", initial=0)
    ref = db.ref("entity-1")

    # Atoms and Refs are different types — neither isinstance the other.
    assert isinstance(atom, Atom)
    assert isinstance(ref, Ref)
    assert not isinstance(atom, Ref)
    assert not isinstance(ref, Atom)
    # And a fresh Atom, Ref pair to the same eid+db are NOT equal —
    # different types short-circuit eq even though they share an eid.
    assert atom != ref


# ---------------------------------------------------------------------------
# db.atom rejects duplicate eid — fresh-allocation-only invariant.
# ---------------------------------------------------------------------------

def test_db_atom_rejects_duplicate_eid():
    db = DB()
    db.atom("counter", initial=0)

    with pytest.raises(ValueError, match="already has an open"):
        db.atom("counter", initial=0)
