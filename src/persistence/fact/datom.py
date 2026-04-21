"""The 8-tuple Datom — the atomic unit of the bitemporal fact store.

Per agent1-fact-spec §1 and paper §4.1:

    d = ⟨e, a, v, τ, τ_sys, ν_from, ν_to, ω⟩

plus a provenance record π and an optional `invalidated_by` pointer that links
a superseded datom to the transaction that superseded it.

Design decisions:

- Frozen dataclass: datoms are values, never mutated. Retraction produces a
  NEW datom whose `op` field is `"retract"`; the original assert stays in the
  log.
- `op` is a plain string rather than an Enum to keep EDN round-tripping cheap
  and to match the wire shape in `agent1-fact-spec.md`.
- Timestamps are required to be timezone-aware. A bitemporal store with naive
  datetimes is a bug factory (see paper §4.1 comment on tx-time vs valid-time).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional

Op = Literal["assert", "retract"]


@dataclass(frozen=True, slots=True)
class Datom:
    """An immutable 8-tuple datom with provenance and invalidation pointer.

    Fields (in the order named by the paper):
        e            — entity id (uuid string or stable identifier)
        a            — attribute (namespaced string, e.g. "project/wacc")
        v            — value (any EDN/JSON-serializable Python value)
        tx           — monotonic transaction id
        tx_time      — τ_sys: when the system learned the fact
        valid_from   — ν_from: when the fact became true in the world
        valid_to     — ν_to: when the fact stopped being true (None = open)
        op           — ω: "assert" or "retract"
        provenance   — π: source, model, prompt-hash, confidence, signature...
        invalidated_by — tx id of the superseding transaction, or None
    """

    e: str
    a: str
    v: Any
    tx: int
    tx_time: datetime
    valid_from: datetime
    valid_to: Optional[datetime]
    op: Op
    provenance: dict = field(default_factory=dict)
    invalidated_by: Optional[int] = None

    def __post_init__(self) -> None:
        if self.op not in ("assert", "retract"):
            raise ValueError(
                f"Datom.op must be 'assert' or 'retract', got {self.op!r}"
            )
        for name, ts in (("tx_time", self.tx_time), ("valid_from", self.valid_from)):
            if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
                raise ValueError(
                    f"Datom.{name} must be timezone-aware, got naive datetime {ts!r}"
                )
        if self.valid_to is not None:
            if self.valid_to.tzinfo is None or self.valid_to.tzinfo.utcoffset(self.valid_to) is None:
                raise ValueError(
                    f"Datom.valid_to must be timezone-aware, got naive datetime {self.valid_to!r}"
                )


__all__ = ["Datom", "Op"]
