"""mem0 interceptor — emits a datom before every legacy mem0 write.

This is the thin wrapper the Memory Palace integration PR will wire up.
Pattern (agent1-fact-spec §7, Phase 1):

    1. Fact store write happens FIRST — the datom becomes durable.
    2. Legacy write happens SECOND — the projection cache is updated.
    3. If step 1 fails, step 2 is SKIPPED and the interceptor raises
       :class:`InterceptorError`. No legacy side-effect has occurred.
    4. If step 1 succeeds but step 2 fails, the datom still lives. An
       operator rebuilds the mem0 projection from the log by running
       ``persistence.fact.projection.rebuild`` over a mem0-aware adapter.

The interceptor is duck-typed on the mem0 client — it only needs
``add(...)`` and ``update(memory_id, ...)`` methods. This means the unit
tests can pass a fake client and CI never depends on mem0 being installed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping, Optional

from persistence.fact.db import DB, _system_clock


# ClockFn alias mirrors the one in persistence.fact.db. Keeping it local
# avoids a hard re-export dependency while still documenting the shape.
ClockFn = Callable[[], datetime]


class InterceptorError(RuntimeError):
    """Raised when the datom emission fails, blocking the legacy write."""


class Mem0Interceptor:
    """Wraps a mem0 client so every mutation lands in the fact log first.

    Args:
        db:         the :class:`DB` that owns the authoritative datom log.
        mem0:       any object exposing ``add(**kw)`` and
                    ``update(memory_id, **kw)``.
        principal:  optional dict stamped into provenance (tenant, agent,
                    user). Per agent3-effect-spec §6, this is what the
                    audit pipeline expects to find on every datom.
        clock:      optional :data:`ClockFn` returning a tz-aware datetime
                    used as the default ``valid_from`` when the caller
                    omits one. Defaults to the DB's clock if available,
                    otherwise the system clock. Replay callers pass a
                    fixed clock so ``valid_from`` is reproducible
                    (ARIS R2 F5).
    """

    def __init__(
        self,
        db: DB,
        mem0: Any,
        principal: Optional[Mapping[str, Any]] = None,
        *,
        clock: Optional[ClockFn] = None,
    ) -> None:
        self.db = db
        self.mem0 = mem0
        self.principal = dict(principal or {})
        # Prefer the explicit arg, then the DB's clock (shared replay seam),
        # then the system clock as the last resort.
        self._clock: ClockFn = clock or getattr(db, "_clock", None) or _system_clock

    # ---- Public API ------------------------------------------------------
    def add(
        self,
        *,
        e: str,
        a: str,
        v: Any,
        valid_from: Optional[datetime] = None,
        provenance: Optional[Mapping[str, Any]] = None,
        **legacy_kwargs: Any,
    ) -> Any:
        """Intercept ``mem0.add`` — datom first, legacy second."""
        vf = valid_from or self._clock()
        try:
            self._emit_datom(
                e=e,
                a=a,
                v=v,
                valid_from=vf,
                op="assert",
                provenance=provenance,
            )
        except Exception as exc:
            raise InterceptorError(
                f"failed to append datom for ({e!r}, {a!r}): {exc}"
            ) from exc
        # Forward to legacy mem0. If it fails, the datom already landed —
        # that is intentional per agent1-fact-spec §7.
        return self.mem0.add(e=e, a=a, v=v, valid_from=vf, **legacy_kwargs)

    def update(
        self,
        memory_id: str,
        *,
        e: str,
        a: str,
        v: Any,
        valid_from: Optional[datetime] = None,
        provenance: Optional[Mapping[str, Any]] = None,
        **legacy_kwargs: Any,
    ) -> Any:
        """Intercept ``mem0.update`` — asserts the new value (auto-retract
        of the prior cardinality-one assert is handled by ``DB.transact``).
        """
        vf = valid_from or self._clock()
        try:
            self._emit_datom(
                e=e,
                a=a,
                v=v,
                valid_from=vf,
                op="assert",
                provenance=provenance,
            )
        except Exception as exc:
            raise InterceptorError(
                f"failed to append datom for ({e!r}, {a!r}): {exc}"
            ) from exc
        return self.mem0.update(
            memory_id, e=e, a=a, v=v, valid_from=vf, **legacy_kwargs
        )

    # ---- Internals -------------------------------------------------------
    def _emit_datom(
        self,
        *,
        e: str,
        a: str,
        v: Any,
        valid_from: datetime,
        op: str,
        provenance: Optional[Mapping[str, Any]],
    ) -> None:
        """Append the datom to the fact log. Raises on failure.

        Separated so tests can monkey-patch this method to drive the
        failure path without stubbing the whole ``DB``. The public
        ``add``/``update`` methods wrap any exception raised here in
        :class:`InterceptorError`.
        """
        prov = {
            **(dict(provenance) if provenance else {}),
            "principal": self.principal,
            "interceptor": "mem0",
        }
        self.db = self.db.transact(
            [
                {
                    "e": e,
                    "a": a,
                    "v": v,
                    "valid_from": valid_from,
                    "op": op,
                }
            ],
            provenance=prov,
        )


__all__ = ["InterceptorError", "Mem0Interceptor"]
