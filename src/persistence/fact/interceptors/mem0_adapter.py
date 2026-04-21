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

The interceptor targets the real ``mem0.Memory`` surface (mem0ai 2.x):

    Memory.add(messages, *, user_id=None, agent_id=None, run_id=None,
               metadata=None, infer=True, memory_type=None, prompt=None)
    Memory.update(memory_id, data, metadata=None)

Datom fields (``e``, ``a``, ``v``, ``valid_from``, ``provenance``) are
carried on the mem0 ``metadata`` dict so downstream mem0 projections can
reconstruct the bitemporal context without a separate schema. The fields
are NEVER forwarded as kwargs to ``mem0.Memory.add`` / ``update`` — doing
so raised ``TypeError`` on the real client (ARIS Round 1 R3 F5).
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


# Datom fields that we carry into mem0 ``metadata`` rather than as kwargs.
_DATOM_METADATA_KEYS = ("e", "a", "v", "valid_from", "provenance")

# Exactly the kwargs the real ``mem0.Memory.add`` accepts (mem0ai 2.x, Apr 2026).
_MEM0_ADD_KWARGS = frozenset(
    {
        "messages",
        "user_id",
        "agent_id",
        "run_id",
        "metadata",
        "infer",
        "memory_type",
        "prompt",
    }
)

# Real ``mem0.Memory.update(memory_id, data, metadata=None)``.
_MEM0_UPDATE_KWARGS = frozenset({"data", "metadata"})


def _merge_metadata(
    caller_metadata: Optional[Mapping[str, Any]],
    *,
    e: str,
    a: str,
    v: Any,
    valid_from: datetime,
    provenance: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Merge caller's mem0 metadata with the datom fields.

    Datom fields win on conflict because they are required to reconstruct
    the bitemporal context; caller keys are preserved otherwise.
    """
    md: dict[str, Any] = dict(caller_metadata or {})
    md.update(
        {
            "e": e,
            "a": a,
            "v": v,
            "valid_from": valid_from.isoformat(),
        }
    )
    if provenance is not None:
        md["provenance"] = dict(provenance)
    return md


def _synthesize_messages(v: Any) -> list[dict[str, str]]:
    """If the caller didn't pass ``messages``, derive a minimal one from v.

    mem0 requires a ``messages`` argument; we synthesize a single-turn
    user message so the interceptor can always satisfy the contract even
    when the caller only wants to land a datom.
    """
    return [{"role": "user", "content": str(v)}]


class Mem0Interceptor:
    """Wraps a mem0 client so every mutation lands in the fact log first.

    Args:
        db:         the :class:`DB` that owns the authoritative datom log.
        mem0:       a mem0 ``Memory``-shaped object. The interceptor calls
                    ``mem0.add(messages, *, user_id, agent_id, run_id,
                    metadata, infer, memory_type, prompt)`` and
                    ``mem0.update(memory_id, data, metadata)`` — EXACTLY
                    the real mem0ai 2.x signatures.
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
        **mem0_kwargs: Any,
    ) -> Any:
        """Intercept ``mem0.add`` — datom first, legacy second.

        Datom fields (``e``, ``a``, ``v``, ``valid_from``, ``provenance``)
        are stamped into ``mem0_kwargs['metadata']``; they are never passed
        as kwargs to the underlying ``mem0.Memory.add``.

        Extra keys in ``mem0_kwargs`` that do NOT belong to the real
        ``Memory.add`` signature are forwarded as-is and will raise
        ``TypeError`` from mem0 — surface the error rather than silently
        dropping data.
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

        # Reshape mem0_kwargs: extract metadata (if any), merge datom
        # fields into it, synthesize messages if missing.
        caller_metadata = mem0_kwargs.pop("metadata", None)
        merged_metadata = _merge_metadata(
            caller_metadata,
            e=e,
            a=a,
            v=v,
            valid_from=vf,
            provenance=provenance,
        )
        messages = mem0_kwargs.pop("messages", None)
        if messages is None:
            messages = _synthesize_messages(v)

        # Whatever's left in mem0_kwargs is forwarded verbatim — if the
        # caller included an unknown kwarg, let mem0 complain the same way
        # it would in production (don't silently drop data).
        return self.mem0.add(
            messages,
            metadata=merged_metadata,
            **mem0_kwargs,
        )

    def update(
        self,
        memory_id: str,
        *,
        e: str,
        a: str,
        v: Any,
        valid_from: Optional[datetime] = None,
        provenance: Optional[Mapping[str, Any]] = None,
        **mem0_kwargs: Any,
    ) -> Any:
        """Intercept ``mem0.update`` — asserts the new value (auto-retract
        of the prior cardinality-one assert is handled by ``DB.transact``).

        Real ``mem0.Memory.update`` signature: ``(memory_id, data,
        metadata=None)``. The interceptor derives ``data`` from ``v`` if
        the caller did not pass it explicitly.
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

        caller_metadata = mem0_kwargs.pop("metadata", None)
        merged_metadata = _merge_metadata(
            caller_metadata,
            e=e,
            a=a,
            v=v,
            valid_from=vf,
            provenance=provenance,
        )
        data = mem0_kwargs.pop("data", None)
        if data is None:
            data = str(v)
        # Any remaining mem0_kwargs will TypeError against the real
        # Memory.update; surface the error like add() does.
        return self.mem0.update(
            memory_id,
            data,
            metadata=merged_metadata,
            **mem0_kwargs,
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
