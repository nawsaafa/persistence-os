"""Ref — immutable handle to an entity-id in a specific DB / branch."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Ref:
    """Immutable reference to an entity in a DB or branched DB.

    Constructed via :meth:`persistence.fact.db.DB.ref` or
    :meth:`persistence.fact.db.DB.new_ref`. The ``db_id`` field pins the
    ref to the DB instance; using a Ref obtained from one DB inside a
    ``dosync`` against another DB raises ``RefBranchMismatch``.

    Equality and hashing are over ``(eid, db_id)`` — two Refs to the same
    entity-id in the same DB compare equal regardless of construction
    site.
    """

    eid: str
    db_id: str

    def __repr__(self) -> str:
        return f"Ref({self.eid!r}, db={self.db_id!r})"


__all__ = ["Ref"]
