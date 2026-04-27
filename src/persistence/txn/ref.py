"""Ref — immutable handle to an entity-id in a specific DB / branch."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, is_dataclass
from decimal import Decimal
from typing import Any

from pyrsistent import PMap, PSet, PVector, pmap, pvector

from persistence.txn.errors import RefValueNotImmutable


# Valid EDN keyword name shape (no leading colon — colon is the keyword
# sigil, stripped at the Datom-store boundary). Matches alphanum + the
# punctuation used in namespaced keyword names: ``./_-``. Used in
# ``Ref.__post_init__`` to reject invalid ``spec_attr`` values.
_SPEC_ATTR_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9./_-]*")


@dataclass(frozen=True, slots=True)
class Ref:
    """Immutable reference to an entity in a DB or branched DB.

    Constructed via :meth:`persistence.fact.db.DB.ref` or
    :meth:`persistence.fact.db.DB.new_ref`. The ``db_id`` field pins the
    ref to the DB instance; using a Ref obtained from one DB inside a
    ``dosync`` against another DB raises ``RefBranchMismatch``.

    Equality and hashing are over ``(eid, db_id)`` — two Refs to the same
    entity-id in the same DB compare equal regardless of construction
    site. ``spec_attr`` is deliberately excluded from eq/hash via
    ``field(compare=False)``: it labels which write-spec applies and
    which entity-attribute name the value lives under, but two refs
    naming the same entity in the same DB are the *same identity* even
    if one was constructed with a custom ``spec_attr``.

    ``spec_attr`` defaults to ``"value"``, preserving v0.5.0a1 behavior
    bit-for-bit for unannotated callers (the global ``WRITE_ATTR``).
    """

    eid: str
    db_id: str
    spec_attr: str = field(default="value", compare=False)

    def __post_init__(self) -> None:
        # Validate ``spec_attr`` shape eagerly: the Datom store enforces
        # only the leading-colon-strip rule on its ``a`` field, so without
        # this guard a malformed ``spec_attr`` would land as a wire-form
        # attribute name only at commit time. EDN keyword names are
        # alphanum + ``./_-``. Reject leading colon, whitespace, empty
        # string. Raises ``ValueError`` rather than spec-layer SpecError
        # because this is a structural invariant of Ref itself, not of
        # any registered write-spec.
        if not isinstance(self.spec_attr, str) or not _SPEC_ATTR_PATTERN.fullmatch(
            self.spec_attr
        ):
            raise ValueError(
                f"Ref.spec_attr {self.spec_attr!r} must be a valid EDN keyword "
                "name (alphanum + ./_-, no leading colon, no whitespace, non-empty)"
            )

    def __repr__(self) -> str:
        if self.spec_attr == "value":
            return f"Ref({self.eid!r}, db={self.db_id!r})"
        return f"Ref({self.eid!r}, db={self.db_id!r}, spec_attr={self.spec_attr!r})"


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
    - frozen dataclass instances (``@dataclass(frozen=True)``); only
      attribute reassignment is blocked — field values are NOT
      recursively checked, so callers must ensure each field holds an
      immutable value.

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

    - ``dict`` → ``PMap`` (recursively freezing values; keys are passed
      through unchanged because dict keys must already be hashable)
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
