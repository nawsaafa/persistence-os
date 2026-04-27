"""MVCC conflict detection over the bitemporal datom log.

The single primitive: ``any_datoms_since(db, t_start, entity_ids)``
returns True iff any datom with ``tx_time > t_start`` touches any of
the given entity-ids. Used by the dosync commit gate to detect that a
ref the body read or wrote was written by another transaction between
the body's snapshot and its commit attempt.

Implementation note: walks ``db.store.since(t_start)`` and filters by
entity-id membership. On SQLiteStore this hits the
``idx_datom_log_txtime`` index (see fact/migrations/0001_datom_log.sql:58)
and is O(K log N) where K is the number of datoms since t_start. On
InMemoryStore this is O(N) full scan — acceptable for tests but a hot
path concern for production hot paths (see design doc § 13).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable


def any_datoms_since(
    db: Any,
    t_start: datetime,
    entity_ids: Iterable[str],
) -> bool:
    """True iff any datom touches an entity in ``entity_ids`` after ``t_start``.

    Empty ``entity_ids`` short-circuits to False. The set conversion
    handles iterables (set, list, tuple).
    """
    eids = set(entity_ids)
    if not eids:
        return False
    for d in db.store.since(t_start):
        if d.e in eids:
            return True
    return False


__all__ = ["any_datoms_since"]
