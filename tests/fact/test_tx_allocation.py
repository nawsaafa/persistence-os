"""Tx allocation is per-Store, not a module global (ARIS R3 F10).

Before the fix, ``persistence.fact.db._tx_counter`` was a module-level
``itertools.count(1)``. That meant:

- Two ``SQLiteStore`` instances pointed at the same on-disk file would
  race through the SAME module counter — IDs were sequential across the
  two processes only because they shared a process, and would collide
  catastrophically across processes.
- A ``SQLiteStore`` restored from an existing log would start tx allocation
  back at 1, stomping on existing rows (uniqueness violation on next
  mark_invalidated / query-by-tx).
- The pytest conftest had to reset the global between tests to keep IDs
  predictable — the kind of shared-mutable-state coupling ARIS R3 flagged.

The fix: every ``Store`` exposes ``next_tx()``. ``InMemoryStore`` walks
its in-memory log; ``SQLiteStore`` reads ``SELECT COALESCE(MAX(tx), 0) + 1
FROM datom_log``. ``DB.transact`` delegates to ``store.next_tx()``.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from persistence.fact import DB, InMemoryStore, SQLiteStore


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Store.next_tx protocol
# ---------------------------------------------------------------------------


class TestStoreNextTx:
    def test_in_memory_store_starts_at_1(self):
        s = InMemoryStore()
        assert s.next_tx() == 1

    def test_sqlite_store_starts_at_1(self, tmp_path):
        s = SQLiteStore(path=str(tmp_path / "db.sqlite"))
        assert s.next_tx() == 1

    def test_in_memory_next_tx_increments_after_writes(self):
        db = DB(InMemoryStore())
        db = db.transact(
            [{"e": "e1", "a": "a", "v": 1, "valid_from": _dt(2026, 1, 1)}]
        )
        # After writing tx=1, next tx must be 2.
        assert db.store.next_tx() == 2

    def test_sqlite_next_tx_increments_after_writes(self, tmp_path):
        store = SQLiteStore(path=str(tmp_path / "db.sqlite"))
        db = DB(store)
        db = db.transact(
            [{"e": "e1", "a": "a", "v": 1, "valid_from": _dt(2026, 1, 1)}]
        )
        db = db.transact(
            [{"e": "e1", "a": "b", "v": 2, "valid_from": _dt(2026, 1, 2)}]
        )
        # Two transacts each allocate one tx (1 and 2); next is 3.
        assert store.next_tx() == 3


# ---------------------------------------------------------------------------
# No module-level _tx_counter leak
# ---------------------------------------------------------------------------


class TestNoModuleGlobalTxCounter:
    def test_two_in_memory_stores_have_independent_counters(self):
        """Two DB(InMemoryStore()) instances must NOT share the counter.

        Before the fix, the shared ``_tx_counter`` meant the second DB's
        first write got tx=N+1 where N was everything every other DB in
        the process had written. This made restored-from-disk stores
        undebuggable and violated the Store-as-tx-authority contract.
        """
        db_a = DB(InMemoryStore())
        db_b = DB(InMemoryStore())
        db_a = db_a.transact(
            [{"e": "e", "a": "a", "v": 1, "valid_from": _dt(2026, 1, 1)}]
        )
        db_b = db_b.transact(
            [{"e": "e", "a": "a", "v": 2, "valid_from": _dt(2026, 1, 1)}]
        )
        tx_a = next(iter(db_a.log())).tx
        tx_b = next(iter(db_b.log())).tx
        assert tx_a == 1, f"db_a first tx should be 1, got {tx_a}"
        assert tx_b == 1, (
            f"db_b must have its own counter starting at 1, got {tx_b} "
            "(module-level _tx_counter leak)"
        )

    def test_two_sqlite_stores_on_same_file_do_not_collide(self, tmp_path):
        """Two ``SQLiteStore`` instances opened on the same file must
        allocate tx ids that don't collide. The second instance must see
        the first instance's max(tx) on init and continue from there.

        This is the concrete Postgres-operator scenario: multi-worker
        Gunicorn, each worker opens its own connection; they must agree
        on the next tx or the log is corrupt.
        """
        dbfile = tmp_path / "shared.sqlite"
        store1 = SQLiteStore(path=str(dbfile))
        store2 = SQLiteStore(path=str(dbfile))

        db1 = DB(store1)
        db1 = db1.transact(
            [{"e": "e", "a": "a", "v": 1, "valid_from": _dt(2026, 1, 1)}]
        )
        # store2 was opened BEFORE the write above. When store2 allocates
        # its next tx, it must see the write in the shared SQLite file and
        # return 2 (not 1, which would collide).
        assert store2.next_tx() == 2, (
            "SQLiteStore.next_tx must read MAX(tx) from the on-disk log, "
            "not from cached module state"
        )
        db2 = DB(store2)
        db2 = db2.transact(
            [{"e": "e2", "a": "a", "v": 3, "valid_from": _dt(2026, 1, 2)}]
        )
        # The two writes must have distinct tx ids.
        tx1 = next(iter(db1.log())).tx
        tx2_list = [d.tx for d in db2.log() if d.e == "e2"]
        assert tx1 == 1
        assert all(t >= 2 for t in tx2_list), (
            f"store2 reused tx id, got {tx2_list!r} with store1 at tx=1"
        )

    def test_sqlite_round_trip_resumes_counter_correctly(self, tmp_path):
        """Close + reopen a SQLiteStore — the next tx must be max(tx)+1,
        not 1. Before the fix the module counter reset on every process
        start, so a restored store started back at 1 and produced
        duplicate tx ids on its first write.
        """
        dbfile = tmp_path / "roundtrip.sqlite"

        store = SQLiteStore(path=str(dbfile))
        db = DB(store)
        for i in range(5):
            db = db.transact(
                [
                    {
                        "e": f"e{i}",
                        "a": "a",
                        "v": i,
                        "valid_from": _dt(2026, 1, 1 + i),
                    }
                ]
            )
        last_tx = max(d.tx for d in db.log())
        assert last_tx == 5
        store.close()

        # Reopen.
        store2 = SQLiteStore(path=str(dbfile))
        assert store2.next_tx() == last_tx + 1, (
            "reopened SQLiteStore must resume tx allocation from max(tx)+1"
        )
        db2 = DB(store2)
        db2 = db2.transact(
            [{"e": "new", "a": "a", "v": 99, "valid_from": _dt(2026, 2, 1)}]
        )
        new_tx = [d.tx for d in db2.log() if d.e == "new"][0]
        assert new_tx == last_tx + 1, (
            f"first write after reopen got tx={new_tx}, expected {last_tx + 1} "
            "(restored-store counter leak)"
        )

    def test_db_module_has_no_module_level_tx_counter(self):
        """Explicit: the module must not carry a module-global counter.
        Catches regressions where someone reintroduces a cache for perf.
        """
        from persistence.fact import db as db_mod

        assert not hasattr(db_mod, "_tx_counter"), (
            "persistence.fact.db._tx_counter leaked back in; tx allocation "
            "must live on the Store instance"
        )
