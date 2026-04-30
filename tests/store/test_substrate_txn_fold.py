"""``Substrate.txn.fold`` curated re-export — PG6 stream #169.

Verifies that:

1. ``Substrate.txn.fold`` is a real callable on the curated namespace,
   not a missing attribute or an escape-hatch reach-through.
2. The ``@experimental`` stability metadata is attached so the spec
   generator (G7 / SDK5) records it as out-of-contract.
3. Calls round-trip into the underlying ``DB.fold`` correctly — same
   accumulator + datom count.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from persistence.sdk import Substrate


def _now() -> datetime:
    return datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)


def _step(acc, item, db):
    fact = {
        "e": f"i-{item}",
        "a": "fold/value",
        "v": item,
        "valid_from": _now(),
    }
    return acc + item, [fact]


class TestCuratedSurface:
    def test_substrate_txn_fold_is_callable(self):
        """``s.txn.fold`` exists and is invokable."""
        with Substrate.open("memory") as s:
            assert callable(s.txn.fold)

    def test_round_trip_through_substrate(self):
        """Calls return the same result as :meth:`DB.fold` directly."""
        with Substrate.open("memory") as s:
            acc, n = s.txn.fold(seed=0, items=[1, 2, 3], fn=_step)
            assert acc == 6
            assert n == 3
            # Datoms landed on the underlying DB through the curated path.
            rows = list(s.escape.fact.store.all_datoms())
            assert {r.v for r in rows} == {1, 2, 3}

    def test_round_trip_with_kwargs(self):
        """``checkpoint_every`` + ``on_error`` flow through unchanged."""
        with Substrate.open("memory") as s:
            acc, n = s.txn.fold(
                seed=0,
                items=[1, 2, 3, 4, 5],
                fn=_step,
                checkpoint_every=2,
                on_error="abort",
            )
            assert acc == 15
            assert n == 5


class TestStabilityMetadata:
    def test_fold_is_marked_experimental(self):
        """The ``@experimental`` decorator attached
        ``__sdk_stability__`` metadata indicating non-contract status.
        """
        with Substrate.open("memory") as s:
            fold_method = s.txn.fold
            # Bound methods retain the underlying function's
            # decorator-attached attributes via __func__.
            underlying = getattr(fold_method, "__func__", fold_method)
            metadata = getattr(underlying, "__sdk_stability__", None)
            assert metadata is not None, (
                "Substrate.txn.fold must carry __sdk_stability__ "
                "metadata so the spec generator can record it as "
                "non-contract"
            )
            assert metadata.get("level") == "experimental"
            # Reason mentions PG6 / R3-M1 so spec readers can locate
            # the design ticket.
            reason = metadata.get("reason") or ""
            assert "PG6" in reason or "R3-M1" in reason

    def test_fold_is_not_stable_v08(self):
        """``s.txn.fold`` must NOT carry ``@stable("v0.8")`` — that
        would commit the v0.8 contract to a surface that the design
        explicitly leaves room to evolve in v0.9.
        """
        with Substrate.open("memory") as s:
            underlying = getattr(s.txn.fold, "__func__", s.txn.fold)
            metadata = getattr(underlying, "__sdk_stability__", {})
            assert metadata.get("level") != "stable"


class TestBackwardCompat:
    def test_other_txn_methods_still_present(self):
        """Adding ``fold`` to ``_TxnNamespace`` did not regress the
        existing dosync / new_ref / ref pass-throughs."""
        with Substrate.open("memory") as s:
            assert callable(s.txn.dosync)
            assert callable(s.txn.new_ref)
            assert callable(s.txn.ref)
            assert callable(s.txn.fold)

    def test_substrate_dir_does_not_leak_fold_attribute(self):
        """``dir(s)`` is a closed contract surface; new ``fold`` lives
        under ``s.txn``, not at the top level."""
        with Substrate.open("memory") as s:
            top_level = dir(s)
            assert "fold" not in top_level
            # txn namespace is part of the closed contract — fold lives
            # inside it.
            assert "txn" in top_level
