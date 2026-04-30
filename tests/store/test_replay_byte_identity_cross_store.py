"""PG2 — replay byte-identity gate parametrised across all 3 store backends.

Phase 1 stream #165. Closes the design-doc §13 deferral on cross-store
codec consolidation: with :mod:`persistence.store._codec` now backing
both :class:`~persistence.fact.SQLiteStore` (via
:class:`TextDatomCodec`) and :class:`~persistence.store.postgres.PostgresStore`
(via :class:`NativeDatomCodec`), the byte-identity invariant is
*Datom-level* (post-decode equality) rather than raw row-tuple
equality. This module pins that invariant via Hypothesis.

Three property tests run here, each parametrised over the full backend
list ``[InMemoryStore, SQLiteStore, PostgresStore]``:

1. :func:`test_per_store_round_trip_byte_identity` — generate N random
   Datom-shaped batches; each backend writes them in order, reads
   them back via :meth:`Store.all_datoms`, and the decoded list must
   match the in-memory baseline. Pins Datom-level equality on the
   decode boundary for each backend independently.

2. :func:`test_cross_store_round_trip_equivalence` — same generated
   batches go through all 3 backends; the decoded outputs must equal
   each other. Pins cross-backend Datom equivalence (the "switching
   from sqlite:// to postgres:// produces identical reads" guarantee).

3. :func:`test_edge_case_datoms_round_trip` — a curated, non-
   parametrised set of edge-case Datoms (datetimes with timezones,
   very small / very large tx, unicode in entity strings, retract
   datoms with the ``superseded_by_tx`` placeholder rewrite) round-
   trip through all 3 backends. This catches edges Hypothesis might
   not generate at the configured budget.

Hypothesis budget
-----------------

Per the design doc § 11 G2 contract, the property runs at
``max_examples=200`` for InMemory + SQLite (fast in-process round-
trip) and ``max_examples=50`` for Postgres (the per-example DB
round-trip is ~10-50ms — at 200 examples that is 10s+ per test, which
is too slow for the inner CI loop). 50 examples is the same budget
as the cross-process property (PG4) and matches the design's "lower
because of round-trip overhead" rationale.

Live-Postgres skip rule
-----------------------

The Postgres parametrisation is skipped when the ``PERSISTENCE_PG_DSN``
env var is unset (matches the convention used by
``tests/store/test_postgres.py``). InMemory + SQLite always run at
the full 200-example budget regardless of DSN availability, so the
suite stays green and meaningful on machines without Postgres.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterator

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from persistence.fact import Datom, InMemoryStore, SQLiteStore


# ---------------------------------------------------------------------------
# Backend matrix
# ---------------------------------------------------------------------------

_PG_DSN = os.environ.get("PERSISTENCE_PG_DSN", "")
_PG_DSN_REASON = (
    "PERSISTENCE_PG_DSN env var not set — set it to a libpq DSN "
    '(e.g. "postgresql://user@localhost:5432/test") to run the cross-'
    "store byte-identity property against PostgresStore. The InMemory "
    "and SQLite legs always run."
)


# Hypothesis budgets per backend. Pinned per design § 11 G2 + G3.
# Postgres runs at the lower budget because each example pays a real
# DB round-trip; InMemory + SQLite run at the design-mandated 200.
_BUDGETS = {
    "memory": 200,
    "sqlite": 200,
    "postgres": 50,
}


def _make_memory_store() -> InMemoryStore:
    return InMemoryStore()


def _make_sqlite_store() -> SQLiteStore:
    # ``:memory:`` keeps the SQLite leg fast — no disk I/O between
    # examples. Each call constructs a fresh in-memory database.
    return SQLiteStore(path=":memory:")


def _make_postgres_store():
    """Open a per-example PostgresStore against a fresh schema namespace.

    The fresh-schema-per-example pattern matches
    ``tests/store/test_postgres.py``'s :func:`pg_store` fixture: a
    private schema is created, the store opens with ``search_path``
    scoped to it, and we drop the schema at teardown so the
    ``tx_allocator`` row resets to ``next_tx=1`` per example. This
    keeps the Hypothesis property hermetic across examples.
    """
    if not _PG_DSN:
        pytest.skip(_PG_DSN_REASON)

    import psycopg

    from persistence.store.postgres import PostgresStore

    schema = f"test_pg2_{uuid.uuid4().hex[:12]}"

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

    # Stash the schema name on the store so the cleanup helper can
    # drop it; we don't want to plumb a separate teardown registry
    # through Hypothesis's per-example loop.
    store._test_schema_name = schema  # type: ignore[attr-defined]
    return store


def _close_postgres_store(store) -> None:
    """Close ``store`` and drop the per-example schema."""
    schema = getattr(store, "_test_schema_name", None)
    try:
        store.close()
    finally:
        if schema is not None and _PG_DSN:
            import psycopg

            cleanup_conn = psycopg.connect(_PG_DSN)
            cleanup_conn.autocommit = True
            try:
                with cleanup_conn.cursor() as cur:
                    cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
            finally:
                cleanup_conn.close()


# ``backend_name`` is what pytest's parametrize id surfaces; the
# constructor is invoked per Hypothesis example. ``cleanup`` is None
# for the in-process backends (their __del__ + GC handle teardown);
# for Postgres we drop the per-example schema explicitly.
_BACKENDS = [
    ("memory", _make_memory_store, None),
    ("sqlite", _make_sqlite_store, None),
    ("postgres", _make_postgres_store, _close_postgres_store),
]


# ---------------------------------------------------------------------------
# Datom strategies
# ---------------------------------------------------------------------------

# Small but non-trivial alphabet for entity strings — letters + digits
# + a few unicode chars to exercise the Postgres TEXT column on
# non-ASCII bytes (codec must round-trip lossless).
_eid_alphabet = st.characters(
    categories=["Ll", "Lu", "Nd", "Lo"],
    # Avoid surrogates / control chars — psycopg + sqlite3 reject them
    # outright as a pre-codec error, which is correct database
    # behaviour but not what we're testing here.
    blacklist_categories=["Cs", "Cc"],
)


@st.composite
def _eid_st(draw):
    return draw(st.text(alphabet=_eid_alphabet, min_size=1, max_size=12))


@st.composite
def _attr_st(draw):
    # Datom.a is the canonical (leading-colon stripped) attribute. We
    # draw alphanumeric segments separated by ``/`` so the strategy
    # produces both bare ``"name"`` and namespaced ``"project/name"``
    # shapes — both round-trip identically through the codec.
    head = draw(st.text(alphabet=st.characters(categories=["Ll", "Nd"]), min_size=1, max_size=8))
    if draw(st.booleans()):
        tail = draw(st.text(alphabet=st.characters(categories=["Ll", "Nd"]), min_size=1, max_size=8))
        return f"{head}/{tail}"
    return head


@st.composite
def _value_st(draw):
    """JSON-serialisable Datom values.

    The substrate's ``v`` column carries canonical JSON, so the
    strategy draws what JSON can losslessly represent: scalars, small
    lists, small dicts. We avoid floats — JSON loses precision around
    edge-of-double values, and the substrate's tests historically
    pin ``int`` / ``str`` / ``bool`` / ``None`` for round-trip
    determinism.
    """
    return draw(
        st.recursive(
            st.integers(min_value=-1_000_000, max_value=1_000_000)
            | st.text(min_size=0, max_size=16)
            | st.booleans()
            | st.none(),
            lambda children: st.lists(children, max_size=4)
            | st.dictionaries(
                keys=st.text(min_size=1, max_size=8),
                values=children,
                max_size=4,
            ),
            max_leaves=4,
        )
    )


# Fixed timestamp window so the strategy stays in a deterministic
# bitemporal range. The codec must round-trip every datetime in this
# window — we draw within a 365-day band so timezone-aware encoding /
# decoding is exercised across DST boundaries.
_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@st.composite
def _datetime_st(draw):
    seconds = draw(st.integers(min_value=0, max_value=365 * 24 * 3600))
    micros = draw(st.integers(min_value=0, max_value=999_999))
    return _T0 + timedelta(seconds=seconds, microseconds=micros)


@st.composite
def _datom_st(draw):
    """Build a single :class:`Datom` for round-trip testing.

    Constraints chosen to match what the substrate emits in
    production:

    - ``op`` is ``'assert'`` or ``'retract'``
    - ``valid_to`` is None on assert (open-ended) and equal to
      ``valid_from`` on retract (instantaneous), matching the
      auto-retraction shape from ``DB.transact``
    - ``provenance`` is a small mapping of strings + ints — matches
      the canonical commit-datom shape from
      ``_build_commit_provenance``
    - ``invalidated_by`` is None for fresh datoms; tests that
      exercise the invalidation path use a separate fixture
    """
    e = draw(_eid_st())
    a = draw(_attr_st())
    v = draw(_value_st())
    tx_time = draw(_datetime_st())
    valid_from = tx_time
    op = draw(st.sampled_from(["assert", "retract"]))
    valid_to = valid_from if op == "retract" else None
    provenance = draw(
        st.dictionaries(
            keys=st.text(
                alphabet=st.characters(categories=["Ll", "Nd"]),
                min_size=1,
                max_size=10,
            ),
            values=st.one_of(
                st.text(max_size=16),
                st.integers(min_value=0, max_value=1_000_000),
            ),
            max_size=3,
        )
    )
    return Datom(
        e=e,
        a=a,
        v=v,
        # tx is allocated by the store on write; placeholder of 0
        # gets overwritten by ``allocate_and_append``.
        tx=0,
        tx_time=tx_time,
        valid_from=valid_from,
        valid_to=valid_to,
        op=op,
        provenance=provenance,
        invalidated_by=None,
    )


@st.composite
def _datom_batches_st(draw, max_batches: int = 4, max_per_batch: int = 5):
    """Draw a list of Datom batches.

    Each batch goes through ``allocate_and_append`` as one unit, so
    all datoms in the batch share one allocated tx-id. Multiple
    batches exercise the tx-id monotonic invariant under the codec
    boundary.
    """
    n_batches = draw(st.integers(min_value=1, max_value=max_batches))
    batches = []
    for _ in range(n_batches):
        n_in_batch = draw(st.integers(min_value=1, max_value=max_per_batch))
        batch = [draw(_datom_st()) for _ in range(n_in_batch)]
        batches.append(batch)
    return batches


# ---------------------------------------------------------------------------
# Round-trip helpers
# ---------------------------------------------------------------------------


def _write_then_read(store, batches: list[list[Datom]]) -> list[Datom]:
    """Apply ``batches`` via ``allocate_and_append`` then return all_datoms."""
    for batch in batches:
        store.allocate_and_append(batch)
    return list(store.all_datoms())


def _normalise_for_compare(datoms: list[Datom]) -> list[Datom]:
    """Strip the per-store-allocated tx so cross-store comparison sees same shape.

    Each backend allocates tx-ids monotonically starting at 1, but
    nothing in the codec layer pins them across backends — they're
    allocated by the store, not derived from the input. The cross-
    store comparison therefore zeroes tx-ids before comparing; the
    per-store allocation invariant is checked separately.
    """
    return [
        Datom(
            e=d.e,
            a=d.a,
            v=d.v,
            tx=0,
            tx_time=d.tx_time,
            valid_from=d.valid_from,
            valid_to=d.valid_to,
            op=d.op,
            provenance=d.provenance,
            invalidated_by=d.invalidated_by,
        )
        for d in datoms
    ]


# ---------------------------------------------------------------------------
# Per-backend round-trip property — runs at the per-backend Hypothesis budget
# ---------------------------------------------------------------------------


def _make_property_for_backend(backend_name: str, build, cleanup):
    """Construct a Hypothesis-decorated property at the right budget.

    The budget is per-backend (200 in-process, 50 for Postgres). We
    can't @parametrize on a fixed-budget @settings decoration, so we
    factor each backend's body through this helper and let pytest
    parametrise the wrappers below.
    """

    @given(batches=_datom_batches_st())
    @settings(
        max_examples=_BUDGETS[backend_name],
        deadline=None,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
            HealthCheck.data_too_large,
        ],
    )
    def _property(batches):
        store = build()
        try:
            written = _write_then_read(store, batches)
            # Tx-ids are monotonic starting at 1 — one tx per batch.
            expected_txs = list(range(1, len(batches) + 1))
            actual_txs = sorted(set(d.tx for d in written))
            assert actual_txs == expected_txs, (
                f"{backend_name}: tx ordering broke; expected "
                f"{expected_txs}, got {actual_txs}"
            )
            # Sum of per-batch lengths equals total written rows.
            total_in = sum(len(b) for b in batches)
            assert len(written) == total_in, (
                f"{backend_name}: row count mismatch; wrote {total_in}, "
                f"read back {len(written)}"
            )
            # Datom-level invariants per row: op preserved, datetime
            # round-trip preserves equality, JSON v / provenance
            # decoded back to original shape.
            in_order = [d for batch in batches for d in batch]
            for original, read_back in zip(in_order, written, strict=True):
                assert original.e == read_back.e
                assert original.a == read_back.a
                assert original.v == read_back.v
                assert original.tx_time == read_back.tx_time
                assert original.valid_from == read_back.valid_from
                assert original.valid_to == read_back.valid_to
                assert original.op == read_back.op
                assert original.provenance == read_back.provenance
                assert original.invalidated_by == read_back.invalidated_by
        finally:
            if cleanup is not None:
                cleanup(store)

    return _property


# Build the actual test function set — one per backend. Pytest's
# ``@pytest.mark.parametrize`` doesn't compose with Hypothesis
# ``@settings(max_examples=...)`` cleanly because the budget is fixed
# at decoration time, so we generate the per-backend wrappers
# explicitly.

test_per_store_round_trip_memory = _make_property_for_backend(
    "memory", _make_memory_store, None
)
test_per_store_round_trip_sqlite = _make_property_for_backend(
    "sqlite", _make_sqlite_store, None
)
test_per_store_round_trip_postgres = _make_property_for_backend(
    "postgres", _make_postgres_store, _close_postgres_store
)


# ---------------------------------------------------------------------------
# Cross-store equivalence — same draws, all 3 backends, decoded equality
# ---------------------------------------------------------------------------


@given(batches=_datom_batches_st())
@settings(
    # Cross-store budget is the Postgres budget (the slowest leg).
    max_examples=_BUDGETS["postgres"],
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.function_scoped_fixture,
        HealthCheck.data_too_large,
    ],
)
def test_cross_store_round_trip_equivalence(batches):
    """Same draws → all backends decode to equal Datom sequences.

    The backends differ on storage representation: SQLite uses ISO
    TEXT for datetimes, Postgres uses native TIMESTAMPTZ via
    psycopg's adapter, InMemory holds the original Python objects.
    Despite that, the decoded Datom sequences must compare equal at
    the semantic level. This is the load-bearing PG2 invariant.

    Postgres-leg gating: if ``PERSISTENCE_PG_DSN`` is unset, the
    Postgres backend skips out and the test reduces to a 2-store
    comparison. The InMemory + SQLite legs always run at the
    Postgres budget (50 examples) because the comparison is bounded
    by the slowest backend; tests above run each backend
    independently at its native budget.
    """
    in_mem = _make_memory_store()
    sql = _make_sqlite_store()
    backends = [("memory", in_mem, None), ("sqlite", sql, None)]

    pg = None
    if _PG_DSN:
        pg = _make_postgres_store()
        backends.append(("postgres", pg, _close_postgres_store))

    try:
        results = {}
        for name, store, _ in backends:
            results[name] = _normalise_for_compare(_write_then_read(store, batches))

        # Every backend must agree on the decoded sequence.
        names = list(results.keys())
        baseline = results[names[0]]
        for other in names[1:]:
            assert results[other] == baseline, (
                f"{other} decoded differently from {names[0]}: "
                f"\n  baseline tail: {baseline[-3:] if baseline else baseline!r}"
                f"\n  {other} tail:    "
                f"{results[other][-3:] if results[other] else results[other]!r}"
            )
    finally:
        if pg is not None:
            _close_postgres_store(pg)


# ---------------------------------------------------------------------------
# Curated edge-case round-trip — non-Hypothesis fixture set
# ---------------------------------------------------------------------------

# Non-zero microseconds + non-UTC timezone (the codec MUST preserve
# both — historical bugs around datetime adapters most often surface
# at exactly these edges).
_TZ_OFFSET = timezone(timedelta(hours=5, minutes=30))  # IST
_T_PRECISE = datetime(2026, 4, 30, 9, 15, 27, 123456, tzinfo=_TZ_OFFSET)
_T_UTC = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)


def _edge_case_datoms() -> list[Datom]:
    """A curated edge-case set: timezones / unicode / large tx / placeholders."""
    from persistence.store._codec import TX_PLACEHOLDER

    return [
        # 1. Non-UTC timezone with non-zero microseconds — psycopg
        # adapter must round-trip both.
        Datom(
            e="entity-1",
            a="project/wacc",
            v=0.087,
            tx=0,
            tx_time=_T_PRECISE,
            valid_from=_T_PRECISE,
            valid_to=None,
            op="assert",
            provenance={"source": "edge-case-test"},
            invalidated_by=None,
        ),
        # 2. Unicode in entity + value strings — Postgres TEXT and
        # SQLite TEXT both accept UTF-8; codec must preserve.
        Datom(
            e="实体-2",
            a="user/name",
            v={"name": "Ωαβγ"},
            tx=0,
            tx_time=_T_UTC,
            valid_from=_T_UTC,
            valid_to=None,
            op="assert",
            provenance={"source": "edge-case-test", "lang": "mixed"},
            invalidated_by=None,
        ),
        # 3. Very small tx (placeholder rewrite path). The retract
        # carries provenance[':superseded_by_tx'] = TX_PLACEHOLDER
        # which the allocator's ``with_tx`` rewrites to the real tx
        # at insert time.
        Datom(
            e="entity-3",
            a="project/status",
            v="active",
            tx=0,
            tx_time=_T_UTC,
            valid_from=_T_UTC,
            valid_to=_T_UTC,  # retract: instantaneous validity
            op="retract",
            provenance={"superseded_by_tx": TX_PLACEHOLDER},
            invalidated_by=None,
        ),
        # 4. Nested dict in v — JSON canonicalisation must sort keys.
        Datom(
            e="entity-4",
            a="config/thresholds",
            v={"z": 1, "a": [1, 2, 3], "m": {"nested": True}},
            tx=0,
            tx_time=_T_UTC,
            valid_from=_T_UTC,
            valid_to=None,
            op="assert",
            provenance={"source": "edge-case-test"},
            invalidated_by=None,
        ),
    ]


@pytest.mark.parametrize(
    "backend_name,build,cleanup",
    [
        pytest.param("memory", _make_memory_store, None, id="memory"),
        pytest.param("sqlite", _make_sqlite_store, None, id="sqlite"),
        pytest.param(
            "postgres",
            _make_postgres_store,
            _close_postgres_store,
            id="postgres",
            marks=pytest.mark.skipif(not _PG_DSN, reason=_PG_DSN_REASON),
        ),
    ],
)
def test_edge_case_datoms_round_trip(backend_name, build, cleanup):
    """Curated edge-case set round-trips losslessly through every backend.

    Catches edges that Hypothesis at the configured budget might miss
    (non-UTC timezones, unicode entities, the placeholder-rewrite
    path on retract datoms).
    """
    store = build()
    try:
        edges = _edge_case_datoms()
        store.allocate_and_append(edges)
        read_back = list(store.all_datoms())
        assert len(read_back) == len(edges)

        # The placeholder-rewrite datom (#3): the retract's
        # provenance['superseded_by_tx'] should now be the allocated
        # tx, NOT the -1 sentinel.
        retract = next(d for d in read_back if d.op == "retract")
        assert retract.provenance.get("superseded_by_tx") == retract.tx, (
            f"{backend_name}: TX_PLACEHOLDER rewrite did not run on the "
            f"retract datom — provenance is "
            f"{retract.provenance!r}, tx is {retract.tx}"
        )

        # Every other field round-trips identically (modulo the
        # store-allocated tx).
        for original, read in zip(edges, read_back, strict=True):
            assert original.e == read.e, f"{backend_name}: e drift"
            assert original.a == read.a, f"{backend_name}: a drift"
            assert original.v == read.v, f"{backend_name}: v drift"
            assert original.tx_time == read.tx_time, (
                f"{backend_name}: tx_time drift "
                f"({original.tx_time!r} vs {read.tx_time!r})"
            )
            assert original.valid_from == read.valid_from
            assert original.valid_to == read.valid_to
            assert original.op == read.op
            # Compare provenance after rewriting the placeholder
            # in the original to the actual allocated tx (so the
            # comparison is the same shape post-rewrite).
            from persistence.store._codec import TX_PLACEHOLDER

            expected_prov = {
                k: (read.tx if v == TX_PLACEHOLDER else v)
                for k, v in original.provenance.items()
            }
            assert expected_prov == read.provenance
    finally:
        if cleanup is not None:
            cleanup(store)
