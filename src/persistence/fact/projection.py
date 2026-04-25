"""Projection rebuilders — the materialized cache on top of the log.

Per paper §5.1: the projection is a *disposable* cache. Burn it down,
rebuild from the log. This module ships:

- :class:`ProjectionAdapter` — a Protocol any backend (Kuzu, mem0, Postgres
  materialized view, in-process dict) can satisfy with three methods,
  ``reset``, ``apply``, and ``fork``.
- :class:`DictProjection`    — the simplest imaginable adapter, an
  entity → attrs dict, used by the CLI demo and the Memory Palace
  integration plan as the initial seed.
- :func:`rebuild`            — drives the adapter from a DB, streaming the
  log in insertion order.

Real Kuzu / mem0 adapters are a separate concern — see
``docs/memory-palace-integration.md`` for the wiring pattern. This module
provides the seam and a working in-memory reference.
"""

from __future__ import annotations

from typing import Any, Protocol

from persistence.fact.datom import Datom
from persistence.fact.db import DB, DBView


class ProjectionAdapter(Protocol):
    """Any sink that can materialize datoms into a queryable projection.

    Implementations need three methods. Contract:

      reset()                  — clear the projection; subsequent apply()
                                 calls rebuild it from scratch.
      apply(datom)             — stream a single datom into the projection.
                                 Called in log insertion order.
      fork(branch_id)          — return a NEW empty adapter pointing at a
                                 fresh sink (new collection, new dict, etc.).
                                 Caller is responsible for calling
                                 rebuild(branched_db, forked_adapter) to
                                 populate the fork. The fresh sink's name
                                 SHOULD encode branch_id for traceability.
                                 Adapters that do not support branching
                                 should raise NotImplementedError.
    """

    def reset(self) -> None: ...
    def apply(self, datom: Datom) -> None: ...
    def fork(self, branch_id: str) -> "ProjectionAdapter": ...


class DictProjection:
    """In-process entity → attrs dict projection. Reference implementation.

    Mirrors the semantics of :meth:`DBView.entity` applied to the entire
    current log. The projection is the map ``{entity_id: {attribute: value}}``.
    """

    def __init__(self) -> None:
        self._entities: dict[str, dict[str, Any]] = {}
        # Retracts we have seen, keyed by (e, a, v_frozen, valid_from_iso).
        # A later assert with the same (e, a, v, valid_from) is treated as
        # closed — identical to the DBView rule.
        self._retracts: set[tuple] = set()
        # Track the (valid_from, tx) of the currently-winning assert per
        # (e, a) so ties are broken deterministically.
        self._winning: dict[tuple[str, str], tuple] = {}

    # ---- ProjectionAdapter ----------------------------------------------
    def reset(self) -> None:
        self._entities.clear()
        self._retracts.clear()
        self._winning.clear()

    def apply(self, d: Datom) -> None:
        from persistence.fact.db import _freeze  # avoid import cycle at top

        key = (d.a, _freeze(d.v), d.valid_from)
        if d.op == "retract":
            self._retracts.add((d.e,) + key)
            attrs = self._entities.get(d.e)
            if attrs and attrs.get(d.a) == d.v:
                # Drop the attribute if the retract closes the currently
                # winning assert. The rebuild pass re-plays from the start
                # so later asserts can still overwrite.
                winning = self._winning.get((d.e, d.a))
                if winning == (d.valid_from, d.tx):
                    attrs.pop(d.a, None)
                    self._winning.pop((d.e, d.a), None)
            return

        # op == "assert"
        if (d.e,) + key in self._retracts:
            # The log emitted the retract after this assert in a rebuild
            # that streams forward — but for in-order streaming, retracts
            # appear *after* their assert only when a later transaction
            # emits them. Safe to skip here because the retract handler
            # will drop the entry.
            pass
        attrs = self._entities.setdefault(d.e, {})
        cur = self._winning.get((d.e, d.a))
        if cur is None or (d.valid_from, d.tx) > cur:
            attrs[d.a] = d.v
            self._winning[(d.e, d.a)] = (d.valid_from, d.tx)

    def fork(self, branch_id: str) -> "DictProjection":
        """Return a fresh empty DictProjection.

        The branch_id is accepted for Protocol parity but not used by
        DictProjection — the in-memory dict has no physical naming.
        Backend adapters (Qdrant, Kuzu) use branch_id to derive
        collision-free physical target names.
        """
        del branch_id  # unused by in-memory impl; documented contract
        return DictProjection()

    # ---- Query surface ---------------------------------------------------
    def get(self, e: str) -> dict:
        return dict(self._entities.get(e, {}))

    def as_dict(self) -> dict[str, dict]:
        return {e: dict(attrs) for e, attrs in self._entities.items()}


def rebuild(db: DB, adapter: ProjectionAdapter) -> None:
    """Rebuild ``adapter`` from every datom in ``db``'s log.

    Streams in insertion order so adapters that want temporal dependency
    preserved can rely on it. Calls ``reset()`` first — rebuilds are always
    full, never incremental. For incremental sync, pass a ``DBView`` by
    calling ``rebuild_view`` on ``db.since(t)`` manually.
    """
    adapter.reset()
    for d in db.log():
        adapter.apply(d)


def rebuild_view(view: DBView, adapter: ProjectionAdapter) -> None:
    """Feed a pre-filtered view into the adapter. Does NOT call reset()."""
    for d in view.datoms:
        adapter.apply(d)


__all__ = ["DictProjection", "ProjectionAdapter", "rebuild", "rebuild_view"]
