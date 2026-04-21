"""DB + DBView — the functional query surface over the datom log.

See ``docs/agent1-fact-spec.md`` §2 for the intended API and §8 for the
reference 76-line prototype. This module is that prototype evolved to (a)
sit on top of a pluggable :class:`~persistence.fact.store.Store`, and (b)
enforce the invariants the paper §4.1 formalizes:

    asOf(D, t)      = {d ∈ D | τ_sys(d) ≤ t}
    validAsOf(D, t) = {d ∈ D | ω(d) = assert ∧ ν_from ≤ t < ν_to}
    history(D, e)   = {d ∈ D | entity(d) = e}, sorted by τ
    branch(D, t, Δ) = asOf(D, t) ∪ Δ

The DB object is itself a *value*: every mutating operation returns a new
DB wrapping the same (or a branched) store, so passing a DB around never
causes spooky action at a distance.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Optional

from persistence.fact.datom import Datom
from persistence.fact.store import InMemoryStore, Store

# Transaction ids are allocated by the Store (see Store.next_tx), not by a
# module-level counter. Two InMemoryStore instances each get their own id
# sequence starting at 1; a SQLiteStore reopened against an existing file
# resumes at ``max(tx) + 1`` instead of stomping on row 1 (ARIS R3 F10).


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hash_fact(fact: dict) -> str:
    """Stable prompt-hash-style digest — first 16 hex chars of sha256 over
    the canonicalized fact. Matches the shape the paper §4.1 provenance
    record expects for ``prompt-hash``."""
    blob = json.dumps(fact, default=str, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


@dataclass
class DB:
    """An append-only datom log, read through the bitemporal query API.

    A DB is a thin functional wrapper over a Store. Methods that "modify"
    the DB return a new DB bound to either the same store (transact) or a
    fresh branch store (branch). Callers can always hold a reference to an
    earlier DB value and re-query it.
    """

    store: Store

    # ---- Construction ----------------------------------------------------
    def __init__(self, store: Optional[Store] = None) -> None:
        # Default to in-memory so ``DB()`` is usable from a notebook.
        self.store = store if store is not None else InMemoryStore()

    # ---- Writes ----------------------------------------------------------
    def transact(
        self,
        facts: list[dict],
        provenance: Optional[dict] = None,
    ) -> "DB":
        """Append a transaction. Cardinality-one auto-retraction built in.

        Each fact dict must have ``e``, ``a``, ``v``, ``valid_from`` and may
        have ``valid_to``, ``op`` (default ``"assert"``). The supplied
        ``provenance`` merges into each datom's provenance, with a per-fact
        ``prompt_hash`` added automatically.

        Returns a NEW DB bound to the same store, so callers can pipe
        ``db = db.transact(...)`` the way the spec prototype does.
        """
        if not facts:
            return self

        prov_base: dict = provenance or {}
        tx = self.store.next_tx()
        now = _now_utc()
        new_datoms: list[Datom] = []
        invalidations: list[tuple[int, int]] = []  # (old_tx, new_tx=tx)

        # Snapshot the log once so auto-retraction sees a stable view.
        current_log = list(self.store.all_datoms())

        for fact in facts:
            op = fact.get("op", "assert")
            vf = _coerce_dt(fact.get("valid_from", now))
            vt = _coerce_dt(fact.get("valid_to")) if fact.get("valid_to") else None

            if op == "assert":
                # Find the most recent open-interval, non-invalidated assert
                # for the same (e, a) and retract it.
                prior = _find_prior_assert(
                    current_log + new_datoms, fact["e"], fact["a"]
                )
                if prior is not None:
                    # Emit a companion :retract whose valid_from is the prior
                    # datom's valid_from and valid_to is the NEW valid_from —
                    # this closes the open interval.
                    companion = Datom(
                        e=prior.e,
                        a=prior.a,
                        v=prior.v,
                        tx=tx,
                        tx_time=now,
                        valid_from=prior.valid_from,
                        valid_to=vf,
                        op="retract",
                        provenance={**prior.provenance, "superseded_by_tx": tx},
                        invalidated_by=None,
                    )
                    new_datoms.append(companion)
                    invalidations.append((prior.tx, tx))

            prov = {
                **prov_base,
                "prompt_hash": _hash_fact(fact),
            }
            new_datoms.append(
                Datom(
                    e=fact["e"],
                    a=fact["a"],
                    v=fact["v"],
                    tx=tx,
                    tx_time=now,
                    valid_from=vf,
                    valid_to=vt,
                    op=op,
                    provenance=prov,
                )
            )

        self.store.append(new_datoms)
        for old_tx, new_tx in invalidations:
            self.store.mark_invalidated(old_tx, new_tx)

        return DB(self.store)

    # ---- Reads -----------------------------------------------------------
    def log(self) -> Iterator[Datom]:
        """Yield every datom in insertion order. Debugging + replication."""
        return self.store.all_datoms()

    def as_of(self, t: datetime) -> "DBView":
        """Transaction-time slice: everything the DB learned on or before t."""
        t = _coerce_dt(t)
        return DBView([d for d in self.store.all_datoms() if d.tx_time <= t])

    def as_of_valid(self, vt: datetime) -> "DBView":
        """Valid-time slice: every assert whose interval contains ``vt``."""
        vt = _coerce_dt(vt)
        out: list[Datom] = []
        for d in self.store.all_datoms():
            if d.op != "assert":
                continue
            if d.valid_from > vt:
                continue
            if d.valid_to is not None and vt >= d.valid_to:
                continue
            out.append(d)
        return DBView(out)

    def history(self, e: str) -> list[Datom]:
        """Every datom touching entity ``e``, sorted by transaction id."""
        return sorted(
            (d for d in self.store.all_datoms() if d.e == e),
            key=lambda d: d.tx,
        )

    def since(self, t: datetime) -> "DBView":
        """Transaction-time delta — feeds incremental sync / replication."""
        t = _coerce_dt(t)
        return DBView(list(self.store.since(t)))

    def branch(self, t: datetime, assertions: list[dict]) -> "DB":
        """Fork a counterfactual DB at ``t`` with hypothetical assertions.

        The branch gets its own fresh :class:`InMemoryStore` seeded from the
        ``as_of(t)`` snapshot. Writes on the branch can never leak into the
        parent store — the paper §4.1 ``branch(D, t, Δ)`` definition.
        """
        seed = list(self.as_of(t).datoms)
        # Re-home the seed datoms into a new in-memory store. We copy the
        # dict fields because Datom itself is frozen and the provenance
        # dicts are shared references.
        branched_store = InMemoryStore()
        branched_store.append(
            [
                Datom(
                    e=d.e,
                    a=d.a,
                    v=d.v,
                    tx=d.tx,
                    tx_time=d.tx_time,
                    valid_from=d.valid_from,
                    valid_to=d.valid_to,
                    op=d.op,
                    provenance=copy.deepcopy(d.provenance),
                    invalidated_by=d.invalidated_by,
                )
                for d in seed
            ]
        )

        db = DB(branched_store)
        if assertions:
            db = db.transact(
                assertions,
                provenance={
                    "source": "branch",
                    "base_tx_time": _coerce_dt(t).isoformat(),
                },
            )
        return db


# ---------------------------------------------------------------------------
# DBView — immutable snapshot with entity projection.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DBView:
    """A filtered slice of the log. Not a separate store — just a list."""

    datoms: list[Datom]

    def entity(self, e: str) -> dict:
        """Materialize entity ``e`` from this slice.

        The projection rule (paper §5.1) is:

          - Only ``op == "assert"`` datoms are candidates.
          - An assert is *closed* iff there exists a ``retract`` datom in the
            same view with matching ``(e, a, v, valid_from)``. Closed asserts
            are excluded — this is how both explicit retracts and auto-
            retractions on cardinality-one overwrites take effect.
          - Among the remaining (open) asserts for the same (e, a), pick the
            one with the greatest ``valid_from``; ties broken by ``tx``.

        Note: ``invalidated_by`` is NOT consulted here. It is a tx-time
        optimization hint used by the covering indexes, not a semantic filter
        — respecting it would break ``as_of_valid`` for ranges in which the
        superseding assert is outside the view.
        """
        retracts: set[tuple[str, Any, datetime]] = set()
        candidates: list[Datom] = []
        for d in self.datoms:
            if d.e != e:
                continue
            if d.op == "retract":
                retracts.add((d.a, _freeze(d.v), d.valid_from))
            elif d.op == "assert":
                candidates.append(d)

        latest: dict[str, Datom] = {}
        for d in candidates:
            key = (d.a, _freeze(d.v), d.valid_from)
            if key in retracts:
                continue
            cur = latest.get(d.a)
            if cur is None or (d.valid_from, d.tx) > (cur.valid_from, cur.tx):
                latest[d.a] = d
        return {a: d.v for a, d in latest.items()}

    def __iter__(self) -> Iterator[Datom]:
        return iter(self.datoms)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _freeze(v: Any) -> Any:
    """Best-effort hashable representation of an EDN value for set membership.

    Primitives and tuples/frozensets pass through. Dicts and lists are
    serialized to a canonical JSON string — stable under key ordering —
    which is good enough for the equality-of-value check in ``entity()``.
    """
    try:
        hash(v)
        return v
    except TypeError:
        return json.dumps(v, default=str, sort_keys=True)


def _coerce_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"naive datetime not allowed: {value!r}")
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"cannot coerce {value!r} to datetime")


def _find_prior_assert(
    datoms: Iterable[Datom], e: str, a: str
) -> Optional[Datom]:
    """Return the most recent open-interval, non-invalidated, un-retracted
    assert for (e, a), scanning the list reversed. O(n) — fine for the
    reference implementation; production walks the EAVT index."""
    for d in reversed(list(datoms)):
        if d.e != e or d.a != a:
            continue
        if d.op != "assert":
            continue
        if d.invalidated_by is not None:
            continue
        if d.valid_to is not None:
            continue
        return d
    return None


__all__ = ["DB", "DBView"]
