"""Tests for the PG6 migration runner.

Per task #169 brief — the runner must:

1. Apply a fresh schema from ``migrations/postgres/*.sql``.
2. Be idempotent (re-init does nothing if all migrations are applied).
3. Record each applied migration in the ``_migrations`` history table.
4. Fail-fast on a bad SQL body (no half-applied row, no orphan history).
5. Discover migration files in lex order regardless of glob ordering.

The live-Postgres tests are gated on ``PERSISTENCE_PG_DSN`` and skip
clean when unset. The discovery / filename-validation tests do NOT
need a database — they exercise the pure-Python path against a temp
directory and always run.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Iterator

import pytest

from persistence.store._migration_runner import (
    MigrationError,
    apply_migrations,
    apply_migrations_with_pool,
    discover_migrations,
)


# ---------------------------------------------------------------------------
# Live-Postgres fixture
# ---------------------------------------------------------------------------
_PG_DSN = os.environ.get("PERSISTENCE_PG_DSN", "")
_PG_DSN_REASON = (
    "PERSISTENCE_PG_DSN env var not set; skipping live-PG migration "
    "runner tests"
)


@pytest.fixture
def pg_dsn() -> str:
    if not _PG_DSN:
        pytest.skip(_PG_DSN_REASON)
    return _PG_DSN


@pytest.fixture
def pg_pool(pg_dsn: str) -> Iterator:
    """Open a per-test Postgres pool against a fresh schema namespace.

    Mirrors the pattern from ``tests/store/test_postgres.py`` so each
    test runs in isolation against a clean schema.
    """
    import psycopg
    from psycopg_pool import ConnectionPool

    schema = f"test_pg6_runner_{uuid.uuid4().hex[:12]}"
    setup_conn = psycopg.connect(_PG_DSN)
    setup_conn.autocommit = True
    try:
        with setup_conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        setup_conn.close()

    sep = "&" if "?" in _PG_DSN else "?"
    scoped_dsn = f"{_PG_DSN}{sep}options=-c%20search_path%3D{schema}"

    def _configure(conn: "psycopg.Connection") -> None:
        conn.autocommit = False

    pool = ConnectionPool(
        conninfo=scoped_dsn,
        min_size=1,
        max_size=2,
        timeout=10.0,
        configure=_configure,
        open=True,
    )
    try:
        yield pool
    finally:
        pool.close()
        cleanup_conn = psycopg.connect(_PG_DSN)
        cleanup_conn.autocommit = True
        try:
            with cleanup_conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            cleanup_conn.close()


# ---------------------------------------------------------------------------
# Discovery (no DB required)
# ---------------------------------------------------------------------------
class TestDiscovery:
    def test_postgres_flavour_finds_0001(self):
        """The shipped 0001_datom_log.sql is discovered in the repo."""
        migrations = discover_migrations("postgres")
        names = [m.name for m in migrations]
        assert "0001_datom_log.sql" in names
        assert migrations[0].id == 1

    def test_lex_order_matches_id_order(self):
        """Migrations are returned in filename-lex order, which matches
        id order under the zero-padded NNNN_ convention."""
        migrations = discover_migrations("postgres")
        ids = [m.id for m in migrations]
        assert ids == sorted(ids)

    def test_unknown_flavour_raises(self):
        with pytest.raises(MigrationError, match="migrations directory missing"):
            discover_migrations("does-not-exist-flavour")

    def test_bad_filename_format_raises(self, tmp_path: Path, monkeypatch):
        """Non-conforming filenames raise :class:`MigrationError`."""
        # Build a temp migrations tree under a fake module-relative
        # path. We monkeypatch the runner's directory resolver to point
        # at our fake tree.
        bogus_dir = tmp_path / "bogus_flavour"
        bogus_dir.mkdir()
        (bogus_dir / "not_a_migration.sql").write_text("-- garbage")

        from persistence.store import _migration_runner as runner_mod

        monkeypatch.setattr(
            runner_mod,
            "_migrations_dir",
            lambda flavour: bogus_dir,
        )
        with pytest.raises(MigrationError, match="does not match"):
            discover_migrations("bogus_flavour")

    def test_duplicate_id_raises(self, tmp_path: Path, monkeypatch):
        """Two files claiming the same numeric id raise."""
        from persistence.store import _migration_runner as runner_mod

        dup_dir = tmp_path / "dup_flavour"
        dup_dir.mkdir()
        (dup_dir / "0001_alpha.sql").write_text("-- ok")
        (dup_dir / "0001_beta.sql").write_text("-- ok")
        monkeypatch.setattr(
            runner_mod,
            "_migrations_dir",
            lambda flavour: dup_dir,
        )
        with pytest.raises(MigrationError, match="duplicate migration id"):
            discover_migrations("dup_flavour")


# ---------------------------------------------------------------------------
# Live-Postgres tests
# ---------------------------------------------------------------------------
class TestLiveApply:
    def test_fresh_schema_applied(self, pg_pool):
        """First invocation against a fresh schema applies every
        on-disk migration and records each one."""
        applied = apply_migrations_with_pool(pg_pool, flavour="postgres")
        assert "0001_datom_log.sql" in applied

        with pg_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name FROM _migrations ORDER BY id ASC"
                )
                names = [row[0] for row in cur.fetchall()]
            conn.commit()
        assert names == applied

    def test_idempotent_reinvocation(self, pg_pool):
        """A second invocation with no new migrations on disk is a
        no-op — empty return list, no extra history rows."""
        first = apply_migrations_with_pool(pg_pool, flavour="postgres")
        assert first  # at least one migration applied first time

        second = apply_migrations_with_pool(pg_pool, flavour="postgres")
        assert second == []

        # _migrations table count is unchanged after the second call.
        with pg_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM _migrations")
                row = cur.fetchone()
            conn.commit()
        assert row is not None
        assert int(row[0]) == len(first)

    def test_records_applied_at(self, pg_pool):
        """``applied_at`` is set by the server; rows have a non-null
        timestamp after the apply."""
        apply_migrations_with_pool(pg_pool, flavour="postgres")
        with pg_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT applied_at FROM _migrations WHERE id = 1"
                )
                row = cur.fetchone()
            conn.commit()
        assert row is not None
        assert row[0] is not None  # timestamp populated

    def test_fail_fast_on_bad_sql(self, pg_pool, tmp_path: Path, monkeypatch):
        """A migration with broken SQL aborts the apply loop and is
        NOT recorded; subsequent invocations re-attempt it."""
        from persistence.store import _migration_runner as runner_mod

        broken_dir = tmp_path / "broken_flavour"
        broken_dir.mkdir()
        # Two migrations: first is fine, second is broken. After the
        # apply we should see the first recorded and the second NOT
        # recorded (and its DDL not applied).
        (broken_dir / "0001_ok.sql").write_text(
            "CREATE TABLE pg6_test_ok (id INTEGER)"
        )
        (broken_dir / "0002_broken.sql").write_text(
            "CREATE TABLE this_is_not_valid_sql ((bad column shape;"
        )
        monkeypatch.setattr(
            runner_mod,
            "_migrations_dir",
            lambda flavour: broken_dir,
        )

        with pytest.raises(Exception):
            # Driver-side error propagates unwrapped from the runner.
            apply_migrations_with_pool(pg_pool, flavour="broken_flavour")

        # Verify: 0001 is recorded; 0002 is NOT.
        with pg_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name FROM _migrations ORDER BY id ASC"
                )
                names = [row[0] for row in cur.fetchall()]
            conn.commit()
        assert names == ["0001_ok.sql"]

        # The broken migration's table does not exist.
        with pg_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = 'this_is_not_valid_sql')"
                )
                row = cur.fetchone()
            conn.commit()
        assert row is not None
        assert row[0] is False

    def test_postgres_store_init_uses_runner(self, pg_dsn: str):
        """End-to-end: ``PostgresStore.__init__`` triggers the runner;
        opening a fresh schema results in ``datom_log`` + ``tx_allocator``
        + ``_migrations`` being present."""
        from persistence.store.postgres import PostgresStore
        import psycopg

        schema = f"test_pg6_init_{uuid.uuid4().hex[:12]}"
        setup_conn = psycopg.connect(pg_dsn)
        setup_conn.autocommit = True
        try:
            with setup_conn.cursor() as cur:
                cur.execute(f'CREATE SCHEMA "{schema}"')
        finally:
            setup_conn.close()

        sep = "&" if "?" in pg_dsn else "?"
        scoped_dsn = f"{pg_dsn}{sep}options=-c%20search_path%3D{schema}"
        store = PostgresStore(dsn=scoped_dsn)
        try:
            with store._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = current_schema() "
                        "ORDER BY table_name"
                    )
                    tables = [row[0] for row in cur.fetchall()]
                conn.commit()
            assert "_migrations" in tables
            assert "datom_log" in tables
            assert "tx_allocator" in tables
        finally:
            store.close()
            cleanup_conn = psycopg.connect(pg_dsn)
            cleanup_conn.autocommit = True
            try:
                with cleanup_conn.cursor() as cur:
                    cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
            finally:
                cleanup_conn.close()
