"""Storage backends for the bitemporal datom log.

The :class:`Store` Protocol is the seam between the pure-data ``DB`` /
``DBView`` layer and whatever backend happens to be under the hood —
in-memory for tests, SQLite for zero-ops single-operator deploys, Postgres
for production.

Per agent1-fact-spec §4 the production layout uses 5 primary indexes
(EAVT/AEVT/AVET/VAET, plus VT-E for bitemporal ranges) and a log-ordered
index for ``since(t)`` replication. Those indexes are created by the SQL
migration in ``migrations/0001_datom_log.sql``; this module just loads that
file into any DB-API-2 connection.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional, Protocol

from persistence.fact.datom import Datom

_MIGRATION_DIR = Path(__file__).with_suffix("").parent / "migrations"


def load_migrations() -> list[tuple[str, str]]:
    """Return ``[(name, sql), ...]`` for every SQL file in ``migrations/``."""
    return sorted(
        (p.name, p.read_text(encoding="utf-8"))
        for p in _MIGRATION_DIR.glob("*.sql")
    )


class Store(Protocol):
    """Minimum contract every backend must satisfy.

    Backends are intentionally dumb. All bitemporal logic (auto-retraction,
    as-of, branch) lives in :class:`~persistence.fact.db.DB` over an iterable
    of datoms; the store's only job is durable append + ordered retrieval.
    """

    def append(self, datoms: Iterable[Datom]) -> None:
        """Append datoms to the log. Must preserve insertion order."""
        ...

    def all_datoms(self) -> Iterator[Datom]:
        """Yield every datom in insertion order. This is the log."""
        ...

    def since(self, tx_time: datetime) -> Iterator[Datom]:
        """Yield datoms whose tx_time is strictly greater than ``tx_time``."""
        ...

    def mark_invalidated(self, tx: int, invalidated_by_tx: int) -> None:
        """Update every datom with ``tx`` == ``tx`` to set invalidated_by.

        Used by the transactor to stamp a superseded cardinality-one datom
        after the new assert lands. The original row's immutability is
        preserved at the level of ``e, a, v, tx_time, op`` — only the
        pointer changes.
        """
        ...

    def next_tx(self) -> int:
        """Return the next monotonic transaction id this Store should use.

        Tx allocation lives on the Store (not on a module-level counter)
        so two Stores backed by different files / databases do not collide,
        and a Store restored from an existing log resumes at ``max(tx)+1``
        instead of overwriting row 1 (ARIS R3 F10).
        """
        ...


# ---------------------------------------------------------------------------
# InMemoryStore — reference backend, fastest path for tests and the CLI demo.
# ---------------------------------------------------------------------------
class InMemoryStore:
    """Simple list-backed reference implementation. Not thread-safe across
    replicas — single-process only. Use SQLite/Postgres for concurrency."""

    def __init__(self) -> None:
        self._log: list[Datom] = []
        self._lock = threading.Lock()

    def append(self, datoms: Iterable[Datom]) -> None:
        with self._lock:
            self._log.extend(datoms)

    def all_datoms(self) -> Iterator[Datom]:
        # Snapshot so the caller iterates a stable list even if append races.
        with self._lock:
            snapshot = list(self._log)
        return iter(snapshot)

    def since(self, tx_time: datetime) -> Iterator[Datom]:
        return (d for d in self.all_datoms() if d.tx_time > tx_time)

    def mark_invalidated(self, tx: int, invalidated_by_tx: int) -> None:
        with self._lock:
            for i, d in enumerate(self._log):
                if d.tx == tx and d.invalidated_by is None and d.op == "assert":
                    # dataclass is frozen → produce a new instance
                    self._log[i] = Datom(
                        e=d.e,
                        a=d.a,
                        v=d.v,
                        tx=d.tx,
                        tx_time=d.tx_time,
                        valid_from=d.valid_from,
                        valid_to=d.valid_to,
                        op=d.op,
                        provenance=d.provenance,
                        invalidated_by=invalidated_by_tx,
                    )

    def next_tx(self) -> int:
        """``max(tx across the log) + 1``; starts at 1 for an empty store."""
        with self._lock:
            if not self._log:
                return 1
            return max(d.tx for d in self._log) + 1


# ---------------------------------------------------------------------------
# SQLiteStore — persistent backend used by the test suite. The schema is the
# same one the Postgres migration produces, so switching backends is just a
# DSN change for any operator willing to run Postgres.
# ---------------------------------------------------------------------------
class SQLiteStore:
    """SQLite-backed datom log. Uses the shared migration SQL."""

    def __init__(self, path: str = ":memory:") -> None:
        self._path = path
        # check_same_thread=False is safe because we serialize via _lock;
        # SQLite itself handles concurrent readers fine.
        self._conn = sqlite3.connect(
            path, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._apply_migrations()

    def _apply_migrations(self) -> None:
        with self._conn:
            for _name, sql in load_migrations():
                self._conn.executescript(sql)

    # ---- Protocol methods ------------------------------------------------
    def append(self, datoms: Iterable[Datom]) -> None:
        rows = [_encode(d) for d in datoms]
        if not rows:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                """
                INSERT INTO datom_log
                  (e, a, v, tx, tx_time, valid_from, valid_to, op,
                   provenance, invalidated_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def all_datoms(self) -> Iterator[Datom]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM datom_log ORDER BY seq ASC"
            )
            rows = cur.fetchall()
        return (_decode(r) for r in rows)

    def since(self, tx_time: datetime) -> Iterator[Datom]:
        t_iso = tx_time.isoformat()
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM datom_log WHERE tx_time > ? ORDER BY seq ASC",
                (t_iso,),
            )
            rows = cur.fetchall()
        return (_decode(r) for r in rows)

    def mark_invalidated(self, tx: int, invalidated_by_tx: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE datom_log
                   SET invalidated_by = ?
                 WHERE tx = ?
                   AND invalidated_by IS NULL
                   AND op = 'assert'
                """,
                (invalidated_by_tx, tx),
            )

    def next_tx(self) -> int:
        """``SELECT COALESCE(MAX(tx), 0) + 1 FROM datom_log`` under lock.

        Reads from the on-disk log so a SQLiteStore reopened against an
        existing file resumes at the correct id, and two stores pointed
        at the same file allocate monotonic ids (ARIS R3 F10).
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT COALESCE(MAX(tx), 0) + 1 FROM datom_log"
            )
            row = cur.fetchone()
        # Result is either a Row (when row_factory=Row) or a plain tuple.
        return int(row[0] if row is not None else 1)

    # ---- Connection management ------------------------------------------
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __del__(self) -> None:  # best-effort cleanup
        try:
            self.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Row <-> Datom encoding. Values are serialized as JSON so SQLite and Postgres
# can both store them in a TEXT column. Timestamps go ISO-8601 for the same
# reason. A thicker codec (BSON, MessagePack) is a drop-in replacement.
# ---------------------------------------------------------------------------
def _encode(d: Datom) -> tuple:
    return (
        d.e,
        d.a,
        json.dumps(d.v, default=str, sort_keys=True),
        d.tx,
        d.tx_time.isoformat(),
        d.valid_from.isoformat(),
        d.valid_to.isoformat() if d.valid_to else None,
        d.op,
        json.dumps(d.provenance, default=str, sort_keys=True),
        d.invalidated_by,
    )


def _decode(row) -> Datom:
    from datetime import datetime as _dt

    return Datom(
        e=row["e"],
        a=row["a"],
        v=json.loads(row["v"]),
        tx=row["tx"],
        tx_time=_dt.fromisoformat(row["tx_time"]),
        valid_from=_dt.fromisoformat(row["valid_from"]),
        valid_to=_dt.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
        op=row["op"],
        provenance=json.loads(row["provenance"]),
        invalidated_by=row["invalidated_by"],
    )


__all__ = ["InMemoryStore", "SQLiteStore", "Store", "load_migrations"]
