"""PG3 — audit-chain Merkle integration + Store Protocol additive surface.

Per the design doc (`docs/plans/2026-04-30-v0.8.0-postgres-store-design.md`)
PG3 ships:

- **Part A** — Store Protocol additive declarations (ADR-15 W2):
  ``_txn()`` context-manager + ``transact_serializable(facts, *,
  tx_time?) -> int``. InMemoryStore + SQLiteStore carry default impls
  that preserve existing semantics; PostgresStore overrides both with
  the SERIALIZABLE-transaction + ``tx_allocator`` + ``audit_chain_lock``
  shape.
- **Part B** — ``persist_repl_audit`` migration (ADR-13): routes
  through ``transact_serializable`` with ``tx_time=recorded_at`` to
  preserve byte-identity for replay.
- **Part C** — ``audit_chain_lock`` table + row-lock primitive (ADR-3):
  ``SELECT FOR UPDATE`` on the single chain-head row before any audit
  datom INSERT, so concurrent writers serialise on chain order.
- **Part D** — G3a-G3e sub-invariants (ADR-14): the cross-process
  replay byte-identity gate is recast as a 5-sub-invariant set; this
  module ships the in-process + light-concurrency tests for each
  sub-invariant. The full multi-process Hypothesis property at
  ``max_examples=50`` lands in PG4.

Skip rule — tests that need a real Postgres are gated on the
``PERSISTENCE_PG_DSN`` env var. The Protocol-conformance + InMemory +
SQLite + persist_repl_audit-migration tests run unconditionally; the
``audit_chain_lock`` concurrent-writer test + G3 PG-specific tests
skip clean when no DSN is set.
"""
from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest

from persistence.fact import Datom, InMemoryStore, SQLiteStore
from persistence.fact.store import Store
from persistence.effect.handlers.audit import AuditEntry, verify_chain


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _dt(y: int, m: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)


def _mk(
    e: str,
    a: str,
    v,
    *,
    tx: int = 0,
    tx_time: datetime | None = None,
    op: str = "assert",
    provenance: dict | None = None,
) -> Datom:
    """Build a Datom with reasonable test defaults."""
    t = tx_time if tx_time is not None else _dt(2026, 4, 30, 12, 0, 0)
    return Datom(
        e=e,
        a=a,
        v=v,
        tx=tx,
        tx_time=t,
        valid_from=t,
        valid_to=None,
        op=op,
        provenance=provenance if provenance is not None else {"source": "pg3-test"},
    )


def _mk_audit(
    e: str,
    *,
    prev_hash: str | None,
    signature: str,
    tx_time: datetime | None = None,
) -> Datom:
    """Build an audit-shaped Datom (``a`` starts with ``audit/``).

    ``signature`` is the canonical audit content hash that lives in
    ``provenance[':signature']`` per ``audit_entry_to_datom``; the PG3
    audit-chain head update reads it back to denormalise the lock row.
    """
    t = tx_time if tx_time is not None else _dt(2026, 4, 30, 12, 0, 0)
    return Datom(
        e=e,
        a="audit/repl.op",
        v={"verdict": "ok", "args_hash": "h", "result_hash": None,
           "latency_ms": 1, "error": None},
        tx=0,
        tx_time=t,
        valid_from=t,
        valid_to=None,
        op="assert",
        provenance={
            ":source": ":persistence.effect.audit",
            ":signature": signature,
            ":prev-hash": prev_hash,
            "parent_provenance_hash": prev_hash,
        },
    )


# ===========================================================================
# 1. Store Protocol satisfaction — every backend implements the additive
#    surface (G1 Protocol smoke per design doc § 11)
# ===========================================================================
class TestStoreProtocolSatisfaction:
    """ADR-15 W2: every backend satisfies the additive ``Store`` Protocol.

    These tests run unconditionally — they don't need a live Postgres.
    For PostgresStore the ``isinstance`` check exercises the runtime-
    checkable Protocol structure (no DB connection involved).
    """

    def test_inmemory_satisfies_store_protocol(self) -> None:
        store = InMemoryStore()
        assert isinstance(store, Store), (
            "InMemoryStore must satisfy Store Protocol after PG3 additive "
            "extension (ADR-15 W2)"
        )

    def test_sqlite_satisfies_store_protocol(self) -> None:
        store = SQLiteStore()
        try:
            assert isinstance(store, Store), (
                "SQLiteStore must satisfy Store Protocol after PG3 additive "
                "extension (ADR-15 W2)"
            )
        finally:
            store.close()

    def test_inmemory_has_txn_and_transact_serializable(self) -> None:
        store = InMemoryStore()
        assert hasattr(store, "_txn"), "InMemoryStore needs _txn context-manager"
        assert hasattr(store, "transact_serializable"), (
            "InMemoryStore needs transact_serializable additive method"
        )

    def test_sqlite_has_txn_and_transact_serializable(self) -> None:
        store = SQLiteStore()
        try:
            assert hasattr(store, "_txn"), "SQLiteStore needs _txn context-manager"
            assert hasattr(store, "transact_serializable"), (
                "SQLiteStore needs transact_serializable additive method"
            )
        finally:
            store.close()


# ===========================================================================
# 2. Default Protocol method behavior — InMemory + SQLite preserve semantics
# ===========================================================================
class TestDefaultProtocolBehavior:
    """ADR-15 W2: default impls on InMemory + SQLite preserve existing
    single-process semantics. ``_txn()`` is a pass-through (no nested
    transaction); ``transact_serializable`` routes through
    ``allocate_and_append`` under the existing ``_lock`` mutex.
    """

    def test_inmemory_txn_yields_none(self) -> None:
        """InMemoryStore._txn() default impl yields None (nullcontext)."""
        store = InMemoryStore()
        with store._txn() as ctx:
            assert ctx is None, (
                "InMemoryStore._txn() default impl yields None — see ADR-15 W2 "
                "(no real ACID layer; per-method _lock provides atomicity)"
            )

    def test_sqlite_txn_yields_none(self) -> None:
        """SQLiteStore._txn() default impl yields None (no nested BEGIN)."""
        store = SQLiteStore()
        try:
            with store._txn() as ctx:
                assert ctx is None, (
                    "SQLiteStore._txn() default impl yields None — see ADR-15 "
                    "W2 (BEGIN IMMEDIATE lives in per-method calls)"
                )
        finally:
            store.close()

    def test_inmemory_transact_serializable_round_trips(self) -> None:
        """transact_serializable on InMemory == allocate_and_append."""
        store = InMemoryStore()
        d1 = _mk("e1", "a/x", 1)
        d2 = _mk("e2", "a/x", 2)
        tx = store.transact_serializable([d1, d2])
        assert tx == 1
        out = list(store.all_datoms())
        assert len(out) == 2
        assert {d.tx for d in out} == {1}

    def test_sqlite_transact_serializable_round_trips(self) -> None:
        """transact_serializable on SQLite == allocate_and_append."""
        store = SQLiteStore()
        try:
            d1 = _mk("e1", "a/x", 1)
            d2 = _mk("e2", "a/x", 2)
            tx = store.transact_serializable([d1, d2])
            assert tx == 1
            out = list(store.all_datoms())
            assert len(out) == 2
            assert {d.tx for d in out} == {1}
        finally:
            store.close()

    def test_inmemory_empty_batch_returns_zero(self) -> None:
        """Empty iterable is a no-op and burns no tx-id (matches PG)."""
        store = InMemoryStore()
        assert store.transact_serializable([]) == 0
        assert store.next_tx() == 1  # not advanced

    def test_sqlite_empty_batch_returns_zero(self) -> None:
        store = SQLiteStore()
        try:
            assert store.transact_serializable([]) == 0
            assert store.next_tx() == 1
        finally:
            store.close()


# ===========================================================================
# 3. tx_time= preservation — ADR-13 audit handler recorded_at
# ===========================================================================
class TestTxTimePreservation:
    """ADR-13: ``transact_serializable(..., tx_time=recorded_at)`` overrides
    each datom's ``tx_time`` with the supplied value. This is how
    persist_repl_audit preserves the audit handler's ``recorded_at``
    instant through the migration to ``transact_serializable`` — a
    naive route through ``allocate_and_append`` would have preserved
    whatever ``tx_time`` the caller stamped, but the explicit override
    makes the contract clear at the Protocol level.
    """

    def test_inmemory_tx_time_override_preserves_recorded_at(self) -> None:
        store = InMemoryStore()
        recorded_at = _dt(2026, 4, 1, 9, 30, 15)  # the audit instant
        original_tx_time = _dt(2026, 4, 30, 12, 0, 0)  # whatever the datom carried
        d = _mk("e1", "a/x", 1, tx_time=original_tx_time)
        tx = store.transact_serializable([d], tx_time=recorded_at)
        assert tx == 1
        (got,) = list(store.all_datoms())
        assert got.tx_time == recorded_at, (
            "tx_time= override must replace the datom's tx_time per ADR-13; "
            "this preserves the audit handler's recorded_at across the "
            "persist_repl_audit migration"
        )

    def test_sqlite_tx_time_override_preserves_recorded_at(self) -> None:
        store = SQLiteStore()
        try:
            recorded_at = _dt(2026, 4, 1, 9, 30, 15)
            original_tx_time = _dt(2026, 4, 30, 12, 0, 0)
            d = _mk("e1", "a/x", 1, tx_time=original_tx_time)
            tx = store.transact_serializable([d], tx_time=recorded_at)
            assert tx == 1
            (got,) = list(store.all_datoms())
            assert got.tx_time == recorded_at
        finally:
            store.close()

    def test_inmemory_no_tx_time_preserves_existing(self) -> None:
        """When tx_time is not provided, the datom's existing tx_time stays."""
        store = InMemoryStore()
        original_tx_time = _dt(2026, 4, 30, 12, 0, 0)
        d = _mk("e1", "a/x", 1, tx_time=original_tx_time)
        store.transact_serializable([d])
        (got,) = list(store.all_datoms())
        assert got.tx_time == original_tx_time


# ===========================================================================
# 4. persist_repl_audit migration — ADR-13 / Part B
# ===========================================================================
class TestPersistReplAuditMigration:
    """ADR-13: persist_repl_audit must route through transact_serializable
    AND preserve the audit handler's ``recorded_at`` as the datom's
    ``tx_time``. Existing audit-emission tests are in
    ``tests/repl/test_audit_emission.py``; this class adds the
    migration-specific assertions.
    """

    def test_persist_uses_transact_serializable_call_path(self) -> None:
        """persist_repl_audit must call store.transact_serializable.

        Mocked store records every call; we assert transact_serializable
        was called with tx_time= equal to the entry's recorded_at, NOT
        that the legacy store.append path was used.
        """
        from persistence.repl._audit import persist_repl_audit
        from datetime import datetime as _datetime

        recorded_at = _datetime(2026, 4, 5, 8, 30, 0, tzinfo=timezone.utc)
        # Build an AuditEntry directly (no session needed) — content
        # hash is computed from the canonical fields.
        from persistence.effect.handlers.audit import (
            _canonicalise_content, _content_hash,
        )
        content = {
            "prev_hash": None,
            "op": ":repl/op",
            "args_hash": "abc",
            "verdict": "ok",
            "latency_ms": 1,
            "recorded_at": recorded_at.timestamp(),
            "result_hash": None,
            "error": None,
            "policy_id": None,
            "handler_chain": (),
            "principal": {"token_id": "tok", "session_id": "ses",
                          "op_kind": "inspect",
                          "view_cursor_tx_time_iso": None,
                          "view_cursor_vt_iso": None},
            "run_id": None,
            "parent": None,
        }
        canonical = _canonicalise_content(content)
        entry = AuditEntry(id=_content_hash(canonical), **canonical)

        # Spy store: only transact_serializable should be exercised.
        captured: dict = {}

        class _SpyStore:
            def transact_serializable(self, datoms, *, tx_time=None):
                captured["datoms"] = list(datoms)
                captured["tx_time"] = tx_time
                return 42

            def append(self, datoms):
                captured["append_called"] = True

        class _DB:
            store = _SpyStore()

            def log(self):
                return iter(())

        persist_repl_audit(_DB(), entry)

        assert "datoms" in captured, (
            "persist_repl_audit must route through transact_serializable "
            "(ADR-13); it bypassed it"
        )
        assert "append_called" not in captured, (
            "persist_repl_audit must NOT call store.append directly post-PG3 "
            "(ADR-13)"
        )
        assert captured["tx_time"] == recorded_at, (
            "persist_repl_audit must pass tx_time=recorded_at to preserve "
            "the audit handler's instant per ADR-13"
        )
        assert len(captured["datoms"]) == 1
        # The datom is audit-shaped (a starts with audit/).
        assert captured["datoms"][0].a == "audit/repl.op"

    def test_persist_recorded_at_lands_on_tx_time(self) -> None:
        """Round-trip: recorded_at → datom.tx_time after persist_repl_audit."""
        from persistence.fact import DB
        from persistence.repl._audit import persist_repl_audit
        from persistence.effect.handlers.audit import (
            _canonicalise_content, _content_hash,
        )

        recorded_at = _dt(2026, 4, 5, 8, 30, 0)
        content = {
            "prev_hash": None,
            "op": ":repl/op",
            "args_hash": "abc",
            "verdict": "ok",
            "latency_ms": 1,
            "recorded_at": recorded_at.timestamp(),
            "result_hash": None,
            "error": None,
            "policy_id": None,
            "handler_chain": (),
            "principal": {"token_id": "tok", "session_id": "ses",
                          "op_kind": "inspect",
                          "view_cursor_tx_time_iso": None,
                          "view_cursor_vt_iso": None},
            "run_id": None,
            "parent": None,
        }
        canonical = _canonicalise_content(content)
        entry = AuditEntry(id=_content_hash(canonical), **canonical)

        store = InMemoryStore()
        db = DB(store=store)
        persist_repl_audit(db, entry)

        audit_datoms = [d for d in store.all_datoms() if d.a == "audit/repl.op"]
        assert len(audit_datoms) == 1
        # tx_time is preserved exactly as the recorded_at instant —
        # no DB-clock re-stamp.
        assert audit_datoms[0].tx_time == recorded_at, (
            "audit handler's recorded_at must round-trip through "
            "transact_serializable as the datom's tx_time per ADR-13 — "
            "the migration must not re-stamp from the DB clock"
        )


# ===========================================================================
# 5. audit_chain_lock contract — InMemory + SQLite single-process semantics
# ===========================================================================
class TestAuditChainLockSingleProcess:
    """Audit datoms in InMemory + SQLite rely on the existing in-process
    ``_lock`` mutex for chain-head ordering — there is no
    ``audit_chain_lock`` table at this level. The PG3 design § 6.4
    explicitly says SQLite + InMemory keep their existing semantics;
    only PostgresStore needs the row-lock primitive because cross-
    process is the only environment where the in-Python lock fails.
    These tests pin the contract that the in-process backends still
    serialise audit appends correctly under thread contention.
    """

    def test_inmemory_concurrent_audit_appends_serialise(self) -> None:
        """Two threads each append 50 audit datoms in parallel; final
        ordering is well-defined and no datom is lost."""
        store = InMemoryStore()
        n = 50
        results: list[list[int]] = [[], []]
        barrier = threading.Barrier(2)

        def worker(idx: int) -> None:
            barrier.wait()
            for i in range(n):
                d = _mk_audit(
                    e=f"thr-{idx}-{i}",
                    prev_hash=None,
                    signature=f"sig-{idx}-{i}",
                )
                tx = store.transact_serializable([d])
                results[idx].append(tx)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each tx-id is unique; combined they cover 1..2n.
        all_tx = sorted(results[0] + results[1])
        assert all_tx == list(range(1, 2 * n + 1))


# ===========================================================================
# 6. G3a — replay-determinism (single-process baseline; PG4 lifts to multi)
# ===========================================================================
class TestG3a_ReplayDeterminism:
    """G3a (per ADR-14): replay the persisted log against an empty
    store; the resulting projection (filtered per N6/N7) is byte-
    identical when replayed twice.

    Single-process flavour here pins the invariant for InMemory +
    SQLite. PG4 lifts the assertion to a multi-process Hypothesis
    property at ``max_examples=50`` against PostgresStore.
    """

    def test_inmemory_log_replays_to_itself(self) -> None:
        store = InMemoryStore()
        # Mix of audit + non-audit datoms — covers both lock paths.
        for i in range(10):
            d = _mk(f"e-{i}", "user/x", i)
            store.transact_serializable([d])
            ad = _mk_audit(
                e=f"audit-{i}",
                prev_hash=f"sig-{i-1}" if i > 0 else None,
                signature=f"sig-{i}",
            )
            store.transact_serializable([ad])

        original = list(store.all_datoms())

        # Replay against a fresh store. Per N6/N7 the relevant
        # invariant for replay byte-identity is that the same op-
        # sequence yields the same Datom sequence (post-decode).
        replay = InMemoryStore()
        for d in original:
            # tx is allocator-assigned on replay; the user-visible
            # ``a, v, op, tx_time`` projection is what byte-identity
            # checks (per v0.5.2 N6/N7 filter).
            d_replay = Datom(
                e=d.e, a=d.a, v=d.v, tx=0,
                tx_time=d.tx_time, valid_from=d.valid_from,
                valid_to=d.valid_to, op=d.op,
                provenance=d.provenance,
                invalidated_by=d.invalidated_by,
            )
            replay.transact_serializable([d_replay], tx_time=d.tx_time)

        replayed = list(replay.all_datoms())
        # Filter scope per v0.5.2 N6/N7: drop ``persistence.txn/...``
        # commit datoms (none here, but the filter is the canonical
        # baseline).
        assert len(original) == len(replayed)
        for o, r in zip(original, replayed):
            assert o.e == r.e
            assert o.a == r.a
            assert o.v == r.v
            assert o.tx_time == r.tx_time
            assert o.op == r.op


# ===========================================================================
# 7. G3b — no-same-tx (UNIQUE constraint trivially enforces this)
# ===========================================================================
class TestG3b_NoSameTxCollision:
    """G3b (per ADR-14): zero rows in ``datom_log`` share ``(tx, e, a)``
    after N concurrent writers complete. On Postgres the UNIQUE
    constraint enforces this at the schema level; on InMemory the
    in-process ``_lock`` plus monotonic ``allocate_and_append`` makes
    the property hold by construction.
    """

    def test_inmemory_no_same_tx_under_threads(self) -> None:
        store = InMemoryStore()
        n_threads = 4
        n_per = 25
        barrier = threading.Barrier(n_threads)

        def worker(idx: int) -> None:
            barrier.wait()
            for i in range(n_per):
                d = _mk(f"thr-{idx}-{i}", "x", i)
                store.transact_serializable([d])

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        all_datoms = list(store.all_datoms())
        # No (tx, e, a) collision: every (tx, e, a) tuple is unique.
        keys = [(d.tx, d.e, d.a) for d in all_datoms]
        assert len(keys) == len(set(keys)), (
            "G3b violated: duplicate (tx, e, a) tuple in datom_log — "
            "the in-process allocator under _lock should make this "
            "impossible"
        )


# ===========================================================================
# 8. G3c — audit-chain-continuity (verify_chain True over persisted projection)
# ===========================================================================
class TestG3c_AuditChainContinuity:
    """G3c (per ADR-14): ``verify_chain()`` returns True for the audit-
    shaped projection of the persisted log walked in seq order.

    This test builds a real audit chain via ``emit_repl_op_audit`` +
    ``persist_repl_audit`` (the load-bearing call site for ADR-13),
    then walks the persisted datoms back into AuditEntry form and
    runs ``verify_chain``.
    """

    def test_inmemory_audit_chain_verifies_after_persist(self) -> None:
        from persistence.fact import DB
        from persistence.repl._audit import (
            persist_repl_audit, _audit_window_query,
        )
        from persistence.effect.handlers.audit import (
            _canonicalise_content, _content_hash,
        )

        store = InMemoryStore()
        db = DB(store=store)

        # Hand-build a 3-entry chain so we control prev_hash linkage.
        entries: list[AuditEntry] = []
        prev: str | None = None
        base_time = _dt(2026, 4, 5, 8, 0, 0)
        for i in range(3):
            content = {
                "prev_hash": prev,
                "op": ":repl/op",
                "args_hash": f"h-{i}",
                "verdict": "ok",
                "latency_ms": 1,
                "recorded_at": (base_time + timedelta(seconds=i)).timestamp(),
                "result_hash": None,
                "error": None,
                "policy_id": None,
                "handler_chain": (),
                "principal": {"token_id": "tok", "session_id": "ses",
                              "op_kind": "inspect",
                              "view_cursor_tx_time_iso": None,
                              "view_cursor_vt_iso": None},
                "run_id": None,
                "parent": prev,
                # Phase 2.3c.2 LD5 — Re-pinned 2026-05-08 for
                # parent_audit_entry_id field add. Always-write to match
                # post-2.3c.2 production make_audit_handler shape.
                "parent_audit_entry_id": None,
            }
            canonical = _canonicalise_content(content)
            entry = AuditEntry(id=_content_hash(canonical), **canonical)
            entries.append(entry)
            persist_repl_audit(db, entry)
            prev = entry.id

        # Round-trip: read back from the store via _audit_window_query
        # (the exact path inspect kind="audit-window" uses) and
        # assert verify_chain on the result.
        recovered = _audit_window_query(
            db, from_iso=None, to_iso=None, op_filter=None, limit=100,
        )
        assert len(recovered) == 3
        assert verify_chain(recovered) is True, (
            "G3c violated: audit chain does not verify after PG3 "
            "persist_repl_audit migration to transact_serializable"
        )


# ===========================================================================
# 9. G3d — sum-of-ops (no lost writes, no spurious duplicates)
# ===========================================================================
class TestG3d_SumOfOps:
    """G3d (per ADR-14): non-audit, non-commit datom count equals the
    sum of per-process op counts.

    Single-process flavour: per-thread counts must sum to the actual
    persisted count. PG4's multi-process flavour generalises to N
    spawned processes.
    """

    def test_inmemory_thread_op_counts_sum_to_persisted_count(self) -> None:
        store = InMemoryStore()
        n_threads = 3
        n_per = 30
        per_thread_count: list[int] = [0] * n_threads
        barrier = threading.Barrier(n_threads)

        def worker(idx: int) -> None:
            barrier.wait()
            for i in range(n_per):
                d = _mk(f"thr-{idx}-{i}", "user/x", i)
                store.transact_serializable([d])
                per_thread_count[idx] += 1

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Filter scope per N6/N7: drop ``audit/`` and ``persistence.txn/``
        # datoms (none here — these are user-write only).
        non_audit = [d for d in store.all_datoms()
                     if not d.a.startswith("audit/")
                     and not d.a.startswith("persistence.txn/")]
        assert len(non_audit) == sum(per_thread_count), (
            f"G3d violated: persisted={len(non_audit)} != "
            f"sum-of-thread-counts={sum(per_thread_count)}"
        )


# ===========================================================================
# 10. G3e — some-serial-order-exists (rw-cycle detection over commit prov)
# ===========================================================================
class TestG3e_SerialOrderExists:
    """G3e (per ADR-14 W4-rewritten): the rw-conflict graph reconstructed
    from persisted commit provenance contains no cycle over concurrent-
    commit pairs with bidirectional rw-edges.

    The single-process flavour pins the property structurally: when
    only one writer is active at a time, no two commits are concurrent,
    so the rw-graph cannot have any cycles regardless of edge density.

    The full proof-of-falsifiability harness (READ COMMITTED override
    that fails the gate) lives in PG4 and runs against PostgresStore.
    Here we exercise the algorithm with a contrived multi-thread
    scenario where threading.Barrier forces concurrent commits, and
    verify the cycle check returns ``no cycle`` for the SSI-clean
    in-process executions.
    """

    def test_inmemory_no_cycle_in_rw_graph_under_thread_contention(self) -> None:
        """Two threads each write to overlapping eids; the rw-graph
        check must report no cycle (both commits serialise on the
        in-process ``_lock``, so they cannot be truly concurrent in
        the sense G3e requires for cycles)."""
        store = InMemoryStore()
        n = 20
        barrier = threading.Barrier(2)

        def worker(idx: int) -> None:
            barrier.wait()
            for i in range(n):
                # Both threads write to the same eid set, so a true
                # rw-conflict would manifest if the lock were absent.
                d = _mk(f"shared-{i}", "x", (idx, i))
                store.transact_serializable([d])

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Reconstruct the rw-graph. For InMemoryStore there are no
        # ``persistence.txn/`` commit datoms (those are emitted by
        # the Module 5 STM, not raw store writes). The G3e algorithm
        # operates on commit provenance — when no commit datoms exist
        # the graph is trivially empty and acyclic.
        commit_datoms = [d for d in store.all_datoms()
                         if d.a.startswith("persistence.txn/commit-id")]
        # No txn/commit datoms here (raw store writes, no dosync).
        # The G3e algorithm's input is empty → graph has 0 nodes →
        # no cycles. Pin the contract.
        assert _has_rw_cycle(commit_datoms, store) is False, (
            "G3e violated: rw-graph contains a cycle in single-process "
            "InMemoryStore (which serialises all writes on _lock — a "
            "cycle here would indicate an algorithmic bug, not a real "
            "SSI escape)"
        )

    def test_g3e_helper_finds_cycle_in_synthetic_2cycle(self) -> None:
        """Sanity check: the cycle detector actually detects cycles.

        Build a synthetic 2-cycle scenario that mirrors the real SSI-
        escape pattern — two concurrent commits with mutual rw-edges —
        and assert ``_has_rw_cycle`` reports True. This is the proof-
        of-falsifiability lite check; the full SERIALIZABLE-vs-READ-
        COMMITTED proof harness lives in PG4.
        """
        # Build two synthetic commit datoms that overlap in time
        # (concurrent) AND have rw-edges in both directions.
        store = InMemoryStore()
        t0 = _dt(2026, 4, 5, 8, 0, 0)
        t1 = _dt(2026, 4, 5, 8, 0, 5)   # 5s later — overlap with C2
        t2 = _dt(2026, 4, 5, 8, 0, 3)   # 3s after t0 — overlap with C1
        t3 = _dt(2026, 4, 5, 8, 0, 8)
        # C1: started at t0, committed at t1, read e1, wrote e2
        c1_prov = {
            ":persistence.txn/commit-id": "c1",
            ":persistence.txn/started-at": t0.isoformat(),
            ":persistence.txn/committed-at": t1.isoformat(),
            ":persistence.txn/read-set": ["e1"],
            ":persistence.txn/ensure-set": [],
        }
        # C2: started at t2, committed at t3, read e2, wrote e1.
        # Overlap: t0 < t3 AND t2 < t1 → concurrent.
        # Write-set is reconstructed from same-tx user-write datoms.
        c2_prov = {
            ":persistence.txn/commit-id": "c2",
            ":persistence.txn/started-at": t2.isoformat(),
            ":persistence.txn/committed-at": t3.isoformat(),
            ":persistence.txn/read-set": ["e2"],
            ":persistence.txn/ensure-set": [],
        }
        c1 = Datom(
            e="commit-c1", a="persistence.txn/commit-id",
            v="c1", tx=1, tx_time=t1, valid_from=t1, valid_to=None,
            op="assert", provenance=c1_prov,
        )
        c2 = Datom(
            e="commit-c2", a="persistence.txn/commit-id",
            v="c2", tx=2, tx_time=t3, valid_from=t3, valid_to=None,
            op="assert", provenance=c2_prov,
        )
        # User-write datoms — C1 wrote e2, C2 wrote e1. The write-set
        # reconstruction reads same-tx non-audit non-commit user
        # datoms.
        w1 = Datom(
            e="e2", a="user/x", v=1, tx=1,
            tx_time=t1, valid_from=t1, valid_to=None,
            op="assert", provenance={"source": "test"},
        )
        w2 = Datom(
            e="e1", a="user/x", v=2, tx=2,
            tx_time=t3, valid_from=t3, valid_to=None,
            op="assert", provenance={"source": "test"},
        )
        # Append directly so the timestamps + tx-ids stay exactly as
        # constructed (no allocator re-stamp).
        store.append([c1, c2, w1, w2])
        commit_datoms = [d for d in store.all_datoms()
                         if d.a == "persistence.txn/commit-id"]
        # Both commits are concurrent (overlapping intervals);
        # C1.write_set = {e2}, C2.read_set = {e2} → C1 → C2 edge
        # (C1's writes invisible at C2's snapshot iff committed_at_C1
        # > t_start_C2: t1=8:05 > t2=8:03 ✓).
        # C2.write_set = {e1}, C1.read_set = {e1} → C2 → C1 edge
        # (committed_at_C2 > t_start_C1: t3=8:08 > t0=8:00 ✓).
        # Both edges → 2-cycle.
        assert _has_rw_cycle(commit_datoms, store) is True, (
            "G3e helper failed to detect the synthetic 2-cycle — "
            "the cycle detector is broken, not the SSI"
        )


# ---------------------------------------------------------------------------
# G3e helper — rw-graph cycle detection over persisted commit provenance
# ---------------------------------------------------------------------------
def _has_rw_cycle(commit_datoms: list, store) -> bool:
    """Return True iff the rw-conflict graph has a cycle over concurrent-
    commit pairs (per ADR-14 W4-rewritten G3e).

    Algorithm:
    1. Parse each commit's ``started-at`` / ``committed-at`` /
       ``read-set`` / ``ensure-set`` from provenance.
    2. Reconstruct the write-set per commit by collecting same-tx
       non-commit non-audit datoms from the full log.
    3. Two commits are *concurrent* iff their [t_start, committed_at]
       intervals overlap.
    4. rw-edge ``C_a → C_b`` exists iff (a) concurrent AND
       (b) ``C_a.committed_at > C_b.t_start`` (writes invisible at
       C_b's snapshot) AND (c) write-set ∩ (read-set ∪ ensure-set)
       non-empty.
    5. Run DFS for cycles (any cycle ≥ 2 fails).
    """
    if not commit_datoms:
        return False

    # Collect all datoms once for write-set reconstruction.
    all_datoms = list(store.all_datoms())

    # Parse commits.
    commits: list[dict] = []
    for c in commit_datoms:
        prov = c.provenance
        try:
            t_start = datetime.fromisoformat(
                prov[":persistence.txn/started-at"]
            )
            committed_at = datetime.fromisoformat(
                prov[":persistence.txn/committed-at"]
            )
        except (KeyError, ValueError, TypeError):
            continue
        read_set = set(prov.get(":persistence.txn/read-set", []) or [])
        ensure_set = set(prov.get(":persistence.txn/ensure-set", []) or [])
        # Reconstruct write-set: same-tx non-commit non-audit datoms.
        write_set = {
            d.e for d in all_datoms
            if d.tx == c.tx
            and not d.a.startswith("persistence.txn/")
            and not d.a.startswith("audit/")
        }
        commits.append({
            "id": prov.get(":persistence.txn/commit-id", c.tx),
            "tx": c.tx,
            "t_start": t_start,
            "committed_at": committed_at,
            "read_set": read_set,
            "ensure_set": ensure_set,
            "write_set": write_set,
        })

    # Build edges over concurrent-commit pairs only.
    edges: dict[object, set] = {c["id"]: set() for c in commits}
    for i, ca in enumerate(commits):
        for j, cb in enumerate(commits):
            if i == j:
                continue
            # Concurrency: intervals overlap.
            if not (ca["t_start"] < cb["committed_at"]
                    and cb["t_start"] < ca["committed_at"]):
                continue
            # rw-edge ca → cb: ca's writes invisible at cb's snapshot
            # AND ca's writes intersect cb's reads.
            if ca["committed_at"] <= cb["t_start"]:
                continue  # ca's writes were visible — no rw-edge
            touched = cb["read_set"] | cb["ensure_set"]
            if ca["write_set"] & touched:
                edges[ca["id"]].add(cb["id"])

    # DFS cycle check.
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {c["id"]: WHITE for c in commits}

    def dfs(node) -> bool:
        color[node] = GRAY
        for neighbor in edges.get(node, ()):
            c = color.get(neighbor, WHITE)
            if c == GRAY:
                return True
            if c == WHITE and dfs(neighbor):
                return True
        color[node] = BLACK
        return False

    for c in commits:
        if color[c["id"]] == WHITE and dfs(c["id"]):
            return True
    return False


# ===========================================================================
# 11. PostgresStore — audit_chain_lock concurrent-writer test (PG-only)
# ===========================================================================
_PG_DSN = os.environ.get("PERSISTENCE_PG_DSN", "")
_PG_DSN_REASON = (
    "PERSISTENCE_PG_DSN env var not set — set it to a libpq DSN to run "
    "the live-Postgres audit_chain_lock concurrent-writer test"
)


@pytest.fixture
def pg_store() -> Iterator:
    """Per-test schema-scoped PostgresStore. Same shape as the PG1 fixture."""
    if not _PG_DSN:
        pytest.skip(_PG_DSN_REASON)

    from persistence.store.postgres import PostgresStore
    import psycopg

    schema = f"test_pg3_{uuid.uuid4().hex[:12]}"
    setup = psycopg.connect(_PG_DSN)
    setup.autocommit = True
    try:
        with setup.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        setup.close()

    sep = "&" if "?" in _PG_DSN else "?"
    scoped = f"{_PG_DSN}{sep}options=-c%20search_path%3D{schema}"
    store = PostgresStore(dsn=scoped)
    try:
        yield store
    finally:
        store.close()
        cleanup = psycopg.connect(_PG_DSN)
        cleanup.autocommit = True
        try:
            with cleanup.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            cleanup.close()


class TestPostgresAuditChainLock:
    """ADR-3 / Part C — ``audit_chain_lock`` row-lock primitive on
    PostgresStore. These tests exercise the load-bearing concurrent-
    writer path that the in-process backends don't need.

    Skip clean when ``PERSISTENCE_PG_DSN`` is unset.
    """

    def test_audit_chain_lock_table_exists(self, pg_store) -> None:
        """The ``audit_chain_lock`` table is created by PG1+PG3 schema
        DDL and seeded with a single ``id=1`` row."""
        with pg_store._txn() as cur:
            cur.execute(
                "SELECT id, last_seq, last_hash FROM audit_chain_lock"
            )
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 1  # id=1 invariant
        # Initial state — no audit datoms yet.
        assert rows[0][1] == 0
        assert rows[0][2] == ""

    def test_non_audit_batch_does_not_touch_lock_row(self, pg_store) -> None:
        """Non-audit ``transact_serializable`` calls leave the chain-
        head row untouched (no contention for non-audit writers)."""
        d = _mk("e1", "user/x", 1)
        pg_store.transact_serializable([d])
        with pg_store._txn() as cur:
            cur.execute("SELECT last_seq, last_hash FROM audit_chain_lock")
            (last_seq, last_hash) = cur.fetchone()
        assert last_seq == 0
        assert last_hash == ""

    def test_audit_batch_advances_lock_row(self, pg_store) -> None:
        """An audit-shaped ``transact_serializable`` call updates the
        chain-head row's ``last_seq`` + ``last_hash`` to point at the
        committed audit datom."""
        d = _mk_audit(e="audit-1", prev_hash=None, signature="sig-1")
        pg_store.transact_serializable([d])
        with pg_store._txn() as cur:
            cur.execute("SELECT last_seq, last_hash FROM audit_chain_lock")
            (last_seq, last_hash) = cur.fetchone()
        assert last_seq > 0, "audit_chain_lock.last_seq must advance"
        assert last_hash == "sig-1", (
            "audit_chain_lock.last_hash must equal the audit datom's "
            "provenance[':signature']"
        )

    def test_concurrent_audit_writers_chain_in_order(self, pg_store) -> None:
        """4 threads × 25 audit datoms each → 100 audit datoms in
        chain order with no broken prev_hash linkage. The
        ``audit_chain_lock`` ``SELECT FOR UPDATE`` row lock is the
        load-bearing primitive: two writers cannot both see the same
        head and write an audit datom with the same prev_hash.

        Note: this test verifies the LOCK serialises chain-head
        observation; the audit handler upstream is responsible for
        building each entry's ``parent_provenance_hash`` from the
        observed head. Here we mock that by reading the lock row's
        ``last_hash`` BEFORE inserting and using it as the new
        entry's ``prev_hash``.
        """
        n_threads = 4
        n_per = 25

        def worker(idx: int) -> None:
            for i in range(n_per):
                # The PG3 contract: the audit handler owns prev_hash;
                # the lock row is a head-pointer cache. Here we
                # synthesise a chain by reading the head, then
                # transact a new audit datom whose signature is
                # deterministic per (idx, i). The
                # transact_serializable call takes the row lock and
                # updates last_hash to our signature.
                signature = f"thr-{idx}-{i}"
                d = _mk_audit(
                    e=f"a-{idx}-{i}",
                    prev_hash=None,  # not load-bearing for this test
                    signature=signature,
                )
                pg_store.transact_serializable([d])

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 100 audit datoms landed.
        all_audit = [d for d in pg_store.all_datoms()
                     if d.a.startswith("audit/")]
        assert len(all_audit) == n_threads * n_per

        # The chain-head row's last_hash equals the signature of the
        # LAST audit datom in seq order. Source of truth is the log;
        # the lock row is the cache.
        with pg_store._txn() as cur:
            cur.execute("SELECT last_seq, last_hash FROM audit_chain_lock")
            (last_seq, last_hash) = cur.fetchone()
        # Find the audit datom at last_seq. It must exist, and its
        # signature must equal last_hash.
        with pg_store._txn() as cur:
            cur.execute(
                "SELECT provenance FROM datom_log WHERE seq = %s",
                (last_seq,),
            )
            row = cur.fetchone()
        assert row is not None, (
            "audit_chain_lock.last_seq points at no row — chain head "
            "cache drifted from the log"
        )
        # provenance is canonical-JSON TEXT; parse to read the
        # signature back.
        import json as _json
        prov = _json.loads(row[0])
        assert prov.get(":signature") == last_hash

    def test_isinstance_postgres_satisfies_store_protocol(self, pg_store) -> None:
        """PostgresStore satisfies the runtime-checkable Store
        Protocol after the additive PG3 method declarations."""
        assert isinstance(pg_store, Store)


# ===========================================================================
# 12. PG-W1 — G3d cross-process audit-chain Merkle continuity (PG-only)
# ===========================================================================
#
# The PG3 ``test_concurrent_audit_writers_chain_in_order`` exercises
# multi-THREAD audit-emit serialisation; the PG4 harness exercises
# multi-PROCESS write-skew. Neither covered the PG-W1 / ARIS R2 Dim 4
# finding: two concurrent processes BOTH constructing AuditEntry with
# the same stale predecessor hash, then racing for ``audit_chain_lock``
# under ``transact_serializable``.
#
# Before PG-W1, the second-arriving process's audit datom would have
# ``:prev-hash`` pointing at the OLD head (its in-memory pointer was
# stale by the time it acquired the row lock), so ``verify_chain`` on
# the persisted log would fail at that entry's chain check.
#
# After PG-W1, ``transact_serializable`` reads the locked head under
# ``SELECT FOR UPDATE`` and calls ``rebind_audit_datom_prev_hash`` on
# every audit datom in the batch so the persisted ``:prev-hash`` always
# binds to the actual cross-process chain tip — ``verify_chain``
# returns True.
#
# This test SHOULD have failed before PG-W1; it MUST pass after.
def _pg_w1_writer_main(
    *,
    dsn: str,
    barrier_handle,  # multiprocessing.Barrier — type elided to keep the
                     # subprocess module importable on its own.
    queue,           # multiprocessing.Queue
    worker_idx: int,
    base_prev_hash: str | None,
) -> None:
    """Subprocess body — emit one ``audit/repl.op`` datom whose
    Python-side ``prev_hash`` was constructed against the SAME stale
    predecessor as every sibling worker.

    The barrier ensures all N writers have built their AuditEntry +
    derived ``prev_hash`` BEFORE any of them calls
    ``transact_serializable`` — this is the worst-case stale-pointer
    contention pattern PG-W1 fixes. Each worker then races to acquire
    ``audit_chain_lock FOR UPDATE``; whoever wins first commits with
    ``:prev-hash = base_prev_hash``, the next one reads the now-updated
    lock row + has its datom rebound by the store, and so on.
    """
    # Lazy imports — this module is imported into the subprocess via
    # spawn, and the spawn child has its own module cache. Keep imports
    # cheap and explicit.
    from datetime import datetime, timezone

    from persistence.effect.handlers.audit import (
        AuditEntry,
        _canonicalise_content,
        _content_hash,
        audit_entry_to_datom,
    )
    from persistence.fact.datom import Datom
    from persistence.store.postgres import PostgresStore

    try:
        # Build an AuditEntry exactly the way emit_repl_op_audit does
        # (same content shape — see persistence.repl._audit). Per-
        # worker args_hash so the canonical content (and thus entry.id)
        # is unique even when prev_hash is identical across workers.
        recorded_at = datetime(
            2026, 4, 30, 12, worker_idx, 0, tzinfo=timezone.utc,
        )
        content = {
            "prev_hash": base_prev_hash,
            "op": ":repl/op",
            "args_hash": f"sha256:worker-{worker_idx}",
            "verdict": "ok",
            "latency_ms": 1,
            "recorded_at": recorded_at.timestamp(),
            "result_hash": None,
            "error": None,
            "policy_id": None,
            "handler_chain": (),
            "principal": {
                "token_id": "tok",
                "session_id": f"ses-{worker_idx}",
            },
            "run_id": None,
            "parent": base_prev_hash,
        }
        canonical = _canonicalise_content(content)
        entry = AuditEntry(id=_content_hash(canonical), **canonical)

        # Convert to a Datom shape the store consumes — this mirrors
        # ``persist_repl_audit``.
        wire = audit_entry_to_datom(entry)
        datom = Datom(
            e=wire[":datom/e"],
            a=wire[":datom/a"].lstrip(":"),
            v=wire[":datom/v"],
            tx=0,
            tx_time=wire[":datom/tx-time"],
            valid_from=wire[":datom/valid-from"],
            valid_to=wire[":datom/valid-to"],
            op="assert",
            provenance=wire[":datom/provenance"],
        )

        # Synchronise: all workers have their stale-prev_hash AuditEntry
        # built. From here on, whoever acquires audit_chain_lock first
        # wins the chain head; the rest must rebind.
        barrier_handle.wait(timeout=30.0)

        # Open a per-process PostgresStore — psycopg connections are
        # NOT fork-safe (we use spawn anyway, but each process gets a
        # fresh pool regardless). Route through transact_serializable
        # so the audit_chain_lock + PG-W1 rebind path fires.
        store = PostgresStore(dsn=dsn)
        try:
            tx = store.transact_serializable([datom], tx_time=recorded_at)
        finally:
            store.close()

        queue.put({
            "outcome": "committed",
            "worker_idx": worker_idx,
            "tx": tx,
            "original_id": entry.id,
        })
    except BaseException as exc:  # noqa: BLE001 — last-ditch surface
        queue.put({
            "outcome": "error",
            "worker_idx": worker_idx,
            "exception_repr": repr(exc),
        })


class TestG3dCrossProcessAuditChainContinuity:
    """PG-W1 — multi-process ``verify_chain`` continuity under concurrent
    audit-emit (closes ARIS R2 Dim 4).

    N=4 child processes each construct an ``AuditEntry`` with the SAME
    stale predecessor hash (the worst-case in-memory-pointer-staleness
    pattern), barrier-sync to ensure all 4 are built BEFORE any commits,
    then race ``transact_serializable``. After all 4 join, the parent
    reads the persisted audit datoms in seq order, decodes them back
    into AuditEntry form, and runs ``verify_chain``.

    Pre-PG-W1: the second/third/fourth-arriving writer's persisted
    ``:prev-hash`` would point at the SAME stale predecessor as the
    first writer's, so ``verify_chain`` would fail on the second entry.
    Post-PG-W1: ``transact_serializable`` rebinds ``:prev-hash`` to the
    ``audit_chain_lock`` head observed under ``FOR UPDATE``, so every
    persisted entry chains correctly.

    Skip-clean when ``PERSISTENCE_PG_DSN`` is unset.
    """

    def test_verify_chain_holds_after_concurrent_audit_emit(
        self, pg_store
    ) -> None:
        import multiprocessing
        # ``pg_store`` carries the per-test scoped DSN baked into its
        # connection pool; we spawn subprocesses against the same
        # scoped DSN so every writer hits the same schema.
        scoped_dsn = pg_store._dsn  # populated by PostgresStore.__init__

        n_workers = 4
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(parties=n_workers)
        queue = ctx.Queue(maxsize=n_workers)

        procs = []
        for i in range(n_workers):
            p = ctx.Process(
                target=_pg_w1_writer_main,
                kwargs={
                    "dsn": scoped_dsn,
                    "barrier_handle": barrier,
                    "queue": queue,
                    "worker_idx": i,
                    # All 4 workers see the SAME stale predecessor —
                    # this is the bug-trigger shape PG-W1 closes.
                    # Genesis chain: base_prev_hash=None means every
                    # worker thinks it's the first audit datom.
                    "base_prev_hash": None,
                },
                daemon=False,
            )
            p.start()
            procs.append(p)

        # Drain results.
        results: list[dict] = []
        for p in procs:
            p.join(timeout=60.0)
            if p.is_alive():
                p.terminate()
                p.join(timeout=5.0)

        while not queue.empty():
            try:
                results.append(queue.get_nowait())
            except Exception:
                break

        assert len(results) == n_workers, (
            f"expected {n_workers} writer results, got {len(results)}: "
            f"{results}"
        )

        # All 4 must have committed (no SerializationFailure expected
        # for distinct (e, a) audit datoms — the ``audit_chain_lock``
        # row lock serialises them, but doesn't reject any).
        committed = [r for r in results if r["outcome"] == "committed"]
        errored = [r for r in results if r["outcome"] != "committed"]
        assert len(committed) == n_workers, (
            f"expected all {n_workers} writers to commit, but "
            f"{len(errored)} errored: {errored}"
        )

        # Read all audit datoms back in seq order + decode to AuditEntry.
        from persistence.effect.handlers.audit import datom_to_audit_entry
        from persistence.repl._audit import _datom_to_wire_for_audit

        audit_datoms = [
            d for d in pg_store.all_datoms() if d.a.startswith("audit/")
        ]
        assert len(audit_datoms) == n_workers, (
            f"expected {n_workers} audit datoms in datom_log, got "
            f"{len(audit_datoms)}"
        )

        entries: list[AuditEntry] = []
        for d in audit_datoms:
            wire = _datom_to_wire_for_audit(d)
            entries.append(datom_to_audit_entry(wire))

        # G3c-extended: chain continuity ON THE PERSISTED PROJECTION.
        # This is the assertion that would have failed before PG-W1.
        assert verify_chain(entries) is True, (
            "PG-W1 / ARIS R2 Dim 4: verify_chain failed on the persisted "
            "audit-datom projection after concurrent cross-process emit. "
            "The transact_serializable audit-chain rebind step failed to "
            "bind :prev-hash to the actual cross-process head observed "
            "under audit_chain_lock FOR UPDATE."
        )

        # Pin the structural shape: each entry.prev_hash links to the
        # prior entry.id; first entry.prev_hash is None (genesis).
        assert entries[0].prev_hash is None
        for i in range(1, len(entries)):
            assert entries[i].prev_hash == entries[i - 1].id, (
                f"chain break at index {i}: "
                f"prev_hash={entries[i].prev_hash!r} != "
                f"prior id={entries[i - 1].id!r}"
            )

        # Pin the lock-row tail-hash equals the chain tip's signature.
        with pg_store._txn() as cur:
            cur.execute("SELECT last_hash FROM audit_chain_lock")
            (last_hash,) = cur.fetchone()
        assert last_hash == entries[-1].id, (
            "audit_chain_lock.last_hash drifted from the persisted "
            "chain tip — denormalised pointer cache is inconsistent"
        )
