"""persistence.store — Phase 1 stream #137 (PG1) backbone for SERIALIZABLE backends.

This package hosts backends that live outside ``persistence.fact`` because
they ship as optional extras: their drivers are not in the substrate's
required dependency set, and the package layout reflects the optional-
import pattern (lazy ``import psycopg`` inside the module body, with a
clean ``BackendNotInstalled`` raise when the extra is not installed).

PG1 ships a single backend:

- :class:`~persistence.store.postgres.PostgresStore` — psycopg 3 +
  ``psycopg_pool.ConnectionPool``. SERIALIZABLE backbone with a
  ``tx_allocator`` row-lock primitive (ADR-4 W1-revised) and
  ``UNIQUE (tx, e, a)`` defence-in-depth. Mirrors the
  :class:`~persistence.fact.Store` Protocol exactly — every method on
  :class:`~persistence.fact.SQLiteStore` has a matching method here, with
  the same shape and the same canonical-JSON value codec, so adapters
  bound to the existing 6-method Protocol continue to work.

The full design doc:
``docs/plans/2026-04-30-v0.8.0-postgres-store-design.md``.

PG2-PG6 (replay byte-identity gates / audit-chain Merkle integration /
fold() executor / cross-process Hypothesis property) build on top of
this backbone.
"""

from __future__ import annotations

__all__: list[str] = [
    # PostgresStore is exported lazily by importing it directly:
    #   from persistence.store.postgres import PostgresStore
    # We do NOT eagerly import here so that ``import persistence.store``
    # works even without the [postgres] extra installed.
]
