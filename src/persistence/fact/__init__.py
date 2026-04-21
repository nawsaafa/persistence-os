"""persistence.fact — Module 1 of Persistence OS.

The bitemporal 8-tuple datom store. See ``docs/agent1-fact-spec.md`` for the
full architectural spec and the paper §4.1 for formal definitions.

Public surface (assembled incrementally as modules land):

- :class:`Datom`   — the 8-tuple immutable fact
- :class:`DB`      — append-only log + query API
- :class:`DBView`  — snapshot view + entity projection
- :class:`Store`   — storage backend protocol
- :class:`InMemoryStore` / :class:`SQLiteStore` — reference backends
"""

from persistence.fact.datom import Datom, Op

__all__ = ["Datom", "Op"]
