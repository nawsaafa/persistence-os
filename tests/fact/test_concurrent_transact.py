"""Tx allocation + append must be atomic under concurrent writers
(ARIS Round 3 polish — P-concurrency).

The original ``next_tx()`` + ``append()`` split is a TOCTOU race: two
threads can both read ``MAX(tx) = 0`` before either inserts, then both
insert with tx=1. The GIL makes this hard to reproduce under light load
(the SQLite call releases it during I/O, but the window is small). Under
a ``threading.Barrier`` the race is reliable.

The fix replaces the split with a single atomic method
``allocate_and_append(datoms)`` that, on the SQLite backend, runs under
``BEGIN IMMEDIATE`` — the writer lock is grabbed on the first statement,
so the ``SELECT COALESCE(MAX(tx),0)+1`` and the ``INSERT`` are serialized.

The test below is written before the fix lands and is expected to FAIL
on the current implementation (collision detected). It should pass
after ``allocate_and_append`` is introduced.
"""

from __future__ import annotations

import sqlite3
import threading
from collections import Counter
from datetime import datetime, timezone

import pytest

from persistence.fact import SQLiteStore, InMemoryStore
from persistence.fact.datom import Datom


def _make_datom(e: str, tx_time: datetime) -> Datom:
    """Build a minimal assert datom with ``tx=-1`` (caller overrides)."""
    return Datom(
        e=e,
        a=":test/value",
        v=e,
        tx=-1,  # placeholder, replaced by allocate_and_append
        tx_time=tx_time,
        valid_from=tx_time,
        valid_to=None,
        op="assert",
        provenance={},
    )


@pytest.mark.parametrize("n_threads,per_thread", [(16, 50)])
def test_sqlite_store_allocate_and_append_no_tx_collisions(
    n_threads: int, per_thread: int, tmp_path
):
    """16 threads × 50 transacts each, all released via a single Barrier.

    Assertions:
    - Every tx id in the final log is unique.
    - Total datoms = n_threads * per_thread.
    - max(tx) == n_threads * per_thread.
    """
    dbfile = tmp_path / "concurrent.sqlite"
    # Open ONE SQLiteStore and share it across threads. check_same_thread=False
    # is already set inside SQLiteStore.__init__, so this exercises the
    # in-process multi-thread race the fix targets.
    store = SQLiteStore(path=str(dbfile))

    barrier = threading.Barrier(n_threads)
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def worker(thread_idx: int) -> None:
        try:
            barrier.wait(timeout=10)
            for i in range(per_thread):
                t = datetime(2026, 1, 1, tzinfo=timezone.utc)
                d = _make_datom(f"t{thread_idx}-i{i}", t)
                # This is the method under test. It must allocate a unique
                # tx id and append in a single atomic transaction.
                store.allocate_and_append([d])
        except BaseException as exc:  # capture & re-raise on main thread
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"worker errors: {errors!r}"

    # Validate the log.
    all_datoms = list(store.all_datoms())
    total = n_threads * per_thread
    assert len(all_datoms) == total, (
        f"expected {total} datoms, got {len(all_datoms)}"
    )

    tx_counts = Counter(d.tx for d in all_datoms)
    duplicates = {tx: c for tx, c in tx_counts.items() if c > 1}
    assert not duplicates, f"tx collisions detected: {duplicates!r}"

    max_tx = max(d.tx for d in all_datoms)
    assert max_tx == total, (
        f"expected max(tx)={total}, got {max_tx} "
        f"(unique txs: {len(tx_counts)})"
    )

    store.close()


def test_in_memory_store_allocate_and_append_no_tx_collisions():
    """InMemoryStore symmetry: must provide the same atomic API."""
    store = InMemoryStore()
    n_threads = 8
    per_thread = 25
    barrier = threading.Barrier(n_threads)

    def worker(thread_idx: int) -> None:
        barrier.wait(timeout=10)
        for i in range(per_thread):
            t = datetime(2026, 1, 1, tzinfo=timezone.utc)
            d = _make_datom(f"t{thread_idx}-i{i}", t)
            store.allocate_and_append([d])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    all_datoms = list(store.all_datoms())
    total = n_threads * per_thread
    assert len(all_datoms) == total

    tx_counts = Counter(d.tx for d in all_datoms)
    duplicates = {tx: c for tx, c in tx_counts.items() if c > 1}
    assert not duplicates, f"tx collisions detected: {duplicates!r}"
    assert max(d.tx for d in all_datoms) == total


def test_allocate_and_append_returns_datoms_with_allocated_tx(tmp_path):
    """The returned datoms must have the tx id assigned, so callers
    that need to build companion rows (retracts, invalidations) can use
    the allocated id without a second query.
    """
    store = SQLiteStore(path=str(tmp_path / "db.sqlite"))
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    d = _make_datom("e1", t)
    out = store.allocate_and_append([d])
    assert len(out) == 1
    assert out[0].tx == 1
    # And the stored datom matches.
    stored = list(store.all_datoms())
    assert len(stored) == 1
    assert stored[0].tx == 1
    store.close()


def test_allocate_and_append_empty_is_noop(tmp_path):
    """Passing an empty iterable must be a no-op (no tx burned)."""
    store = SQLiteStore(path=str(tmp_path / "db.sqlite"))
    out = store.allocate_and_append([])
    assert out == []
    assert list(store.all_datoms()) == []
    # Next allocation must still start at 1.
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    d = _make_datom("e", t)
    out2 = store.allocate_and_append([d])
    assert out2[0].tx == 1
    store.close()
