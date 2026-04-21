"""Conformance tests for the Store protocol.

Every backend must pass the same round-trip + ordering contract. New backends
(Postgres, on-disk Kuzu adapter, etc.) get added to the ``store`` fixture in
``conftest.py`` and inherit the whole suite for free.
"""

from __future__ import annotations

from datetime import datetime, timezone

from persistence.fact import Datom


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def _mk(e, a, v, tx, op="assert", invalidated_by=None, valid_to=None):
    return Datom(
        e=e,
        a=a,
        v=v,
        tx=tx,
        tx_time=_dt(2026, 4, 14),
        valid_from=_dt(2026, 4, 14),
        valid_to=valid_to,
        op=op,
        provenance={"source": "test"},
        invalidated_by=invalidated_by,
    )


class TestStoreRoundtrip:
    def test_empty_store_returns_empty_log(self, store):
        assert list(store.all_datoms()) == []

    def test_append_then_read(self, store):
        d = _mk("p-042", "project/wacc", 0.087, 1)
        store.append([d])
        assert list(store.all_datoms()) == [d]

    def test_append_preserves_insertion_order(self, store):
        d1 = _mk("p-042", "project/wacc", 0.087, 1)
        d2 = _mk("p-042", "project/wacc", 0.091, 2)
        d3 = _mk("p-043", "project/wacc", 0.065, 3)
        store.append([d1])
        store.append([d2, d3])
        assert list(store.all_datoms()) == [d1, d2, d3]

    def test_append_empty_is_noop(self, store):
        store.append([])
        assert list(store.all_datoms()) == []

    def test_roundtrip_preserves_all_fields_including_invalidated_by(self, store):
        d = Datom(
            e="p-042",
            a="project/wacc",
            v={"nested": [1, 2, 3], "currency": "USD"},
            tx=7,
            tx_time=_dt(2026, 4, 14),
            valid_from=_dt(2026, 4, 10),
            valid_to=_dt(2026, 4, 19),
            op="assert",
            provenance={"source": "dfi-agent", "confidence": 0.82},
            invalidated_by=11,
        )
        store.append([d])
        (got,) = list(store.all_datoms())
        assert got == d

    def test_update_invalidated_by_mutates_existing_row(self, store):
        d = _mk("p-042", "project/wacc", 0.087, 1)
        store.append([d])
        store.mark_invalidated(tx=1, invalidated_by_tx=2)
        (got,) = list(store.all_datoms())
        assert got.invalidated_by == 2

    def test_since_returns_only_rows_after_t(self, store):
        t0 = _dt(2026, 4, 10)
        t1 = _dt(2026, 4, 14)
        t2 = _dt(2026, 4, 19)
        older = Datom("e", "a", 1, 1, t0, t0, None, "assert", {})
        newer = Datom("e", "a", 2, 2, t2, t2, None, "assert", {})
        store.append([older, newer])
        assert list(store.since(t1)) == [newer]
