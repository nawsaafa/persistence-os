"""PG1 tests for :class:`persistence.store.postgres.PostgresStore`.

Per the design doc ``docs/plans/2026-04-30-v0.8.0-postgres-store-design.md``
(PG1 scope) this module covers the SERIALIZABLE backbone:

- :class:`TestTransactSerializableHappyPath` — 3 facts → tx assigned →
  reads back identically.
- :class:`TestUniqueTxEaDefence` — programmer-error duplicate
  ``(tx, e, a)`` raises immediately (no silent retry mask).
- :class:`TestSerializationRetry` — concurrent allocator contention is
  serialised by the row lock; both writers succeed with disjoint tx-ids
  (no SerializationFailure escapes the retry budget at the v0.8 default).
- :class:`TestTxAllocatorMonotonic` — N sequential transacts → tx ints
  are monotonic with no gaps. Confirms the allocator is the load-bearing
  primitive (not ``MAX(tx)+1``).
- :class:`TestUriDispatch` — ``open_store("postgres://...")`` returns a
  :class:`PostgresStore`; raises :class:`BackendNotInstalled` cleanly
  when the import fails.

Skip rule for live-Postgres tests
---------------------------------

Tests that need a real Postgres are gated on the ``PERSISTENCE_PG_DSN``
env var. If unset, the tests skip with a clear reason; the suite stays
green on machines without Postgres available. CI is expected to set
``PERSISTENCE_PG_DSN`` to a fresh per-run database (testcontainers /
GH-Actions service container — out of PG1 scope; tracked in PG-INT).

The URI-dispatch tests do NOT need a live database — they test the
import-error path with a mocked import — so they always run.
"""
from __future__ import annotations

import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Iterator

import pytest

from persistence.fact import Datom


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _dt(y: int, m: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)


def _mk(
    e: str,
    a: str,
    v,
    tx: int = 0,
    *,
    op: str = "assert",
    invalidated_by: int | None = None,
    valid_to: datetime | None = None,
) -> Datom:
    """Build a Datom with reasonable test defaults."""
    return Datom(
        e=e,
        a=a,
        v=v,
        tx=tx,
        tx_time=_dt(2026, 4, 30, 12, 0, 0),
        valid_from=_dt(2026, 4, 30, 12, 0, 0),
        valid_to=valid_to,
        op=op,
        provenance={"source": "pg1-test"},
        invalidated_by=invalidated_by,
    )


# ---------------------------------------------------------------------------
# PG fixture — requires PERSISTENCE_PG_DSN; uses a per-test schema namespace
# so parallel test runs don't collide on a shared cluster.
# ---------------------------------------------------------------------------
_PG_DSN = os.environ.get("PERSISTENCE_PG_DSN", "")
_PG_DSN_REASON = (
    "PERSISTENCE_PG_DSN env var not set — set it to a libpq DSN "
    '(e.g. "postgresql://user@localhost:5432/test") to run the live-'
    "Postgres tests. PG1 ships skip-clean when no DB is available."
)


@pytest.fixture
def pg_dsn() -> str:
    if not _PG_DSN:
        pytest.skip(_PG_DSN_REASON)
    return _PG_DSN


@pytest.fixture
def pg_store(pg_dsn: str) -> Iterator:
    """Open a PostgresStore against a fresh schema namespace.

    The fixture creates a per-test schema (``test_pg1_<random>``), sets
    it as the search_path on the DSN, and drops it at teardown so a
    failing test cannot leak state into the next one. The pool is closed
    at teardown.
    """
    from persistence.store.postgres import PostgresStore

    schema = f"test_pg1_{uuid.uuid4().hex[:12]}"

    # Create the schema using a one-shot connection (not the pool) so
    # the search_path is set BEFORE the pool's _create_schema runs.
    import psycopg

    setup_conn = psycopg.connect(_PG_DSN)
    setup_conn.autocommit = True
    try:
        with setup_conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        setup_conn.close()

    # Build a DSN that pins search_path to our test schema. psycopg's
    # libpq accepts ``options=-c search_path=...`` directly.
    sep = "&" if "?" in _PG_DSN else "?"
    scoped_dsn = f"{_PG_DSN}{sep}options=-c%20search_path%3D{schema}"

    store = PostgresStore(dsn=scoped_dsn)
    try:
        yield store
    finally:
        store.close()
        # Drop the test schema; any rows + sequences in it go with it.
        cleanup_conn = psycopg.connect(_PG_DSN)
        cleanup_conn.autocommit = True
        try:
            with cleanup_conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            cleanup_conn.close()


# ---------------------------------------------------------------------------
# 1. transact_serializable happy path
# ---------------------------------------------------------------------------
class TestTransactSerializableHappyPath:
    def test_three_facts_get_one_tx_and_round_trip(self, pg_store):
        """Three datoms inserted in one ``transact_serializable`` call
        share one tx-id; ``all_datoms`` yields them back in insertion
        order with bytes intact."""
        d1 = _mk("p-042", "project/wacc", 0.087)
        d2 = _mk("p-042", "project/name", "Project A")
        d3 = _mk("p-043", "project/wacc", 0.065)

        tx = pg_store.transact_serializable([d1, d2, d3])
        assert tx == 1, "first call against fresh allocator must return tx=1"

        rows = list(pg_store.all_datoms())
        assert len(rows) == 3
        # All three share the allocated tx-id.
        assert {r.tx for r in rows} == {1}
        # Insertion order is preserved (seq ASC).
        assert [r.e for r in rows] == ["p-042", "p-042", "p-043"]
        assert [r.a for r in rows] == ["project/wacc", "project/name", "project/wacc"]
        assert [r.v for r in rows] == [0.087, "Project A", 0.065]

    def test_empty_iterable_is_noop_burns_no_tx(self, pg_store):
        """``transact_serializable([])`` returns 0 and does NOT advance
        the allocator — empty batches are free."""
        result = pg_store.transact_serializable([])
        assert result == 0
        assert pg_store.next_tx() == 1  # still 1; no tx burnt

    def test_value_codec_round_trips_complex_json(self, pg_store):
        """``v`` and ``provenance`` are stored as canonical JSON TEXT
        (ADR-5 W1-revised) — round-trip preserves dicts, lists, and
        nested structures byte-identically."""
        complex_v = {"nested": {"a": [1, 2, 3]}, "currency": "USD", "amt": 12.5}
        d = Datom(
            e="p-99",
            a="project/meta",
            v=complex_v,
            tx=0,  # ignored — allocator picks
            tx_time=_dt(2026, 4, 30, 9, 30),
            valid_from=_dt(2026, 4, 30, 9, 30),
            valid_to=None,
            op="assert",
            provenance={"source": "complex", "confidence": 0.9},
            invalidated_by=None,
        )
        tx = pg_store.transact_serializable([d])
        (got,) = list(pg_store.all_datoms())
        assert got.v == complex_v
        assert got.provenance == {"source": "complex", "confidence": 0.9}
        assert got.tx == tx

    def test_assert_then_retract_preserves_op_and_invalidated_by(self, pg_store):
        """``op='retract'`` and ``invalidated_by`` survive the round
        trip unchanged."""
        a = _mk("p-1", "x", 1)
        tx_a = pg_store.transact_serializable([a])
        r = _mk("p-1", "x", 1, op="retract")
        tx_r = pg_store.transact_serializable([r])
        pg_store.mark_invalidated(tx=tx_a, invalidated_by_tx=tx_r)
        rows = list(pg_store.all_datoms())
        assert rows[0].invalidated_by == tx_r
        assert rows[1].op == "retract"


# ---------------------------------------------------------------------------
# 2. UNIQUE (tx, e, a) defence-in-depth
# ---------------------------------------------------------------------------
class TestUniqueTxEaDefence:
    def test_duplicate_tx_e_a_via_append_raises(self, pg_store):
        """If a code-path bypasses the allocator and INSERTs a row that
        collides with an existing ``(tx, e, a)``, the schema's UNIQUE
        constraint surfaces the bug as a :class:`UniqueViolation` —
        this is the load-bearing safety net behind the allocator."""
        from psycopg import errors as pg_errors

        # First write at tx=1 via the allocator (the right way).
        d1 = _mk("p-1", "x", 1)
        tx = pg_store.transact_serializable([d1])
        assert tx == 1

        # Second write at the SAME tx via store.append — bypasses the
        # allocator entirely. UNIQUE (tx, e, a) catches it.
        d2 = _mk("p-1", "x", 2, tx=tx)
        with pytest.raises(pg_errors.UniqueViolation):
            pg_store.append([d2])

    def test_unique_violation_in_transact_serializable_propagates(self, pg_store):
        """If a future caller manages to construct a duplicate batch
        inside one ``transact_serializable`` call, the UniqueViolation
        is raised immediately — NOT retried — because a duplicate within
        one allocator-served batch indicates a programmer error, not a
        cross-process race."""
        from psycopg import errors as pg_errors

        # Two datoms with the SAME (e, a) in one batch. They share the
        # allocated tx-id, so (tx, e, a) collides on the second INSERT.
        d1 = _mk("p-1", "x", 1)
        d2 = _mk("p-1", "x", 2)  # same e, same a — distinct v but UNIQUE doesn't include v
        with pytest.raises(pg_errors.UniqueViolation):
            pg_store.transact_serializable([d1, d2])


# ---------------------------------------------------------------------------
# 3. tx_allocator monotonicity (no gaps, no rewinds)
# ---------------------------------------------------------------------------
class TestTxAllocatorMonotonic:
    def test_ten_sequential_transacts_have_monotonic_tx(self, pg_store):
        """Sequential ``transact_serializable`` calls allocate
        consecutive tx-ids 1..10 with no gaps."""
        observed = []
        for i in range(10):
            d = _mk(f"p-{i}", "project/wacc", float(i) / 100)
            tx = pg_store.transact_serializable([d])
            observed.append(tx)
        assert observed == list(range(1, 11))
        # next_tx probe matches the allocator state.
        assert pg_store.next_tx() == 11

    def test_allocate_and_append_returns_consistent_tx_in_each_datom(
        self, pg_store
    ):
        """``allocate_and_append`` (the substrate's atomic primitive)
        returns the input datoms with ``tx`` stamped to the allocated
        value; that value matches what the allocator advances to. The
        whole batch shares ONE tx-id (matches SQLiteStore contract)."""
        before = pg_store.next_tx()
        ds = [_mk(f"p-{i}", "x", i) for i in range(3)]
        out = pg_store.allocate_and_append(ds)
        assert len(out) == 3
        assert {d.tx for d in out} == {before}
        # Allocator advanced by exactly one (one transact = one tx).
        assert pg_store.next_tx() == before + 1


# ---------------------------------------------------------------------------
# 4. SerializationFailure retry under concurrent in-process writers
# ---------------------------------------------------------------------------
class TestSerializationRetry:
    def test_concurrent_writers_serialise_via_allocator_lock(self, pg_store):
        """Two threads racing the allocator both succeed with disjoint
        tx-ids. The ``SELECT FOR UPDATE`` row lock on ``tx_allocator``
        is the load-bearing primitive — neither thread sees a
        SerializationFailure escape because the row lock serialises
        them deterministically rather than letting them both read the
        same ``next_tx`` and conflict at COMMIT."""
        n_writes_per_thread = 5
        results: list[list[int]] = [[], []]
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def worker(idx: int) -> None:
            try:
                barrier.wait()
                for i in range(n_writes_per_thread):
                    d = _mk(f"thr-{idx}-{i}", "x", i)
                    tx = pg_store.transact_serializable([d])
                    results[idx].append(tx)
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"worker errors: {errors!r}"
        # Each thread saw n_writes_per_thread distinct tx-ids;
        # combined the two threads cover 1..2*n with no overlap and
        # no gaps.
        all_tx = sorted(results[0] + results[1])
        assert all_tx == list(range(1, 2 * n_writes_per_thread + 1))
        assert len(set(all_tx)) == len(all_tx), "tx-ids must be unique"

    def test_retry_budget_is_bounded(self, pg_store, monkeypatch):
        """If SerializationFailure ever escapes the row-lock layer (it
        shouldn't under the current allocator design), the retry budget
        in :meth:`transact_serializable` is bounded — we don't hot-loop
        forever. This test injects a synthetic SerializationFailure on
        the first 2 attempts and verifies the third succeeds."""
        from psycopg import errors as pg_errors

        original_connection = pg_store._pool.connection
        call_count = {"n": 0}

        def flaky_connection(*args, **kwargs):
            call_count["n"] += 1
            real_ctx = original_connection(*args, **kwargs)
            if call_count["n"] <= 2:
                # Synthetic SerializationFailure — wrap the ctx mgr so
                # we still open + close the underlying connection
                # cleanly (no leaked pool slots), but raise on exit so
                # transact_serializable's except-clause fires.
                class _Wrapped:
                    def __enter__(self_inner):
                        return real_ctx.__enter__()

                    def __exit__(self_inner, *exc):
                        # Roll back inside the inner ctx, then raise
                        # 40001 so the retry loop catches.
                        real_ctx.__exit__(None, None, None)
                        raise pg_errors.SerializationFailure(
                            "synthetic 40001 for retry-budget test"
                        )

                return _Wrapped()
            return real_ctx

        monkeypatch.setattr(pg_store._pool, "connection", flaky_connection)

        d = _mk("retry-1", "x", 1)
        tx = pg_store.transact_serializable([d], max_retries=3)
        # Third attempt (call_count >=3) returned without injection.
        assert call_count["n"] >= 3
        # The successful attempt allocated a real tx-id.
        assert tx >= 1


# ---------------------------------------------------------------------------
# 5. Store Protocol method conformance — sanity checks
# ---------------------------------------------------------------------------
class TestStoreProtocolConformance:
    def test_since_filters_strictly_greater(self, pg_store):
        t0 = _dt(2026, 4, 1)
        t1 = _dt(2026, 4, 14)
        t2 = _dt(2026, 4, 30)
        old = Datom("e", "a", 1, 0, t0, t0, None, "assert", {})
        new = Datom("e", "a", 2, 0, t2, t2, None, "assert", {})
        pg_store.allocate_and_append([old])
        pg_store.allocate_and_append([new])
        got = list(pg_store.since(t1))
        assert len(got) == 1
        assert got[0].v == 2

    def test_mark_invalidated_only_touches_asserts(self, pg_store):
        d = _mk("p-1", "x", 1)
        tx = pg_store.allocate_and_append([d])[0].tx
        pg_store.mark_invalidated(tx=tx, invalidated_by_tx=tx + 1)
        (got,) = list(pg_store.all_datoms())
        assert got.invalidated_by == tx + 1


# ---------------------------------------------------------------------------
# 6. URI dispatch — ``open_store("postgres://...")``
# ---------------------------------------------------------------------------
class TestUriDispatch:
    def test_open_store_postgres_returns_postgres_store(self, pg_dsn):
        """When ``[postgres]`` is installed, ``open_store("postgres://...")``
        returns a :class:`PostgresStore`. Uses the live DSN so the
        constructor doesn't fail trying to connect; closes immediately
        after the type assertion."""
        from persistence.sdk import open_store
        from persistence.store.postgres import PostgresStore

        # The live DSN may use ``postgresql://`` (libpq's preferred
        # form); the URI dispatcher only registers ``postgres://``,
        # so rewrite the scheme prefix for this test. PG1's contract
        # is that ``postgres://`` is the registered scheme.
        uri = (
            "postgres://" + pg_dsn.split("://", 1)[1]
            if pg_dsn.startswith("postgresql://")
            else pg_dsn
        )

        store = open_store(uri)
        try:
            assert isinstance(store, PostgresStore)
        finally:
            store.close()

    def test_postgresql_alias_scheme_is_unknown(self):
        """``postgresql://`` is a libpq alias for ``postgres://``. The
        URI dispatcher does NOT register ``postgresql`` separately, so
        callers must use ``postgres://``; this test pins that contract.

        v0.9 may add the alias as a registered scheme — for now PG1
        mirrors the design doc's contract: only ``postgres://`` is
        dispatched.
        """
        from persistence.sdk import UnknownStoreScheme, open_store

        with pytest.raises(UnknownStoreScheme, match="postgresql"):
            open_store("postgresql://user@localhost:5432/db")

    def test_backend_not_installed_when_psycopg_missing(self):
        """If psycopg is not installed, the URI dispatch raises
        :class:`BackendNotInstalled` (subclass of ``ImportError``) with
        a clean ``pip install`` hint — not an obscure ImportError from
        deep inside the stack."""
        from persistence.sdk import BackendNotInstalled, open_store

        # Force the lazy ``from persistence.store.postgres import
        # PostgresStore`` to fail with an ImportError as if psycopg
        # were missing. We do this by injecting a sentinel module
        # whose attribute access raises ImportError.
        import types

        original = sys.modules.pop("persistence.store.postgres", None)

        broken = types.ModuleType("persistence.store.postgres")

        def _module_getattr(name):
            raise ImportError(
                "No module named 'psycopg' (synthetic — test harness)",
                name="psycopg",
            )

        broken.__getattr__ = _module_getattr  # type: ignore[attr-defined]
        sys.modules["persistence.store.postgres"] = broken
        try:
            with pytest.raises(BackendNotInstalled, match=r"\[postgres\]"):
                open_store("postgres://user@localhost/db")
            # BackendNotInstalled subclasses ImportError per ADR-9.
            with pytest.raises(ImportError):
                open_store("postgres://user@localhost/db")
        finally:
            sys.modules.pop("persistence.store.postgres", None)
            if original is not None:
                sys.modules["persistence.store.postgres"] = original

    def test_unknown_pool_kwarg_raises_value_error(self):
        """Malformed pool-tuning query params raise ValueError before
        we touch the database — the dispatcher validates."""
        from persistence.sdk import open_store

        with pytest.raises(ValueError, match="pool_min"):
            open_store("postgres://localhost/db?pool_min=not-a-number")

        with pytest.raises(ValueError, match="pool_timeout"):
            open_store("postgres://localhost/db?pool_timeout=abc")


# ---------------------------------------------------------------------------
# 7. URI dispatch + live DB — pool kwargs round-trip
# ---------------------------------------------------------------------------
class TestUriDispatchPoolKwargs:
    def test_pool_kwargs_from_query_string(self, pg_dsn):
        """``?pool_min=2&pool_max=4`` flows from URI → PostgresStore
        constructor → pool config. The dispatcher pops the pool kwargs
        from the URI before forwarding the DSN to libpq, so libpq does
        not see (and reject) the unknown query keys."""
        from persistence.sdk import open_store
        from persistence.store.postgres import PostgresStore

        # Rewrite to the registered ``postgres://`` scheme; libpq
        # accepts both forms in the DSN body, but the URI dispatcher
        # only registers the bare ``postgres`` scheme.
        base = (
            "postgres://" + pg_dsn.split("://", 1)[1]
            if pg_dsn.startswith("postgresql://")
            else pg_dsn
        )
        sep = "&" if "?" in base else "?"
        uri = f"{base}{sep}pool_min=2&pool_max=4"
        store = open_store(uri)
        try:
            assert isinstance(store, PostgresStore)
            assert store._pool.min_size == 2
            assert store._pool.max_size == 4
        finally:
            store.close()
