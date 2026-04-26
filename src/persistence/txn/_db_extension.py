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
        try:
            setattr(db, _DB_ID_ATTR, db_id)
        except AttributeError:
            # frozen dataclass without slots accepting setattr — fall back
            # to a hash-keyed dict (not implemented for v0.5.0a1 since DB
            # is a regular @dataclass and accepts setattr).
            raise
    return db_id


def _ref(self: Any, eid: str) -> Ref:
    """DB.ref(eid) — handle to existing/future entity by id."""
    if not isinstance(eid, str):
        raise TypeError(f"eid must be str, got {type(eid).__name__}")
    return Ref(eid=eid, db_id=_get_db_id(self))


def _new_ref(self: Any, initial: Optional[Any] = None) -> Ref:
    """DB.new_ref(initial=...) — allocate a fresh uuid7 entity-id.

    If ``initial`` is provided, it must be an immutable value
    (pyrsistent / frozen built-in / frozen dataclass). The substrate
    does NOT eagerly materialize the initial value — that happens on
    first ``tx.assoc(ref, ...)`` inside a dosync. (Phase B wires this.)
    Storing the pending initial on the Ref is intentionally avoided to
    keep Ref a pure handle.
    """
    if initial is not None and not is_immutable_value(initial):
        raise RefValueNotImmutable(
            f"db.new_ref(initial=...) value must be immutable; got "
            f"{type(initial).__name__!r}. Use persistence.txn.freeze(...) "
            f"or wrap in pyrsistent.pmap/pvector/pset."
        )
    eid = str(uuid.uuid4())  # uuid4; uuid7 not in stdlib until 3.13+  # noqa: wall-clock
    ref = Ref(eid=eid, db_id=_get_db_id(self))
    # The initial value is not stored on the Ref. Phase B's
    # _attach_initial_value sentinel may carry it forward via a weak
    # registry if/when needed; for now, callers do:
    #     r = db.new_ref(initial=pmap({...}))
    #     with db.dosync() as tx: tx.assoc(r, pmap({...}))
    # which is the explicit form. We keep the parameter so the API
    # matches the design doc and so a future improvement can persist
    # the initial without breaking callers.
    return ref


def _attach_txn_methods(db_cls: type) -> None:
    """Attach the four DB-level txn methods to ``db_cls``.

    Idempotent — safe to call multiple times. Test code may reset the
    methods via ``del db_cls.ref`` if needed.
    """
    db_cls.ref = _ref            # type: ignore[attr-defined]
    db_cls.new_ref = _new_ref    # type: ignore[attr-defined]
    # dosync + dosync_decorator are attached in Phase B.


__all__ = ["_attach_txn_methods"]
