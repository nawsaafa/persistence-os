"""Coercion registry for canonical-form serialization (R3-M4, v0.3.0a1).

`Node.id` hashes the canonical JSON form of `_canonical_dict(node)`, but
`json.dumps` only handles dict / list / tuple / str / int / float / bool /
None. Real plan authors want `datetime`, `Decimal`, `UUID`, `bytes`,
`frozenset`, and EDN symbols inside attrs. The coercion registry maps
each non-JSON-native type to a deterministic callable that returns a
JSON-serializable value, applied at id-time only — `node.attrs` stays
faithful to the author's input.

Cross-host determinism contract (§6 of the design doc):
- Defaults are populated at module import time from a frozen table.
- Runtime registration is forbidden in production. The
  ``PERSISTENCE_PLAN_ALLOW_RUNTIME_REGISTRATION`` env var unlocks it
  for test harnesses only.
- Schema-impact changes to defaults bump ``PLAN_CANONICAL_VERSION``
  (see _ast.py), invalidating every persisted Node.id under the old
  version. The migration plan (parallel registries + recompute_ids)
  is deferred to v0.4+ per §9 scope cut.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable
from uuid import UUID

import edn_format

#: Coercion type alias — callable that takes any value and returns a value
#: ``json.dumps`` accepts (str | int | float | bool | None | list | dict).
Coercion = Callable[[Any], Any]


_RUNTIME_REGISTRATION_SENTINEL = "PERSISTENCE_PLAN_ALLOW_RUNTIME_REGISTRATION"


def _runtime_registration_allowed() -> bool:
    """True when the test harness sentinel env var is set to a truthy value."""
    val = os.environ.get(_RUNTIME_REGISTRATION_SENTINEL, "")
    return val.lower() in {"1", "true", "yes", "on"}


def _coerce_datetime(v: datetime) -> str:
    return v.isoformat()


def _coerce_date(v: date) -> str:
    return v.isoformat()


def _coerce_bytes(v: bytes) -> str:
    return v.hex()


def _coerce_decimal(v: Decimal) -> str:
    return str(v)


def _coerce_uuid(v: UUID) -> str:
    return str(v)


def _coerce_frozenset(v: frozenset) -> list:
    """Sort by canonical form to remove insertion-order non-determinism.

    Elements must themselves be coercible — recursion happens at the
    walker layer (see _ast.py::_coerce_value), so this just produces a
    list whose elements will be walked next.
    """
    return sorted(v, key=lambda x: (type(x).__name__, repr(x)))


def _coerce_symbol(v: edn_format.Symbol) -> str:
    """edn_format.Symbol → bare string. Absorbs the v0.1 _edn_to_python
    workaround for symbols like ``->`` in :signature attrs."""
    return str(v)


#: Default registry table — frozen at import time. The order here matters
#: only for documentation; lookup is by exact-type then MRO walk.
_DEFAULT_TABLE: dict[type, Coercion] = {
    datetime: _coerce_datetime,
    # NOTE: datetime is a subclass of date in stdlib, so `date` MUST be
    # consulted only after `datetime` for exact-type lookups. Since exact
    # match is checked first (see lookup_coercion), this ordering is
    # naturally correct without further care.
    date: _coerce_date,
    bytes: _coerce_bytes,
    Decimal: _coerce_decimal,
    UUID: _coerce_uuid,
    frozenset: _coerce_frozenset,
    edn_format.Symbol: _coerce_symbol,
}


# Live registry — starts as a shallow copy of the defaults. Registration
# under the sentinel env var mutates this dict; lookup consults it.
_REGISTRY: dict[type, Coercion] = dict(_DEFAULT_TABLE)


def register_coercion(
    target_type: type,
    fn: Coercion | None = None,
    *,
    replace: bool = False,
) -> Coercion:
    """Register a coercion for ``target_type``.

    Production mode (no ``PERSISTENCE_PLAN_ALLOW_RUNTIME_REGISTRATION`` env
    var) raises ``RuntimeError``. The static-registry contract guarantees
    cross-host determinism: two hosts that compute ``Node.id`` for the same
    logical tree must agree on the registry, and the only way to guarantee
    that is to forbid runtime mutation.

    Re-registration of an already-registered type raises ``ValueError``
    unless ``replace=True`` (test-only).

    Usable as a decorator::

        @register_coercion(MyType)
        def _coerce_my_type(v: MyType) -> str:
            return v.canonical_form()

    Or as a function call::

        register_coercion(MyType, lambda v: v.canonical_form())
    """
    if not _runtime_registration_allowed():
        raise RuntimeError(
            f"register_coercion is forbidden in production mode. Set "
            f"{_RUNTIME_REGISTRATION_SENTINEL}=1 to enable (test harness only). "
            f"Production registries must use the static defaults shipped in "
            f"persistence.plan._coerce; see PLAN_CANONICAL_VERSION."
        )

    if fn is None:
        # Decorator form: register_coercion(MyType) → returns decorator
        def _decorator(actual_fn: Coercion) -> Coercion:
            register_coercion(target_type, actual_fn, replace=replace)
            return actual_fn

        return _decorator  # type: ignore[return-value]

    if target_type in _REGISTRY and not replace:
        raise ValueError(
            f"register_coercion: type {target_type.__name__} is already "
            f"registered. Pass replace=True to override (test-only)."
        )
    _REGISTRY[target_type] = fn
    return fn


def unregister_coercion(target_type: type) -> None:
    """Remove a coercion. Test-only — raises in production mode.

    Removing a default coercion (datetime, bytes, etc.) is permitted
    under the sentinel because tests need to verify strict-error
    behavior on unregistered types. Production paths can rely on the
    sentinel being absent.
    """
    if not _runtime_registration_allowed():
        raise RuntimeError(
            f"unregister_coercion is forbidden in production mode. Set "
            f"{_RUNTIME_REGISTRATION_SENTINEL}=1 (test harness only)."
        )
    _REGISTRY.pop(target_type, None)


def lookup_coercion(target_type: type) -> Coercion | None:
    """Look up the coercion for ``target_type``.

    Exact-type match first; on miss, walks the MRO so subclasses inherit
    a base class's coercion. Returns ``None`` if no match — the walker
    raises ``TypeError`` at id-time, not here.
    """
    fn = _REGISTRY.get(target_type)
    if fn is not None:
        return fn
    for base in target_type.__mro__[1:]:
        fn = _REGISTRY.get(base)
        if fn is not None:
            return fn
    return None


__all__ = [
    "Coercion",
    "lookup_coercion",
    "register_coercion",
    "unregister_coercion",
]
