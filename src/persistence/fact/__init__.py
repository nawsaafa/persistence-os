"""persistence.fact — Module 1 of Persistence OS.

The bitemporal 8-tuple datom store. See ``docs/agent1-fact-spec.md`` for the
full architectural spec and the paper §4.1 for formal definitions.

Public surface:

- :class:`CausalDAG`  — result of DB.causal_history (seeds + parents map)
- :class:`Datom`      — the 8-tuple immutable fact
- :class:`DB`         — append-only log + query API
- :class:`DBView`  — snapshot view + entity projection
- :class:`Store`   — storage backend protocol
- :class:`InMemoryStore` / :class:`SQLiteStore` — reference backends
- :func:`load_migrations` — list of ``(name, sql)`` DDL blobs
- :class:`Provenance`  — typed schema for ``Datom.provenance``
"""

from persistence.fact.datom import Datom, Op, Provenance
from persistence.fact.db import CausalDAG, DB, DBView
from persistence.fact.projection import (
    DictProjection,
    ProjectionAdapter,
    rebuild,
    rebuild_view,
)
from persistence.fact.store import InMemoryStore, SQLiteStore, Store, load_migrations
from persistence.fact.wire import datom_to_wire, wire_to_datom

__all__ = [
    "CausalDAG",
    "DB",
    "DBView",
    "Datom",
    "DictProjection",
    "InMemoryStore",
    "Op",
    "ProjectionAdapter",
    "Provenance",
    "SQLiteStore",
    "Store",
    "datom_to_wire",
    "load_migrations",
    "rebuild",
    "rebuild_view",
    "wire_to_datom",
]
