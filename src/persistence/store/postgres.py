"""PostgresStore — SERIALIZABLE backbone for the bitemporal datom log.

Phase 1 stream #137 (PG1). Sibling to :class:`persistence.fact.SQLiteStore`;
implements the same :class:`persistence.fact.Store` Protocol shape so
adapters bound to the existing 6 methods continue to work without changes.

Design source of truth:
``docs/plans/2026-04-30-v0.8.0-postgres-store-design.md``

PG1 deliverables (this module):

1. ``datom_log`` table mirroring the SQLite schema column-for-column
   (``v`` and ``provenance`` stored as TEXT canonical JSON per ADR-5
   W1-revised — NOT JSONB; byte-identical to SQLite's TEXT columns).
2. ``UNIQUE (tx, e, a)`` constraint on ``datom_log`` — defence-in-depth
   safety net behind the ``tx_allocator`` row-lock allocator.
3. ``tx_allocator`` single-row table (``id=1``, ``next_tx``) — the hard
   correctness primitive for monotonic tx-id allocation under
   SERIALIZABLE per ADR-4 W1-revised. Plan-independent: no reliance
   on SSI predicate-lock placement on a ``MAX()`` aggregate.
4. :meth:`PostgresStore.transact_serializable` — the load-bearing
   atomic primitive per ADR-15 W2: ``BEGIN ISOLATION LEVEL
   SERIALIZABLE`` → ``SELECT FOR UPDATE`` allocator → ``UPDATE
   next_tx`` → batch ``INSERT`` → ``COMMIT``. Retries on
   ``SerializationFailure`` (40001) with bounded exponential backoff;
   raises immediately on ``UniqueViolation`` (23505) since the
   defence-in-depth catch should never fire when the allocator is
   correct.
5. :meth:`PostgresStore._txn` — store-level SERIALIZABLE-transaction
   context-manager that yields a cursor; commits on clean exit, rolls
   back on exception. Used by replay / read paths.
6. :meth:`PostgresStore.allocate_and_append` — the substrate's atomic
   primitive; layers an in-process :class:`threading.RLock` ahead of
   the cross-process row-lock for performance (§ 8.3 of design doc).

PG1 does NOT ship:

- The ``audit_chain_lock`` Merkle-continuity table (ADR-3) — that
  lands in PG3 once the audit-chain integration is wired through
  ``persistence.repl._audit``. Until then the audit chain still works
  in single-process mode (the existing in-Python ``_audit_chain_state``
  is correct for one process); PG3 promotes it to multi-process.
- Replay byte-identity Hypothesis property at ``max_examples=200``
  parametrised over PostgresStore — that lands in PG2.
- Cross-process Hypothesis property at ``max_examples=50`` — that
  lands in PG4 with the multiprocessing harness.
- ``fold()`` executor wiring — PG6.

Connection pool model
---------------------

PG1 uses :class:`psycopg_pool.ConnectionPool` with sane v0.8 defaults
(``min_size=1``, ``max_size=10``, ``timeout=30``). The pool is opened
on construction and closed by :meth:`close` / ``__del__``. Every
connection in the pool is configured with
``default_transaction_isolation = 'serializable'`` so any short-lived
read path (``all_datoms``, ``since``, ``next_tx``) outside an active
``_txn()`` still runs at the right isolation level — this matches
SQLiteStore's invariant that ``BEGIN IMMEDIATE`` always wraps writes.

Lazy import pattern
-------------------

This module imports :mod:`psycopg` and :mod:`psycopg_pool` lazily inside
:meth:`PostgresStore.__init__` so that ``import persistence.store.postgres``
itself does NOT raise if the ``[postgres]`` extra is not installed. The
URI dispatcher (:func:`persistence.sdk.open_store`) catches the
:class:`ImportError` from the lazy import and re-raises it as
:class:`persistence.sdk.BackendNotInstalled` with a clean install hint.
This matches Adapter SDK ADR-9.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Iterable, Iterator, TYPE_CHECKING

from persistence.fact.datom import Datom
from persistence.store._codec import NativeDatomCodec, with_tx as _with_tx
from persistence.store._migration_runner import apply_migrations_with_pool

if TYPE_CHECKING:  # pragma: no cover — import only for static type checkers
    import psycopg
    import psycopg_pool


# ---------------------------------------------------------------------------
# Schema DDL — applied via the PG6 migration runner on first connect.
# ---------------------------------------------------------------------------
#
# PG1 (`d911270`) shipped a single inline ``_SCHEMA_DDL`` string here. PG6
# (Phase 1 stream #169) split it into a real migration file at
# :file:`migrations/postgres/0001_datom_log.sql` and replaced the
# ``_create_schema()`` body with a call into
# :func:`persistence.store._migration_runner.apply_migrations_with_pool`.
# The on-wire DDL is byte-identical (same ``CREATE TABLE IF NOT EXISTS``
# + same indexes + same ``tx_allocator`` row + same ``UNIQUE (tx, e, a)``
# constraint) so existing databases keep working without any data
# migration; the only change is that future PG3/PG4 migrations
# (``0002_audit_chain_lock.sql`` etc.) live next to ``0001_datom_log.sql``
# instead of being smuggled into the inline string.
#
# The runner records every applied migration in a ``_migrations`` history
# table (``id INTEGER PK / name TEXT UNIQUE / applied_at TIMESTAMPTZ``)
# inside the same transaction as the migration body, so a re-init that
# scans an unchanged on-disk migrations directory is a no-op. See
# ``persistence.store._migration_runner`` for the runner contract.


# ---------------------------------------------------------------------------
# Retry budget for SerializationFailure under the SERIALIZABLE backbone.
# ---------------------------------------------------------------------------
# Per ADR-9 / § 5.4 of the design doc, SSI false-positive rate at the v0.8
# coding-agent workload is < 1%; a 3-attempt budget with exponential backoff
# (50ms / 100ms / 200ms) covers the long tail without becoming a hot loop.
# Higher-level callers (``Transaction._run`` in v0.5.2) layer their own
# ``max_retries=256`` budget over this — the two retry layers compose.
_DEFAULT_MAX_RETRIES = 3
_RETRY_BACKOFF_S = (0.05, 0.10, 0.20)


# ---------------------------------------------------------------------------
# PostgresStore
# ---------------------------------------------------------------------------
class PostgresStore:
    """Postgres-backed datom log with SERIALIZABLE isolation.

    Mirrors :class:`persistence.fact.SQLiteStore`'s Protocol surface: every
    method on the SQLite reference impl has a matching method here. The
    canonical-JSON value codec is shared (``_encode`` / ``_decode`` below
    are byte-identical to ``persistence.fact.store._encode`` /
    ``_decode``), so a datom round-tripped through PostgresStore is
    indistinguishable from one round-tripped through SQLiteStore.

    Per ADR-9 (W1-revised) + ADR-15 (W2) PostgresStore additionally
    exposes:

    - :meth:`_txn` — store-level SERIALIZABLE-transaction context-manager
      yielding an active :class:`psycopg.Cursor`.
    - :meth:`transact_serializable` — atomic primitive for cross-process
      tx-id allocation + datom INSERT under one SERIALIZABLE
      transaction. Returns the freshly-allocated tx-id.

    Both methods are additive to the Store Protocol (PG3 will add
    default impls on InMemoryStore + SQLiteStore so the additivity is
    non-breaking; PG1 ships only the PostgresStore override).
    """

    # ----- construction ---------------------------------------------------

    def __init__(
        self,
        dsn: str,
        *,
        pool_min: int = 1,
        pool_max: int = 10,
        pool_timeout: float = 30.0,
    ) -> None:
        """Open a PostgresStore against ``dsn``.

        Args:
            dsn: psycopg-style DSN, e.g.
                ``"postgresql://user:pass@host:5432/dbname"``. Both the
                ``postgres://`` and ``postgresql://`` schemes are
                accepted in the DSN body (they are aliases at libpq).
            pool_min: minimum pool size (default 1).
            pool_max: maximum pool size (default 10).
            pool_timeout: seconds to wait for a free connection before
                raising (default 30).

        Raises:
            ImportError: if the ``[postgres]`` extra is not installed.
                The :func:`persistence.sdk.open_store` dispatcher catches
                this and re-raises as :class:`BackendNotInstalled`.
            psycopg.OperationalError: if the server is unreachable.
        """
        # Lazy import — module import must succeed without [postgres]; the
        # error path is the dispatcher's BackendNotInstalled re-raise.
        try:
            import psycopg
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover — exercised via mock
            raise ImportError(
                "PostgresStore requires the [postgres] extra: "
                'pip install "persistence[postgres]"  '
                f"(missing: {exc.name})"
            ) from exc

        self._dsn = dsn

        # In-process serialiser. The cross-process correctness primitive
        # is the SERIALIZABLE transaction + ``tx_allocator`` row-lock; the
        # in-process RLock just collapses N in-process writers down to a
        # single allocator-row contender (cheaper than each thread doing
        # a network round-trip to queue on the row lock). Per § 8.3 of
        # the design doc.
        #
        # RLock (reentrant) matches InMemoryStore + SQLiteStore: the
        # surrounding Txn._run() holds it across conflict-check + commit,
        # and the inner ``allocate_and_append`` re-acquires on the same
        # thread.
        self._lock = threading.RLock()

        # Canonical codec — datetime fields pass through psycopg's
        # TIMESTAMPTZ adapter (ADR-16). PG2 consolidation: this is the
        # only place the encoded-row shape is defined; ``_encode`` and
        # ``_decode_tuple`` previously redefined locally here have been
        # promoted to :class:`persistence.store._codec.NativeDatomCodec`.
        self._codec = NativeDatomCodec()

        # Capture the IsolationLevel enum in closure — psycopg 3 exposes
        # it at the module level. Resolving once avoids re-importing on
        # every fresh-connection configure callback.
        _serializable = psycopg.IsolationLevel.SERIALIZABLE

        # Open the pool. ``configure=`` runs once per fresh connection to
        # pin the isolation level so even short-lived read paths default
        # to SERIALIZABLE (matches SQLiteStore's BEGIN IMMEDIATE invariant
        # that EVERY write — read or otherwise — runs under the writer-
        # lock barrier).
        def _configure(conn: "psycopg.Connection") -> None:
            # autocommit=False is the psycopg 3 default but we set it
            # explicitly so a future driver default change does not
            # silently break the SERIALIZABLE backbone.
            conn.autocommit = False
            conn.isolation_level = _serializable

        self._pool: "psycopg_pool.ConnectionPool" = ConnectionPool(
            conninfo=dsn,
            min_size=pool_min,
            max_size=pool_max,
            timeout=pool_timeout,
            configure=_configure,
            open=True,
        )

        # Apply schema on first connect. Idempotent (CREATE TABLE IF NOT
        # EXISTS + ON CONFLICT DO NOTHING for the seed row), so re-opens
        # against an existing database are no-ops. PG6 may add a proper
        # migration runner; PG1 ships this one-shot form.
        self._create_schema()

    def _create_schema(self) -> None:
        """Apply pending migrations via the PG6 forward-only runner.

        Delegates to
        :func:`persistence.store._migration_runner.apply_migrations_with_pool`
        which scans :file:`migrations/postgres/*.sql` lexicographically,
        applies any file not already in the ``_migrations`` history
        table (each migration body + its history-row INSERT inside one
        transaction), and is a no-op when everything is already up to
        date.

        The list of just-applied migration names is intentionally
        discarded — operators who care about migration apply ordering
        consult the ``_migrations`` table directly.
        """
        apply_migrations_with_pool(self._pool, flavour="postgres")

    # ----- transaction context manager (additive Protocol method) ---------

    @contextmanager
    def _txn(self) -> Iterator["psycopg.Cursor"]:
        """Open a SERIALIZABLE transaction; yield a cursor.

        Per ADR-15 W2 this is the store-level transaction context-manager.
        On clean exit COMMIT is issued; on exception ROLLBACK and re-
        raise. The yielded cursor is bound to a connection checked out
        of the pool for the duration of the block.

        Note: the connection's isolation level is already set to
        SERIALIZABLE via the pool's ``configure=`` callback, so any
        ``BEGIN`` that psycopg 3 implicitly issues on first cursor
        execute runs under SERIALIZABLE without further plumbing.

        Yields:
            A :class:`psycopg.Cursor` on a SERIALIZABLE-mode connection.
            The cursor is invalid after block exit.
        """
        with self._pool.connection() as conn:
            try:
                with conn.cursor() as cur:
                    yield cur
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    # ----- transact_serializable (additive Protocol method) ---------------

    def transact_serializable(
        self,
        datoms: Iterable[Datom],
        *,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> int:
        """Atomically allocate a tx-id and insert ``datoms`` under SERIALIZABLE.

        Per ADR-15 W2 this is the load-bearing atomic primitive for
        cross-process correctness. Inside one SERIALIZABLE transaction:

        1. ``SELECT next_tx FROM tx_allocator WHERE id=1 FOR UPDATE``
           — row-locks the allocator. Any concurrent writer queues
           here.
        2. ``UPDATE tx_allocator SET next_tx = next_tx + 1 WHERE id=1``
           — advances the allocator by ONE. The whole batch shares
           this single tx-id (matches InMemoryStore + SQLiteStore
           contract: one transact = one tx).
        3. ``INSERT INTO datom_log ...`` for every datom in the batch,
           sharing the allocated tx-id.
        4. ``COMMIT`` — releases the row lock.

        Two writers that race the allocator both queue on the
        ``FOR UPDATE`` row lock; their tx-ids are guaranteed disjoint
        by Postgres row-lock semantics — independent of SSI predicate-
        lock placement and independent of the planner's choice for
        ``MAX()``.

        On :class:`psycopg.errors.SerializationFailure` (40001) the
        whole transaction is retried with exponential backoff up to
        ``max_retries`` (3 by default — 50ms / 100ms / 200ms). On
        :class:`psycopg.errors.UniqueViolation` (23505) the call
        raises immediately: a UniqueViolation on ``(tx, e, a)`` means
        the defence-in-depth schema constraint fired, which should
        only happen if a future code-path bypassed the allocator
        entirely. That is a programmer error, not a retryable race
        — surface it immediately so it gets fixed at the source.

        Args:
            datoms: iterable of :class:`Datom`. The ``tx`` field on each
                input datom is ignored; the allocator picks the real
                value. Empty iterable is a no-op and burns no tx-id.
            max_retries: maximum SerializationFailure retries before
                surfacing the error to the caller.

        Returns:
            The freshly-allocated tx-id (int) shared by all inserted
            datoms. ``0`` if ``datoms`` is empty.

        Raises:
            psycopg.errors.SerializationFailure: after ``max_retries``
                exhausted.
            psycopg.errors.UniqueViolation: on first occurrence
                (defence-in-depth catch — should not happen with the
                allocator working).
        """
        # Lazy import for error classes — kept inside the method so
        # module-level import works without [postgres].
        from psycopg import errors as pg_errors

        materialised = list(datoms)
        if not materialised:
            return 0

        last_err: BaseException | None = None
        for attempt in range(max_retries + 1):
            try:
                with self._pool.connection() as conn:
                    try:
                        with conn.cursor() as cur:
                            # 1. Row-lock the allocator.
                            cur.execute(
                                "SELECT next_tx FROM tx_allocator "
                                "WHERE id = 1 FOR UPDATE"
                            )
                            row = cur.fetchone()
                            if row is None:
                                # Should never happen — schema seeds
                                # (id=1, next_tx=1). If it does, the
                                # database was tampered with; surface
                                # loudly rather than silently re-seed.
                                raise RuntimeError(
                                    "PostgresStore.transact_serializable: "
                                    "tx_allocator row id=1 missing — "
                                    "schema not initialised"
                                )
                            new_tx = int(row[0])

                            # 2. Advance the allocator by ONE — the
                            # whole batch shares this single tx-id
                            # (matches InMemoryStore + SQLiteStore
                            # contract: one transact = one tx). The
                            # design doc § 4.2a contemplates an N-id
                            # batch shape for a future writer that
                            # wants per-datom tx-ids, but the v0.5.x
                            # substrate semantics are "all datoms in
                            # a batch share the allocated tx" and PG1
                            # preserves that exactly so adapter
                            # behaviour stays cross-backend identical.
                            cur.execute(
                                "UPDATE tx_allocator "
                                "SET next_tx = next_tx + 1 WHERE id = 1"
                            )

                            # 3. Stamp + INSERT the batch.
                            stamped = [_with_tx(d, new_tx) for d in materialised]
                            cur.executemany(
                                """
                                INSERT INTO datom_log
                                  (e, a, v, tx, tx_time, valid_from,
                                   valid_to, op, provenance,
                                   invalidated_by)
                                VALUES (%s, %s, %s, %s, %s, %s,
                                        %s, %s, %s, %s)
                                """,
                                [self._codec.encode(d) for d in stamped],
                            )
                        # 4. COMMIT — releases the row lock.
                        conn.commit()
                        return new_tx
                    except BaseException:
                        conn.rollback()
                        raise
            except pg_errors.SerializationFailure as exc:
                # Retryable: the SERIALIZABLE COMMIT detected a rw-
                # cycle. Sleep with bounded exponential backoff and
                # try again. The retry uses a fresh connection and
                # re-reads the allocator (which by now has advanced
                # for the winner).
                last_err = exc
                if attempt < max_retries:
                    time.sleep(
                        _RETRY_BACKOFF_S[
                            min(attempt, len(_RETRY_BACKOFF_S) - 1)
                        ]
                    )
                    continue
                raise
            except pg_errors.UniqueViolation:
                # Programmer error — should not happen if every writer
                # goes through the allocator. Re-raise immediately for
                # visibility; do not mask as a retryable conflict.
                raise
        # Defensive fallback — only reached if max_retries < 0, which
        # the type signature does not guarantee against. ``last_err``
        # holds the last SerializationFailure we saw.
        if last_err is not None:
            raise last_err
        raise RuntimeError(  # pragma: no cover
            "PostgresStore.transact_serializable: unreachable retry exit"
        )

    # ----- Store Protocol methods -----------------------------------------

    def append(self, datoms: Iterable[Datom]) -> None:
        """Append datoms with the tx-ids the caller chose.

        Low-level: does NOT allocate. Used by tests + admin paths that
        already carry an allocated tx-id. Multi-process callers should
        prefer :meth:`allocate_and_append` or
        :meth:`transact_serializable` so the allocator owns the tx
        space.
        """
        rows = [self._codec.encode(d) for d in datoms]
        if not rows:
            return
        with self._lock:
            with self._pool.connection() as conn:
                try:
                    with conn.cursor() as cur:
                        cur.executemany(
                            """
                            INSERT INTO datom_log
                              (e, a, v, tx, tx_time, valid_from,
                               valid_to, op, provenance,
                               invalidated_by)
                            VALUES (%s, %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s)
                            """,
                            rows,
                        )
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise

    def allocate_and_append(self, datoms: Iterable[Datom]) -> list[Datom]:
        """Atomically allocate next tx-id and append, multi-process safe.

        Routes through :meth:`transact_serializable` so the allocator-
        row-lock + SERIALIZABLE primitive is the single load-bearing
        path for tx-id allocation. Returns the input datoms with their
        ``tx`` field stamped to the allocated value.
        """
        materialised = list(datoms)
        if not materialised:
            return []
        with self._lock:
            tx = self.transact_serializable(materialised)
        return [_with_tx(d, tx) for d in materialised]

    def all_datoms(self) -> Iterator[Datom]:
        """Yield every datom in physical insertion order (``seq`` ASC)."""
        with self._lock:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT e, a, v, tx, tx_time, valid_from, "
                        "valid_to, op, provenance, invalidated_by "
                        "FROM datom_log ORDER BY seq ASC"
                    )
                    rows = cur.fetchall()
                conn.commit()  # release the read snapshot's locks
        return (self._codec.decode(r) for r in rows)

    def since(self, tx_time: datetime) -> Iterator[Datom]:
        """Yield datoms whose ``tx_time`` is strictly greater than ``tx_time``."""
        with self._lock:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT e, a, v, tx, tx_time, valid_from, "
                        "valid_to, op, provenance, invalidated_by "
                        "FROM datom_log WHERE tx_time > %s "
                        "ORDER BY seq ASC",
                        (tx_time,),
                    )
                    rows = cur.fetchall()
                conn.commit()
        return (self._codec.decode(r) for r in rows)

    def mark_invalidated(self, tx: int, invalidated_by_tx: int) -> None:
        """Stamp ``invalidated_by`` on every assert datom with ``tx == tx``."""
        with self._lock:
            with self._pool.connection() as conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE datom_log
                               SET invalidated_by = %s
                             WHERE tx = %s
                               AND invalidated_by IS NULL
                               AND op = 'assert'
                            """,
                            (invalidated_by_tx, tx),
                        )
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise

    def next_tx(self) -> int:
        """Return the id that the next ``allocate_and_append`` call would assign.

        Read-only probe: reads ``tx_allocator.next_tx`` without locking.
        The returned value is a snapshot — concurrent allocators may
        advance the row before the caller acts on it. Use
        :meth:`allocate_and_append` to atomically reserve.
        """
        with self._lock:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT next_tx FROM tx_allocator WHERE id = 1"
                    )
                    row = cur.fetchone()
                conn.commit()
        if row is None:
            # Schema was not seeded — defensive default that matches
            # the seed value. Should never happen with the migration
            # applied; surfaces as 1 rather than erroring so callers
            # see the same starting point as a fresh InMemoryStore.
            return 1
        return int(row[0])

    # ----- lifecycle ------------------------------------------------------

    def close(self) -> None:
        """Close the pool. Idempotent."""
        with self._lock:
            try:
                self._pool.close()
            except Exception:
                pass

    def __del__(self) -> None:  # best-effort cleanup
        try:
            self.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Codec — see ``persistence.store._codec.NativeDatomCodec``.
# ---------------------------------------------------------------------------
# PG2 (Phase 1 stream #165) replaced the locally-redefined
# ``_encode`` / ``_decode_tuple`` / ``_with_tx`` helpers with a single
# canonical implementation in ``persistence.store._codec``. The
# datetime-as-TIMESTAMPTZ shape is preserved exactly: psycopg 3's
# adapter handles encode + decode, so no ISO round-trip is forced on
# the Postgres path. See ADR-16 in
# ``docs/plans/2026-04-30-v0.8.0-postgres-store-design.md`` § 13a for
# the codec-strategy decision record.

__all__ = ["PostgresStore"]
