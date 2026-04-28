"""Tests for ``persistence.repl._ops.branch_op`` (D5).

30 tests across 8 groups:

- Happy path                                           (5)
- Capability                                           (3)
- Branch depth                                         (5)
- Expired-parent reject                                (4)
- Validation                                           (3)
- Session mutation                                     (5)
- Determinism                                          (3)
- Cross-session isolation                              (2)

See design doc ``docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md``
sections 5.0 (time vocabulary), 5.4 (branch contract), 6.4
(expired-parent reject), ADR-9 (error codes), and 10 (D5).

Test pattern: in-memory ``DB`` + a fixed clock, drive ``branch_op``
directly with a ``WSServer`` instance to exercise the
``server._active_sessions`` registry mutation. Branch keeps the
WS-level session_id stable; the operator's branch handle is the
deterministic ``branch_id`` returned in the response.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from persistence.fact import DB, InMemoryStore
from persistence.repl import (
    Capability,
    CapabilitySet,
    ERR_BRANCH_DEPTH_EXCEEDED,
    ERR_CAPABILITY_DENIED,
    ERR_INVALID_PARAMS,
    ERR_SESSION_EXPIRED,
    WSServer,
    make_session,
)
from persistence.repl._ops import (
    MAX_BRANCH_DEPTH,
    _derive_branch_id,
    branch_op,
    inspect_op,
)
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
def server(db, clock_fixed):
    return WSServer(db, runtime_clock=clock_fixed)


def _make_branch_session(
    server: WSServer,
    clock,
    *,
    token_id: str = "token-id-branch01",
    extra_caps: frozenset[Capability] = frozenset(),
    expires_at: datetime | None = None,
):
    """Build + register a session with branch:fork capability."""
    caps = frozenset({Capability("branch", "fork")}) | extra_caps
    cs = CapabilitySet(caps=caps, expires_at=expires_at)
    session = make_session(token_id, cs, runtime_clock=clock)
    server._active_sessions[session.session_id] = session
    return session


# ===========================================================================
# 1. Happy path (5)
# ===========================================================================
class TestBranchHappyPath:
    @pytest.mark.asyncio
    async def test_branch_from_current_clock(self, server, clock_fixed):
        session = _make_branch_session(server, clock_fixed)
        result = await branch_op(
            session,
            server.db,
            {},
            server=server,
        )
        # No tx_time + no session.cursor → falls back to session.clock().
        assert result["tx_time_iso"] == _DEFAULT_T.isoformat()
        assert result["parent_chain_depth"] == 1
        assert result["branch_id"].startswith("branch:")
        assert len(result["branch_id"]) == len("branch:") + 16

    @pytest.mark.asyncio
    async def test_branch_from_past_cursor_in_params(
        self, server, clock_fixed
    ):
        session = _make_branch_session(server, clock_fixed)
        target = _dt(2025, 6, 1).isoformat()
        result = await branch_op(
            session,
            server.db,
            {"tx_time_iso": target},
            server=server,
        )
        assert result["tx_time_iso"] == target

    @pytest.mark.asyncio
    async def test_branch_from_session_cursor_when_params_omitted(
        self, server, clock_fixed
    ):
        session = _make_branch_session(server, clock_fixed)
        # Pre-set the session cursor.
        session_cursor = _dt(2025, 6, 1).isoformat()
        session = dataclasses.replace(
            session, view_cursor_tx_time_iso=session_cursor
        )
        server._active_sessions[session.session_id] = session
        result = await branch_op(
            session,
            server.db,
            {},  # no tx_time_iso
            server=server,
        )
        # Should pick up session.view_cursor_tx_time_iso.
        assert result["tx_time_iso"] == session_cursor

    @pytest.mark.asyncio
    async def test_parent_chain_depth_increments(self, server, clock_fixed):
        session = _make_branch_session(server, clock_fixed)
        assert session.parent_chain_depth == 0
        result = await branch_op(
            session, server.db, {}, server=server
        )
        assert result["parent_chain_depth"] == 1
        # Next branch increments to 2.
        s2 = server._active_sessions[session.session_id]
        result2 = await branch_op(
            s2, server.db, {}, server=server
        )
        assert result2["parent_chain_depth"] == 2

    @pytest.mark.asyncio
    async def test_label_echoed_in_response(self, server, clock_fixed):
        session = _make_branch_session(server, clock_fixed)
        result = await branch_op(
            session,
            server.db,
            {"label": "what-if-rate-cut"},
            server=server,
        )
        assert result["label"] == "what-if-rate-cut"


# ===========================================================================
# 2. Capability (3)
# ===========================================================================
class TestBranchCapability:
    @pytest.mark.asyncio
    async def test_no_branch_fork_cap_raises_capability_denied(
        self, server, clock_fixed
    ):
        cs = CapabilitySet(caps=frozenset())
        session = make_session(
            "token-id-nobranch", cs, runtime_clock=clock_fixed
        )
        with pytest.raises(_OpError) as excinfo:
            await branch_op(
                session, server.db, {}, server=server
            )
        assert excinfo.value.code == ERR_CAPABILITY_DENIED
        assert "branch:fork" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_branch_fork_from_cursor_only_is_not_sufficient(
        self, server, clock_fixed
    ):
        # ``fork-from-cursor`` qualifier is reserved for a future
        # narrower policy (branch only AT the current cursor, never an
        # explicit past tx_time). It does NOT grant the general
        # ``branch:fork`` capability.
        cs = CapabilitySet(
            caps=frozenset({Capability("branch", "fork-from-cursor")})
        )
        session = make_session(
            "token-id-fromcursor", cs, runtime_clock=clock_fixed
        )
        with pytest.raises(_OpError) as excinfo:
            await branch_op(
                session, server.db, {}, server=server
            )
        assert excinfo.value.code == ERR_CAPABILITY_DENIED

    @pytest.mark.asyncio
    async def test_branch_fork_cap_allows_fork(self, server, clock_fixed):
        session = _make_branch_session(server, clock_fixed)
        # No exception.
        result = await branch_op(
            session, server.db, {}, server=server
        )
        assert "branch_id" in result


# ===========================================================================
# 3. Branch depth (5)
# ===========================================================================
class TestBranchDepth:
    @pytest.mark.asyncio
    async def test_depth_0_to_1(self, server, clock_fixed):
        session = _make_branch_session(server, clock_fixed)
        assert session.parent_chain_depth == 0
        result = await branch_op(session, server.db, {}, server=server)
        assert result["parent_chain_depth"] == 1

    @pytest.mark.asyncio
    async def test_depth_15_to_16(self, server, clock_fixed):
        session = _make_branch_session(server, clock_fixed)
        # Force depth=15 by direct dataclasses.replace (bypassing the loop
        # to avoid 15× branch_op calls).
        session = dataclasses.replace(session, parent_chain_depth=15)
        server._active_sessions[session.session_id] = session
        result = await branch_op(session, server.db, {}, server=server)
        assert result["parent_chain_depth"] == 16

    @pytest.mark.asyncio
    async def test_depth_16_rejects(self, server, clock_fixed):
        session = _make_branch_session(server, clock_fixed)
        session = dataclasses.replace(session, parent_chain_depth=16)
        server._active_sessions[session.session_id] = session
        with pytest.raises(_OpError) as excinfo:
            await branch_op(session, server.db, {}, server=server)
        assert excinfo.value.code == ERR_BRANCH_DEPTH_EXCEEDED
        assert "16" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_depth_above_16_also_rejects(self, server, clock_fixed):
        session = _make_branch_session(server, clock_fixed)
        session = dataclasses.replace(session, parent_chain_depth=99)
        server._active_sessions[session.session_id] = session
        with pytest.raises(_OpError) as excinfo:
            await branch_op(session, server.db, {}, server=server)
        assert excinfo.value.code == ERR_BRANCH_DEPTH_EXCEEDED

    @pytest.mark.asyncio
    async def test_depth_count_survives_multiple_branches(
        self, server, clock_fixed
    ):
        session = _make_branch_session(server, clock_fixed)
        # Branch 5 times in sequence.
        for i in range(1, 6):
            result = await branch_op(
                session, server.db, {}, server=server
            )
            assert result["parent_chain_depth"] == i
            session = server._active_sessions[session.session_id]


# ===========================================================================
# 4. Expired-parent reject (4)
# ===========================================================================
class TestBranchExpiredParent:
    @pytest.mark.asyncio
    async def test_expired_cap_set_rejects_with_session_expired(
        self, server
    ):
        # Session clock at T_now > expires_at → cap_set.is_expired(now) True.
        t_now = _dt(2027, 1, 1)
        t_exp = _dt(2026, 6, 1)
        clock = _fixed_clock(t_now)
        session = _make_branch_session(
            server, clock, expires_at=t_exp, token_id="token-id-expired1"
        )
        with pytest.raises(_OpError) as excinfo:
            await branch_op(session, server.db, {}, server=server)
        assert excinfo.value.code == ERR_SESSION_EXPIRED
        assert "expired" in excinfo.value.message.lower()

    @pytest.mark.asyncio
    async def test_expired_at_exact_boundary_rejects(self, server):
        # Boundary: now == expires_at is already expired (inclusive).
        t = _dt(2026, 6, 1)
        clock = _fixed_clock(t)
        session = _make_branch_session(
            server, clock, expires_at=t, token_id="token-id-boundary1"
        )
        with pytest.raises(_OpError) as excinfo:
            await branch_op(session, server.db, {}, server=server)
        assert excinfo.value.code == ERR_SESSION_EXPIRED

    @pytest.mark.asyncio
    async def test_non_expired_cap_set_succeeds(self, server, clock_fixed):
        session = _make_branch_session(
            server,
            clock_fixed,
            expires_at=_dt(3000, 1, 1),
            token_id="token-id-future1",
        )
        result = await branch_op(session, server.db, {}, server=server)
        assert "branch_id" in result

    @pytest.mark.asyncio
    async def test_expiry_checked_before_depth_limit(self, server):
        # An expired session with parent_chain_depth=99 must surface
        # ERR_SESSION_EXPIRED, NOT ERR_BRANCH_DEPTH_EXCEEDED — expiry
        # gates earlier than depth.
        t_now = _dt(2027, 1, 1)
        t_exp = _dt(2026, 6, 1)
        clock = _fixed_clock(t_now)
        session = _make_branch_session(
            server,
            clock,
            expires_at=t_exp,
            token_id="token-id-expdepth1",
        )
        session = dataclasses.replace(session, parent_chain_depth=99)
        server._active_sessions[session.session_id] = session
        with pytest.raises(_OpError) as excinfo:
            await branch_op(session, server.db, {}, server=server)
        assert excinfo.value.code == ERR_SESSION_EXPIRED


# ===========================================================================
# 5. Validation (3)
# ===========================================================================
class TestBranchValidation:
    @pytest.mark.asyncio
    async def test_malformed_tx_time_iso_raises(self, server, clock_fixed):
        session = _make_branch_session(server, clock_fixed)
        with pytest.raises(_OpError) as excinfo:
            await branch_op(
                session,
                server.db,
                {"tx_time_iso": "not-an-iso"},
                server=server,
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS
        assert "tx_time_iso" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_non_string_label_raises(self, server, clock_fixed):
        session = _make_branch_session(server, clock_fixed)
        with pytest.raises(_OpError) as excinfo:
            await branch_op(
                session,
                server.db,
                {"label": 12345},
                server=server,
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS
        assert "label" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_missing_label_defaults_to_empty_string(
        self, server, clock_fixed
    ):
        session = _make_branch_session(server, clock_fixed)
        result = await branch_op(
            session, server.db, {}, server=server
        )
        assert result["label"] == ""


# ===========================================================================
# 6. Session mutation (5)
# ===========================================================================
class TestBranchSessionMutation:
    @pytest.mark.asyncio
    async def test_active_sessions_updated_with_new_cursor_and_depth(
        self, server, clock_fixed
    ):
        session = _make_branch_session(server, clock_fixed)
        target = _dt(2025, 6, 1).isoformat()
        await branch_op(
            session,
            server.db,
            {"tx_time_iso": target},
            server=server,
        )
        new_session = server._active_sessions[session.session_id]
        assert new_session.view_cursor_tx_time_iso == target
        assert new_session.parent_chain_depth == 1

    @pytest.mark.asyncio
    async def test_subsequent_inspect_uses_branch_cursor(
        self, server, clock_fixed
    ):
        session = _make_branch_session(
            server,
            clock_fixed,
            extra_caps=frozenset({Capability("inspect", "read")}),
        )
        target = _dt(2025, 6, 1).isoformat()
        await branch_op(
            session,
            server.db,
            {"tx_time_iso": target},
            server=server,
        )
        new_session = server._active_sessions[session.session_id]
        result = await inspect_op(
            new_session,
            server.db,
            {"kind": "entity", "params": {"entity_id": "e1"}},
        )
        assert result["cursor_iso"] == target

    @pytest.mark.asyncio
    async def test_branch_preserves_session_id(self, server, clock_fixed):
        session = _make_branch_session(server, clock_fixed)
        sid_before = session.session_id
        await branch_op(session, server.db, {}, server=server)
        new_session = server._active_sessions[sid_before]
        assert new_session.session_id == sid_before

    @pytest.mark.asyncio
    async def test_branch_preserves_cap_set_token_and_auth_clock(
        self, server, clock_fixed
    ):
        session = _make_branch_session(server, clock_fixed)
        await branch_op(session, server.db, {}, server=server)
        new_session = server._active_sessions[session.session_id]
        assert new_session.cap_set == session.cap_set
        assert new_session.token_id == session.token_id
        assert new_session.auth_clock_iso == session.auth_clock_iso

    @pytest.mark.asyncio
    async def test_params_cursor_overrides_session_cursor(
        self, server, clock_fixed
    ):
        session = _make_branch_session(server, clock_fixed)
        # Set session cursor to T_session.
        t_session = _dt(2025, 1, 1).isoformat()
        session = dataclasses.replace(
            session, view_cursor_tx_time_iso=t_session
        )
        server._active_sessions[session.session_id] = session
        # Pass a different cursor in params.
        t_params = _dt(2024, 6, 1).isoformat()
        result = await branch_op(
            session,
            server.db,
            {"tx_time_iso": t_params},
            server=server,
        )
        assert result["tx_time_iso"] == t_params


# ===========================================================================
# 7. Determinism (3)
# ===========================================================================
class TestBranchDeterminism:
    @pytest.mark.asyncio
    async def test_same_session_id_and_tx_iso_yields_same_branch_id(
        self, server, clock_fixed
    ):
        session = _make_branch_session(server, clock_fixed)
        target = _dt(2025, 6, 1).isoformat()
        # Direct helper call — no state mutation involved.
        bid1 = _derive_branch_id(session.session_id, target)
        bid2 = _derive_branch_id(session.session_id, target)
        assert bid1 == bid2

    @pytest.mark.asyncio
    async def test_different_sessions_yield_different_branch_ids(
        self, server, clock_fixed
    ):
        session_a = _make_branch_session(
            server, clock_fixed, token_id="token-id-aaaaaaaa1"
        )
        clock_b = _fixed_clock(_dt(2026, 5, 9, 13, 0, 0))
        session_b = _make_branch_session(
            server, clock_b, token_id="token-id-bbbbbbbb1"
        )
        target = _dt(2025, 6, 1).isoformat()
        bid_a = _derive_branch_id(session_a.session_id, target)
        bid_b = _derive_branch_id(session_b.session_id, target)
        assert bid_a != bid_b

    @pytest.mark.asyncio
    async def test_different_tx_iso_yields_different_branch_ids(
        self, server, clock_fixed
    ):
        session = _make_branch_session(server, clock_fixed)
        bid_1 = _derive_branch_id(session.session_id, _dt(2025, 6, 1).isoformat())
        bid_2 = _derive_branch_id(session.session_id, _dt(2024, 6, 1).isoformat())
        assert bid_1 != bid_2


# ===========================================================================
# 8. Cross-session isolation (2)
# ===========================================================================
class TestBranchCrossSessionIsolation:
    @pytest.mark.asyncio
    async def test_branch_in_a_does_not_affect_b(self, server, clock_fixed):
        session_a = _make_branch_session(
            server, clock_fixed, token_id="token-id-aaaaaaaa2"
        )
        clock_b = _fixed_clock(_dt(2026, 5, 9, 13, 0, 0))
        session_b = _make_branch_session(
            server, clock_b, token_id="token-id-bbbbbbbb2"
        )
        await branch_op(
            session_a,
            server.db,
            {"tx_time_iso": _dt(2025, 6, 1).isoformat()},
            server=server,
        )
        b_after = server._active_sessions[session_b.session_id]
        # session_b's record is untouched.
        assert b_after.parent_chain_depth == 0
        assert b_after.view_cursor_tx_time_iso is None

    @pytest.mark.asyncio
    async def test_two_sessions_branch_independently(
        self, server, clock_fixed
    ):
        session_a = _make_branch_session(
            server, clock_fixed, token_id="token-id-aaaaaaaa3"
        )
        clock_b = _fixed_clock(_dt(2026, 5, 9, 13, 0, 0))
        session_b = _make_branch_session(
            server, clock_b, token_id="token-id-bbbbbbbb3"
        )
        # Re-fetch session_a from the registry between calls so each
        # branch_op sees the depth-incremented predecessor (the WS
        # dispatcher does this between messages; we mirror it here).
        await branch_op(session_a, server.db, {}, server=server)
        session_a = server._active_sessions[session_a.session_id]
        await branch_op(session_a, server.db, {}, server=server)
        await branch_op(session_b, server.db, {}, server=server)
        a_after = server._active_sessions[session_a.session_id]
        b_after = server._active_sessions[session_b.session_id]
        assert a_after.parent_chain_depth == 2
        assert b_after.parent_chain_depth == 1
