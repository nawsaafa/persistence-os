"""End-to-end tests for ``DB`` — the bitemporal query surface.

These exercise the full semantics from agent1-fact-spec §2 and paper §4.1:

    as_of           τ_sys ≤ t
    as_of_valid     ν_from ≤ t < ν_to and op = assert
    history         every datom for entity e, in tx order
    since           τ_sys > t (incremental sync)
    branch          counterfactual over an earlier snapshot

Plus the operational requirements:

    transact round-trip, retract round-trip, auto-retraction of superseded
    cardinality-one asserts, DBView.entity projection, and the idempotence
    invariant for as_of at times ≥ now (the plan.edn verify gate).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from persistence.fact import DB, Datom, InMemoryStore, SQLiteStore


def _dt(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# transact / retract
# ---------------------------------------------------------------------------
class TestTransact:
    def test_transact_returns_new_db_with_appended_datoms(self, store):
        db = DB(store)
        db2 = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.087,
              "valid_from": _dt(2026, 4, 14)}],
            provenance={"source": "dfi-agent"},
        )
        assert isinstance(db2, DB)
        assert len(list(db2.log())) == 1

    def test_transact_assigns_monotonic_tx_ids(self, store):
        db = DB(store)
        db = db.transact(
            [{"e": "a", "a": "x", "v": 1, "valid_from": _dt(2026, 1, 1)}],
            provenance={},
        )
        db = db.transact(
            [{"e": "b", "a": "x", "v": 2, "valid_from": _dt(2026, 1, 2)}],
            provenance={},
        )
        txs = [d.tx for d in db.log()]
        assert txs == sorted(txs)
        assert len(set(txs)) == 2

    def test_retract_round_trip(self, store):
        db = DB(store)
        db = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.087,
              "valid_from": _dt(2026, 4, 14)}],
            provenance={"source": "dfi-agent"},
        )
        db = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.087,
              "valid_from": _dt(2026, 4, 14),
              "op": "retract"}],
            provenance={"source": "human-override"},
        )
        # Current view has nothing for p-042/project/wacc
        entity_now = db.as_of(_dt(2030, 1, 1)).entity("p-042")
        assert "project/wacc" not in entity_now

    def test_auto_retraction_on_cardinality_one_overwrite(self, store):
        """A new :assert on the same (e, a) emits a companion :retract."""
        db = DB(store)
        db = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.087,
              "valid_from": _dt(2026, 4, 14)}],
            provenance={"source": "dfi-agent"},
        )
        db = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.091,
              "valid_from": _dt(2026, 4, 19)}],
            provenance={"source": "dfi-agent-rerun"},
        )
        log = list(db.log())
        # We expect at least: assert(0.087), retract(0.087), assert(0.091)
        retracts = [d for d in log if d.op == "retract"]
        asserts = [d for d in log if d.op == "assert"]
        assert len(asserts) == 2
        assert len(retracts) == 1
        # The original assert has been stamped with invalidated_by.
        orig = next(d for d in log if d.op == "assert" and d.v == 0.087)
        assert orig.invalidated_by is not None


# ---------------------------------------------------------------------------
# as_of / as_of_valid — three-point-in-time correctness
# ---------------------------------------------------------------------------
class TestAsOf:
    def _setup_wacc_history(self, store):
        """Reproduce the agent1-fact-spec §8 WACC scenario."""
        db = DB(store)
        db = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.087,
              "valid_from": _dt(2026, 4, 14)}],
            provenance={"source": "dfi-agent", "confidence": 0.82},
        )
        db = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.091,
              "valid_from": _dt(2026, 4, 19)}],
            provenance={"source": "dfi-agent-rerun", "confidence": 0.88},
        )
        return db

    def test_as_of_now_returns_latest(self, store):
        db = self._setup_wacc_history(store)
        entity = db.as_of(_dt(2030, 1, 1)).entity("p-042")
        assert entity["project/wacc"] == 0.091

    def test_as_of_valid_earlier_returns_earlier(self, store):
        """Hot query: validAsOf(D, Apr 15) should see 0.087 (valid from 14)."""
        db = self._setup_wacc_history(store)
        entity = db.as_of_valid(_dt(2026, 4, 15)).entity("p-042")
        assert entity["project/wacc"] == 0.087

    def test_as_of_valid_later_returns_later(self, store):
        db = self._setup_wacc_history(store)
        entity = db.as_of_valid(_dt(2026, 4, 20)).entity("p-042")
        assert entity["project/wacc"] == 0.091

    def test_as_of_filters_on_tx_time(self, store):
        """asOf predicate is about τ_sys, not ν_from."""
        t0 = _dt(2026, 4, 14)
        db = DB(store).transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.087, "valid_from": t0}],
            provenance={},
        )
        # A point strictly AFTER the transaction's tx_time. We read it off
        # the log rather than sampling the wall clock a second time — that
        # avoids a nanosecond-scale race between transact() and the test.
        (only,) = list(db.log())
        hot_t = only.tx_time + timedelta(microseconds=1)
        cold = db.as_of(datetime(1999, 1, 1, tzinfo=timezone.utc))
        assert cold.entity("p-042") == {}
        hot = db.as_of(hot_t)
        assert hot.entity("p-042") == {"project/wacc": 0.087}


# ---------------------------------------------------------------------------
# history / since
# ---------------------------------------------------------------------------
class TestHistoryAndSince:
    def test_history_returns_full_lineage_for_entity(self, store):
        db = DB(store)
        db = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.087, "valid_from": _dt(2026, 4, 14)}],
            provenance={},
        )
        db = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.091, "valid_from": _dt(2026, 4, 19)}],
            provenance={},
        )
        hist = db.history("p-042")
        # Original assert + companion retract + new assert = 3 datoms
        assert len(hist) == 3
        # History is sorted by tx (paper §4.1 definition)
        assert [d.tx for d in hist] == sorted(d.tx for d in hist)

    def test_history_is_empty_for_unknown_entity(self, store):
        db = DB(store)
        assert db.history("no-such-entity") == []

    def test_since_returns_only_newly_asserted(self, store):
        t0 = _dt(2026, 4, 14)
        db = DB(store)
        db = db.transact(
            [{"e": "a", "a": "x", "v": 1, "valid_from": t0}],
            provenance={},
        )
        ts_cut = datetime.now(timezone.utc)
        # Now add another
        import time as _time

        _time.sleep(0.002)
        db = db.transact(
            [{"e": "b", "a": "x", "v": 2, "valid_from": _dt(2026, 4, 15)}],
            provenance={},
        )
        delta = list(db.since(ts_cut))
        assert len(delta) == 1
        assert delta[0].e == "b"


# ---------------------------------------------------------------------------
# branch — counterfactual isolation
# ---------------------------------------------------------------------------
class TestBranch:
    def test_branch_does_not_mutate_original_db(self, store):
        db = DB(store)
        db = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.087, "valid_from": _dt(2026, 4, 14)}],
            provenance={"source": "dfi-agent"},
        )
        cf = db.branch(
            datetime.now(timezone.utc),
            [{"e": "p-042", "a": "project/wacc", "v": 0.095,
              "valid_from": _dt(2026, 4, 14)}],
        )
        assert db.as_of(datetime.now(timezone.utc)).entity("p-042") == {"project/wacc": 0.087}
        assert cf.as_of(datetime.now(timezone.utc)).entity("p-042") == {"project/wacc": 0.095}

    def test_branch_is_isolated_counterfactual(self, store):
        """The branched DB writes into its own store, not the parent's."""
        db = DB(store)
        db = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.087, "valid_from": _dt(2026, 4, 14)}],
            provenance={},
        )
        cf = db.branch(
            datetime.now(timezone.utc),
            [{"e": "p-042", "a": "project/wacc", "v": 0.095,
              "valid_from": _dt(2026, 4, 14)}],
        )
        # Append a second fact on the counterfactual: the original DB must
        # not see it.
        cf = cf.transact(
            [{"e": "p-042", "a": "project/npv", "v": 123.0, "valid_from": _dt(2026, 4, 14)}],
            provenance={"source": "cf-scoring"},
        )
        assert "project/npv" not in db.as_of(datetime.now(timezone.utc)).entity("p-042")
        assert cf.as_of(datetime.now(timezone.utc)).entity("p-042")["project/npv"] == 123.0

    def test_branch_provenance_marks_source(self, store):
        db = DB(store).transact(
            [{"e": "e", "a": "a", "v": 1, "valid_from": _dt(2026, 1, 1)}],
            provenance={"source": "real"},
        )
        cf = db.branch(
            datetime.now(timezone.utc),
            [{"e": "e", "a": "a", "v": 2, "valid_from": _dt(2026, 1, 1)}],
        )
        hypothetical = [d for d in cf.log() if d.v == 2]
        assert hypothetical, "branch should include the hypothetical datom"
        assert hypothetical[0].provenance.get("source") == "branch"


# ---------------------------------------------------------------------------
# Invariant — plan.edn verify gate
# ---------------------------------------------------------------------------
class TestAsOfIdempotence:
    """[:verify {:prover :invariant
                 :claim "as-of(db, t) is idempotent for t >= now"}]"""

    def test_as_of_future_is_idempotent(self, store):
        db = DB(store).transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.087, "valid_from": _dt(2026, 4, 14)}],
            provenance={},
        )
        far_future = datetime.now(timezone.utc) + timedelta(days=365 * 100)
        view1 = db.as_of(far_future)
        view2 = db.as_of(far_future)
        # Same datoms, same order
        assert [d for d in view1.datoms] == [d for d in view2.datoms]
        # Same entity projection
        assert view1.entity("p-042") == view2.entity("p-042")
        # Same as querying now (nothing can have changed post-now)
        now_view = db.as_of(datetime.now(timezone.utc))
        assert view1.entity("p-042") == now_view.entity("p-042")

    def test_as_of_future_after_no_writes_is_stable_across_calls(self, store):
        db = DB(store).transact(
            [{"e": "a", "a": "x", "v": 1, "valid_from": _dt(2026, 1, 1)}],
            provenance={},
        )
        t = datetime.now(timezone.utc) + timedelta(days=1)
        first = db.as_of(t).entity("a")
        second = db.as_of(t).entity("a")
        third = db.as_of(t).entity("a")
        assert first == second == third == {"x": 1}


# ---------------------------------------------------------------------------
# DBView.entity projection details
# ---------------------------------------------------------------------------
class TestEntityProjection:
    def test_multiple_attributes_materialized(self, store):
        db = DB(store).transact(
            [
                {"e": "p-042", "a": "project/wacc", "v": 0.087, "valid_from": _dt(2026, 4, 14)},
                {"e": "p-042", "a": "project/country", "v": "EG", "valid_from": _dt(2026, 4, 14)},
            ],
            provenance={},
        )
        e = db.as_of(datetime.now(timezone.utc)).entity("p-042")
        assert e == {"project/wacc": 0.087, "project/country": "EG"}

    def test_retracted_attributes_excluded_from_projection(self, store):
        db = DB(store).transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.087, "valid_from": _dt(2026, 4, 14)}],
            provenance={},
        )
        db = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.087,
              "valid_from": _dt(2026, 4, 14), "op": "retract"}],
            provenance={},
        )
        e = db.as_of(datetime.now(timezone.utc)).entity("p-042")
        assert "project/wacc" not in e


# ---------------------------------------------------------------------------
# Clock injection (ARIS R2 F5) — a DB constructed with a frozen clock must
# stamp every tx_time from that clock, not from the wall clock. This is the
# seam replay uses to reproduce tx_time deterministically.
# ---------------------------------------------------------------------------
class TestClockInjection:
    def test_injected_clock_is_used_for_tx_time(self, store):
        fixed = _dt(2026, 4, 21, 12, 0)
        db = DB(store, clock=lambda: fixed)
        db = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.087,
              "valid_from": _dt(2026, 4, 14)}],
            provenance={"source": "dfi-agent"},
        )
        log = list(db.log())
        assert len(log) == 1
        assert log[0].tx_time == fixed

    def test_injected_clock_survives_transact_return(self, store):
        """DB.transact returns a new DB bound to the same clock so chained
        transacts all use the injected clock."""
        fixed = _dt(2026, 4, 21, 12, 0)
        db = DB(store, clock=lambda: fixed)
        db = db.transact(
            [{"e": "a", "a": "x", "v": 1, "valid_from": fixed}], provenance={}
        )
        db = db.transact(
            [{"e": "b", "a": "x", "v": 2, "valid_from": fixed}], provenance={}
        )
        for d in db.log():
            assert d.tx_time == fixed

    def test_injected_clock_survives_branch(self, store):
        """A counterfactual branch inherits the parent DB's clock."""
        fixed = _dt(2026, 4, 21, 12, 0)
        db = DB(store, clock=lambda: fixed)
        db = db.transact(
            [{"e": "p", "a": "w", "v": 0.1, "valid_from": fixed}], provenance={}
        )
        cf = db.branch(fixed, [{"e": "p", "a": "w", "v": 0.2, "valid_from": fixed}])
        for d in cf.log():
            assert d.tx_time == fixed


# ---------------------------------------------------------------------------
# Backend parity — sanity check that in-memory and SQLite agree on the same
# scenario end-to-end (belt-and-braces on top of the conformance suite).
# ---------------------------------------------------------------------------
def test_inmemory_and_sqlite_agree(tmp_path):
    def play(s):
        db = DB(s)
        db = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.087, "valid_from": _dt(2026, 4, 14)}],
            provenance={"source": "dfi-agent"},
        )
        db = db.transact(
            [{"e": "p-042", "a": "project/wacc", "v": 0.091, "valid_from": _dt(2026, 4, 19)}],
            provenance={"source": "dfi-agent-rerun"},
        )
        return {
            "now": db.as_of(datetime.now(timezone.utc)).entity("p-042"),
            "apr15": db.as_of_valid(_dt(2026, 4, 15)).entity("p-042"),
            "apr20": db.as_of_valid(_dt(2026, 4, 20)).entity("p-042"),
            "history_len": len(db.history("p-042")),
        }

    mem = play(InMemoryStore())
    sql = play(SQLiteStore(path=str(tmp_path / "db.sqlite")))
    assert mem == sql
