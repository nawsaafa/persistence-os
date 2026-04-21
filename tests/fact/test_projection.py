"""Tests for the projection rebuilder.

Per paper §5.1, the materialized projection (Kuzu + mem0 in production) is a
*disposable cache* that is a pure function of the log. This module ships the
simplest possible projection — a dict keyed by entity — along with the
adapter plumbing that a future Kuzu/mem0 projection can slot into.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from persistence.fact import DB, Datom, InMemoryStore
from persistence.fact.projection import (
    DictProjection,
    ProjectionAdapter,
    rebuild,
)


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


class TestDictProjection:
    def test_rebuild_materialises_current_view(self):
        db = DB(InMemoryStore())
        db = db.transact(
            [
                {"e": "p-042", "a": "project/wacc", "v": 0.087, "valid_from": _dt(2026, 4, 14)},
                {"e": "p-042", "a": "project/country", "v": "EG", "valid_from": _dt(2026, 4, 14)},
                {"e": "p-043", "a": "project/wacc", "v": 0.065, "valid_from": _dt(2026, 4, 14)},
            ],
            provenance={},
        )
        proj = DictProjection()
        rebuild(db, proj)
        assert proj.get("p-042") == {"project/wacc": 0.087, "project/country": "EG"}
        assert proj.get("p-043") == {"project/wacc": 0.065}

    def test_rebuild_reflects_auto_retraction(self):
        db = DB(InMemoryStore())
        db = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.087, "valid_from": _dt(2026, 4, 14)}],
            provenance={},
        )
        db = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.091, "valid_from": _dt(2026, 4, 19)}],
            provenance={},
        )
        proj = DictProjection()
        rebuild(db, proj)
        assert proj.get("p-042") == {"project/wacc": 0.091}

    def test_rebuild_is_idempotent(self):
        db = DB(InMemoryStore()).transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.087, "valid_from": _dt(2026, 4, 14)}],
            provenance={},
        )
        proj = DictProjection()
        rebuild(db, proj)
        snapshot = dict(proj.as_dict())
        rebuild(db, proj)
        rebuild(db, proj)
        assert proj.as_dict() == snapshot


class TestProjectionAdapterStubs:
    """Kuzu + mem0 projections will be separate concerns — this confirms the
    extension point is there (a duck-typed sink any adapter can implement)."""

    def test_custom_adapter_receives_every_datom(self):
        received: list[Datom] = []

        class RecordingAdapter:
            def reset(self):
                received.clear()

            def apply(self, datom):
                received.append(datom)

        db = DB(InMemoryStore()).transact(
            [{"e": "x", "a": "a", "v": 1, "valid_from": _dt(2026, 1, 1)}],
            provenance={},
        )
        rebuild(db, RecordingAdapter())
        assert len(received) == 1

    def test_projection_adapter_protocol_matches_dict_projection(self):
        # Protocol check — DictProjection must satisfy ProjectionAdapter.
        proj: ProjectionAdapter = DictProjection()
        assert hasattr(proj, "reset")
        assert hasattr(proj, "apply")
