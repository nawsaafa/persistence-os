"""Tests for ``persistence.repl._ws`` and ``_protocol`` (D2).

30 tests across 6 groups:

- JSON-RPC envelope (parse + builders)            (8)
- WS auth handshake                               (8)
- Pre-auth ops rejected                           (4)
- Method not found                                (3)
- Skeleton ops raise NotImplementedError          (4)
- Static / route                                  (3)

See design doc ``docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md``
sections ADR-1, ADR-2, ADR-6, ADR-9, 6.2, 7.1, 7.2 and 10 (D2).

Test pattern: ``aiohttp.test_utils.TestServer + TestClient`` driven by
``@pytest.mark.asyncio`` (the project uses ``asyncio_mode = strict`` —
see ``pytest.ini``). ``pytest-aiohttp`` is NOT a dependency.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from persistence.fact import DB, InMemoryStore
from persistence.repl import (
    Capability,
    ERR_AUTH_FAILED,
    ERR_INTERNAL_ERROR,
    ERR_INVALID_REQUEST,
    ERR_METHOD_NOT_FOUND,
    ERR_PARSE_ERROR,
    ERR_TOKEN_INVALID,
    Request,
    WSServer,
    make_error_response,
    make_notification,
    make_response,
    mint_token,
    parse_request,
    revoke_token,
    store_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _dt(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def _fixed_clock(t: datetime):
    """Return a callable returning ``t`` (deterministic session_id pin)."""

    def clock() -> datetime:
        return t

    return clock


_DEFAULT_T = _dt(2026, 5, 9, 12, 0, 0)


@pytest.fixture
def db() -> DB:
    """Fresh in-memory DB per test (no fixture cross-talk).

    Same pattern as ``tests/repl/test_caps.py`` (D1).
    """
    return DB(InMemoryStore())


@pytest.fixture
def clock_fixed():
    return _fixed_clock(_DEFAULT_T)


@pytest.fixture
def server(db: DB, clock_fixed) -> WSServer:
    return WSServer(db, runtime_clock=clock_fixed)


@pytest_asyncio.fixture
async def client(server: WSServer):
    """``aiohttp.test_utils.TestClient`` wired to the server's app.

    ``asyncio_mode = strict`` (see ``pytest.ini``) means async fixtures
    must be tagged explicitly via ``@pytest_asyncio.fixture``. This is
    the workaround for the absence of ``pytest-aiohttp``.
    """
    test_server = TestServer(server.app)
    async with TestClient(test_server) as c:
        yield c


def _stored_token_str(db: DB, clock_fixed, **kwargs: Any) -> str:
    """Mint + persist a token; return the raw token string for ``repl/auth``."""
    caps = kwargs.pop(
        "caps", frozenset({Capability("inspect", "read"), Capability("edit", "write")})
    )
    t = mint_token(caps=caps, **kwargs)
    store_token(db, t, runtime_clock=clock_fixed)
    return t.token_str


async def _auth(ws, token_str: str, *, req_id: int = 1) -> dict:
    """Drive ``repl/auth`` and return the decoded response."""
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
# 1. JSON-RPC envelope (8)
# ===========================================================================
class TestProtocolEnvelope:
    def test_parse_request_happy_path(self):
        req = parse_request(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "repl/inspect",
                "params": {"q": "fact"},
            }
        )
        assert isinstance(req, Request)
        assert req.jsonrpc == "2.0"
        assert req.id == 7
        assert req.method == "repl/inspect"
        assert req.params == {"q": "fact"}

    def test_parse_request_missing_jsonrpc_raises(self):
        with pytest.raises(ValueError, match="jsonrpc"):
            parse_request({"id": 1, "method": "x", "params": {}})

    def test_parse_request_missing_method_raises(self):
        with pytest.raises(ValueError, match="method"):
            parse_request({"jsonrpc": "2.0", "id": 1, "params": {}})

    def test_parse_request_non_string_method_raises(self):
        with pytest.raises(ValueError, match="method"):
            parse_request({"jsonrpc": "2.0", "id": 1, "method": 42, "params": {}})

    def test_parse_request_non_dict_params_raises(self):
        with pytest.raises(ValueError, match="params"):
            parse_request(
                {"jsonrpc": "2.0", "id": 1, "method": "x", "params": [1, 2, 3]}
            )

    def test_parse_request_notification_id_none(self):
        # Notifications omit id (or set it to None).
        req = parse_request({"jsonrpc": "2.0", "method": "evt", "params": {"k": 1}})
        assert req.id is None
        assert req.method == "evt"

    def test_make_response_shape(self):
        out = make_response(42, {"ok": True})
        assert out == {"jsonrpc": "2.0", "id": 42, "result": {"ok": True}}

    def test_make_error_response_with_and_without_data(self):
        # No data: omit the "data" key entirely (per JSON-RPC §5.1).
        out = make_error_response(1, -32600, "bad")
        assert out == {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": "bad"},
        }
        # With data: include it.
        out_data = make_error_response(2, -32001, "denied", data={"why": "no cap"})
        assert out_data == {
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": -32001, "message": "denied", "data": {"why": "no cap"}},
        }
        # And bonus: make_notification has no id.
        notif = make_notification("evt", {"x": 1})
        assert notif == {"jsonrpc": "2.0", "method": "evt", "params": {"x": 1}}
        assert "id" not in notif


# ===========================================================================
# 2. WS auth handshake (8)
# ===========================================================================
class TestAuthHandshake:
    @pytest.mark.asyncio
    async def test_valid_token_returns_session_id(self, client, db, clock_fixed):
        tok = _stored_token_str(db, clock_fixed)
        async with client.ws_connect("/ws") as ws:
            resp = await _auth(ws, tok)
        assert resp["id"] == 1
        assert "result" in resp
        assert isinstance(resp["result"]["session_id"], str)
        assert len(resp["result"]["session_id"]) == 16

    @pytest.mark.asyncio
    async def test_invalid_token_returns_token_invalid(self, client, db, clock_fixed):
        async with client.ws_connect("/ws") as ws:
            resp = await _auth(ws, "persistence.repl/never-issued-xxxxxxxxxxxxxx")
        assert "error" in resp
        assert resp["error"]["code"] == ERR_TOKEN_INVALID

    @pytest.mark.asyncio
    async def test_missing_params_token_returns_invalid_request(self, client):
        async with client.ws_connect("/ws") as ws:
            await ws.send_json(
                {"jsonrpc": "2.0", "id": 1, "method": "repl/auth", "params": {}}
            )
            resp = await ws.receive_json()
        assert "error" in resp
        assert resp["error"]["code"] == ERR_INVALID_REQUEST
        assert "token" in resp["error"]["message"]

    @pytest.mark.asyncio
    async def test_expired_token_returns_token_invalid(self, db):
        # Issue at T0 with expiry T_exp < T_now.
        t0 = _dt(2026, 1, 1)
        t_exp = _dt(2026, 6, 1)
        t_now = _dt(2027, 1, 1)
        from persistence.repl import mint_token, store_token

        tok = mint_token(
            caps=frozenset({Capability("inspect", "read")}),
            expires_at=t_exp,
        )
        store_token(db, tok, runtime_clock=_fixed_clock(t0))
        srv = WSServer(db, runtime_clock=_fixed_clock(t_now))
        async with TestClient(TestServer(srv.app)) as c:
            async with c.ws_connect("/ws") as ws:
                resp = await _auth(ws, tok.token_str)
        assert "error" in resp
        assert resp["error"]["code"] == ERR_TOKEN_INVALID

    @pytest.mark.asyncio
    async def test_revoked_token_returns_token_invalid(self, client, db, clock_fixed):
        tok = mint_token(caps=frozenset({Capability("inspect", "read")}))
        store_token(db, tok, runtime_clock=clock_fixed)
        revoke_token(db, tok.token_id, runtime_clock=clock_fixed)
        async with client.ws_connect("/ws") as ws:
            resp = await _auth(ws, tok.token_str)
        assert "error" in resp
        assert resp["error"]["code"] == ERR_TOKEN_INVALID

    @pytest.mark.asyncio
    async def test_auth_response_includes_auth_clock_iso(
        self, client, db, clock_fixed
    ):
        tok = _stored_token_str(db, clock_fixed)
        async with client.ws_connect("/ws") as ws:
            resp = await _auth(ws, tok)
        assert resp["result"]["auth_clock_iso"] == _DEFAULT_T.isoformat()

    @pytest.mark.asyncio
    async def test_auth_response_includes_caps_list(self, client, db, clock_fixed):
        tok = _stored_token_str(
            db,
            clock_fixed,
            caps=frozenset(
                {
                    Capability("inspect", "read"),
                    Capability("edit", "write"),
                }
            ),
        )
        async with client.ws_connect("/ws") as ws:
            resp = await _auth(ws, tok)
        caps_seen = {(c["op"], c["qualifier"]) for c in resp["result"]["caps"]}
        assert ("inspect", "read") in caps_seen
        assert ("edit", "write") in caps_seen

    @pytest.mark.asyncio
    async def test_re_auth_replaces_session(self, db, clock_fixed):
        # Two auths on the same WS → second replaces first in the
        # active-sessions registry. Use distinct clocks so the two
        # sessions have distinct ids.
        t0 = _dt(2026, 5, 9, 12, 0, 0)
        t1 = _dt(2026, 5, 9, 13, 0, 0)
        clock_state = {"t": t0}

        def stepping_clock() -> datetime:
            return clock_state["t"]

        srv = WSServer(db, runtime_clock=stepping_clock)
        # Mint two tokens (so session_ids differ via token_id too).
        tok1 = mint_token(caps=frozenset({Capability("inspect", "read")}))
        tok2 = mint_token(caps=frozenset({Capability("edit", "write")}))
        store_token(db, tok1, runtime_clock=stepping_clock)
        store_token(db, tok2, runtime_clock=stepping_clock)
        async with TestClient(TestServer(srv.app)) as c:
            async with c.ws_connect("/ws") as ws:
                clock_state["t"] = t0
                r1 = await _auth(ws, tok1.token_str, req_id=1)
                sid1 = r1["result"]["session_id"]
                clock_state["t"] = t1
                r2 = await _auth(ws, tok2.token_str, req_id=2)
                sid2 = r2["result"]["session_id"]
        assert sid1 != sid2
        # Only the second session remains in the registry.
        assert sid1 not in srv._active_sessions
        assert sid2 not in srv._active_sessions  # popped on disconnect


# ===========================================================================
# 3. Pre-auth ops rejected (4)
# ===========================================================================
class TestPreAuthRejection:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method",
        ["repl/inspect", "repl/edit", "repl/rewind", "repl/branch"],
    )
    async def test_pre_auth_op_returns_auth_failed(self, client, method):
        async with client.ws_connect("/ws") as ws:
            await ws.send_json(
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}
            )
            resp = await ws.receive_json()
        assert "error" in resp
        assert resp["error"]["code"] == ERR_AUTH_FAILED


# ===========================================================================
# 4. Method not found (3)
# ===========================================================================
class TestMethodNotFound:
    @pytest.mark.asyncio
    async def test_unknown_method_returns_method_not_found(
        self, client, db, clock_fixed
    ):
        tok = _stored_token_str(db, clock_fixed)
        async with client.ws_connect("/ws") as ws:
            await _auth(ws, tok)
            await ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "totally-not-a-method",
                    "params": {},
                }
            )
            resp = await ws.receive_json()
        assert "error" in resp
        assert resp["error"]["code"] == ERR_METHOD_NOT_FOUND

    @pytest.mark.asyncio
    async def test_repl_foo_returns_method_not_found(self, client, db, clock_fixed):
        tok = _stored_token_str(db, clock_fixed)
        async with client.ws_connect("/ws") as ws:
            await _auth(ws, tok)
            await ws.send_json(
                {"jsonrpc": "2.0", "id": 10, "method": "repl/foo", "params": {}}
            )
            resp = await ws.receive_json()
        assert "error" in resp
        assert resp["error"]["code"] == ERR_METHOD_NOT_FOUND

    @pytest.mark.asyncio
    async def test_method_dispatch_is_case_sensitive(
        self, client, db, clock_fixed
    ):
        # "REPL/INSPECT" must NOT match "repl/inspect".
        tok = _stored_token_str(db, clock_fixed)
        async with client.ws_connect("/ws") as ws:
            await _auth(ws, tok)
            await ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "REPL/INSPECT",
                    "params": {},
                }
            )
            resp = await ws.receive_json()
        assert "error" in resp
        assert resp["error"]["code"] == ERR_METHOD_NOT_FOUND


# ===========================================================================
# 5. Skeleton ops raise NotImplementedError (1 — D6 only)
# ===========================================================================
# D3/D4/D5 ship inspect/rewind/branch; only ``repl/edit`` remains stubbed
# pending D6. The full op-level test coverage moved to
# ``test_inspect.py`` / ``test_rewind.py`` / ``test_branch.py``.
class TestSkeletonOpsNotImplemented:
    @pytest.mark.asyncio
    async def test_post_auth_edit_returns_internal_error(
        self, client, db, clock_fixed
    ):
        tok = _stored_token_str(db, clock_fixed)
        async with client.ws_connect("/ws") as ws:
            await _auth(ws, tok)
            await ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 20,
                    "method": "repl/edit",
                    "params": {},
                }
            )
            resp = await ws.receive_json()
        assert "error" in resp
        # NotImplementedError → ERR_INTERNAL_ERROR (D6 will overwrite).
        assert resp["error"]["code"] == ERR_INTERNAL_ERROR


# ===========================================================================
# 6. Static / route (3)
# ===========================================================================
class TestStaticRoute:
    @pytest.mark.asyncio
    async def test_get_root_returns_200(self, client):
        # D8 not yet shipped → placeholder is served, but status is 200.
        resp = await client.get("/")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_get_root_content_type_text_html(self, client):
        resp = await client.get("/")
        ctype = resp.headers.get("Content-Type", "")
        assert "text/html" in ctype

    @pytest.mark.asyncio
    async def test_get_unknown_path_returns_404(self, client):
        resp = await client.get("/totally-not-a-path")
        assert resp.status == 404
