"""W3 / ADR-11 — ``repl/inspect kind=audit-window`` polls do NOT self-emit.

Browser polls audit-tail at ~1 Hz to backfill the audit-tail pane. If
every poll wrote a new ``:repl/op`` entry, the pane would saturate at
1 Hz with self-references and the underlying Merkle chain would
accumulate one entry per second forever — a Module-2 audit-log DOS.

This test drives the WS dispatcher (``WSServer._handle_message``) end
to end so the gate is verified at the canonical emission site, NOT at
the per-op-handler unit-test boundary.

Other inspect kinds (entity / plan / causal-history) MUST still audit;
the gate is targeted exactly at ``audit-window``.

See design doc ``docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md``
section ADR-11 (W3).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from persistence.fact import DB, InMemoryStore
from persistence.repl import (
    Capability,
    WSServer,
    mint_token,
    store_token,
)


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
def server(db: DB, clock_fixed) -> WSServer:
    return WSServer(db, runtime_clock=clock_fixed)


@pytest_asyncio.fixture
async def client(server: WSServer):
    test_server = TestServer(server.app)
    async with TestClient(test_server) as c:
        yield c


def _stored_token_str(db: DB, clock_fixed) -> str:
    """Mint + persist a token with full inspect+edit caps."""
    t = mint_token(
        caps=frozenset(
            {Capability("inspect", "read"), Capability("edit", "write")}
        )
    )
    store_token(db, t, runtime_clock=clock_fixed)
    return t.token_str


async def _auth(ws, token_str: str, *, req_id: int = 1) -> dict:
    await ws.send_json(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "repl/auth",
            "params": {"token": token_str},
        }
    )
    return await ws.receive_json()


# ===========================================================================
# Tests
# ===========================================================================
class TestAuditWindowSelfLoop:
    @pytest.mark.asyncio
    async def test_ten_consecutive_audit_window_polls_do_not_self_emit(
        self, client, server, db, clock_fixed
    ):
        """Ten back-to-back ``audit-window`` polls leave the in-memory ring
        with zero new entries. The auth handshake DOES emit, so we
        snapshot the ring AFTER auth and assert the delta is zero.
        """
        token_str = _stored_token_str(db, clock_fixed)
        async with client.ws_connect("/ws") as ws:
            await _auth(ws, token_str)

            # Snapshot ring AFTER auth — auth emits one :repl/op entry.
            ring_after_auth = list(server._audit_entries)
            ring_size_after_auth = len(ring_after_auth)
            assert ring_size_after_auth >= 1, (
                "auth must emit one entry — sanity check on the test harness"
            )

            # Drive ten audit-window polls. Each goes through the full
            # WS dispatcher (parse → envelope → dispatch → audit gate).
            for i in range(10):
                await ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": 100 + i,
                        "method": "repl/inspect",
                        "params": {"kind": "audit-window"},
                    }
                )
                resp = await ws.receive_json()
                assert "result" in resp, resp
                # Each call returns the SAME entries list — the auth
                # entry plus any prior emits, but no self-emits from
                # the polls themselves.
                assert isinstance(resp["result"]["entries"], list)

            # Ring is unchanged from post-auth: zero new entries from
            # the ten polls. (And the entries the polls *return* are
            # exactly the post-auth snapshot, by content.)
            assert len(server._audit_entries) == ring_size_after_auth, (
                f"audit-window polls self-emitted: ring grew from "
                f"{ring_size_after_auth} to {len(server._audit_entries)}"
            )
            # Identity check: the ring contents are byte-identical to
            # the post-auth snapshot.
            assert [e.id for e in server._audit_entries] == [
                e.id for e in ring_after_auth
            ]

    @pytest.mark.asyncio
    async def test_inspect_kind_entity_still_audits(
        self, client, server, db, clock_fixed
    ):
        """The gate is targeted at ``audit-window`` only — other inspect
        kinds (entity / plan / causal-history) MUST still audit.
        Drives one ``audit-window`` poll (no emit) THEN one
        ``kind=entity`` call (must emit one entry).
        """
        token_str = _stored_token_str(db, clock_fixed)
        async with client.ws_connect("/ws") as ws:
            await _auth(ws, token_str)
            ring_size_after_auth = len(server._audit_entries)

            # Poll: no emit.
            await ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 200,
                    "method": "repl/inspect",
                    "params": {"kind": "audit-window"},
                }
            )
            await ws.receive_json()
            assert len(server._audit_entries) == ring_size_after_auth

            # kind=entity: MUST emit one entry. Use a flat-shape
            # request (W3 / ADR-12) so the handler dispatches correctly.
            await ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 201,
                    "method": "repl/inspect",
                    "params": {"kind": "entity", "entity_id": "foo"},
                }
            )
            await ws.receive_json()
            assert len(server._audit_entries) == ring_size_after_auth + 1
            new_entry = server._audit_entries[-1]
            assert new_entry.principal["op_kind"] == "inspect"

    @pytest.mark.asyncio
    async def test_audit_window_with_args_still_does_not_self_emit(
        self, client, server, db, clock_fixed
    ):
        """The gate fires whenever ``kind == "audit-window"``, regardless
        of any other args (op_filter, limit, from_iso, …). Without this
        invariant, a client that started polling with ``op_filter`` set
        would still saturate the chain.
        """
        token_str = _stored_token_str(db, clock_fixed)
        async with client.ws_connect("/ws") as ws:
            await _auth(ws, token_str)
            ring_size_after_auth = len(server._audit_entries)

            for params in [
                {"kind": "audit-window", "limit": 50},
                {"kind": "audit-window", "op_filter": ":llm/call"},
                {
                    "kind": "audit-window",
                    "from_iso": _dt(2026, 5, 1).isoformat(),
                    "to_iso": _dt(2026, 5, 31).isoformat(),
                },
            ]:
                await ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": 300,
                        "method": "repl/inspect",
                        "params": params,
                    }
                )
                resp = await ws.receive_json()
                assert "result" in resp, resp

            assert len(server._audit_entries) == ring_size_after_auth
