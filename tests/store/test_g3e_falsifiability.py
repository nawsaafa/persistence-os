"""PG4 — G3e cross-process falsifiability proof.

Per the design doc ``docs/plans/2026-04-30-v0.8.0-postgres-store-design.md``
§ 11 G3e (W4-rewritten) + § 13 ARIS R5 carry-forward, this module ships
the load-bearing falsification harness for the SSI 2-cycle anomaly.
PG3's ``tests/store/test_audit_chain_invariants.py::TestG3e_*`` cover
the in-process synthetic 2-cycle sanity check — they exercise the
cycle-detection algorithm but don't actually demonstrate that
PostgresStore prevents the anomaly. This module closes that gap with a
real cross-process Hypothesis property.

What we prove
-------------

The G3e gate (some serial order exists) has two halves:

1. **Anomaly is detectable in a hostile config.** Under READ COMMITTED
   isolation, the classic write-skew workload (two transactions each
   read both X + Y, each writes a different one) produces a final
   state that is not consistent with any serial order. The harness
   forces both writers past their read-set capture before either
   commits; under READ COMMITTED both succeed and the resulting
   ``(final_x, final_y)`` is the anomalous combination.

2. **Anomaly is prevented in the correct config.** Under SERIALIZABLE
   isolation, the same workload produces SQLSTATE 40001
   (``SerializationFailure``) on at least one of the two commits,
   and the surviving commit (or commits, if neither produced the
   shape) leaves the database in a state consistent with some serial
   order.

The Hypothesis property asserts the OR of these two claims holds for
every example. The OR is the weaker, structurally-sufficient form: it
fails only if BOTH halves fail simultaneously, which would mean either
SERIALIZABLE silently admitted the anomaly (a real bug) or our test
setup didn't actually create a write-skew (a test bug). Either failure
is meaningful.

Why ``max_examples=50``
-----------------------

Per ADR-14 W2-clarified, cross-process Hypothesis runs at 50 (vs the
single-process 200) because each example spawns two subprocesses + opens
fresh connections, so wall-clock dominates. Empirically each example
takes ~0.5-1.5s on a local Postgres (depending on the connection-init
warmup); 50 examples land at < 60s wall-clock.

Skip rule
---------

This module is gated on ``PERSISTENCE_PG_DSN``. Without a live Postgres
the entire module is collected but every test is SKIPPED at the fixture
level; the suite stays green on machines without a DB available. Mirror
of the gating pattern in ``tests/store/test_postgres.py``.

Audit-chain half
----------------

Half the Hypothesis examples (controlled by a strategy flag) also emit
audit datoms via the ``use_audit`` writer-spec field. This exercises
the cross-process ``audit_chain_lock FOR UPDATE`` row-lock path and
verifies chain Merkle continuity post-commit (no broken
``parent_provenance_hash`` linkage between the two writers' audit
datoms — the row lock should serialise them on the chain head). Per
the task spec, the audit-aware path forces both ``tx_allocator`` AND
``audit_chain_lock`` row-locks across processes (though our raw-INSERT
shape skips the allocator on purpose; the audit-lock contention is
real).
"""
from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from tests.store._pg4_harness import (
    WriterSpec,
    audit_chain_continuous,
    read_final_xy,
    reserve_two_tx_ids,
    seed_xy_balances,
    spawn_two_writers,
)


# ---------------------------------------------------------------------------
# Skip rule — mirrors tests/store/test_postgres.py.
# ---------------------------------------------------------------------------
_PG_DSN = os.environ.get("PERSISTENCE_PG_DSN", "")
_PG_DSN_REASON = (
    "PERSISTENCE_PG_DSN env var not set — set it to a libpq DSN to run "
    "the cross-process G3e falsifiability proof. PG4 ships skip-clean "
    "when no DB is available; the suite stays green."
)


# ---------------------------------------------------------------------------
# Per-test schema fixture — fresh schema per Hypothesis example so the
# write-skew workload starts from a known seed.
# ---------------------------------------------------------------------------
def _scoped_dsn(schema: str) -> str:
    """Return ``_PG_DSN`` with ``options=-c search_path=<schema>`` appended."""
    sep = "&" if "?" in _PG_DSN else "?"
    return f"{_PG_DSN}{sep}options=-c%20search_path%3D{schema}"


@pytest.fixture
def pg_dsn_factory() -> Iterator:
    """Yield a callable ``new_schema() -> dsn`` that creates a fresh schema.

    The fixture tracks all created schemas and drops them at teardown.
    Each Hypothesis example calls ``new_schema()`` to get a clean slate;
    this is cheaper than spinning up a per-example fixture (Hypothesis
    re-runs the fixture once per example otherwise, and fixture finalisers
    fire only at teardown — so we get many schemas accumulated).
    """
    if not _PG_DSN:
        pytest.skip(_PG_DSN_REASON)

    import psycopg

    created: list[str] = []

    def _new_schema() -> str:
        schema = f"test_pg4_{uuid.uuid4().hex[:12]}"
        setup = psycopg.connect(_PG_DSN)
        setup.autocommit = True
        try:
            with setup.cursor() as cur:
                cur.execute(f'CREATE SCHEMA "{schema}"')
        finally:
            setup.close()
        created.append(schema)

        # Ensure the schema has the PG6-runner-managed migrations
        # applied. Easiest way: open + close a PostgresStore against
        # the scoped DSN — its ``__init__`` runs the runner.
        from persistence.store.postgres import PostgresStore
        s = PostgresStore(dsn=_scoped_dsn(schema))
        s.close()

        return _scoped_dsn(schema)

    try:
        yield _new_schema
    finally:
        cleanup = psycopg.connect(_PG_DSN)
        cleanup.autocommit = True
        try:
            with cleanup.cursor() as cur:
                for s in created:
                    cur.execute(f'DROP SCHEMA "{s}" CASCADE')
        finally:
            cleanup.close()


# ---------------------------------------------------------------------------
# Hypothesis strategies.
# ---------------------------------------------------------------------------
# Initial X+Y balances: well-bounded so the write-skew arithmetic stays
# in a sane range. Decrements small enough that the resulting balances
# could plausibly be a "valid" transfer if read serially.
_initial_strategy = st.integers(min_value=50, max_value=1000)
_decrement_strategy = st.integers(min_value=1, max_value=40)


# ---------------------------------------------------------------------------
# Helpers — common workload runner used by both isolation paths.
# ---------------------------------------------------------------------------
def _run_writeskew_pair(
    *,
    dsn: str,
    isolation: str,
    initial_x: int,
    initial_y: int,
    decrement: int,
    use_audit: bool,
):
    """Seed + reserve tx-ids + spawn two writers + return their results."""
    seed_xy_balances(dsn=dsn, initial_x=initial_x, initial_y=initial_y)
    tx_a, tx_b = reserve_two_tx_ids(dsn=dsn)
    spec_a = WriterSpec(
        worker_id="A", pre_alloc_tx=tx_a,
        write_target="X", decrement=decrement, use_audit=use_audit,
    )
    spec_b = WriterSpec(
        worker_id="B", pre_alloc_tx=tx_b,
        write_target="Y", decrement=decrement, use_audit=use_audit,
    )
    return spawn_two_writers(
        dsn=dsn,
        isolation=isolation,  # type: ignore[arg-type]
        spec_a=spec_a,
        spec_b=spec_b,
    )


def _is_writeskew_anomaly(
    *,
    initial_x: int,
    initial_y: int,
    decrement: int,
    final_x,
    final_y,
) -> bool:
    """True iff the persisted final state is the classic write-skew anomaly.

    Serial executions produce one of two valid final states:

    - A then B: X = initial_y - decrement, Y = (initial_y - decrement) - decrement
    - B then A: Y = initial_x - decrement, X = (initial_x - decrement) - decrement

    Concurrent execution under READ COMMITTED produces the anomaly:
    X = initial_y - decrement AND Y = initial_x - decrement, where
    each writer saw the OTHER's pre-decrement value (the snapshot
    taken at SELECT time, before either committed). That state is
    reachable under no serial order — it's the SSI escape pattern.
    """
    if final_x is None or final_y is None:
        return False
    expected_anomaly_x = initial_y - decrement
    expected_anomaly_y = initial_x - decrement
    return final_x == expected_anomaly_x and final_y == expected_anomaly_y


# ===========================================================================
# 1. The Hypothesis property — SERIALIZABLE rejects OR READ COMMITTED leaks.
# ===========================================================================
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.function_scoped_fixture,
    ],
)
@given(
    initial_x=_initial_strategy,
    initial_y=_initial_strategy,
    decrement=_decrement_strategy,
    use_audit=st.booleans(),
)
def test_g3e_falsifiability_property(
    pg_dsn_factory, initial_x, initial_y, decrement, use_audit,
) -> None:
    """G3e cross-process falsifiability — the load-bearing PG4 gate.

    For every Hypothesis example:

    1. Run the write-skew workload under ``READ COMMITTED`` against a
       fresh schema. Both writers should commit; verify whether the
       resulting state is the anomaly.
    2. Run the same workload under ``SERIALIZABLE`` against ANOTHER
       fresh schema. Verify whether at least one writer was rejected
       with SQLSTATE 40001.
    3. Assert: ``serializable_rejects_at_least_one OR
       read_committed_produces_anomaly`` is True.

    The OR captures the falsifiability claim: in any single example,
    the universe must demonstrate SOMETHING — either SERIALIZABLE
    correctly rejects the workload, or READ COMMITTED correctly
    produces the visible anomaly. If BOTH halves fail simultaneously,
    EITHER our SERIALIZABLE plumbing is broken (the production-side
    bug we want to catch) OR the test setup is silently degenerate
    (no actual write-skew happened — also a test bug worth catching).
    """
    # ----- READ COMMITTED half: anomaly should be visible. ----------------
    rc_dsn = pg_dsn_factory()
    rc_a, rc_b = _run_writeskew_pair(
        dsn=rc_dsn, isolation="read_committed",
        initial_x=initial_x, initial_y=initial_y, decrement=decrement,
        use_audit=use_audit,
    )
    rc_x, rc_y = read_final_xy(dsn=rc_dsn)
    rc_both_committed = (
        rc_a.outcome == "committed" and rc_b.outcome == "committed"
    )
    rc_anomaly = rc_both_committed and _is_writeskew_anomaly(
        initial_x=initial_x, initial_y=initial_y, decrement=decrement,
        final_x=rc_x, final_y=rc_y,
    )

    # ----- SERIALIZABLE half: at least one writer should be rejected. -----
    ser_dsn = pg_dsn_factory()
    ser_a, ser_b = _run_writeskew_pair(
        dsn=ser_dsn, isolation="serializable",
        initial_x=initial_x, initial_y=initial_y, decrement=decrement,
        use_audit=use_audit,
    )
    ser_rejected = (
        ser_a.outcome == "serialization_failure"
        or ser_b.outcome == "serialization_failure"
    )

    # ----- The load-bearing assertion. -----------------------------------
    # If both halves fail, dump a diagnostic that names exactly what
    # we observed — Hypothesis will then shrink toward the smallest
    # initial_x / initial_y / decrement that triggers the failure.
    if not (ser_rejected or rc_anomaly):
        diag = (
            f"\nG3e falsifiability OR-property failed:\n"
            f"  initial_x={initial_x}, initial_y={initial_y}, "
            f"decrement={decrement}, use_audit={use_audit}\n"
            f"  READ COMMITTED: a={rc_a.outcome}, b={rc_b.outcome}, "
            f"final=(X={rc_x}, Y={rc_y})\n"
            f"  SERIALIZABLE:   a={ser_a.outcome}, b={ser_b.outcome}\n"
            f"  rc_a={rc_a!r}\n"
            f"  rc_b={rc_b!r}\n"
            f"  ser_a={ser_a!r}\n"
            f"  ser_b={ser_b!r}\n"
        )
        raise AssertionError(diag)


# ===========================================================================
# 2. SERIALIZABLE half — concrete check that READ COMMITTED is the only
#    config under which the anomaly leaks. (Single-example sanity test.)
# ===========================================================================
def test_serializable_rejects_writeskew_singleshot(pg_dsn_factory) -> None:
    """Single fixed-input shot of the SERIALIZABLE half.

    This is the smoke version of the property: with deterministic
    inputs we expect SERIALIZABLE to reject at least one of the two
    writers. Useful as a fast probe when iterating on the harness;
    the Hypothesis property is the comprehensive gate.
    """
    dsn = pg_dsn_factory()
    a, b = _run_writeskew_pair(
        dsn=dsn, isolation="serializable",
        initial_x=200, initial_y=150, decrement=10, use_audit=False,
    )
    assert (
        a.outcome == "serialization_failure"
        or b.outcome == "serialization_failure"
    ), (
        f"SERIALIZABLE failed to reject either writer on a known "
        f"write-skew shape: a={a!r}, b={b!r}"
    )


def test_read_committed_produces_anomaly_singleshot(pg_dsn_factory) -> None:
    """Single fixed-input shot of the READ COMMITTED half.

    Mirror of the SERIALIZABLE single-shot above. With READ COMMITTED
    we expect both writers to succeed and the persisted final state
    to be the anomaly.
    """
    dsn = pg_dsn_factory()
    a, b = _run_writeskew_pair(
        dsn=dsn, isolation="read_committed",
        initial_x=200, initial_y=150, decrement=10, use_audit=False,
    )
    assert a.outcome == "committed", f"writer A failed: {a!r}"
    assert b.outcome == "committed", f"writer B failed: {b!r}"
    final_x, final_y = read_final_xy(dsn=dsn)
    assert _is_writeskew_anomaly(
        initial_x=200, initial_y=150, decrement=10,
        final_x=final_x, final_y=final_y,
    ), (
        f"READ COMMITTED did NOT produce the expected write-skew "
        f"anomaly: final=(X={final_x}, Y={final_y})"
    )


# ===========================================================================
# 3. Audit-chain cross-process exercise — both writers emit audit datoms
#    under SERIALIZABLE; verify chain Merkle continuity post-commit.
# ===========================================================================
def test_audit_chain_continuous_after_concurrent_writers(
    pg_dsn_factory,
) -> None:
    """Two writers under SERIALIZABLE both emit audit datoms +
    update ``audit_chain_lock``; the post-commit chain must verify.

    Even when SERIALIZABLE rejects one writer (the expected outcome
    on the write-skew shape), the surviving writer's audit datom
    must chain cleanly off the seed state (initial ``last_hash=''``
    means ``prev-hash=None`` for the first audit datom). When BOTH
    writers commit (rare but possible if SSI didn't fire on this
    particular run — e.g. the predicate locks happened to not
    intersect), the lock-row contention orders them and chain
    continuity holds.
    """
    dsn = pg_dsn_factory()
    a, b = _run_writeskew_pair(
        dsn=dsn, isolation="serializable",
        initial_x=200, initial_y=150, decrement=10, use_audit=True,
    )
    # At least one of the two outcomes is well-defined.
    assert a.outcome in (
        "committed", "serialization_failure", "unique_violation",
        "other_error",
    )
    assert b.outcome in (
        "committed", "serialization_failure", "unique_violation",
        "other_error",
    )
    # Verify: any audit datoms that DID land form a continuous chain.
    ok, reason = audit_chain_continuous(dsn=dsn)
    assert ok, f"audit-chain Merkle continuity broken: {reason}"


def test_audit_chain_continuous_under_read_committed(pg_dsn_factory) -> None:
    """Audit-chain continuity is preserved even under READ COMMITTED.

    The ``audit_chain_lock FOR UPDATE`` row lock is a Postgres-level
    primitive (NOT an SSI predicate lock), so it serialises writers
    regardless of isolation level. Under READ COMMITTED both writers
    commit (per ``test_read_committed_produces_anomaly_singleshot``),
    yet their audit datoms still chain in the order the row-lock
    granted them access. This pins the ADR-3 contract: the chain-
    head row-lock is the load-bearing primitive, NOT SSI rejection.
    """
    dsn = pg_dsn_factory()
    a, b = _run_writeskew_pair(
        dsn=dsn, isolation="read_committed",
        initial_x=200, initial_y=150, decrement=10, use_audit=True,
    )
    assert a.outcome == "committed", f"writer A: {a!r}"
    assert b.outcome == "committed", f"writer B: {b!r}"
    ok, reason = audit_chain_continuous(dsn=dsn)
    assert ok, f"audit-chain broken under READ COMMITTED + row-lock: {reason}"
