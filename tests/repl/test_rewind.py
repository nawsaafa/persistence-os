"""Tests for ``persistence.repl._ops.rewind_op`` (D4).

20 tests across 6 groups:

- Set tx_time_iso → cursor updated                     (4)
- Validation                                           (4)
- Capability                                           (3)
- Session mutation                                     (5)
- Idempotent                                           (2)
- Cross-session isolation                              (2)

See design doc ``docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md``
sections 5.0 (time vocabulary), 5.3 (rewind contract), ADR-9 (error
codes), and 10 (D4).

Test pattern: in-memory ``DB`` + a fixed clock, drive ``rewind_op``
directly with a ``WSServer`` instance to exercise the
``server._active_sessions`` registry mutation. The dispatcher's re-read
contract is covered in ``test_ws.py`` D2 — here we pin the registry-
write half of the contract.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from persistence.fact import DB, InMemoryStore
from persistence.repl import (
    Capability,
    CapabilitySet,
    ERR_CAPABILITY_DENIED,
    ERR_INVALID_PARAMS,
    WSServer,
    make_session,
)
from persistence.repl._ops import inspect_op, rewind_op
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


_DEFAULT_T = _dt(2099, 1, 1, 12, 0, 0)


@pytest.fixture
def db() -> DB:
    return DB(InMemoryStore())


@pytest.fixture
def clock_fixed():
    return _fixed_clock(_DEFAULT_T)


@pytest.fixture
def server(db, clock_fixed):
    return WSServer(db, runtime_clock=clock_fixed)


def _make_rewind_session(
    server: WSServer,
    clock_fixed,
    *,
    token_id: str = "token-id-rewind1",
    extra_caps: frozenset[Capability] = frozenset(),
) -> "object":
    """Build + register a session with rewind:any capability."""
    caps = frozenset({Capability("rewind", "any")}) | extra_caps
    cs = CapabilitySet(caps=caps)
    session = make_session(token_id, cs, runtime_clock=clock_fixed)
    server._active_sessions[session.session_id] = session
    return session


# ===========================================================================
# 1. Set tx_time_iso → cursor updated (4)
# ===========================================================================
class TestRewindCursorUpdate:
    @pytest.mark.asyncio
    async def test_set_valid_tx_time_iso(self, server, clock_fixed):
        session = _make_rewind_session(server, clock_fixed)
        target = _dt(2025, 6, 1, 10, 0, 0).isoformat()
        result = await rewind_op(
            session,
            server.db,
            {"tx_time_iso": target},
            server=server,
        )
        assert result["view_cursor_tx_time_iso"] == target
        assert result["view_cursor_vt_iso"] is None

    @pytest.mark.asyncio
    async def test_set_null_clears_cursor(self, server, clock_fixed):
        session = _make_rewind_session(server, clock_fixed)
        # Pre-set a cursor so we can confirm clearing.
        session = dataclasses.replace(
            session, view_cursor_tx_time_iso=_dt(2025, 1, 1).isoformat()
        )
        server._active_sessions[session.session_id] = session
        result = await rewind_op(
            session,
            server.db,
            {"tx_time_iso": None, "vt_iso": None},
            server=server,
        )
        assert result["view_cursor_tx_time_iso"] is None
        assert result["view_cursor_vt_iso"] is None
        # Registry entry reflects the cleared cursor.
        new_session = server._active_sessions[session.session_id]
        assert new_session.view_cursor_tx_time_iso is None
        assert new_session.view_cursor_vt_iso is None

    @pytest.mark.asyncio
    async def test_set_both_tx_and_vt(self, server, clock_fixed):
        session = _make_rewind_session(server, clock_fixed)
        tx_iso = _dt(2025, 6, 1).isoformat()
        vt_iso = _dt(2025, 5, 1).isoformat()
        result = await rewind_op(
            session,
            server.db,
            {"tx_time_iso": tx_iso, "vt_iso": vt_iso},
            server=server,
        )
        assert result["view_cursor_tx_time_iso"] == tx_iso
        assert result["view_cursor_vt_iso"] == vt_iso

    @pytest.mark.asyncio
    async def test_set_vt_only(self, server, clock_fixed):
        session = _make_rewind_session(server, clock_fixed)
        vt_iso = _dt(2025, 5, 1).isoformat()
        result = await rewind_op(
            session,
            server.db,
            {"vt_iso": vt_iso},
            server=server,
        )
        assert result["view_cursor_tx_time_iso"] is None
        assert result["view_cursor_vt_iso"] == vt_iso


# ===========================================================================
# 2. Validation (4)
# ===========================================================================
class TestRewindValidation:
    @pytest.mark.asyncio
    async def test_malformed_tx_time_iso_raises(self, server, clock_fixed):
        session = _make_rewind_session(server, clock_fixed)
        with pytest.raises(_OpError) as excinfo:
            await rewind_op(
                session,
                server.db,
                {"tx_time_iso": "not-an-iso-string"},
                server=server,
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS
        assert "tx_time_iso" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_malformed_vt_iso_raises(self, server, clock_fixed):
        session = _make_rewind_session(server, clock_fixed)
        with pytest.raises(_OpError) as excinfo:
            await rewind_op(
                session,
                server.db,
                {"vt_iso": "definitely-not-a-date"},
                server=server,
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS
        assert "vt_iso" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_non_string_tx_time_iso_raises(self, server, clock_fixed):
        session = _make_rewind_session(server, clock_fixed)
        with pytest.raises(_OpError) as excinfo:
            await rewind_op(
                session,
                server.db,
                {"tx_time_iso": 12345},  # int, not str
                server=server,
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_non_string_vt_iso_raises(self, server, clock_fixed):
        session = _make_rewind_session(server, clock_fixed)
        with pytest.raises(_OpError) as excinfo:
            await rewind_op(
                session,
                server.db,
                {"vt_iso": ["not", "a", "string"]},
                server=server,
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS


# ===========================================================================
# 3. Capability (3)
# ===========================================================================
class TestRewindCapability:
    @pytest.mark.asyncio
    async def test_no_rewind_any_cap_raises_capability_denied(
        self, server, clock_fixed
    ):
        # No caps at all.
        cs = CapabilitySet(caps=frozenset())
        session = make_session(
            "token-id-norewind", cs, runtime_clock=clock_fixed
        )
        with pytest.raises(_OpError) as excinfo:
            await rewind_op(
                session,
                server.db,
                {"tx_time_iso": _dt(2025, 1, 1).isoformat()},
                server=server,
            )
        assert excinfo.value.code == ERR_CAPABILITY_DENIED
        assert "rewind:any" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_rewind_branch_only_is_not_sufficient(
        self, server, clock_fixed
    ):
        # rewind:branch-only is for "rewind only inside a branch" (a future
        # policy); it does NOT grant general rewind:any.
        cs = CapabilitySet(
            caps=frozenset({Capability("rewind", "branch-only")})
        )
        session = make_session(
            "token-id-branchonly", cs, runtime_clock=clock_fixed
        )
        with pytest.raises(_OpError) as excinfo:
            await rewind_op(
                session,
                server.db,
                {"tx_time_iso": _dt(2025, 1, 1).isoformat()},
                server=server,
            )
        assert excinfo.value.code == ERR_CAPABILITY_DENIED

    @pytest.mark.asyncio
    async def test_rewind_any_allows_arbitrary_tx_time(
        self, server, clock_fixed
    ):
        session = _make_rewind_session(server, clock_fixed)
        # A wide range of valid tx_times all succeed.
        for iso in [
            _dt(1900, 1, 1).isoformat(),
            _dt(2026, 5, 9).isoformat(),
            _dt(3000, 12, 31).isoformat(),
        ]:
            result = await rewind_op(
                session,
                server.db,
                {"tx_time_iso": iso},
                server=server,
            )
            assert result["view_cursor_tx_time_iso"] == iso


# ===========================================================================
# 4. Session mutation (5)
# ===========================================================================
class TestRewindSessionMutation:
    @pytest.mark.asyncio
    async def test_active_sessions_updated_with_new_cursor(
        self, server, clock_fixed
    ):
        session = _make_rewind_session(server, clock_fixed)
        target = _dt(2025, 6, 1).isoformat()
        await rewind_op(
            session,
            server.db,
            {"tx_time_iso": target},
            server=server,
        )
        new_session = server._active_sessions[session.session_id]
        assert new_session.view_cursor_tx_time_iso == target

    @pytest.mark.asyncio
    async def test_subsequent_inspect_uses_new_cursor(
        self, server, clock_fixed
    ):
        # rewind sets cursor; an inspect that defers to session.cursor
        # MUST observe the new value.
        session = _make_rewind_session(
            server,
            clock_fixed,
            extra_caps=frozenset({Capability("inspect", "read")}),
        )
        target = _dt(2025, 6, 1).isoformat()
        await rewind_op(
            session,
            server.db,
            {"tx_time_iso": target},
            server=server,
        )
        new_session = server._active_sessions[session.session_id]
        result = await inspect_op(
            new_session,
            server.db,
            {"kind": "entity", "entity_id": "e1"},
        )
        assert result["cursor_iso"] == target

    @pytest.mark.asyncio
    async def test_rewind_to_null_then_inspect_uses_session_clock(
        self, server, clock_fixed
    ):
        session = _make_rewind_session(
            server,
            clock_fixed,
            extra_caps=frozenset({Capability("inspect", "read")}),
        )
        # Rewind to a coordinate, then back to null.
        await rewind_op(
            session,
            server.db,
            {"tx_time_iso": _dt(2025, 1, 1).isoformat()},
            server=server,
        )
        session = server._active_sessions[session.session_id]
        await rewind_op(
            session,
            server.db,
            {"tx_time_iso": None, "vt_iso": None},
            server=server,
        )
        session = server._active_sessions[session.session_id]
        result = await inspect_op(
            session,
            server.db,
            {"kind": "entity", "entity_id": "e1"},
        )
        assert result["cursor_iso"] == clock_fixed().isoformat()

    @pytest.mark.asyncio
    async def test_rewind_preserves_session_id(self, server, clock_fixed):
        session = _make_rewind_session(server, clock_fixed)
        sid_before = session.session_id
        await rewind_op(
            session,
            server.db,
            {"tx_time_iso": _dt(2025, 1, 1).isoformat()},
            server=server,
        )
        new_session = server._active_sessions[sid_before]
        assert new_session.session_id == sid_before

    @pytest.mark.asyncio
    async def test_rewind_preserves_other_session_fields(
        self, server, clock_fixed
    ):
        session = _make_rewind_session(server, clock_fixed)
        await rewind_op(
            session,
            server.db,
            {"tx_time_iso": _dt(2025, 1, 1).isoformat()},
            server=server,
        )
        new_session = server._active_sessions[session.session_id]
        assert new_session.cap_set == session.cap_set
        assert new_session.token_id == session.token_id
        assert new_session.auth_clock_iso == session.auth_clock_iso
        assert new_session.parent_chain_depth == session.parent_chain_depth


# ===========================================================================
# 5. Idempotent (2)
# ===========================================================================
class TestRewindIdempotent:
    @pytest.mark.asyncio
    async def test_rewind_to_same_cursor_twice_returns_same_result(
        self, server, clock_fixed
    ):
        session = _make_rewind_session(server, clock_fixed)
        target = _dt(2025, 6, 1).isoformat()
        r1 = await rewind_op(
            session,
            server.db,
            {"tx_time_iso": target},
            server=server,
        )
        session = server._active_sessions[session.session_id]
        r2 = await rewind_op(
            session,
            server.db,
            {"tx_time_iso": target},
            server=server,
        )
        assert r1 == r2

    @pytest.mark.asyncio
    async def test_rewind_double_call_keeps_session_id_stable(
        self, server, clock_fixed
    ):
        session = _make_rewind_session(server, clock_fixed)
        sid = session.session_id
        await rewind_op(
            session,
            server.db,
            {"tx_time_iso": _dt(2025, 6, 1).isoformat()},
            server=server,
        )
        s2 = server._active_sessions[sid]
        await rewind_op(
            s2,
            server.db,
            {"tx_time_iso": _dt(2025, 6, 1).isoformat()},
            server=server,
        )
        s3 = server._active_sessions[sid]
        assert s3.session_id == sid


# ===========================================================================
# 6. Cross-session isolation (2)
# ===========================================================================
class TestRewindCrossSessionIsolation:
    @pytest.mark.asyncio
    async def test_rewind_in_a_does_not_affect_b(self, server, clock_fixed):
        session_a = _make_rewind_session(
            server, clock_fixed, token_id="token-id-aaaa1234"
        )
        # session_b uses a different clock so its session_id differs.
        clock_b = _fixed_clock(_dt(2099, 1, 1, 13, 0, 0))
        session_b = _make_rewind_session(
            server, clock_b, token_id="token-id-bbbb5678"
        )
        assert session_a.session_id != session_b.session_id
        target = _dt(2025, 1, 1).isoformat()
        await rewind_op(
            session_a,
            server.db,
            {"tx_time_iso": target},
            server=server,
        )
        # session_b's record is untouched.
        b_after = server._active_sessions[session_b.session_id]
        assert b_after.view_cursor_tx_time_iso is None

    @pytest.mark.asyncio
    async def test_two_sessions_have_independent_cursors(
        self, server, clock_fixed
    ):
        session_a = _make_rewind_session(
            server, clock_fixed, token_id="token-id-aaaa1234"
        )
        clock_b = _fixed_clock(_dt(2099, 1, 1, 13, 0, 0))
        session_b = _make_rewind_session(
            server, clock_b, token_id="token-id-bbbb5678"
        )
        target_a = _dt(2025, 1, 1).isoformat()
        target_b = _dt(2024, 6, 1).isoformat()
        await rewind_op(
            session_a, server.db, {"tx_time_iso": target_a}, server=server
        )
        await rewind_op(
            session_b, server.db, {"tx_time_iso": target_b}, server=server
        )
        a_after = server._active_sessions[session_a.session_id]
        b_after = server._active_sessions[session_b.session_id]
        assert a_after.view_cursor_tx_time_iso == target_a
        assert b_after.view_cursor_tx_time_iso == target_b
