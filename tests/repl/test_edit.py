"""Tests for ``persistence.repl._ops.edit_op`` (D6).

30 tests across 5 groups:

- Propose step                                  (8)
- Confirm step happy path                       (5)
- Confirm step rejection paths                  (10)
- Capability matrix                             (4)
- request_hash semantics                        (3)

See design doc ``docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md``
sections ADR-7 (two-step propose-confirm WITHOUT server-side preview),
5.2 (edit op contract), and ADR-9 (error codes -32005 request_hash
mismatch, -32008 stale-cursor edit).

Test pattern: in-memory ``DB`` + a fixed clock; drive ``edit_op``
directly with a ``WSServer`` instance. Edit step 1 (propose) is a
pure re-hash — no commit. Step 2 (confirm) calls ``db.transact``
through the substrate's normal write path, so datoms actually land
in the fact store and can be read back via ``db.as_of``.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from persistence.effect.canonical import canonical_hash
from persistence.fact import DB, InMemoryStore
from persistence.repl import (
    Capability,
    CapabilitySet,
    ERR_CAPABILITY_DENIED,
    ERR_INVALID_PARAMS,
    ERR_REQUEST_HASH_MISMATCH,
    ERR_STALE_CURSOR_EDIT,
    WSServer,
    make_session,
)
from persistence.repl._ops import _compute_request_hash, edit_op
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
_DEFAULT_VF = _dt(2099, 1, 1, 12, 0, 0).isoformat()


@pytest.fixture
def db() -> DB:
    # Pin the DB clock to the same fixed value as the session clock so
    # tx_time is deterministic and matches the response's
    # tx_time_iso exactly.
    return DB(InMemoryStore(), clock=_fixed_clock(_DEFAULT_T))


@pytest.fixture
def clock_fixed():
    return _fixed_clock(_DEFAULT_T)


@pytest.fixture
def server(db, clock_fixed):
    return WSServer(db, runtime_clock=clock_fixed)


def _make_edit_session(
    server: WSServer,
    clock,
    *,
    token_id: str = "token-id-edit01",
    caps: frozenset[Capability] | None = None,
    expires_at: datetime | None = None,
    cursor_iso: str | None = None,
):
    """Build + register a session with the supplied caps.

    Default caps include ``edit:write``. Pass ``caps=frozenset()`` to
    test the "no caps" path; pass an explicit ``caps`` to override.
    """
    if caps is None:
        caps = frozenset({Capability("edit", "write")})
    cs = CapabilitySet(caps=caps, expires_at=expires_at)
    session = make_session(token_id, cs, runtime_clock=clock)
    if cursor_iso is not None:
        session = dataclasses.replace(session, view_cursor_tx_time_iso=cursor_iso)
    server._active_sessions[session.session_id] = session
    return session


def _one_datom():
    return [
        {
            "e": "user-42",
            "a": "user/email",
            "v": "alice@example.com",
            "valid_from": _DEFAULT_VF,
        }
    ]


def _two_datoms():
    return [
        {
            "e": "user-42",
            "a": "user/email",
            "v": "alice@example.com",
            "valid_from": _DEFAULT_VF,
        },
        {
            "e": "user-42",
            "a": "user/name",
            "v": "Alice",
            "valid_from": _DEFAULT_VF,
        },
    ]


# ===========================================================================
# 1. Propose step (8)
# ===========================================================================
class TestEditPropose:
    @pytest.mark.asyncio
    async def test_propose_returns_requires_confirmation(
        self, server, clock_fixed
    ):
        session = _make_edit_session(server, clock_fixed)
        result = await edit_op(
            session,
            server.db,
            {"datoms": _one_datom()},
            server=server,
        )
        assert result["requires_confirmation"] is True
        assert "request_hash" in result
        assert "echo" in result

    @pytest.mark.asyncio
    async def test_propose_includes_preview_note(self, server, clock_fixed):
        session = _make_edit_session(server, clock_fixed)
        result = await edit_op(
            session,
            server.db,
            {"datoms": _one_datom()},
            server=server,
        )
        # Spec requires a non-empty preview_note explaining no
        # server-side preview was computed.
        assert "preview_note" in result
        assert isinstance(result["preview_note"], str)
        assert "preview" in result["preview_note"].lower()

    @pytest.mark.asyncio
    async def test_request_hash_deterministic_for_identical_datoms(
        self, server, clock_fixed
    ):
        session = _make_edit_session(server, clock_fixed)
        params = {"datoms": _one_datom()}
        r1 = await edit_op(session, server.db, params, server=server)
        r2 = await edit_op(session, server.db, dict(params), server=server)
        assert r1["request_hash"] == r2["request_hash"]

    @pytest.mark.asyncio
    async def test_request_hash_differs_for_different_datoms(
        self, server, clock_fixed
    ):
        session = _make_edit_session(server, clock_fixed)
        r1 = await edit_op(
            session, server.db, {"datoms": _one_datom()}, server=server
        )
        other = [
            {
                "e": "user-99",
                "a": "user/email",
                "v": "bob@example.com",
                "valid_from": _DEFAULT_VF,
            }
        ]
        r2 = await edit_op(
            session, server.db, {"datoms": other}, server=server
        )
        assert r1["request_hash"] != r2["request_hash"]

    @pytest.mark.asyncio
    async def test_request_hash_ignores_confirm_field(
        self, server, clock_fixed
    ):
        session = _make_edit_session(server, clock_fixed)
        r_no_confirm = await edit_op(
            session,
            server.db,
            {"datoms": _one_datom()},
            server=server,
        )
        r_confirm_false = await edit_op(
            session,
            server.db,
            {"datoms": _one_datom(), "confirm": False},
            server=server,
        )
        # Both propose-step responses; both hash over datoms only
        # (confirm field stripped). Must be equal.
        assert r_no_confirm["request_hash"] == r_confirm_false["request_hash"]

    @pytest.mark.asyncio
    async def test_request_hash_ignores_request_hash_field_itself(
        self, server, clock_fixed
    ):
        session = _make_edit_session(server, clock_fixed)
        r = await edit_op(
            session,
            server.db,
            {"datoms": _one_datom()},
            server=server,
        )
        # If we propose AGAIN with the previously-returned hash
        # carried in the params, the recomputed hash must be the
        # same (i.e., the field is stripped before hashing —
        # idempotent re-hash).
        r_with_hash = await edit_op(
            session,
            server.db,
            {"datoms": _one_datom(), "request_hash": r["request_hash"]},
            server=server,
        )
        assert r["request_hash"] == r_with_hash["request_hash"]

    @pytest.mark.asyncio
    async def test_propose_with_explicit_confirm_false_same_as_omitting(
        self, server, clock_fixed
    ):
        session = _make_edit_session(server, clock_fixed)
        r_omit = await edit_op(
            session, server.db, {"datoms": _one_datom()}, server=server
        )
        r_false = await edit_op(
            session,
            server.db,
            {"datoms": _one_datom(), "confirm": False},
            server=server,
        )
        assert r_omit["requires_confirmation"] is True
        assert r_false["requires_confirmation"] is True
        assert r_omit["request_hash"] == r_false["request_hash"]

    @pytest.mark.asyncio
    async def test_propose_echo_carries_datoms_verbatim(
        self, server, clock_fixed
    ):
        session = _make_edit_session(server, clock_fixed)
        datoms = _one_datom()
        result = await edit_op(
            session,
            server.db,
            {"datoms": datoms},
            server=server,
        )
        # echo field carries datoms list back verbatim.
        assert result["echo"]["datoms"] == datoms


# ===========================================================================
# 2. Confirm step happy path (5)
# ===========================================================================
class TestEditConfirmHappyPath:
    @pytest.mark.asyncio
    async def test_confirm_with_valid_hash_commits(self, server, clock_fixed):
        session = _make_edit_session(server, clock_fixed)
        datoms = _one_datom()
        # Propose to get the hash.
        proposed = await edit_op(
            session, server.db, {"datoms": datoms}, server=server
        )
        # Confirm.
        result = await edit_op(
            session,
            server.db,
            {
                "datoms": datoms,
                "confirm": True,
                "request_hash": proposed["request_hash"],
            },
            server=server,
        )
        assert result["committed"] is True

    @pytest.mark.asyncio
    async def test_confirm_response_shape(self, server, clock_fixed):
        session = _make_edit_session(server, clock_fixed)
        datoms = _one_datom()
        proposed = await edit_op(
            session, server.db, {"datoms": datoms}, server=server
        )
        result = await edit_op(
            session,
            server.db,
            {
                "datoms": datoms,
                "confirm": True,
                "request_hash": proposed["request_hash"],
            },
            server=server,
        )
        assert "tx_time_iso" in result
        assert "tx" in result
        assert "datom_count" in result
        assert result["datom_count"] == 1
        assert isinstance(result["tx"], int)

    @pytest.mark.asyncio
    async def test_datoms_actually_land_in_store(self, server, clock_fixed):
        session = _make_edit_session(server, clock_fixed)
        datoms = _one_datom()
        proposed = await edit_op(
            session, server.db, {"datoms": datoms}, server=server
        )
        await edit_op(
            session,
            server.db,
            {
                "datoms": datoms,
                "confirm": True,
                "request_hash": proposed["request_hash"],
            },
            server=server,
        )
        # Read back via the same path programmatic callers use.
        view = server.db.as_of(_DEFAULT_T)
        entity = view.entity("user-42")
        assert entity.get("user/email") == "alice@example.com"

    @pytest.mark.asyncio
    async def test_multi_datom_confirm_commits_all_in_one_tx(
        self, server, clock_fixed
    ):
        session = _make_edit_session(server, clock_fixed)
        datoms = _two_datoms()
        proposed = await edit_op(
            session, server.db, {"datoms": datoms}, server=server
        )
        result = await edit_op(
            session,
            server.db,
            {
                "datoms": datoms,
                "confirm": True,
                "request_hash": proposed["request_hash"],
            },
            server=server,
        )
        assert result["datom_count"] == 2
        # Both attributes land on the same entity.
        view = server.db.as_of(_DEFAULT_T)
        entity = view.entity("user-42")
        assert entity.get("user/email") == "alice@example.com"
        assert entity.get("user/name") == "Alice"

    @pytest.mark.asyncio
    async def test_confirm_does_not_mutate_session(self, server, clock_fixed):
        session = _make_edit_session(server, clock_fixed)
        datoms = _one_datom()
        cap_set_before = session.cap_set
        depth_before = session.parent_chain_depth
        cursor_before = session.view_cursor_tx_time_iso
        proposed = await edit_op(
            session, server.db, {"datoms": datoms}, server=server
        )
        await edit_op(
            session,
            server.db,
            {
                "datoms": datoms,
                "confirm": True,
                "request_hash": proposed["request_hash"],
            },
            server=server,
        )
        # Session in registry is unchanged: edit only mutates the
        # fact store, not the per-session state.
        registered = server._active_sessions[session.session_id]
        assert registered.cap_set == cap_set_before
        assert registered.parent_chain_depth == depth_before
        assert registered.view_cursor_tx_time_iso == cursor_before


# ===========================================================================
# 3. Confirm step rejection paths (10)
# ===========================================================================
class TestEditConfirmRejection:
    @pytest.mark.asyncio
    async def test_confirm_without_request_hash_rejects(
        self, server, clock_fixed
    ):
        session = _make_edit_session(server, clock_fixed)
        with pytest.raises(_OpError) as excinfo:
            await edit_op(
                session,
                server.db,
                {"datoms": _one_datom(), "confirm": True},
                server=server,
            )
        assert excinfo.value.code == ERR_REQUEST_HASH_MISMATCH

    @pytest.mark.asyncio
    async def test_confirm_with_wrong_request_hash_rejects(
        self, server, clock_fixed
    ):
        session = _make_edit_session(server, clock_fixed)
        with pytest.raises(_OpError) as excinfo:
            await edit_op(
                session,
                server.db,
                {
                    "datoms": _one_datom(),
                    "confirm": True,
                    "request_hash": "sha256:" + "0" * 64,
                },
                server=server,
            )
        assert excinfo.value.code == ERR_REQUEST_HASH_MISMATCH

    @pytest.mark.asyncio
    async def test_confirm_with_hash_from_different_datoms_rejects(
        self, server, clock_fixed
    ):
        session = _make_edit_session(server, clock_fixed)
        # Get a hash for one set of datoms.
        other_datoms = [
            {
                "e": "user-99",
                "a": "user/email",
                "v": "bob@example.com",
                "valid_from": _DEFAULT_VF,
            }
        ]
        proposed = await edit_op(
            session, server.db, {"datoms": other_datoms}, server=server
        )
        # Try to commit with DIFFERENT datoms but the OLD hash.
        with pytest.raises(_OpError) as excinfo:
            await edit_op(
                session,
                server.db,
                {
                    "datoms": _one_datom(),
                    "confirm": True,
                    "request_hash": proposed["request_hash"],
                },
                server=server,
            )
        assert excinfo.value.code == ERR_REQUEST_HASH_MISMATCH

    @pytest.mark.asyncio
    async def test_confirm_with_view_cursor_rejects_with_stale_cursor(
        self, server, clock_fixed
    ):
        # Set a non-None view cursor — operator must branch first.
        session = _make_edit_session(
            server, clock_fixed, cursor_iso=_dt(2025, 1, 1).isoformat()
        )
        # Compute the right hash so we exercise the cursor check
        # specifically (not a false positive on hash mismatch).
        params = {
            "datoms": _one_datom(),
            "confirm": True,
        }
        # Recompute expected hash same way the op does.
        expected = _compute_request_hash(params)
        params["request_hash"] = expected
        with pytest.raises(_OpError) as excinfo:
            await edit_op(session, server.db, params, server=server)
        assert excinfo.value.code == ERR_STALE_CURSOR_EDIT
        assert "branch" in excinfo.value.message.lower()

    @pytest.mark.asyncio
    async def test_confirm_with_propose_only_cap_rejects(
        self, server, clock_fixed
    ):
        session = _make_edit_session(
            server,
            clock_fixed,
            caps=frozenset({Capability("edit", "propose-only")}),
            token_id="token-id-propose1",
        )
        # Even with a valid hash, propose-only is not allowed to confirm.
        proposed = await edit_op(
            session, server.db, {"datoms": _one_datom()}, server=server
        )
        with pytest.raises(_OpError) as excinfo:
            await edit_op(
                session,
                server.db,
                {
                    "datoms": _one_datom(),
                    "confirm": True,
                    "request_hash": proposed["request_hash"],
                },
                server=server,
            )
        assert excinfo.value.code == ERR_CAPABILITY_DENIED
        assert "edit:write" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_confirm_with_no_edit_caps_rejects(
        self, server, clock_fixed
    ):
        session = _make_edit_session(
            server,
            clock_fixed,
            caps=frozenset(),
            token_id="token-id-nocaps1",
        )
        with pytest.raises(_OpError) as excinfo:
            await edit_op(
                session,
                server.db,
                {
                    "datoms": _one_datom(),
                    "confirm": True,
                    "request_hash": "sha256:" + "0" * 64,
                },
                server=server,
            )
        assert excinfo.value.code == ERR_CAPABILITY_DENIED

    @pytest.mark.asyncio
    async def test_confirm_with_empty_datoms_rejects(
        self, server, clock_fixed
    ):
        session = _make_edit_session(server, clock_fixed)
        with pytest.raises(_OpError) as excinfo:
            await edit_op(
                session,
                server.db,
                {"datoms": [], "confirm": True, "request_hash": "x"},
                server=server,
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_confirm_with_malformed_datom_rejects(
        self, server, clock_fixed
    ):
        session = _make_edit_session(server, clock_fixed)
        # Missing 'a' key.
        bad_datoms = [{"e": "user-42", "v": "alice@example.com"}]
        with pytest.raises(_OpError) as excinfo:
            await edit_op(
                session,
                server.db,
                {
                    "datoms": bad_datoms,
                    "confirm": True,
                    "request_hash": "x",
                },
                server=server,
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_confirm_with_non_list_datoms_rejects(
        self, server, clock_fixed
    ):
        session = _make_edit_session(server, clock_fixed)
        with pytest.raises(_OpError) as excinfo:
            await edit_op(
                session,
                server.db,
                {
                    "datoms": {"not": "a list"},
                    "confirm": True,
                    "request_hash": "x",
                },
                server=server,
            )
        assert excinfo.value.code == ERR_INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_confirm_after_rewind_rejects_stale_cursor(
        self, server, clock_fixed
    ):
        # Add rewind:any so the cursor can be set in the first place;
        # edit:write so the confirm path is reachable for the
        # cursor-check exercise.
        session = _make_edit_session(
            server,
            clock_fixed,
            caps=frozenset(
                {Capability("edit", "write"), Capability("rewind", "any")}
            ),
            token_id="token-id-rewindedit",
        )
        # Manually replicate rewind effect — set the cursor on the
        # session record without exercising rewind_op (which is
        # tested separately). The edit_op MUST honour
        # session.view_cursor_tx_time_iso regardless of how it got set.
        session = dataclasses.replace(
            session, view_cursor_tx_time_iso=_dt(2025, 1, 1).isoformat()
        )
        server._active_sessions[session.session_id] = session
        params = {
            "datoms": _one_datom(),
            "confirm": True,
        }
        params["request_hash"] = _compute_request_hash(params)
        with pytest.raises(_OpError) as excinfo:
            await edit_op(session, server.db, params, server=server)
        assert excinfo.value.code == ERR_STALE_CURSOR_EDIT


# ===========================================================================
# 4. Capability matrix (4)
# ===========================================================================
class TestEditCapabilityMatrix:
    @pytest.mark.asyncio
    async def test_propose_only_can_propose(self, server, clock_fixed):
        session = _make_edit_session(
            server,
            clock_fixed,
            caps=frozenset({Capability("edit", "propose-only")}),
            token_id="token-id-prop-only-prop",
        )
        result = await edit_op(
            session,
            server.db,
            {"datoms": _one_datom()},
            server=server,
        )
        assert result["requires_confirmation"] is True

    @pytest.mark.asyncio
    async def test_propose_only_cannot_confirm(self, server, clock_fixed):
        session = _make_edit_session(
            server,
            clock_fixed,
            caps=frozenset({Capability("edit", "propose-only")}),
            token_id="token-id-prop-only-rej",
        )
        proposed = await edit_op(
            session, server.db, {"datoms": _one_datom()}, server=server
        )
        with pytest.raises(_OpError) as excinfo:
            await edit_op(
                session,
                server.db,
                {
                    "datoms": _one_datom(),
                    "confirm": True,
                    "request_hash": proposed["request_hash"],
                },
                server=server,
            )
        assert excinfo.value.code == ERR_CAPABILITY_DENIED

    @pytest.mark.asyncio
    async def test_edit_write_allows_both_steps(self, server, clock_fixed):
        session = _make_edit_session(
            server,
            clock_fixed,
            caps=frozenset({Capability("edit", "write")}),
            token_id="token-id-write-both",
        )
        # Propose works.
        proposed = await edit_op(
            session, server.db, {"datoms": _one_datom()}, server=server
        )
        assert proposed["requires_confirmation"] is True
        # Confirm works.
        result = await edit_op(
            session,
            server.db,
            {
                "datoms": _one_datom(),
                "confirm": True,
                "request_hash": proposed["request_hash"],
            },
            server=server,
        )
        assert result["committed"] is True

    @pytest.mark.asyncio
    async def test_no_edit_caps_rejects_both_steps(self, server, clock_fixed):
        session = _make_edit_session(
            server,
            clock_fixed,
            caps=frozenset(),
            token_id="token-id-edit-nocaps",
        )
        # Propose rejected.
        with pytest.raises(_OpError) as excinfo:
            await edit_op(
                session,
                server.db,
                {"datoms": _one_datom()},
                server=server,
            )
        assert excinfo.value.code == ERR_CAPABILITY_DENIED
        # Confirm rejected.
        with pytest.raises(_OpError) as excinfo2:
            await edit_op(
                session,
                server.db,
                {
                    "datoms": _one_datom(),
                    "confirm": True,
                    "request_hash": "sha256:" + "0" * 64,
                },
                server=server,
            )
        assert excinfo2.value.code == ERR_CAPABILITY_DENIED


# ===========================================================================
# 5. request_hash semantics (3)
# ===========================================================================
class TestRequestHashSemantics:
    @pytest.mark.asyncio
    async def test_propose_then_confirm_round_trip(
        self, server, clock_fixed
    ):
        """Propose → copy hash → confirm: succeeds. The canonical
        operator workflow round-trip."""
        session = _make_edit_session(server, clock_fixed)
        datoms = _one_datom()
        proposed = await edit_op(
            session, server.db, {"datoms": datoms}, server=server
        )
        # Copy hash verbatim.
        confirmed = await edit_op(
            session,
            server.db,
            {
                "datoms": datoms,
                "confirm": True,
                "request_hash": proposed["request_hash"],
            },
            server=server,
        )
        assert confirmed["committed"] is True

    @pytest.mark.asyncio
    async def test_mutated_datom_with_original_hash_rejects(
        self, server, clock_fixed
    ):
        """Propose → mutate one value → confirm with original hash:
        rejects. This is exactly the fat-finger guard."""
        session = _make_edit_session(server, clock_fixed)
        datoms_v1 = _one_datom()
        proposed = await edit_op(
            session, server.db, {"datoms": datoms_v1}, server=server
        )
        # Mutate one value but keep the same hash.
        datoms_v2 = [
            {**datoms_v1[0], "v": "alice+typo@example.com"}
        ]
        with pytest.raises(_OpError) as excinfo:
            await edit_op(
                session,
                server.db,
                {
                    "datoms": datoms_v2,
                    "confirm": True,
                    "request_hash": proposed["request_hash"],
                },
                server=server,
            )
        assert excinfo.value.code == ERR_REQUEST_HASH_MISMATCH

    @pytest.mark.asyncio
    async def test_helper_consistency_externally_computable(
        self, server, clock_fixed
    ):
        """The hash returned by propose equals what
        ``_compute_request_hash`` (and ``canonical_hash`` directly)
        would compute over the same params-minus-confirm. This
        guarantees an operator can verify the hash locally."""
        session = _make_edit_session(server, clock_fixed)
        datoms = _one_datom()
        params = {"datoms": datoms}
        proposed = await edit_op(
            session, server.db, params, server=server
        )
        # _compute_request_hash on the SAME params dict.
        expected_via_helper = _compute_request_hash(params)
        # canonical_hash directly on the canonicalised params (no
        # confirm/request_hash to strip in this case).
        expected_via_direct = canonical_hash({"datoms": datoms})
        assert proposed["request_hash"] == expected_via_helper
        assert proposed["request_hash"] == expected_via_direct
