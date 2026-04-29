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


def test_db_atom_concurrent_allocation_linearises():
    """v0.5.2 R2 W1 (MAJOR-1 closure): smoke-test — N threads racing
    on ``db.atom(eid, ...)`` for the SAME eid must produce exactly
    one open ``"value"`` assert end-state, regardless of timing.

    The MAJOR-1 fix wraps the pre-existence scan AND the initial
    transact under one ``store._lock`` window so concurrent
    allocators linearise. Without the lock, multiple threads can
    pass the guard before either writes and silently allocate two
    "fresh" atoms over the same eid (final count of ``"value"``
    asserts > 1, both handles compare equal).

    Note: on CPython, the GIL often makes the unlocked race
    win-by-default (the log()-walk + transact pair is short enough
    bytecode-wise that no other thread interleaves). The strongest
    cross-platform invariant we assert is the post-fix end-state:
    exactly one open assert per eid. This is a lock-spanning
    *property* test, not a deterministic bug reproduction —
    free-threaded CPython, no-GIL, or busier hosts may surface the
    race more aggressively, and the spanning lock is the structural
    fix that protects all of them.
    """
    import threading

    n_threads = 8
    iterations = 20
    bug_total_open_asserts = 0
    bug_total_winners = 0
    for i in range(iterations):
        db = DB()
        eid = f"shared-counter-{i}"
        results: list = []
        errors: list = []
        start = threading.Event()

        def w(tag: int):
            start.wait()
            try:
                a = db.atom(eid, initial=tag * 100)
                results.append((tag, a))
            except ValueError:
                errors.append(tag)

        threads = [threading.Thread(target=w, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join(timeout=10)

        # Winners + losers must sum to N: every thread either committed
        # or raised ValueError; no thread silently dropped.
        assert len(results) + len(errors) == n_threads, (
            f"iter {i}: results={len(results)} + errors={len(errors)}"
            f" != n_threads={n_threads}"
        )

        # v0.5.2 R2 W1 round-2 nit closure: pin the contract literally —
        # exactly ONE winner, the rest raise ``ValueError``. The
        # end-state ``len(opens) == 1`` invariant below already implied
        # this (since each successful db.atom emits an open assert),
        # but the explicit count assertion is what the docstring
        # promises and surfaces a regression earlier if a future patch
        # breaks the "exactly one wins" property without breaking the
        # open-assert count (e.g. a hypothetical retraction-then-write
        # path).
        assert len(results) == 1, (
            f"iter {i}: expected exactly 1 winner, got {len(results)}"
        )
        assert len(errors) == n_threads - 1, (
            f"iter {i}: expected {n_threads - 1} ValueError losers,"
            f" got {len(errors)}"
        )

        # End-state: exactly one open ``value`` assert. This is the
        # strongest invariant and what MAJOR-1 actually breaks.
        opens = [
            d
            for d in db.log()
            if d.e == eid
            and d.a == "value"
            and d.op == "assert"
            and d.invalidated_by is None
            and d.valid_to is None
        ]
        assert len(opens) == 1, (
            f"iter {i}: expected 1 open value assert on {eid},"
            f" got {len(opens)} (race fired — fix is missing)"
        )
        bug_total_open_asserts += len(opens)
        bug_total_winners += len(results)

    # Defensive aggregate sanity (in case any single-iter assertion
    # would have a `len(opens)==1` accidental pass with multiple
    # winners — should not happen because we assert per-iter, but
    # keeps the cumulative invariant cheap to read).
    assert bug_total_open_asserts == iterations
    assert bug_total_winners >= iterations  # ≥1 winner per iter
