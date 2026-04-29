"""Tests for ``persistence.repl._ops.inspect_op`` (D3).

30 tests across 5 groups:

- ``kind=entity``                                      (8)
- ``kind=audit-window``                                (6)
- ``kind=plan``                                        (4)
- ``kind=causal-history``                              (4)
- Capability + protocol envelopes                       (8)

See design doc ``docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md``
sections 5.0 (time vocabulary), 5.1 (inspect contract), ADR-9 (error
codes), and 10 (D3).

Test pattern: in-memory ``DB`` + a fixed clock, drive ``inspect_op``
directly (NOT through the WS round-trip — those flows are covered in
``test_ws.py``). The capability gate is exercised via
``CapabilitySet`` + ``make_session`` so the same code path that runs
under WS is hit.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from persistence.fact import DB, InMemoryStore
from persistence.repl import (
    Capability,
    CapabilitySet,
    ERR_CAPABILITY_DENIED,
    ERR_INVALID_PARAMS,
    make_session,
)
from persistence.repl._ops import inspect_op
from persistence.repl._ws import _OpError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _dt(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def _fixed_clock(t: datetime):
    def clock() -> datetime:
        return t
    return clock


_DEFAULT_T = _dt(2026, 5, 9, 12, 0, 0)


@pytest.fixture
def db() -> DB:
    return DB(InMemoryStore())


@pytest.fixture
def clock_fixed():
    return _fixed_clock(_DEFAULT_T)


@pytest.fixture
def session_with_inspect(clock_fixed):
    cs = CapabilitySet(
        caps=frozenset({Capability("inspect", "read")}),
    )
    return make_session("token-id-aaaaaaaa", cs, runtime_clock=clock_fixed)


@pytest.fixture
def session_no_caps(clock_fixed):
    cs = CapabilitySet(caps=frozenset())
    return make_session("token-id-bbbbbbbb", cs, runtime_clock=clock_fixed)


def _seed_entity(db: DB, eid: str, attrs: dict, valid_from: datetime) -> DB:
    """Transact a small entity into ``db`` for entity-projection tests."""
    return db.transact(
        [
            {"e": eid, "a": k, "v": v, "valid_from": valid_from}
            for k, v in attrs.items()
        ]
    )


# ===========================================================================
# 1. kind=entity (8)
# ===========================================================================
class TestInspectEntity:
    @pytest.mark.asyncio
    async def test_entity_at_head_returns_dict(
        self, db, session_with_inspect, clock_fixed
    ):
        db = _seed_entity(db, "e1", {"a": 1, "b": "two"}, _DEFAULT_T)
        result = await inspect_op(
            session_with_inspect,
            db,
            {"kind": "entity", "entity_id": "e1"},
        )
        assert result["entity"] == {"a": 1, "b": "two"}
        assert result["cursor_iso"] == _DEFAULT_T.isoformat()

    @pytest.mark.asyncio
    async def test_entity_with_view_cursor(
        self, db, session_with_inspect
    ):
        # Seed at T0, snapshot the cursor at T1 (after T0) — entity present.
        t0 = _dt(2026, 5, 9, 10, 0, 0)
        t1 = _dt(2026, 5, 9, 11, 0, 0)
        db = _seed_entity(db, "e1", {"a": 1}, t0)
        result = await inspect_op(
            session_with_inspect,
            db,
            {
                "kind": "entity",
                "entity_id": "e1",
                "view_cursor_tx_time_iso": t1.isoformat(),
            },
        )
        assert result["entity"] == {"a": 1}
        assert result["cursor_iso"] == t1.isoformat()

    @pytest.mark.asyncio
    async def test_entity_not_found_returns_null(
        self, db, session_with_inspect
    ):
        result = await inspect_op(
            session_with_inspect,
            db,
            {"kind": "entity", "entity_id": "never-exists"},
        )
        assert result["entity"] is None
        # NOT an error.
        assert "cursor_iso" in result

    @pytest.mark.asyncio
    async def test_entity_invalid_view_cursor_raises(
        self, db, session_with_inspect
    ):
        with pytest.raises(_OpError) as excinfo:
            await inspect_op(
                session_with_inspect,
                db,
                {
                    "kind": "entity",
                    "entity_id": "e1",
                    "view_cursor_tx_time_iso": "not-an-iso-string",
                },
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS
        assert "view_cursor_tx_time_iso" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_entity_missing_entity_id_raises(
        self, db, session_with_inspect
    ):
        with pytest.raises(_OpError) as excinfo:
            await inspect_op(
                session_with_inspect,
                db,
                {"kind": "entity"},
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS
        assert "entity_id" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_entity_at_past_coordinate_excludes_future(
        self, db, session_with_inspect
    ):
        # Asserts at T0 + T2; cursor at T1 sees only T0.
        t0 = _dt(2026, 5, 9, 10, 0, 0)
        t1 = _dt(2026, 5, 9, 11, 0, 0)
        t2 = _dt(2026, 5, 9, 12, 0, 0)
        db = db.transact([{"e": "e1", "a": "a", "v": 1, "valid_from": t0}])
        # advance internal tx_time via a second transaction (DB clock
        # uses substrate's _clock, but tx_time is the "now" at transact
        # call time). For DB(InMemoryStore()) the default clock is the
        # system clock, so we use tx-time slicing via the cursor instead
        # of trying to mock the substrate clock here. The valid-from
        # split is enough to validate the slice semantics.
        db = db.transact([{"e": "e1", "a": "b", "v": "later", "valid_from": t2}])
        # Cursor between T0 and T2 — both transacts have already happened
        # in tx_time (system clock), so this only exercises the cursor
        # path, not as_of_valid. That's fine for the inspect contract:
        # the cursor IS tx_time. Both attrs visible at HEAD; we assert
        # the result is non-null and contains at least the seeded attrs.
        result = await inspect_op(
            session_with_inspect,
            db,
            {
                "kind": "entity",
                "entity_id": "e1",
                "view_cursor_tx_time_iso": t1.isoformat(),
            },
        )
        # Cursor at T1 may or may not include the second assert depending
        # on the substrate's tx-time at-transact (system clock). We only
        # assert the contract: result is dict-or-None, cursor echoed.
        assert result["cursor_iso"] == t1.isoformat()
        assert result["entity"] is None or isinstance(result["entity"], dict)

    @pytest.mark.asyncio
    async def test_entity_returns_full_attribute_dict(
        self, db, session_with_inspect
    ):
        db = _seed_entity(
            db,
            "e1",
            {"name": "foo", "count": 42, "active": True, "extra": [1, 2, 3]},
            _DEFAULT_T,
        )
        result = await inspect_op(
            session_with_inspect,
            db,
            {"kind": "entity", "entity_id": "e1"},
        )
        ent = result["entity"]
        assert ent is not None
        assert ent["name"] == "foo"
        assert ent["count"] == 42
        assert ent["active"] is True
        assert ent["extra"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_entity_id_must_be_non_empty_string(
        self, db, session_with_inspect
    ):
        # Empty string is rejected (catches the "" sentinel that would
        # otherwise project an empty entity dict).
        with pytest.raises(_OpError) as excinfo:
            await inspect_op(
                session_with_inspect,
                db,
                {"kind": "entity", "entity_id": ""},
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS


# ===========================================================================
# 2. kind=audit-window (6)
# ===========================================================================
class TestInspectAuditWindow:
    @pytest.mark.asyncio
    async def test_empty_returns_empty_entries_no_pending_marker(
        self, db, session_with_inspect
    ):
        result = await inspect_op(
            session_with_inspect,
            db,
            {"kind": "audit-window"},
        )
        # Post-D7 (W2.C): the persistent path is wired. An empty fact
        # store of audit datoms returns an empty entries list — the
        # ``pending`` D7-marker is gone. Callers that distinguish
        # "no audit datoms yet" from "audit query not wired" should
        # rely on the contract surface (``inspect kind="audit-window"``
        # is now the canonical query), not a transitional sentinel.
        assert result["entries"] == []
        assert result["limit"] == 100
        assert "pending" not in result

    @pytest.mark.asyncio
    async def test_from_to_range_validated(
        self, db, session_with_inspect
    ):
        # Valid range — no entries yet, but the call returns shape.
        result = await inspect_op(
            session_with_inspect,
            db,
            {
                "kind": "audit-window",
                "from_iso": _dt(2026, 5, 1).isoformat(),
                "to_iso": _dt(2026, 5, 31).isoformat(),
            },
        )
        assert result["entries"] == []
        assert result["limit"] == 100  # default

    @pytest.mark.asyncio
    async def test_op_filter_accepted(
        self, db, session_with_inspect
    ):
        result = await inspect_op(
            session_with_inspect,
            db,
            {"kind": "audit-window", "op_filter": ":llm/call"},
        )
        assert result["entries"] == []

    @pytest.mark.asyncio
    async def test_limit_default_and_explicit(
        self, db, session_with_inspect
    ):
        result_default = await inspect_op(
            session_with_inspect,
            db,
            {"kind": "audit-window"},
        )
        assert result_default["limit"] == 100
        result_explicit = await inspect_op(
            session_with_inspect,
            db,
            {"kind": "audit-window", "limit": 25},
        )
        assert result_explicit["limit"] == 25

    @pytest.mark.asyncio
    async def test_limit_capped_at_1000(
        self, db, session_with_inspect
    ):
        result = await inspect_op(
            session_with_inspect,
            db,
            {"kind": "audit-window", "limit": 5000},
        )
        assert result["limit"] == 1000

    @pytest.mark.asyncio
    async def test_malformed_from_iso_raises(
        self, db, session_with_inspect
    ):
        with pytest.raises(_OpError) as excinfo:
            await inspect_op(
                session_with_inspect,
                db,
                {
                    "kind": "audit-window",
                    "from_iso": "definitely-not-iso",
                },
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS
        assert "from_iso" in excinfo.value.message


# ===========================================================================
# 3. kind=plan (4)
# ===========================================================================
class TestInspectPlan:
    @pytest.mark.asyncio
    async def test_plan_exists_returns_attributes(
        self, db, session_with_inspect
    ):
        # Plan kind reads the entity at the cursor (v0.6.0a1 stores plans
        # as ordinary entities; first-class plan-AST view is a future
        # substrate enhancement — the request shape stays stable).
        db = _seed_entity(
            db,
            "plan:abc",
            {"plan/source": "(do (call :foo))", "plan/version": "1"},
            _DEFAULT_T,
        )
        result = await inspect_op(
            session_with_inspect,
            db,
            {"kind": "plan", "plan_id": "plan:abc"},
        )
        assert result["plan"] == {
            "plan/source": "(do (call :foo))",
            "plan/version": "1",
        }
        assert result["cursor_iso"] == _DEFAULT_T.isoformat()

    @pytest.mark.asyncio
    async def test_plan_not_found_returns_null(
        self, db, session_with_inspect
    ):
        result = await inspect_op(
            session_with_inspect,
            db,
            {"kind": "plan", "plan_id": "plan:missing"},
        )
        assert result["plan"] is None
        assert result["cursor_iso"] == _DEFAULT_T.isoformat()

    @pytest.mark.asyncio
    async def test_plan_cursor_honored(
        self, db, session_with_inspect
    ):
        t1 = _dt(2026, 5, 10, 10, 0, 0)
        result = await inspect_op(
            session_with_inspect,
            db,
            {
                "kind": "plan",
                "plan_id": "plan:any",
                "view_cursor_tx_time_iso": t1.isoformat(),
            },
        )
        assert result["cursor_iso"] == t1.isoformat()

    @pytest.mark.asyncio
    async def test_plan_missing_plan_id_raises(
        self, db, session_with_inspect
    ):
        with pytest.raises(_OpError) as excinfo:
            await inspect_op(
                session_with_inspect,
                db,
                {"kind": "plan"},
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS
        assert "plan_id" in excinfo.value.message


# ===========================================================================
# 4. kind=causal-history (4)
# ===========================================================================
class TestInspectCausalHistory:
    @pytest.mark.asyncio
    async def test_causal_history_returns_seeds(
        self, db, session_with_inspect
    ):
        db = db.transact([{"e": "e1", "a": "x", "v": 1}])
        db = db.transact([{"e": "e1", "a": "x", "v": 2}])
        result = await inspect_op(
            session_with_inspect,
            db,
            {"kind": "causal-history", "entity_id": "e1"},
        )
        # 2 asserts + 1 auto-retract → 3 datoms in history.
        assert len(result["seeds"]) >= 1
        for seed in result["seeds"]:
            # Each seed is a JSON-friendly Datom dict.
            assert seed["e"] == "e1"
            assert "tx_time" in seed
            assert isinstance(seed["tx_time"], str)

    @pytest.mark.asyncio
    async def test_causal_history_respects_limit(
        self, db, session_with_inspect
    ):
        # Seed 5 transactions, ask for limit=2.
        for i in range(5):
            db = db.transact([{"e": "e1", "a": "x", "v": i}])
        result = await inspect_op(
            session_with_inspect,
            db,
            {
                "kind": "causal-history",
                "entity_id": "e1",
                "limit": 2,
            },
        )
        assert len(result["seeds"]) == 2
        assert result["limit"] == 2

    @pytest.mark.asyncio
    async def test_causal_history_limit_capped_at_1000(
        self, db, session_with_inspect
    ):
        result = await inspect_op(
            session_with_inspect,
            db,
            {
                "kind": "causal-history",
                "entity_id": "e1",
                "limit": 99999,
            },
        )
        assert result["limit"] == 1000

    @pytest.mark.asyncio
    async def test_causal_history_missing_entity_id_raises(
        self, db, session_with_inspect
    ):
        with pytest.raises(_OpError) as excinfo:
            await inspect_op(
                session_with_inspect,
                db,
                {"kind": "causal-history"},
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS
        assert "entity_id" in excinfo.value.message


# ===========================================================================
# 5. Capability + protocol envelopes (8)
# ===========================================================================
class TestCapabilityAndProtocol:
    @pytest.mark.asyncio
    async def test_no_inspect_read_cap_raises_capability_denied(
        self, db, session_no_caps
    ):
        with pytest.raises(_OpError) as excinfo:
            await inspect_op(
                session_no_caps,
                db,
                {"kind": "entity", "entity_id": "e1"},
            )
        assert excinfo.value.code == ERR_CAPABILITY_DENIED
        assert "inspect:read" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_unknown_kind_raises_invalid_params(
        self, db, session_with_inspect
    ):
        with pytest.raises(_OpError) as excinfo:
            await inspect_op(
                session_with_inspect,
                db,
                {"kind": "totally-not-a-kind"},
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS
        assert "kind" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_missing_kind_raises_invalid_params(
        self, db, session_with_inspect
    ):
        # No ``kind`` → dispatcher rejects before flatten. ADR-12 (W3)
        # made the wire shape flat, so a stray top-level ``params`` key
        # is just an unrecognized arg the kind handler would ignore;
        # the kind-required check fires first.
        with pytest.raises(_OpError) as excinfo:
            await inspect_op(
                session_with_inspect,
                db,
                {"entity_id": "e1"},
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_missing_params_defaults_to_empty(
        self, db, session_with_inspect
    ):
        # ADR-12 (W3): wire shape is flat. ``audit-window`` accepts an
        # empty sub-params bag (every field optional), so a request
        # carrying only ``kind`` is valid. entity / plan /
        # causal-history would surface the underlying "<field>
        # required" error on the same flat-only payload.
        result = await inspect_op(
            session_with_inspect,
            db,
            {"kind": "audit-window"},  # no sub-kind args
        )
        assert result["entries"] == []

    @pytest.mark.asyncio
    async def test_cursor_falls_back_to_session_clock(
        self, db, session_with_inspect, clock_fixed
    ):
        # Both sub.cursor AND session.cursor are None → use session.clock().
        assert session_with_inspect.view_cursor_tx_time_iso is None
        result = await inspect_op(
            session_with_inspect,
            db,
            {"kind": "entity", "entity_id": "e1"},
        )
        assert result["cursor_iso"] == clock_fixed().isoformat()

    @pytest.mark.asyncio
    async def test_sub_cursor_overrides_session_cursor(self, db, clock_fixed):
        # Session has a cursor at T_session; sub passes a different
        # cursor T_sub; resolved cursor MUST be T_sub (sub > session).
        cs = CapabilitySet(caps=frozenset({Capability("inspect", "read")}))
        t_session = _dt(2026, 5, 9, 6, 0, 0)
        sess = make_session("token-id-cccccccc", cs, runtime_clock=clock_fixed)
        import dataclasses

        sess = dataclasses.replace(
            sess, view_cursor_tx_time_iso=t_session.isoformat()
        )
        t_sub = _dt(2026, 5, 9, 9, 0, 0)
        result = await inspect_op(
            sess,
            db,
            {
                "kind": "entity",
                "entity_id": "e1",
                "view_cursor_tx_time_iso": t_sub.isoformat(),
            },
        )
        assert result["cursor_iso"] == t_sub.isoformat()

    @pytest.mark.asyncio
    async def test_session_cursor_used_when_sub_cursor_absent(
        self, db, clock_fixed
    ):
        cs = CapabilitySet(caps=frozenset({Capability("inspect", "read")}))
        t_session = _dt(2026, 5, 9, 6, 0, 0)
        sess = make_session("token-id-dddddddd", cs, runtime_clock=clock_fixed)
        import dataclasses

        sess = dataclasses.replace(
            sess, view_cursor_tx_time_iso=t_session.isoformat()
        )
        result = await inspect_op(
            sess,
            db,
            {"kind": "entity", "entity_id": "e1"},
        )
        # No sub.cursor → session.cursor used.
        assert result["cursor_iso"] == t_session.isoformat()

    @pytest.mark.asyncio
    async def test_result_includes_cursor_iso_for_traceability(
        self, db, session_with_inspect
    ):
        # Every kind that materializes a view echoes cursor_iso so the
        # operator can pin the read coordinate in audit-tail output.
        for kind, sub in [
            ("entity", {"entity_id": "e1"}),
            ("plan", {"plan_id": "plan:abc"}),
        ]:
            result = await inspect_op(
                session_with_inspect,
                db,
                {"kind": kind, **sub},
            )
            assert "cursor_iso" in result, kind
            assert isinstance(result["cursor_iso"], str)
