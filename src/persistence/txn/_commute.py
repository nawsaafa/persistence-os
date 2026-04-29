"""Commutative writes + curated registry — Module 5 § F3.

``tx.commute(ref, fn_id, *args)`` is Clojure's
``LockingTransaction.doCommute`` (commutative-writes-without-conflict
primitive). The two-phase semantic:

  1. **At body call:** look up ``fn_id`` in the curated registry, apply
     against the optimistic committed-or-locally-written value, return
     the new value.
  2. **At commit (inside writer-lock):** re-read the ref's latest
     committed value, re-apply ``fn``, write the result. The body-eager
     value is discarded; only the commit-time value reaches the log.

Soundness depends on ``fn`` being commutative: ``f(f(v, a), b) ==
f(f(v, b), a)``. The substrate does NOT verify this — the registry is
curated to functions known to be commutative. User-defined commute fns
live behind :func:`register_commute` which raises in production mode
and is unlocked only by the
``PERSISTENCE_TXN_ALLOW_RUNTIME_REGISTRATION`` sentinel env var (mirrors
``register_coercion`` at :mod:`persistence.plan._coerce`). This keeps
cross-host determinism: two hosts replaying a ``:commute`` log entry
must agree on the fn lookup, and the only way to guarantee that is to
forbid runtime mutation.

Curated commute fns (initial cut):

============================== ===========================================
``fn_id``                       Commutativity
============================== ===========================================
``"inc-by"``                    Unconditionally commutative.
``"sum-into"``                  Unconditionally commutative.
``"set-union"``                 Unconditionally commutative.
``"dict-merge-shallow"``        Commutative ONLY on disjoint key sets.
                                On overlap, deterministic last-write-wins
                                per commit-order; caller-aware.
============================== ===========================================

Why ``dict-merge-shallow`` ships despite the caveat: dropping it would
force callers to fall back to ``tx.alter`` (which forces retries under
any concurrent write), defeating the whole point of ``commute``. The
disjoint-key case is the dominant real workload (e.g., signal-id
aggregation from N independent producers into a shared map). The
non-disjoint case is opt-in and explicitly documented here.

See ``docs/plans/2026-04-29-v0.5.2-clojure-parity-design.md`` § F3.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from pyrsistent import PMap, pmap


# ---------------------------------------------------------------------------
# Sentinel — exactly mirrors ``persistence.plan._coerce._RUNTIME_REGISTRATION_SENTINEL``.
# ---------------------------------------------------------------------------
_RUNTIME_REGISTRATION_SENTINEL = "PERSISTENCE_TXN_ALLOW_RUNTIME_REGISTRATION"


def _runtime_registration_allowed() -> bool:
    """True when the test harness sentinel env var is set to a truthy value."""
    val = os.environ.get(_RUNTIME_REGISTRATION_SENTINEL, "")
    return val.strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Curated commute fns. Three are unconditionally commutative; one ships
# with documented caveat.
# ---------------------------------------------------------------------------
def _inc_by(v: Any, n: int) -> int:
    """Increment ``v`` by ``n``. ``v is None`` coerces to 0.

    Unconditionally commutative: addition over ints is commutative.
    """
    return (v if v is not None else 0) + n


def _sum_into(v: Any, n: int) -> int:
    """Add ``n`` into ``v``. Same semantics as ``inc-by``; named
    distinctly so callers can express intent (running sum vs counter).

    Unconditionally commutative.
    """
    return (v if v is not None else 0) + n


def _set_union(v: Any, items: Any) -> frozenset:
    """Union ``items`` into the frozenset ``v``. ``v is None`` coerces to
    an empty frozenset.

    Unconditionally commutative: set union is commutative AND idempotent
    on overlapping inputs.
    """
    base = v if v is not None else frozenset()
    return frozenset(base) | frozenset(items)


def _dict_merge_shallow(v: Any, m: Any) -> PMap:
    """Shallow PMap merge.

    NOTE: commutative ONLY on disjoint key sets. On overlap, the result
    is deterministic last-write-wins per commit-order — callers must
    either guarantee disjoint keys OR explicitly accept the LWW
    commit-order resolution. Implemented as ``v.update(m)``.

    The ``v is None`` branch coerces to ``pmap()``. ``m`` may be a dict
    or a PMap; any other shape will fail the ``pmap(...)`` coercion at
    call time.
    """
    base = v if v is not None else pmap()
    if not isinstance(base, PMap):
        base = pmap(base)
    return base.update(pmap(m))


_DEFAULT_COMMUTE_TABLE: dict[str, Callable] = {
    "inc-by": _inc_by,
    "sum-into": _sum_into,
    "set-union": _set_union,
    "dict-merge-shallow": _dict_merge_shallow,
}


# Mutable copy seeded from defaults; gated by the sentinel env var.
_REGISTRY: dict[str, Callable] = dict(_DEFAULT_COMMUTE_TABLE)


def register_commute(
    fn_id: str,
    fn: Callable | None = None,
    *,
    replace: bool = False,
) -> Callable:
    """Register a commute fn under ``fn_id``.

    Production mode (no ``PERSISTENCE_TXN_ALLOW_RUNTIME_REGISTRATION``
    env var) raises ``RuntimeError``. The static-registry contract
    guarantees cross-host determinism: two hosts replaying a
    ``:commute`` log entry must agree on the fn lookup, and the only
    way to guarantee that is to forbid runtime mutation.

    Re-registration of an already-registered ``fn_id`` raises
    ``ValueError`` unless ``replace=True`` (test-only).

    Usable as a decorator::

        @register_commute("my-fn")
        def my_fn(v, x):
            ...

    Or as a function call::

        register_commute("my-fn", lambda v, x: ...)

    Soundness reminder: the substrate does NOT verify that the
    registered fn is actually commutative. Caller is responsible for
    ensuring ``f(f(v, a), b) == f(f(v, b), a)`` for all ``v, a, b``.
    See the ``"dict-merge-shallow"`` registry comment for the LWW
    caveat applied to a near-commutative-but-not-quite case.
    """
    if not _runtime_registration_allowed():
        raise RuntimeError(
            f"register_commute is forbidden in production mode. Set "
            f"{_RUNTIME_REGISTRATION_SENTINEL}=1 to enable (test harness "
            f"only). Production registries must use the static defaults "
            f"shipped in persistence.txn._commute."
        )

    if fn is None:
        # Decorator form: register_commute("my-fn") → returns decorator
        def _decorator(actual_fn: Callable) -> Callable:
            register_commute(fn_id, actual_fn, replace=replace)
            return actual_fn

        return _decorator  # type: ignore[return-value]

    if fn_id in _REGISTRY and not replace:
        raise ValueError(
            f"register_commute: fn_id {fn_id!r} is already registered. "
            f"Pass replace=True to override (test-only)."
        )
    _REGISTRY[fn_id] = fn
    return fn


def unregister_commute(fn_id: str) -> None:
    """Remove a commute fn. Test-only — raises in production mode.

    Removing a default fn (``inc-by``, ``sum-into``, ``set-union``,
    ``dict-merge-shallow``) is permitted under the sentinel because
    tests need to verify strict-error behavior on unregistered fn_ids.
    Production paths can rely on the sentinel being absent.
    """
    if not _runtime_registration_allowed():
        raise RuntimeError(
            f"unregister_commute is forbidden in production mode. Set "
            f"{_RUNTIME_REGISTRATION_SENTINEL}=1 (test harness only)."
        )
    _REGISTRY.pop(fn_id, None)


def lookup_commute(fn_id: str) -> Callable | None:
    """Look up the commute fn for ``fn_id``. Returns ``None`` on miss.

    The caller (``Transaction.commute`` and ``_build_commute_facts``)
    raises a structured error on miss; this lookup is exact-match only,
    no MRO walk (the registry is keyed by string ids, not types).
    """
    return _REGISTRY.get(fn_id)


__all__ = [
    "lookup_commute",
    "register_commute",
    "unregister_commute",
]
