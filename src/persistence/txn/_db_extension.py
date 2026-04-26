"""Attach txn methods (db.ref, db.new_ref, db.dosync) to fact.db.DB.

This is the single inbound coupling-point from txn → fact. The DB class
itself stays in fact/; this module monkey-patches the four new methods
onto it at module-import time.

Phase A ships ``ref`` and ``new_ref``. Phase B adds ``dosync`` and
``dosync_decorator``.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from persistence.txn.errors import RefValueNotImmutable
from persistence.txn.ref import Ref, is_immutable_value

# Per-DB-instance id used to bind Refs to the DB they were constructed
# against. Stored on the DB via setattr; computed lazily on first access.
_DB_ID_ATTR = "_persistence_txn_db_id"


def _get_db_id(db: Any) -> str:
    db_id = getattr(db, _DB_ID_ATTR, None)
    if db_id is None:
        # Use the python id() of the DB object as the basis. This is
        # process-local and stable for the lifetime of the DB; sufficient
        # for ref-branch-mismatch detection within one process.
        db_id = f"db-{id(db):x}"
        # DB is a regular @dataclass and accepts setattr. If a future
        # variant is frozen/slotted this will raise AttributeError; the
        # fix at that point is a WeakKeyDictionary side table.
        setattr(db, _DB_ID_ATTR, db_id)
    return db_id


def _ref(self: Any, eid: str) -> Ref:
    """DB.ref(eid) — handle to existing/future entity by id."""
    if not isinstance(eid, str):
        raise TypeError(f"eid must be str, got {type(eid).__name__}")
    return Ref(eid=eid, db_id=_get_db_id(self))


def _new_ref(self: Any, initial: Optional[Any] = None) -> Ref:
    """DB.new_ref(initial=...) — allocate a fresh UUID4 entity-id.

    The ``initial`` parameter is validated as immutable and then
    DISCARDED in v0.5.0a1 — the Ref returned does NOT carry the
    initial value forward. To set a starting value, follow up with
    ``tx.assoc(ref, value)`` inside a dosync (Phase B).

    The parameter is accepted now so callers can write API-stable code
    against the Phase A surface; a future revision may persist it
    through a side table without breaking signatures.

    Accepted ``initial`` types (when provided): pyrsistent.PMap /
    PVector / PSet, frozen built-in, or frozen dataclass instance.
    """
    if initial is not None and not is_immutable_value(initial):
        raise RefValueNotImmutable(
            f"db.new_ref(initial=...) value must be immutable; got "
            f"{type(initial).__name__!r}. Use persistence.txn.freeze(...) "
            f"or wrap in pyrsistent.pmap/pvector/pset."
        )
    eid = str(uuid.uuid4())  # uuid4; uuid7 not in stdlib until 3.13+  # noqa: wall-clock
    return Ref(eid=eid, db_id=_get_db_id(self))


def _attach_txn_methods(db_cls: type) -> None:
    """Attach the txn DB-level methods to ``db_cls``.

    Re-entrant safe — repeated calls overwrite the attributes with the
    same module-level functions, so the result is unchanged. Test code
    may reset via ``del db_cls.ref`` to force re-attach.
    """
    db_cls.ref = _ref            # type: ignore[attr-defined]
    db_cls.new_ref = _new_ref    # type: ignore[attr-defined]
    # dosync + dosync_decorator are attached in Phase B.


__all__ = ["_attach_txn_methods"]
