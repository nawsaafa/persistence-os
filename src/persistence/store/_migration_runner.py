"""Forward-only SQL migration runner for the ``persistence.store`` backends.

Phase 1 stream #169 (PG6). Closes the ADR-2 + ADR-11 deferral that PG1
flagged in §13 of the design doc:

    Migration-file split deferred ... PG1 ships DDL inline in
    ``postgres.py`` (idempotent CREATE TABLE IF NOT EXISTS). The
    migration-file split + ``load_migrations(backend)`` flavoured loader
    is **deferred to PG6 (migration runner scaffolding)**, where it
    lands alongside a real migration runner.

PG6 ships:

1. The migration-file split — :file:`migrations/postgres/0001_datom_log.sql`
   carries the same DDL that PG1 emitted inline; PG3 will land
   :file:`0002_audit_chain_lock.sql` against the same convention.
2. The runner in this module — scans a flavoured directory
   (``migrations/<backend>/*.sql``) lexicographically, applies every
   migration that is not already recorded in a ``_migrations`` history
   table, and records each one on success.

Design choices (ADR-11 reaffirmed)
----------------------------------

- **Forward-only.** v0.8 has no ``down`` / ``rollback`` migrations. A
  schema rollback at the substrate layer is a re-deploy + restore-from-
  backup operation, not an in-runner concern. Keeping the runner one-
  way means the history table is a strict append-only ledger and
  reasoning about state is "what was the last applied migration?"
  rather than "what if the down side races a future up side?".
- **No Alembic.** The runner is one short module + a
  ``CREATE TABLE _migrations`` row. Pulling in Alembic would force
  every PostgresStore deploy to ship Alembic's import graph and pin
  another versioned dependency for a feature that today is "apply a
  list of files in lexical order".
- **Idempotent re-init.** Re-opening a PostgresStore against an
  existing database is a no-op — the runner enumerates the on-disk
  filenames, reads which names are already in ``_migrations``, and
  applies the difference. A redeploy that adds no new files
  short-circuits at the diff step without taking any DDL locks.
- **Fail-fast.** Any ``execute()`` raising aborts the whole apply
  loop; the offending migration is NOT recorded; the next runner
  invocation will retry (or, more usefully, surface the same error
  immediately at deploy-time so the operator sees it).
- **DDL transactions.** Each migration body runs inside a single
  ``BEGIN`` ... ``COMMIT`` block, and the matching ``INSERT INTO
  _migrations`` row is recorded inside the same transaction so a
  partial-DDL-apply followed by an interrupted commit cannot
  desynchronise the history table from the live schema. (Postgres
  allows DDL inside transactions; CREATE/ALTER are transactional.)
- **Connection sourcing.** The runner takes a *connection* (any
  psycopg-compatible Connection: ``conn.cursor()``,
  ``conn.commit()``, ``conn.rollback()``). PostgresStore passes a
  pooled connection in; a future test or admin path could pass any
  short-lived connection. The runner does NOT own the connection's
  lifecycle.

Backend flavour
---------------

The runner is parameterised on a backend flavour (``"postgres"``).
The migrations directory is :file:`<package>/store/migrations/<flavour>`.
v0.8 ships the ``postgres`` flavour. v0.9 (or later) may add a
``sqlite`` flavour to retire the ``persistence.fact.migrations``
single-file path; that's deliberately out of scope here — PG6 only
moves the Postgres DDL.

History table shape
-------------------

::

    CREATE TABLE IF NOT EXISTS _migrations (
        id          INTEGER PRIMARY KEY,
        name        TEXT    NOT NULL UNIQUE,
        applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

- ``id`` is the integer prefix parsed from the filename (e.g.
  ``0001_datom_log.sql`` → ``1``). Filename convention:
  ``<id-zero-padded>_<slug>.sql``. Numbering starts at 1.
- ``name`` is the full filename (e.g. ``"0001_datom_log.sql"``);
  ``UNIQUE`` so a second runner invocation cannot re-apply the same
  file even if a stale row has the same ``id`` (defensive).
- ``applied_at`` is the server-side wall clock. Useful for forensics;
  the runner does not branch on it.

The history table is itself created only after the first migration is
present — the runner uses ``CREATE TABLE IF NOT EXISTS`` on the
history schema before reading it, so the very first apply on a fresh
database creates ``_migrations`` and ``0001_datom_log.sql``'s tables
in lexical order across two transactions (history table first; then
migrations one-by-one).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------
# Match ``NNNN_<slug>.sql`` where the integer prefix may be zero-padded but
# must be present and at least one digit wide. The slug is free-form (any
# chars except ``/``) so future renames do not have to fit a tight regex.
_MIGRATION_FILENAME = re.compile(r"^(?P<id>\d+)_[^/]+\.sql$")


# ---------------------------------------------------------------------------
# Connection / cursor protocols
# ---------------------------------------------------------------------------
# We type against a structural Protocol rather than psycopg's concrete
# classes so the runner is dependency-light: the ``[postgres]`` extra is
# already imported by PostgresStore at instantiation; the runner reuses
# whatever it gets passed without a second import.
class _CursorLike(Protocol):
    def execute(self, query: str, params: Any = ...) -> Any: ...

    def fetchall(self) -> Sequence[tuple[Any, ...]]: ...

    def __enter__(self) -> "_CursorLike": ...

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any: ...


class _ConnectionLike(Protocol):
    def cursor(self) -> _CursorLike: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


# ---------------------------------------------------------------------------
# Migration record
# ---------------------------------------------------------------------------
class Migration:
    """A single migration file ready to apply.

    Trivial value type; not a dataclass to keep the import-time graph as
    small as possible. The tuple-like fields below are read by the runner
    only — callers should not construct one directly.
    """

    __slots__ = ("id", "name", "path", "sql")

    def __init__(self, id: int, name: str, path: Path, sql: str) -> None:
        self.id = id
        self.name = name
        self.path = path
        self.sql = sql

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        return f"Migration(id={self.id!r}, name={self.name!r})"


class MigrationError(RuntimeError):
    """Raised on filename-parse failures and other runner-side errors.

    Driver-side errors (``psycopg.errors.SyntaxError``, etc.) propagate
    through the runner unwrapped — operators want to see the raw SQLSTATE
    and message, not a wrapped one.
    """


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def _migrations_dir(flavour: str) -> Path:
    """Return the directory holding ``<flavour>`` migration files.

    The path is computed relative to this module file so that the runner
    works both from a source checkout (``src/persistence/store/...``) and
    from an installed wheel (the migration files are listed in
    :file:`pyproject.toml` ``[tool.setuptools.package-data]``).
    """
    return Path(__file__).resolve().parent / "migrations" / flavour


def discover_migrations(flavour: str) -> list[Migration]:
    """Return every migration file under :func:`_migrations_dir` in lex order.

    Args:
        flavour: backend flavour, e.g. ``"postgres"``. The directory
            :file:`<store>/migrations/<flavour>` must exist; an empty
            directory is acceptable (returns ``[]``).

    Returns:
        Migrations sorted by filename ascending. Lex order on the
        zero-padded ``NNNN_`` prefix is the same as id order, so the
        ``id`` column matches the apply order.

    Raises:
        MigrationError: if the directory is missing, if a filename does
            not match ``NNNN_<slug>.sql``, or if two files share the
            same numeric id.
    """
    base = _migrations_dir(flavour)
    if not base.exists():
        raise MigrationError(
            f"migrations directory missing: {base!s} "
            f"(flavour={flavour!r}); fresh installs ship the directory "
            f"with at least one migration so this should never happen "
            f"in practice"
        )
    if not base.is_dir():
        raise MigrationError(
            f"migrations path is not a directory: {base!s}"
        )

    out: list[Migration] = []
    seen_ids: dict[int, str] = {}
    for path in sorted(base.glob("*.sql")):
        match = _MIGRATION_FILENAME.match(path.name)
        if match is None:
            raise MigrationError(
                f"migration filename {path.name!r} in {base!s} does not "
                f"match the required ``NNNN_<slug>.sql`` shape"
            )
        mig_id = int(match.group("id"))
        if mig_id in seen_ids:
            raise MigrationError(
                f"duplicate migration id {mig_id} in {base!s}: "
                f"{seen_ids[mig_id]!r} vs {path.name!r}"
            )
        seen_ids[mig_id] = path.name
        out.append(
            Migration(
                id=mig_id,
                name=path.name,
                path=path,
                sql=path.read_text(encoding="utf-8"),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS _migrations (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def _ensure_history_table(conn: _ConnectionLike) -> None:
    """Idempotently create the ``_migrations`` history table.

    Runs in its own transaction (the surrounding caller may not be in
    a transaction yet); commits on clean exit.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(_HISTORY_DDL)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _applied_names(conn: _ConnectionLike) -> set[str]:
    """Return the set of migration filenames already recorded as applied."""
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM _migrations")
        rows = cur.fetchall()
    return {row[0] for row in rows}


def _apply_one(conn: _ConnectionLike, mig: Migration) -> None:
    """Apply a single migration in one transaction.

    Both the DDL body AND the matching ``INSERT INTO _migrations`` row
    live in the same transaction so a crash mid-DDL leaves no orphan
    history row, and a crash between DDL and history-record leaves the
    migration unrecorded — the next runner pass will simply re-apply
    the file (which is required to be idempotent at the SQL layer for
    this exact reason; the convention is that every shipped migration
    uses ``CREATE TABLE IF NOT EXISTS`` etc., matching the SQLite
    pattern at :file:`persistence/fact/migrations`).
    """
    try:
        with conn.cursor() as cur:
            cur.execute(mig.sql)
            cur.execute(
                "INSERT INTO _migrations (id, name) VALUES (%s, %s)",
                (mig.id, mig.name),
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def apply_migrations(
    conn: _ConnectionLike,
    *,
    flavour: str = "postgres",
) -> list[str]:
    """Apply every pending migration on ``conn``; return names applied.

    The runner is idempotent: a second call with the same migrations
    on disk and the same ``_migrations`` table state is a no-op (no
    DDL executed, no history rows inserted). It is also fail-fast:
    any error from a migration aborts the loop and propagates. The
    failing migration is NOT recorded, so re-running the runner will
    retry that migration (typically after the operator fixed the
    underlying issue, e.g. dropped a half-applied object).

    Args:
        conn: a psycopg-compatible Connection. The runner uses
            ``conn.cursor()``, ``conn.commit()``, ``conn.rollback()``;
            it does NOT close the connection.
        flavour: backend flavour to load migrations for; default
            ``"postgres"`` since v0.8 only ships that one.

    Returns:
        List of migration filenames applied in this invocation, in
        application order. Empty list if everything was already up to
        date.

    Raises:
        MigrationError: on filename / discovery errors.
        psycopg.errors.*: any driver-side error from a migration body.
    """
    _ensure_history_table(conn)
    applied = _applied_names(conn)

    discovered = discover_migrations(flavour)
    just_applied: list[str] = []
    for mig in discovered:
        if mig.name in applied:
            continue
        _apply_one(conn, mig)
        just_applied.append(mig.name)
    return just_applied


# ---------------------------------------------------------------------------
# Convenience: apply via a callable that yields a connection
# ---------------------------------------------------------------------------
def apply_migrations_with_pool(
    pool: Any,
    *,
    flavour: str = "postgres",
) -> list[str]:
    """Apply migrations on a connection checked out of ``pool``.

    Thin wrapper for :class:`psycopg_pool.ConnectionPool` callers. The
    pool's ``connection()`` context manager is the canonical way to
    borrow a connection; this helper uses it so callers don't have to
    replicate the pattern.

    Args:
        pool: any object exposing a ``connection()`` context-manager
            yielding a psycopg-compatible connection.
        flavour: see :func:`apply_migrations`.

    Returns:
        Same shape as :func:`apply_migrations`.
    """
    with pool.connection() as conn:
        return apply_migrations(conn, flavour=flavour)


__all__ = [
    "Migration",
    "MigrationError",
    "apply_migrations",
    "apply_migrations_with_pool",
    "discover_migrations",
]
