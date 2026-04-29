"""Ref — immutable handle to an entity-id in a specific DB / branch."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, is_dataclass
from decimal import Decimal
from typing import Any

from pyrsistent import PMap, PSet, PVector, pmap, pvector

from persistence.txn.errors import RefValueNotImmutable


# EDN keyword name shape (no leading colon — colon is the keyword sigil,
# stripped at the Datom-store boundary). Per the EDN spec
# (https://github.com/edn-format/edn — Symbols + Keywords sections):
#
#   - A name has at most ONE ``/`` (namespace separator); both segments
#     follow symbol grammar.
#   - A standard segment head is an alpha char or one of
#     ``* ! _ ? $ % & = < >``.
#   - The body chars include those plus digits and ``+ - . # :``.
#   - Special leaders ``- + .`` are permitted only if the next char (if
#     any) is non-digit — this disambiguates the symbol path from the
#     number path (e.g. ``-1`` is a number, ``-foo`` is a symbol).
#
# Used in ``Ref.__post_init__`` to reject invalid ``spec_attr`` values.
# v0.5.2 N8 tightening over v0.5.1 N3 ``[A-Za-z0-9][A-Za-z0-9./_-]*``:
# gains rejections of leading-digit segments, multi-``/`` paths, empty
# segments, and special-leader-then-digit; loosens to admit valid
# EDN-shaped names like ``-foo``, ``foo123``, single-char ``+``/``-``/``.``.
_STANDARD_HEAD = r"[A-Za-z*!_?$%&=<>]"
_BODY = r"[A-Za-z0-9*+!\-_?$%&=<>.#:]"
_SEG = (
    rf"(?:{_STANDARD_HEAD}{_BODY}*"
    rf"|[\-+.](?![0-9]){_BODY}*)"
)
_SPEC_ATTR_PATTERN = re.compile(rf"^{_SEG}(?:/{_SEG})?$")


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
        # attribute name only at commit time. We require an EDN keyword
        # name (see ``_SPEC_ATTR_PATTERN`` above for the full grammar).
        # Raises ``ValueError`` rather than spec-layer SpecError because
        # this is a structural invariant of Ref itself, not of any
        # registered write-spec.
        if not isinstance(self.spec_attr, str) or not _SPEC_ATTR_PATTERN.fullmatch(
            self.spec_attr
        ):
            raise ValueError(
                f"Ref.spec_attr {self.spec_attr!r} must be a valid EDN keyword "
                "name. EDN spec rules: non-empty; no leading ':' (the keyword "
                "sigil is stripped at the Datom-store boundary); no whitespace; "
                "at most one '/' (namespace separator) with non-empty segments "
                "on both sides; each segment starts with an alpha or one of "
                "'*!_?$%&=<>', OR with one of '-+.' followed by a non-digit "
                "(the EDN second-char rule disambiguating symbols from numbers); "
                "remaining chars are alphanumerics or one of '*+-!_?$%&=<>.#:'. "
                "Examples that fail: '0/foo' (leading-digit segment), "
                "'foo/bar/baz' (multi-'/'), '/foo' or 'foo/' (empty segment), "
                "'-1'/'+42'/'.5' (special leader followed by digit)."
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
