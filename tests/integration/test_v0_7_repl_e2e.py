"""D-INT — v0.7.0a1 REPL end-to-end integration test (Stream D).

Five tests that exercise the FULL REPL flow over a live ``WSServer``
(via :class:`aiohttp.test_utils.TestServer` / :class:`TestClient` —
mirrors :mod:`tests/repl/test_ws.py`'s pattern for the real WS round
trip without binding a kernel TCP socket). The per-op contract tests
already live under ``tests/repl/test_inspect.py`` /
``tests/repl/test_edit.py`` / ``tests/repl/test_rewind.py`` /
``tests/repl/test_branch.py`` / ``tests/repl/test_audit_emission.py`` /
``tests/repl/test_caps.py`` / ``tests/repl/test_ws.py`` /
``tests/repl/test_ws_audit_window_self_loop.py``. What's missing — and
what this file pins — is the FULL flow stitched through the WS surface,
plus the load-bearing W2 chain-continuity invariant **at the REPL
boundary**: every op writes Datoms that, replayed alone (no in-memory
state, no ring), reconstruct the same view.

Tests:

1. ``test_e2e_inspect_after_edit`` — auth → inspect → edit (propose +
   confirm) → inspect-and-see-the-edit. Confirms the WS dispatcher
   threads ``edit_op``'s commit through the substrate and that the
   subsequent ``inspect`` op reads the new state.
2. ``test_e2e_rewind_cursor_does_not_mutate`` — rewind sets a per-session
   view-cursor; intervening ``db.transact`` writes still land; cursor
   reads return the past state regardless. Cursor-clear restores HEAD.
3. ``test_e2e_branch_records_cursor_and_depth`` — ``repl/branch`` returns
   a deterministic ``branch_id`` AND increments ``parent_chain_depth``;
   subsequent ``repl/edit`` at the past cursor is rejected with
   ``ERR_STALE_CURSOR_EDIT`` (substrate's branch contract: branch is a
   cursor + depth marker, NOT a database fork — design §3 / §5.2).
4. ``test_e2e_replay_from_datoms_alone_byte_identity`` ⭐ KEY INVARIANT —
   capture every datom that lands in ``db_a.store`` after a real op
   sequence; transact / append them into a fresh ``db_b.store``; assert
   the two stores are byte-identical via ``canonical_dumps`` over the
   datom-projection list, AND that an entity read on ``db_b`` matches
   the REPL ``inspect`` answer on ``db_a``. This is the W2 chain-
   continuity guarantee at the REPL boundary.
5. ``test_e2e_audit_chain_intact_across_ops`` — drive a 4-op sequence;
   reconstruct the persisted ``:repl/op`` chain via
   ``_audit_window_query``; ``verify_chain`` returns ``True``; tampering
   one entry's ``prev_hash`` flips it to ``False``; ``audit-window``
   poll repeated does NOT add to the ring (W3 self-loop fix verified
   end-to-end through the live WS surface).

Style cues borrowed from ``tests/repl/test_ws.py`` (TestServer + TestClient
fixture pattern), ``tests/integration/test_v0_6_plan_execution.py``
(integration test docstring discipline), and ``tests/integration/
test_v0_6_5_mcts.py`` (replay-from-datoms-alone shape via Step 7 byte-
identity assertion on ``tree_dump``).

See ``docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md`` sections
ADR-1 / ADR-3 / ADR-4 / ADR-5 / ADR-7 / ADR-9 / ADR-11.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from persistence.effect.canonical import canonical_dumps
from persistence.effect.handlers.audit import verify_chain
from persistence.fact import DB, InMemoryStore
from persistence.fact.datom import Datom
from persistence.repl import (
    Capability,
    ERR_STALE_CURSOR_EDIT,
    WSServer,
    mint_token,
    store_token,
)
from persistence.repl._audit import _audit_window_query


# ---------------------------------------------------------------------------
# Fixtures (mirror tests/repl/test_ws.py — kept local rather than factored
# into conftest so this file is self-contained for D-INT review).
# ---------------------------------------------------------------------------
def _dt(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


_T0 = _dt(2026, 5, 9, 12, 0, 0)
_T1 = _dt(2026, 5, 9, 12, 1, 0)
_T2 = _dt(2026, 5, 9, 12, 2, 0)
_T3 = _dt(2026, 5, 9, 12, 3, 0)


def _stepping_clock(state: dict[str, datetime]):
    """A clock that returns ``state['t']``; tests advance ``state['t']``
    between sends so ``tx_time`` and audit ``recorded_at`` are
    deterministic. Mirror of ``test_ws.py::test_re_auth_replaces_session``.
    """

    def clock() -> datetime:
        return state["t"]

    return clock


@pytest.fixture
def clock_state() -> dict[str, datetime]:
    return {"t": _T0}


@pytest.fixture
def db(clock_state) -> DB:
    """Wire the SAME stepping clock into the DB so ``db.transact`` (called
    both directly by the test and indirectly by ``edit_op``) stamps
    ``tx_time`` from the test-controlled instant. Without this the DB
    would default to the real ``_system_clock``, and rewound cursor
    reads in this file (which all sit in 2026-05-09) would see edits
    landing at real-time wall-clock — breaking the cursor-isolation
    semantics the tests pin.
    """
    return DB(InMemoryStore(), clock=_stepping_clock(clock_state))


@pytest.fixture
def server(db: DB, clock_state) -> WSServer:
    return WSServer(db, runtime_clock=_stepping_clock(clock_state))


@pytest_asyncio.fixture
async def client(server: WSServer):
    """``aiohttp.test_utils`` round-trip — same pattern as test_ws.py."""
    test_server = TestServer(server.app)
    async with TestClient(test_server) as c:
        yield c


def _full_caps() -> frozenset[Capability]:
    """Token caps wide enough to exercise every op in this file."""
    return frozenset(
        {
            Capability("auth", "login"),
            Capability("inspect", "read"),
            Capability("edit", "write"),
            Capability("rewind", "any"),
            Capability("branch", "fork"),
        }
    )


def _store_full_token(db: DB, clock_state) -> str:
    """Mint + persist a full-cap token; return the raw string."""
    tok = mint_token(caps=_full_caps())
    store_token(db, tok, runtime_clock=_stepping_clock(clock_state))
    return tok.token_str


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


async def _send(ws, method: str, params: dict, *, req_id: int) -> dict:
    await ws.send_json(
        {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    )
    return await ws.receive_json()


def _datoms_signature(store) -> str:
    """Canonical-JSON projection of every datom in ``store``.

    Used as the byte-identity primitive in Test 4. We can't hash the
    Datom dataclasses directly (frozen dataclasses with ``datetime``
    fields), and we don't want to compare object identity, so we
    project every slot to a JSON-friendly tuple and feed the whole
    list through ``canonical_dumps`` — the same ordering-discipline
    the substrate's audit Merkle chain uses.
    """
    projected = [
        {
            "e": d.e,
            "a": d.a,
            "v": d.v,
            "tx": d.tx,
            "tx_time": d.tx_time.isoformat(),
            "valid_from": d.valid_from.isoformat(),
            "valid_to": d.valid_to.isoformat() if d.valid_to is not None else None,
            "op": d.op,
            "provenance": dict(d.provenance),
            "invalidated_by": d.invalidated_by,
        }
        for d in store.all_datoms()
    ]
    return canonical_dumps(projected)


# ===========================================================================
# Test 1 — inspect-after-edit
# ===========================================================================
@pytest.mark.asyncio
async def test_e2e_inspect_after_edit(client, db, clock_state):
    """Auth → inspect (sees seed) → edit (propose+confirm) → inspect (sees edit).

    The substrate-side ``InMemoryStore`` is mutated by ``edit_op``'s
    ``db.transact`` (the new DB returned is discarded but the store is
    shared with ``self.db`` on the server), so the post-confirm
    ``inspect`` reads the new state without any re-binding.
    """
    tok_str = _store_full_token(db, clock_state)
    # Seed two attrs on ``user-42`` directly via the DB (not through the
    # REPL) so the first ``inspect`` has something to read.
    db.transact(
        [
            {"e": "user-42", "a": "user/email", "v": "alice@example.com",
             "valid_from": _T0},
            {"e": "user-42", "a": "user/role", "v": "admin",
             "valid_from": _T0},
        ]
    )

    async with client.ws_connect("/ws") as ws:
        auth_resp = await _auth(ws, tok_str)
        assert "result" in auth_resp

        # Inspect — sees the seeded attrs.
        clock_state["t"] = _T1
        r1 = await _send(
            ws, "repl/inspect",
            {"kind": "entity", "entity_id": "user-42"},
            req_id=2,
        )
        assert r1["result"]["entity"] == {
            "user/email": "alice@example.com",
            "user/role": "admin",
        }

        # Edit — propose first.
        clock_state["t"] = _T2
        propose = await _send(
            ws, "repl/edit",
            {
                "datoms": [
                    {"e": "user-42", "a": "user/role", "v": "superadmin",
                     "valid_from": _T2.isoformat()}
                ],
            },
            req_id=3,
        )
        assert propose["result"]["requires_confirmation"] is True
        request_hash = propose["result"]["request_hash"]

        # Edit — confirm with matching request_hash.
        confirm = await _send(
            ws, "repl/edit",
            {
                "datoms": [
                    {"e": "user-42", "a": "user/role", "v": "superadmin",
                     "valid_from": _T2.isoformat()}
                ],
                "confirm": True,
                "request_hash": request_hash,
            },
            req_id=4,
        )
        assert confirm["result"]["committed"] is True
        assert confirm["result"]["datom_count"] == 1

        # Inspect again — role is now "superadmin", email unchanged.
        clock_state["t"] = _T3
        r2 = await _send(
            ws, "repl/inspect",
            {"kind": "entity", "entity_id": "user-42"},
            req_id=5,
        )
        assert r2["result"]["entity"]["user/role"] == "superadmin"
        assert r2["result"]["entity"]["user/email"] == "alice@example.com"


# ===========================================================================
# Test 2 — rewind cursor is read-only & sticky
# ===========================================================================
@pytest.mark.asyncio
async def test_e2e_rewind_cursor_does_not_mutate(client, db, clock_state):
    """Rewind sets a view-cursor; intervening writes still land in the
    store; cursor reads return state-at-cursor; clearing returns HEAD.
    """
    tok_str = _store_full_token(db, clock_state)
    # Three states across (T0 → T1 → T2 → T3).
    db.transact([{"e": "doc-1", "a": "doc/title", "v": "draft", "valid_from": _T0}])
    clock_state["t"] = _T1
    db.transact([{"e": "doc-1", "a": "doc/title", "v": "review", "valid_from": _T1}])
    clock_state["t"] = _T2
    db.transact([{"e": "doc-1", "a": "doc/title", "v": "approved", "valid_from": _T2}])

    # Snapshot store length BEFORE the WS interactions so we can verify
    # the bypass-write later didn't get swallowed.
    pre_ws_len = sum(1 for _ in db.store.all_datoms())

    async with client.ws_connect("/ws") as ws:
        clock_state["t"] = _T3
        await _auth(ws, tok_str)

        # Rewind to T1 — view-cursor moves backward.
        r_rew = await _send(
            ws, "repl/rewind",
            {"tx_time_iso": _T1.isoformat()},
            req_id=2,
        )
        assert r_rew["result"]["view_cursor_tx_time_iso"] == _T1.isoformat()

        # Inspect — at T1 the title is "review" (NOT the latest "approved").
        r_at_t1 = await _send(
            ws, "repl/inspect",
            {"kind": "entity", "entity_id": "doc-1"},
            req_id=3,
        )
        assert r_at_t1["result"]["entity"]["doc/title"] == "review"

        # Bypass the REPL: transact a fourth update directly via db. This
        # MUST land in the store regardless of the rewound view-cursor.
        clock_state["t"] = _T3
        db.transact(
            [{"e": "doc-1", "a": "doc/title", "v": "published", "valid_from": _T3}]
        )
        post_bypass_len = sum(1 for _ in db.store.all_datoms())
        assert post_bypass_len > pre_ws_len, (
            "bypass write did not land in the store — cursor must not "
            "gate writes"
        )

        # Inspect again at the rewound cursor — STILL "review" (cursor
        # is sticky, the bypass write has tx_time=T3 which is later
        # than the cursor T1).
        r_at_t1_again = await _send(
            ws, "repl/inspect",
            {"kind": "entity", "entity_id": "doc-1"},
            req_id=4,
        )
        assert r_at_t1_again["result"]["entity"]["doc/title"] == "review"

        # Clear the cursor (rewind with both fields None) — falls back
        # to HEAD via session.clock(). At HEAD all four updates apply
        # so the latest visible value is "published".
        await _send(
            ws, "repl/rewind",
            {"tx_time_iso": None, "vt_iso": None},
            req_id=5,
        )
        r_at_head = await _send(
            ws, "repl/inspect",
            {"kind": "entity", "entity_id": "doc-1"},
            req_id=6,
        )
        assert r_at_head["result"]["entity"]["doc/title"] == "published"


# ===========================================================================
# Test 3 — branch records cursor + depth; stale-cursor edit rejected
# ===========================================================================
@pytest.mark.asyncio
async def test_e2e_branch_records_cursor_and_depth(client, db, clock_state):
    """``repl/branch`` is a cursor + depth marker, not a database fork.

    Per design §3 / §5.2 + ``_ops.branch_op`` docstring: the substrate
    does NOT physically fork the DB on ``branch`` — it sets the
    session's ``view_cursor_tx_time_iso`` and increments
    ``parent_chain_depth``. Edits at a non-HEAD cursor are rejected
    with ``ERR_STALE_CURSOR_EDIT`` ("branch first to fork the cursor
    before editing in the past"). This test pins exactly that
    contract end-to-end.

    Two independent observations:
    - the post-branch ``inspect`` returns state-at-branch-tx;
    - an attempt to ``repl/edit confirm:true`` on the branched
      session is rejected; later, on a separate session at HEAD,
      the same edit succeeds — proving the branched session's
      cursor ISOLATED its inspect view but the substrate
      explicitly refuses past-coordinate writes.
    """
    tok_str = _store_full_token(db, clock_state)
    db.transact([{"e": "exp-1", "a": "exp/value", "v": 1, "valid_from": _T0}])
    clock_state["t"] = _T1
    db.transact([{"e": "exp-1", "a": "exp/value", "v": 2, "valid_from": _T1}])

    async with client.ws_connect("/ws") as ws:
        clock_state["t"] = _T2
        auth_resp = await _auth(ws, tok_str)
        assert "result" in auth_resp

        # Branch from T0 — cursor moves to T0, parent_chain_depth → 1.
        r_branch = await _send(
            ws, "repl/branch",
            {"tx_time_iso": _T0.isoformat(), "label": "experiment-A"},
            req_id=2,
        )
        assert r_branch["result"]["tx_time_iso"] == _T0.isoformat()
        assert r_branch["result"]["parent_chain_depth"] == 1
        assert r_branch["result"]["label"] == "experiment-A"
        assert r_branch["result"]["branch_id"].startswith("branch:")
        # Deterministic 16-hex suffix.
        assert len(r_branch["result"]["branch_id"]) == len("branch:") + 16
        first_branch_id = r_branch["result"]["branch_id"]

        # Inspect on the branched session — sees state at T0 (value=1),
        # NOT the later T1 update (value=2).
        r_at_branch = await _send(
            ws, "repl/inspect",
            {"kind": "entity", "entity_id": "exp-1"},
            req_id=3,
        )
        assert r_at_branch["result"]["entity"]["exp/value"] == 1

        # Edit on the branched session at past-cursor — must reject
        # with ERR_STALE_CURSOR_EDIT (the propose step is fine — it's
        # a pure re-hash; only confirm is gated).
        propose = await _send(
            ws, "repl/edit",
            {"datoms": [
                {"e": "exp-1", "a": "exp/value", "v": 99,
                 "valid_from": _T2.isoformat()}
            ]},
            req_id=4,
        )
        assert propose["result"]["requires_confirmation"] is True
        confirm_attempt = await _send(
            ws, "repl/edit",
            {
                "datoms": [
                    {"e": "exp-1", "a": "exp/value", "v": 99,
                     "valid_from": _T2.isoformat()}
                ],
                "confirm": True,
                "request_hash": propose["result"]["request_hash"],
            },
            req_id=5,
        )
        assert "error" in confirm_attempt
        assert confirm_attempt["error"]["code"] == ERR_STALE_CURSOR_EDIT

        # Branch a second time from a different coordinate — depth
        # increments to 2, branch_id is a different deterministic value.
        r_branch_2 = await _send(
            ws, "repl/branch",
            {"tx_time_iso": _T1.isoformat(), "label": "experiment-B"},
            req_id=6,
        )
        assert r_branch_2["result"]["parent_chain_depth"] == 2
        assert r_branch_2["result"]["branch_id"] != first_branch_id

    # Open a SEPARATE session — fresh cursor at HEAD — and confirm the
    # same edit succeeds. This proves the branch isolated the FIRST
    # session without poisoning HEAD on a fresh connection.
    async with client.ws_connect("/ws") as ws2:
        clock_state["t"] = _T3
        await _auth(ws2, tok_str, req_id=10)
        propose2 = await _send(
            ws2, "repl/edit",
            {"datoms": [
                {"e": "exp-1", "a": "exp/value", "v": 7,
                 "valid_from": _T3.isoformat()}
            ]},
            req_id=11,
        )
        confirm2 = await _send(
            ws2, "repl/edit",
            {
                "datoms": [
                    {"e": "exp-1", "a": "exp/value", "v": 7,
                     "valid_from": _T3.isoformat()}
                ],
                "confirm": True,
                "request_hash": propose2["result"]["request_hash"],
            },
            req_id=12,
        )
        assert confirm2["result"]["committed"] is True


# ===========================================================================
# Test 4 — replay-from-datoms-alone byte-identity (KEY INVARIANT)
# ===========================================================================
@pytest.mark.asyncio
async def test_e2e_replay_from_datoms_alone_byte_identity(
    client, db, clock_state,
):
    """Capture every datom that lands in ``db_a.store`` after a real
    REPL session; replay them into a fresh ``db_b.store``; assert
    byte-identity via canonical-JSON projection AND that an entity
    read on ``db_b`` matches the REPL ``inspect`` answer on ``db_a``.

    This is the W2 chain-continuity guarantee at the REPL boundary:
    nothing the REPL emits depends on in-memory server state; the
    fact-store datoms are the ONLY thing required to reconstruct the
    full view (auth tokens, REPL audit chain, edit-emitted state,
    everything).

    Method: we use ``store.append`` directly on the replay store,
    preserving every Datom slot byte-identically (including ``tx``,
    ``tx_time``, ``provenance``, ``invalidated_by``). ``db.transact``
    cannot be used here because it allocates a fresh ``tx`` and
    re-stamps ``tx_time`` from its own clock — exactly the same
    constraint that drove ``persist_repl_audit`` to bypass
    ``db.transact`` in favor of ``store.append`` (see ``_audit.py``
    docstring).
    """
    tok_str = _store_full_token(db, clock_state)
    # Seed at T0 so audit + token + seed all share an instant.
    db.transact([{"e": "x-1", "a": "x/value", "v": "initial", "valid_from": _T0}])

    # Drive a multi-op WS session.
    async with client.ws_connect("/ws") as ws:
        clock_state["t"] = _T1
        await _auth(ws, tok_str)

        # Inspect at HEAD.
        r_inspect_head = await _send(
            ws, "repl/inspect",
            {"kind": "entity", "entity_id": "x-1"},
            req_id=2,
        )
        assert r_inspect_head["result"]["entity"]["x/value"] == "initial"

        # Edit (propose + confirm).
        propose = await _send(
            ws, "repl/edit",
            {"datoms": [
                {"e": "x-1", "a": "x/value", "v": "updated",
                 "valid_from": _T1.isoformat()}
            ]},
            req_id=3,
        )
        await _send(
            ws, "repl/edit",
            {
                "datoms": [
                    {"e": "x-1", "a": "x/value", "v": "updated",
                     "valid_from": _T1.isoformat()}
                ],
                "confirm": True,
                "request_hash": propose["result"]["request_hash"],
            },
            req_id=4,
        )

        # Rewind to T0 + inspect (cursor read).
        clock_state["t"] = _T2
        await _send(
            ws, "repl/rewind",
            {"tx_time_iso": _T0.isoformat()},
            req_id=5,
        )
        r_at_t0 = await _send(
            ws, "repl/inspect",
            {"kind": "entity", "entity_id": "x-1"},
            req_id=6,
        )
        assert r_at_t0["result"]["entity"]["x/value"] == "initial"

    # ---- Capture phase --------------------------------------------------
    # Take a stable snapshot of every datom in db_a.store.
    captured: list[Datom] = list(db.store.all_datoms())
    assert len(captured) > 0, "captured datom list is empty — fixture broken"
    # The capture must include audit datoms emitted by the REPL.
    audit_count = sum(1 for d in captured if d.a.startswith("audit/"))
    assert audit_count >= 4, (
        f"expected ≥ 4 audit datoms (auth + inspect + edit + rewind + "
        f"inspect = 5; one per non-audit-window op) but found {audit_count}"
    )

    # ---- Replay phase ---------------------------------------------------
    # Build a totally fresh store; append the captured datoms verbatim.
    # ``store.append`` preserves every slot — we do NOT route through
    # db.transact (which would re-stamp tx + tx_time and break byte
    # identity).
    db_b = DB(InMemoryStore())
    db_b.store.append(captured)

    # ---- Byte-identity assertion ---------------------------------------
    sig_a = _datoms_signature(db.store)
    sig_b = _datoms_signature(db_b.store)
    assert sig_a == sig_b, (
        "store-projection byte-identity broken between db_a (live) and "
        "db_b (replayed-from-datoms-alone)"
    )

    # ---- View-equivalence assertion -------------------------------------
    # Independent of the byte-identity check: confirm an entity read on
    # db_b yields the same shape the REPL surfaced on db_a (the LATEST
    # state — the rewound cursor on db_a was per-session, not per-DB).
    view_b_at_t1 = db_b.as_of(_T1).entity("x-1")
    assert view_b_at_t1 is not None
    assert view_b_at_t1["x/value"] == "updated"


# ===========================================================================
# Test 5 — audit chain intact across ops + W3 self-loop verified
# ===========================================================================
@pytest.mark.asyncio
async def test_e2e_audit_chain_intact_across_ops(
    client, db, clock_state, server,
):
    """Drive a 4-op sequence; reconstruct the persisted audit chain via
    ``_audit_window_query``; ``verify_chain`` returns True. Tampering one
    entry's ``prev_hash`` (via direct re-construction of an entry whose
    prev_hash points at a wrong predecessor) breaks the chain. Repeated
    ``inspect kind=audit-window`` polls do NOT add to the ring (W3 / ADR-11).
    """
    tok_str = _store_full_token(db, clock_state)
    db.transact([{"e": "u-1", "a": "u/n", "v": 0, "valid_from": _T0}])

    async with client.ws_connect("/ws") as ws:
        clock_state["t"] = _T1
        await _auth(ws, tok_str)
        # Op 1 — inspect (entity, NOT audit-window — that wouldn't audit).
        await _send(
            ws, "repl/inspect",
            {"kind": "entity", "entity_id": "u-1"},
            req_id=2,
        )
        # Op 2 — edit (propose + confirm = ONE op handler invocation
        # per leg = TWO :repl/op audit entries).
        propose = await _send(
            ws, "repl/edit",
            {"datoms": [
                {"e": "u-1", "a": "u/n", "v": 1, "valid_from": _T1.isoformat()}
            ]},
            req_id=3,
        )
        await _send(
            ws, "repl/edit",
            {
                "datoms": [
                    {"e": "u-1", "a": "u/n", "v": 1, "valid_from": _T1.isoformat()}
                ],
                "confirm": True,
                "request_hash": propose["result"]["request_hash"],
            },
            req_id=4,
        )
        # Op 3 — rewind.
        await _send(
            ws, "repl/rewind",
            {"tx_time_iso": _T0.isoformat()},
            req_id=5,
        )
        # Op 4 — branch (note: post-rewind, tx_time_iso=None falls
        # through to session.view_cursor_tx_time_iso).
        await _send(
            ws, "repl/branch",
            {"label": "post-rewind"},
            req_id=6,
        )

        # ---- W3 self-loop check ------------------------------------
        # Snapshot the in-memory ring length BEFORE polling
        # audit-window. Then poll twice. The ring length MUST NOT
        # increase — audit-window polls are excluded from
        # self-emission per ADR-11.
        ring_before = list(server._audit_entries)
        await _send(
            ws, "repl/inspect",
            {"kind": "audit-window"},
            req_id=7,
        )
        await _send(
            ws, "repl/inspect",
            {"kind": "audit-window"},
            req_id=8,
        )
        ring_after = list(server._audit_entries)
        assert len(ring_after) == len(ring_before), (
            f"audit-window poll self-emitted: ring grew "
            f"{len(ring_before)} → {len(ring_after)} (W3/ADR-11 broken)"
        )

    # ---- Verify chain on persisted entries ------------------------
    # Reconstruct from the FACT STORE (not the ring). This is the
    # path a reconnecting client would use — durable continuity
    # across server restart.
    persisted = _audit_window_query(
        db, from_iso=None, to_iso=None, op_filter=":repl/op", limit=1000,
    )
    # Sequence: auth + inspect + edit-propose + edit-confirm + rewind + branch
    # = 6 :repl/op audit entries (every successful op handler emits exactly
    # one — the edit op is invoked twice by the dispatcher, once per leg).
    assert len(persisted) >= 6, (
        f"expected ≥ 6 persisted :repl/op entries, got {len(persisted)}; "
        "audit emission gap"
    )
    # Each entry's prev_hash chains to the previous entry's id.
    prev: str | None = None
    for entry in persisted:
        assert entry.prev_hash == prev, (
            f"chain pointer broken at entry id={entry.id!r}: "
            f"prev_hash={entry.prev_hash!r} expected={prev!r}"
        )
        prev = entry.id
    # And the canonical Merkle verifier returns True.
    assert verify_chain(persisted) is True

    # ---- Tamper check ---------------------------------------------
    # Build a tampered list where ONE entry's prev_hash is wrong. We
    # use ``dataclasses.replace`` to forge the tampered copy (the
    # AuditEntry is frozen). The id stays the same as the original
    # but prev_hash no longer matches its predecessor — verify_chain
    # MUST flip to False.
    if len(persisted) >= 2:
        tampered = list(persisted)
        bad_idx = len(tampered) - 1  # tamper the tail
        tampered[bad_idx] = dataclasses.replace(
            tampered[bad_idx],
            prev_hash="sha256:" + ("0" * 64),  # plausibly-shaped wrong hash
        )
        assert verify_chain(tampered) is False, (
            "verify_chain failed to detect tampered prev_hash — "
            "Merkle continuity guarantee broken"
        )
