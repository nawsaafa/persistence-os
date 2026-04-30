"""Recommended :class:`psycopg_pool.ConnectionPool` kwargs for PostgresStore.

Phase 1 stream #168 (PG5). Pure-data helper — does NOT open a pool, does
NOT mutate :class:`~persistence.store.postgres.PostgresStore`. Callers opt
in by passing the result into their own ``ConnectionPool`` instance::

    from psycopg_pool import ConnectionPool
    from persistence.store._pool_config import recommended_pool_kwargs

    pool = ConnectionPool(conninfo=dsn, **recommended_pool_kwargs())

The full deployment matrix (pgbouncer transaction-mode incompatibility,
Aurora/RDS writer-endpoint constraint, failover-resilience caveat) is in
:file:`docs/operations/postgres-deployment.md`.

Why this is a separate module
-----------------------------

PG1's :class:`PostgresStore` constructor accepts ``pool_min`` /
``pool_max`` / ``pool_timeout`` only — the per-statement ``options``
string (``statement_timeout`` / ``lock_timeout`` /
``idle_in_transaction_session_timeout``) is NOT a constructor parameter.
That is deliberate: PG1's surface is conservative and the pool kwargs
this module produces are an external recipe callers wire into their own
``ConnectionPool`` when they want the recommended timeouts. PG5 does NOT
plumb these into the ``PostgresStore.__init__`` signature; that is a
v0.8.x or v0.9 decision.

The three timeouts together form the v0.8 "production-ready"
configuration:

- ``statement_timeout`` bounds runaway queries from above.
- ``lock_timeout`` bounds waits on the ``tx_allocator`` and
  ``audit_chain_lock`` row-locks; on timeout, psycopg surfaces a
  ``LockNotAvailable`` (SQLSTATE ``55P03``), and the substrate's existing
  ADR-15 SerializationFailure retry budget is the next defence (callers
  needing failover-resilience layer their own retry above
  ``transact_serializable`` — see ADR-15 + the deployment doc).
- ``idle_in_transaction_session_timeout`` prevents leaked open
  transactions from holding the row-locks for the rest of the session
  lifetime.
"""

from __future__ import annotations

__all__ = ["recommended_pool_kwargs"]


def recommended_pool_kwargs(
    *,
    statement_timeout_ms: int = 5000,
    lock_timeout_ms: int = 2000,
    idle_in_transaction_timeout_ms: int = 10000,
) -> dict[str, object]:
    """Return recommended ``ConnectionPool`` kwargs for PostgresStore.

    The returned mapping carries a single key ``"kwargs"`` whose value is
    the ``connect()``-time options dict consumed by
    :class:`psycopg_pool.ConnectionPool`. psycopg forwards ``options`` to
    libpq, which applies each ``-c key=value`` pair as a per-connection
    GUC at startup time. The three GUCs returned here are deliberately
    chosen so that no GUC requires SUPERUSER to set (every Postgres role
    can SET these three at session scope).

    Args:
        statement_timeout_ms: per-statement timeout in milliseconds.
            Default 5000 (5s). Bounded above the expected 50-200ms
            ``transact_serializable`` wall-clock by ~25-100x; meant to
            catch genuinely runaway queries (full-table scans on large
            ``datom_log``, missing index, etc.) NOT to fight the
            row-lock primitive (use ``lock_timeout`` for that).
        lock_timeout_ms: per-lock-acquire timeout in milliseconds.
            Default 2000 (2s). Bounds waits on the ``tx_allocator`` /
            ``audit_chain_lock`` row-locks. On expiry psycopg raises a
            ``LockNotAvailable`` (SQLSTATE ``55P03``) — distinct from
            ``SerializationFailure`` (``40001``). PostgresStore's ADR-15
            retry budget covers ``40001`` only; callers needing
            ``55P03``-resilience add their own retry layer.
        idle_in_transaction_timeout_ms: idle-in-transaction timeout in
            milliseconds. Default 10000 (10s). Aborts an open
            transaction whose client has gone idle, preventing leaked
            row-locks on ``tx_allocator`` / ``audit_chain_lock``.

    Returns:
        A dict ready to splat into ``ConnectionPool(**...)``::

            {"kwargs": {"options": "-c statement_timeout=5000 ..."}}

        ``ConnectionPool`` forwards ``kwargs`` to every
        ``psycopg.connect()`` call, which forwards ``options`` to libpq.

    Raises:
        ValueError: if any timeout is non-positive. A zero timeout in
            Postgres means "no timeout" but every documented
            recommendation in :file:`docs/operations/postgres-deployment.md`
            relies on the bounded-wait semantics; making zero
            unrepresentable here flags a footgun at construction.

    Example:
        Direct connection (recommended)::

            from psycopg_pool import ConnectionPool
            from persistence.store._pool_config import recommended_pool_kwargs

            pool = ConnectionPool(conninfo=dsn, **recommended_pool_kwargs())

        Custom timeouts (e.g., a long-running maintenance worker)::

            pool = ConnectionPool(
                conninfo=dsn,
                **recommended_pool_kwargs(
                    statement_timeout_ms=60_000,
                    idle_in_transaction_timeout_ms=120_000,
                ),
            )

    See Also:
        :file:`docs/operations/postgres-deployment.md` for the full
        deployment matrix (pgbouncer transaction-mode incompatible,
        Aurora/RDS writer-endpoint only, failover-resilience requires
        a caller-side retry layer).
    """
    if statement_timeout_ms <= 0:
        raise ValueError(
            f"statement_timeout_ms must be positive (got {statement_timeout_ms}); "
            "zero would disable the bound — see "
            "docs/operations/postgres-deployment.md for why every "
            "PostgresStore deploy wants a finite statement timeout."
        )
    if lock_timeout_ms <= 0:
        raise ValueError(
            f"lock_timeout_ms must be positive (got {lock_timeout_ms}); "
            "zero would disable the bound on tx_allocator / audit_chain_lock "
            "row-lock waits."
        )
    if idle_in_transaction_timeout_ms <= 0:
        raise ValueError(
            f"idle_in_transaction_timeout_ms must be positive "
            f"(got {idle_in_transaction_timeout_ms}); zero would disable "
            "the bound on leaked open transactions holding row-locks."
        )

    options = (
        f"-c statement_timeout={statement_timeout_ms} "
        f"-c lock_timeout={lock_timeout_ms} "
        f"-c idle_in_transaction_session_timeout={idle_in_transaction_timeout_ms}"
    )
    return {"kwargs": {"options": options}}
