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
from typing import Any, Literal, Optional, TypedDict

Op = Literal["assert", "retract"]


class Provenance(TypedDict, total=False):
    """Typed provenance schema for Datom.provenance (v0.4.0a1).

    All keys are optional (total=False) so existing free-form dict
    callers remain valid at runtime; the TypedDict purely documents
    the conventional schema for static type checkers and code readers.

    Required by convention (enforced at the few sites that read these):
      source                   — origin descriptor (e.g. "test", "audit:bankability-v3")

    Optional named keys (lifted from the formerly-free-form dict):
      tx_time                  — ISO-8601 timestamp; redundant with Datom.tx_time but appears in wire form
      handler_id               — registered effect-handler name (Ferrari/brains pin: model-pluggable)
      canonical_call           — sha256 hex of canonical EDN of {model, prompt, tools, params}
      parent_provenance_hash   — equivalent to audit handler's :prev-hash; walks the causal DAG backwards
      superseded_by_tx         — Phase-1 companion-retract sentinel

    Backend-specific overflow:
      extra                    — dict of any additional keys not lifted into the schema
    """

    source: str
    tx_time: str
    handler_id: Optional[str]
    canonical_call: Optional[str]
    parent_provenance_hash: Optional[str]
    superseded_by_tx: Optional[int]
    extra: dict[str, Any]


#: Set of keys lifted to top-level Provenance fields by `provenance_from_dict`.
#: Adding a key here also requires extending the Provenance TypedDict.
_PROVENANCE_KNOWN_KEYS = frozenset({
    "source",
    "tx_time",
    "handler_id",
    "canonical_call",
    "parent_provenance_hash",
    "superseded_by_tx",
})


def provenance_from_dict(raw: dict) -> Provenance:
    """Coerce a free-form dict to a Provenance TypedDict.

    Known keys (see _PROVENANCE_KNOWN_KEYS) lift to top-level fields;
    everything else lands in ``extra``. Pre-existing ``extra`` keys are
    preserved and merged with newly-uncategorized top-level keys.

    The wire format and canonical hash are unchanged because this only
    rearranges where keys live in the dict — Provenance is a dict at
    runtime, and the canonical form serializes both shapes identically
    (sort_keys=True flattens any structural difference).
    """
    if not raw:
        return {}

    out: Provenance = {}
    extra: dict = dict(raw.get("extra", {}))

    for k, v in raw.items():
        if k == "extra":
            continue
        if k in _PROVENANCE_KNOWN_KEYS:
            out[k] = v   # type: ignore[literal-required]
        else:
            extra[k] = v

    if extra:
        out["extra"] = extra
    return out


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
    provenance: Provenance = field(default_factory=dict)  # type: ignore[assignment]
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
        # ARIS Round 4 W4-wire-identity (closes R3 N2) — the canonical
        # in-memory form of ``a`` and ``provenance["source"]`` is bare
        # (no leading colon). The wire form uniformly prepends ``":"`` in
        # ``datom_to_wire``. Normalising at construction time means:
        #
        #   Datom(a=":x/y") and Datom(a="x/y") produce identical values
        #
        # so ``wire_to_datom ∘ datom_to_wire`` is the identity on every
        # input, not just the bare-string subdomain. The dataclass is
        # frozen + slotted, so we mutate via object.__setattr__.
        #
        # ARIS Round 5 W5-datom-idempotent (closes R3 R4-N3) — use
        # ``lstrip(":")`` rather than ``[1:]`` so double-colon inputs
        # (``"::x/y"``) collapse fully to ``"x/y"`` and canonicalisation
        # is idempotent under repeat construction.
        if isinstance(self.a, str):
            stripped_a = self.a.lstrip(":")
            if stripped_a != self.a:
                object.__setattr__(self, "a", stripped_a)
        if isinstance(self.provenance, dict):
            src = self.provenance.get("source")
            if isinstance(src, str):
                stripped_src = src.lstrip(":")
                if stripped_src != src:
                    # The dict itself is mutable
                    # (``field(default_factory=dict)`` is not frozen,
                    # even in a frozen dataclass), so we mutate in
                    # place. Any alias the caller still holds sees the
                    # canonicalised value — the round-trip invariant
                    # depends on the whole provenance mapping being
                    # canonical.
                    self.provenance["source"] = stripped_src


__all__ = ["Datom", "Op", "Provenance", "provenance_from_dict"]
