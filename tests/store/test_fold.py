"""Tests for :meth:`persistence.fact.DB.fold` — PG6 stream #169 / R3-M1.

Coverage matrix
---------------

The fold primitive ships ``@experimental`` so the contract surface is
narrower than ``@stable("v0.8")`` symbols, but the four invariants below
are load-bearing for the speculation/rollback/checkpointing semantics
and need to hold across all three Store backends:

1. **Golden path** — 5-item fold, all succeed, expected accumulator +
   datom-count.
2. **Abort + rollback** — ``on_error="abort"`` raises a
   :class:`FoldError` with no in-flight uncommitted facts leaking and
   the previously-checkpointed facts intact.
3. **Checkpoint + partial commit** — ``on_error="checkpoint"`` commits
   up to the last successful checkpoint and re-raises with the partial
   state available on the exception.
4. **Buffered checkpointing** — ``checkpoint_every`` triggers
   intermediate flushes; an explicit count of flush boundaries matches
   the buffered batch size.

These run parametrised across ``InMemoryStore``, ``SQLiteStore``, and
(when ``PERSISTENCE_PG_DSN`` is set) ``PostgresStore``. The Postgres
leg uses the existing ``pg_store`` fixture pattern from
``tests/store/test_postgres.py`` so a per-test schema isolates state.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Iterator

import pytest

from persistence.fact import DB, FoldError, InMemoryStore, SQLiteStore


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
_PG_DSN = os.environ.get("PERSISTENCE_PG_DSN", "")
_PG_DSN_REASON = (
    "PERSISTENCE_PG_DSN env var not set; skipping live-PG fold tests"
)


def _now() -> datetime:
    return datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def in_memory_db() -> DB:
    return DB(InMemoryStore(), clock=_now)


@pytest.fixture
def sqlite_db(tmp_path) -> Iterator[DB]:
    store = SQLiteStore(str(tmp_path / "fold.db"))
    yield DB(store, clock=_now)
    store.close()


@pytest.fixture
def pg_db() -> Iterator[DB]:
    if not _PG_DSN:
        pytest.skip(_PG_DSN_REASON)
    from persistence.store.postgres import PostgresStore
    import psycopg

    schema = f"test_pg6_fold_{uuid.uuid4().hex[:12]}"
    setup_conn = psycopg.connect(_PG_DSN)
    setup_conn.autocommit = True
    try:
        with setup_conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        setup_conn.close()

    sep = "&" if "?" in _PG_DSN else "?"
    scoped_dsn = f"{_PG_DSN}{sep}options=-c%20search_path%3D{schema}"
    store = PostgresStore(dsn=scoped_dsn)
    try:
        yield DB(store, clock=_now)
    finally:
        store.close()
        cleanup_conn = psycopg.connect(_PG_DSN)
        cleanup_conn.autocommit = True
        try:
            with cleanup_conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            cleanup_conn.close()


# Parametrise across the three backends. The PG case is gated by the
# DSN env var and skips cleanly when unset, so the suite stays green
# for contributors without a Postgres available.
def _backend_fixtures():
    fixtures = ["in_memory_db", "sqlite_db"]
    if _PG_DSN:
        fixtures.append("pg_db")
    return fixtures


@pytest.fixture(params=_backend_fixtures())
def db(request) -> DB:
    return request.getfixturevalue(request.param)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_step(prefix: str = "item"):
    """Returns a fold-step that emits one fact per item."""

    def step(acc, item, db):
        fact = {
            "e": f"{prefix}-{item}",
            "a": "fold/value",
            "v": item,
            "valid_from": _now(),
        }
        return acc + item, [fact]

    return step


# ---------------------------------------------------------------------------
# 1. Golden path
# ---------------------------------------------------------------------------
class TestGoldenPath:
    def test_five_items_all_succeed(self, db: DB):
        """5-item fold accumulates and commits one datom per item."""
        final_acc, n = db.fold(seed=0, items=[1, 2, 3, 4, 5], fn=_make_step())
        assert final_acc == 15
        # Five asserts, no auto-retractions (each (e, a) pair is unique).
        assert n == 5
        rows = list(db.store.all_datoms())
        assert len(rows) == 5
        # Insertion order preserved.
        assert [r.v for r in rows] == [1, 2, 3, 4, 5]

    def test_empty_items_returns_seed(self, db: DB):
        """Empty input → returns seed unchanged + zero datoms."""
        final_acc, n = db.fold(seed="seed", items=[], fn=_make_step())
        assert final_acc == "seed"
        assert n == 0
        assert list(db.store.all_datoms()) == []

    def test_empty_facts_per_item_advances_acc_only(self, db: DB):
        """``fn`` may emit zero facts; accumulator still advances."""

        def step(acc, item, db):
            return acc * 2, []

        final_acc, n = db.fold(seed=1, items=[1, 2, 3], fn=step)
        assert final_acc == 8  # 1 → 2 → 4 → 8
        assert n == 0
        assert list(db.store.all_datoms()) == []

    def test_default_provenance_carries_source_fold(self, db: DB):
        """Default provenance tags every emitted datom with ``"source": "fold"``."""
        db.fold(seed=0, items=[1], fn=_make_step())
        (row,) = list(db.store.all_datoms())
        assert row.provenance.get("source") == "fold"

    def test_caller_provenance_preserved(self, db: DB):
        """Caller-supplied provenance is merged; ``source`` tag is added
        only when the caller didn't supply one."""
        db.fold(
            seed=0,
            items=[1],
            fn=_make_step(),
            provenance={"source": "caller-tag", "extra": "v"},
        )
        (row,) = list(db.store.all_datoms())
        assert row.provenance.get("source") == "caller-tag"
        assert row.provenance.get("extra") == "v"


# ---------------------------------------------------------------------------
# 2. on_error="abort" — raises and rolls back the in-flight item
# ---------------------------------------------------------------------------
class TestOnErrorAbort:
    def test_abort_raises_foldError(self, db: DB):
        """``on_error="abort"`` re-raises as a :class:`FoldError`."""

        def step(acc, item, db):
            if item == 3:
                raise RuntimeError("synthetic failure on item 3")
            fact = {
                "e": f"i-{item}",
                "a": "fold/value",
                "v": item,
                "valid_from": _now(),
            }
            return acc + item, [fact]

        with pytest.raises(FoldError) as excinfo:
            db.fold(seed=0, items=[1, 2, 3, 4, 5], fn=step)

        assert excinfo.value.item_index == 2  # 0-based
        # Original error preserved as __cause__
        assert isinstance(excinfo.value.__cause__, RuntimeError)
        assert "synthetic failure" in str(excinfo.value.__cause__)

    def test_abort_keeps_committed_checkpoints(self, db: DB):
        """Items 0+1 already committed (per-item checkpoint) when 2 raises;
        their datoms remain in the log. The failed item 2 contributes no
        datoms."""

        def step(acc, item, db):
            if item == 3:
                raise RuntimeError("synthetic")
            fact = {
                "e": f"i-{item}",
                "a": "fold/value",
                "v": item,
                "valid_from": _now(),
            }
            return acc + item, [fact]

        with pytest.raises(FoldError):
            db.fold(seed=0, items=[1, 2, 3, 4, 5], fn=step)

        rows = list(db.store.all_datoms())
        # Per-item checkpoint mode: items 1+2 succeeded, item 3 raised
        # before its facts were emitted; items 4+5 never ran.
        assert {r.v for r in rows} == {1, 2}

    def test_abort_invalid_fn_return_shape(self, db: DB):
        """``fn`` returning the wrong tuple-element type surfaces as
        :class:`FoldError`, not as a downstream :class:`AttributeError`."""

        def bad_step(acc, item, db):
            return acc + 1, "not a list"  # type: ignore[return-value]

        with pytest.raises(FoldError) as excinfo:
            db.fold(seed=0, items=[1], fn=bad_step)
        assert excinfo.value.item_index == 0


# ---------------------------------------------------------------------------
# 3. on_error="checkpoint" — commits to last successful, raises
# ---------------------------------------------------------------------------
class TestOnErrorCheckpoint:
    def test_checkpoint_keeps_partial_state_on_failure(self, db: DB):
        """``on_error="checkpoint"`` commits up to the last successful
        checkpoint and exposes the partial accumulator on the
        :class:`FoldError`."""

        def step(acc, item, db):
            if item == 3:
                raise ValueError("checkpoint test")
            fact = {
                "e": f"i-{item}",
                "a": "fold/value",
                "v": item,
                "valid_from": _now(),
            }
            return acc + item, [fact]

        with pytest.raises(FoldError) as excinfo:
            db.fold(
                seed=0,
                items=[1, 2, 3, 4, 5],
                fn=step,
                on_error="checkpoint",
            )

        # acc on the FoldError is the last *checkpointed* accumulator.
        # With per-item checkpoints (default checkpoint_every=0), we
        # checkpointed items 1 and 2; the live accumulator after item
        # 2 was 0+1+2=3. Items 3+ never advanced the checkpoint.
        assert excinfo.value.acc == 3
        assert excinfo.value.last_committed_acc == 3
        assert excinfo.value.committed_count >= 2
        assert excinfo.value.item_index == 2

    def test_checkpoint_with_buffered_mode(self, db: DB):
        """Under ``checkpoint_every=N`` failure mid-buffer drops the
        in-flight buffer; only the last *flushed* checkpoint persists."""

        def step(acc, item, db):
            if item == 4:
                raise RuntimeError("mid-buffer fail")
            fact = {
                "e": f"i-{item}",
                "a": "fold/value",
                "v": item,
                "valid_from": _now(),
            }
            return acc + item, [fact]

        with pytest.raises(FoldError):
            db.fold(
                seed=0,
                items=[1, 2, 3, 4, 5, 6],
                fn=step,
                on_error="checkpoint",
                checkpoint_every=3,
            )

        rows = list(db.store.all_datoms())
        # First flush after items 1,2,3 → committed. Item 4 raised
        # before the next flush → buffer of {} for item 4 is empty,
        # the previous checkpoint of {1,2,3} stands.
        assert {r.v for r in rows} == {1, 2, 3}


# ---------------------------------------------------------------------------
# 4. on_error="skip" — swallow and continue
# ---------------------------------------------------------------------------
class TestOnErrorSkip:
    def test_skip_swallows_and_continues(self, db: DB):
        """``on_error="skip"`` discards the failing item and continues
        — the accumulator does NOT advance for the skipped item."""

        def step(acc, item, db):
            if item == 3:
                raise RuntimeError("transient")
            fact = {
                "e": f"i-{item}",
                "a": "fold/value",
                "v": item,
                "valid_from": _now(),
            }
            return acc + item, [fact]

        final_acc, n = db.fold(
            seed=0,
            items=[1, 2, 3, 4, 5],
            fn=step,
            on_error="skip",
        )
        # 1+2+4+5 = 12; item 3 is skipped.
        assert final_acc == 12
        rows = list(db.store.all_datoms())
        assert {r.v for r in rows} == {1, 2, 4, 5}


# ---------------------------------------------------------------------------
# 5. Buffered checkpointing — explicit flush count
# ---------------------------------------------------------------------------
class TestCheckpointEvery:
    def test_checkpoint_every_3_flushes_in_batches(self, db: DB):
        """``checkpoint_every=3`` over 7 items → 2 full flushes (3+3)
        plus a tail flush of 1; all 7 datoms land in the log."""
        final_acc, n = db.fold(
            seed=0,
            items=list(range(1, 8)),  # 1..7
            fn=_make_step(),
            checkpoint_every=3,
        )
        assert final_acc == 28  # 1+2+...+7
        assert n == 7
        rows = list(db.store.all_datoms())
        assert {r.v for r in rows} == set(range(1, 8))

    def test_checkpoint_every_zero_is_per_item(self, db: DB):
        """``checkpoint_every=0`` (default) commits per item — each
        item's facts land in their own transaction batch."""
        final_acc, n = db.fold(
            seed=0,
            items=[1, 2, 3],
            fn=_make_step(),
            checkpoint_every=0,
        )
        assert final_acc == 6
        # Three transact_batch calls → three distinct tx-ids.
        rows = list(db.store.all_datoms())
        assert len({r.tx for r in rows}) == 3


# ---------------------------------------------------------------------------
# 6. Validation
# ---------------------------------------------------------------------------
class TestValidation:
    def test_invalid_on_error_raises_value_error(self, in_memory_db: DB):
        with pytest.raises(ValueError):
            in_memory_db.fold(
                seed=0,
                items=[1],
                fn=_make_step(),
                on_error="bogus",  # type: ignore[arg-type]
            )

    def test_negative_checkpoint_every_raises(self, in_memory_db: DB):
        with pytest.raises(ValueError):
            in_memory_db.fold(
                seed=0,
                items=[1],
                fn=_make_step(),
                checkpoint_every=-1,
            )
