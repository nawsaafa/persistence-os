"""Ref — immutable handle to an entity-id in a specific DB / branch."""
from __future__ import annotations

from dataclasses import dataclass, is_dataclass
from decimal import Decimal
from typing import Any

from pyrsistent import PMap, PSet, PVector, pmap, pvector

from persistence.txn.errors import RefValueNotImmutable


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


# Built-in immutable types accepted directly as ref values.
_IMMUTABLE_BUILTINS = (
    str, int, bool, float, Decimal, bytes,
    frozenset, tuple,
    type(None),
    PMap, PSet, PVector,
)


def is_immutable_value(value: Any) -> bool:
    """True iff ``value`` is acceptable as a ref value.

    Accepted:
    - pyrsistent.PMap, PVector, PSet
    - frozenset, tuple, str, int, bool, float, Decimal, bytes, None
    - frozen dataclass instances (``@dataclass(frozen=True)``)

    Rejected:
    - dict, list, set
    - mutable dataclass instances
    - arbitrary objects with __dict__
    """
    if isinstance(value, _IMMUTABLE_BUILTINS):
        return True
    if is_dataclass(value) and not isinstance(value, type):
        params = getattr(type(value), "__dataclass_params__", None)
        if params is not None and getattr(params, "frozen", False):
            return True
    return False


def freeze(value: Any) -> Any:
    """Recursively convert mutable collections to pyrsistent equivalents.

    - ``dict`` → ``PMap`` (recursively freezing values)
    - ``list`` → ``PVector`` (recursively freezing items)
    - already-immutable values pass through unchanged
    - ``set`` → raises (use ``frozenset`` or ``pset`` explicitly to avoid
      ambiguity with ``PSet`` ordering)

    Use this at the boundary where you receive raw user input and need
    to put it into a ref.
    """
    if is_immutable_value(value):
        return value
    if isinstance(value, dict):
        return pmap({k: freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return pvector(freeze(v) for v in value)
    if isinstance(value, set):
        raise RefValueNotImmutable(
            "freeze() refuses to coerce 'set' (ambiguous ordering); "
            "use frozenset(...) or pyrsistent.pset(...) explicitly"
        )
    raise RefValueNotImmutable(
        f"cannot freeze value of type {type(value).__name__!r}; "
        f"ref values must be immutable (pyrsistent.PMap/PVector/PSet, "
        f"frozenset, tuple, str, int, bool, float, Decimal, bytes, None, "
        f"or @dataclass(frozen=True))"
    )


__all__ = ["Ref", "freeze", "is_immutable_value"]
