"""Tests for ``persistence.repl._caps`` and ``_session`` (D1).

35 tests across 6 groups:

- Capability validation                          (5)
- CapabilitySet semantics + canonical-JSON       (8 incl. 1 Hypothesis)
- Token + _token_id                              (5)
- store_token + validate_token round-trip        (8)
- make_session + deterministic session_id        (5)
- list_tokens                                    (4)

See design doc ``docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md``
sections ADR-3, 6.1-6.4, and 10 (D1).
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from persistence.effect.canonical import canonical_dumps
from persistence.fact import DB, InMemoryStore
from persistence.repl import (
    ALL_CAPS,
    Capability,
    CapabilitySet,
    QUALIFIERS_BY_OP,
    Token,
    UnknownCapability,
    _derive_session_id,
    _token_id,
    list_tokens,
    make_session,
    mint_token,
    revoke_token,
    store_token,
    validate_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _dt(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def _fixed_clock(t: datetime):
    """Return a callable that always returns ``t`` (deterministic)."""
    def clock() -> datetime:
        return t
    return clock


@pytest.fixture
def db() -> DB:
    """Fresh in-memory DB per test (no fixture cross-talk)."""
    return DB(InMemoryStore())


@pytest.fixture
def clock_fixed():
    """Default fixed clock at 2026-05-09 12:00:00 UTC."""
    return _fixed_clock(_dt(2099, 1, 1, 12, 0, 0))


# All capabilities for set-construction in tests.
ALL_CAP_OBJECTS: frozenset[Capability] = frozenset(
    Capability(op, q) for op, q in ALL_CAPS
)


# ===========================================================================
# 1. Capability validation (5)
# ===========================================================================
class TestCapability:
    def test_valid_pairs_construct(self):
        # Every (op, qualifier) in the closed set constructs without raising.
        for op, qualifiers in QUALIFIERS_BY_OP.items():
            for q in qualifiers:
                c = Capability(op, q)
                assert c.op == op
                assert c.qualifier == q

    def test_unknown_op_raises(self):
        with pytest.raises(UnknownCapability):
            Capability("not-an-op", "read")

    def test_unknown_qualifier_raises(self):
        # Known op, unknown qualifier
        with pytest.raises(UnknownCapability):
            Capability("inspect", "not-a-qualifier")

    def test_capability_is_frozen(self):
        c = Capability("inspect", "read")
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.op = "edit"  # type: ignore[misc]

    def test_capability_equality_and_hash(self):
        c1 = Capability("inspect", "read")
        c2 = Capability("inspect", "read")
        c3 = Capability("edit", "write")
        assert c1 == c2
        assert hash(c1) == hash(c2)
        assert c1 != c3
        # frozenset membership uses hash+eq
        s = frozenset({c1})
        assert c2 in s
        assert c3 not in s


# ===========================================================================
# 2. CapabilitySet semantics + canonical-JSON (8)
# ===========================================================================
class TestCapabilitySet:
    def test_has_returns_true_on_contained(self):
        cs = CapabilitySet(
            caps=frozenset({Capability("inspect", "read")}),
        )
        assert cs.has("inspect", "read") is True

    def test_has_returns_false_on_missing(self):
        cs = CapabilitySet(
            caps=frozenset({Capability("inspect", "read")}),
        )
        assert cs.has("edit", "write") is False

    def test_is_expired_true_when_now_past_expires(self):
        cs = CapabilitySet(
            caps=frozenset(),
            expires_at=_dt(2026, 1, 1),
        )
        assert cs.is_expired(_dt(2026, 6, 1)) is True

    def test_is_expired_false_when_now_before_expires(self):
        cs = CapabilitySet(
            caps=frozenset(),
            expires_at=_dt(2026, 12, 1),
        )
        assert cs.is_expired(_dt(2026, 6, 1)) is False

    def test_is_expired_false_when_no_expiry(self):
        cs = CapabilitySet(caps=frozenset(), expires_at=None)
        assert cs.is_expired(_dt(3000, 1, 1)) is False

    def test_canonical_round_trip_with_expiry(self):
        cs = CapabilitySet(
            caps=frozenset({
                Capability("inspect", "read"),
                Capability("edit", "write"),
            }),
            expires_at=_dt(2026, 12, 31),
            label="ops",
        )
        encoded = canonical_dumps(cs.to_canonical())
        cs2 = CapabilitySet.from_canonical_dict(json.loads(encoded))
        assert cs == cs2

    def test_canonical_round_trip_with_no_expiry(self):
        cs = CapabilitySet(
            caps=frozenset({Capability("inspect", "read")}),
            expires_at=None,
            label="forever-token",
        )
        encoded = canonical_dumps(cs.to_canonical())
        cs2 = CapabilitySet.from_canonical_dict(json.loads(encoded))
        assert cs == cs2

    @given(
        caps=st.frozensets(st.sampled_from(sorted(ALL_CAP_OBJECTS, key=lambda c: (c.op, c.qualifier)))),
        label=st.text(max_size=64),
        expires_at=st.one_of(
            st.none(),
            st.datetimes(
                min_value=datetime(2020, 1, 1),
                max_value=datetime(2099, 12, 31),
                timezones=st.just(timezone.utc),
            ),
        ),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_canonical_json_property_round_trip(
        self,
        caps: frozenset[Capability],
        label: str,
        expires_at: datetime | None,
    ):
        cs = CapabilitySet(caps=caps, expires_at=expires_at, label=label)
        encoded = canonical_dumps(cs.to_canonical())
        cs2 = CapabilitySet.from_canonical_dict(json.loads(encoded))
        assert cs == cs2
        # And canonical_dumps is byte-stable for equal sets:
        assert canonical_dumps(cs.to_canonical()) == canonical_dumps(
            cs2.to_canonical()
        )


# ===========================================================================
# 3. Token + _token_id (5)
# ===========================================================================
class TestToken:
    def test_token_id_deterministic(self):
        s = "persistence.repl/abcdef"
        assert _token_id(s) == _token_id(s)

    def test_token_id_is_16_hex(self):
        tid = _token_id("persistence.repl/anything")
        assert len(tid) == 16
        # Every char is a lowercase hex digit
        assert all(c in "0123456789abcdef" for c in tid)

    def test_mint_token_produces_unique_token_str(self):
        # 256 bits of entropy → collision probability negligible across
        # any practical sample. 100 mints, no duplicates.
        seen: set[str] = set()
        for _ in range(100):
            t = mint_token(caps=frozenset())
            assert t.token_str not in seen
            seen.add(t.token_str)

    def test_mint_token_id_matches_token_id_helper(self):
        t = mint_token(
            caps=frozenset({Capability("inspect", "read")}),
        )
        assert t.token_id == _token_id(t.token_str)

    def test_mint_token_format_prefix(self):
        t = mint_token(caps=frozenset())
        assert t.token_str.startswith("persistence.repl/")
        # The base64url body after the slash:
        body = t.token_str.removeprefix("persistence.repl/")
        assert len(body) > 0
        # secrets.token_urlsafe(32) yields ~43 base64url chars
        assert len(body) >= 40


# ===========================================================================
# 4. store_token + validate_token round-trip (8)
# ===========================================================================
class TestStoreAndValidate:
    def test_validate_after_store_returns_cap_set(self, db, clock_fixed):
        t = mint_token(
            caps=frozenset({Capability("inspect", "read")}),
            expires_at=_dt(2100, 1, 1),
            label="ops",
        )
        store_token(db, t, runtime_clock=clock_fixed)
        cs = validate_token(db, t.token_str, runtime_clock=clock_fixed)
        assert cs is not None
        assert cs.has("inspect", "read")
        assert cs.label == "ops"
        assert cs.expires_at == _dt(2100, 1, 1)

    def test_validate_unknown_token_returns_none(self, db, clock_fixed):
        cs = validate_token(
            db,
            "persistence.repl/never-issued-xxxxxxxxxxxxxxxxxxxxxxxx",
            runtime_clock=clock_fixed,
        )
        assert cs is None

    def test_validate_revoked_token_returns_none(self, db, clock_fixed):
        t = mint_token(caps=frozenset({Capability("inspect", "read")}))
        store_token(db, t, runtime_clock=clock_fixed)
        revoke_token(db, t.token_id, runtime_clock=clock_fixed)
        cs = validate_token(db, t.token_str, runtime_clock=clock_fixed)
        assert cs is None

    def test_validate_expired_token_returns_none(self, db):
        # Issue at T0, validate at T1 where T1 > expires.
        t0 = _dt(2026, 1, 1)
        t1 = _dt(2027, 6, 1)
        expires = _dt(2026, 6, 1)
        t = mint_token(
            caps=frozenset({Capability("inspect", "read")}),
            expires_at=expires,
        )
        store_token(db, t, runtime_clock=_fixed_clock(t0))
        cs = validate_token(db, t.token_str, runtime_clock=_fixed_clock(t1))
        assert cs is None

    def test_validate_at_exact_expires_at_returns_none(self, db):
        # Boundary: now == expires_at is already expired (inclusive).
        t0 = _dt(2026, 1, 1)
        expires = _dt(2026, 6, 1)
        t = mint_token(
            caps=frozenset({Capability("inspect", "read")}),
            expires_at=expires,
        )
        store_token(db, t, runtime_clock=_fixed_clock(t0))
        cs = validate_token(db, t.token_str, runtime_clock=_fixed_clock(expires))
        assert cs is None

    def test_validate_just_before_expires_at_returns_cap_set(self, db):
        t0 = _dt(2026, 1, 1)
        expires = _dt(2026, 6, 1)
        # 1 second before expires
        just_before = expires - timedelta(seconds=1)
        t = mint_token(
            caps=frozenset({Capability("inspect", "read")}),
            expires_at=expires,
        )
        store_token(db, t, runtime_clock=_fixed_clock(t0))
        cs = validate_token(
            db, t.token_str, runtime_clock=_fixed_clock(just_before)
        )
        assert cs is not None
        assert cs.has("inspect", "read")

    def test_validate_after_revoke_even_if_cap_set_valid(self, db, clock_fixed):
        # A token with a future expiry is still rejected after revocation.
        t = mint_token(
            caps=frozenset({Capability("inspect", "read")}),
            expires_at=_dt(3000, 1, 1),
        )
        store_token(db, t, runtime_clock=clock_fixed)
        # Confirm valid pre-revoke
        assert validate_token(db, t.token_str, runtime_clock=clock_fixed) is not None
        revoke_token(db, t.token_id, runtime_clock=clock_fixed)
        cs = validate_token(db, t.token_str, runtime_clock=clock_fixed)
        assert cs is None

    def test_idempotent_revoke_does_not_raise(self, db, clock_fixed):
        t = mint_token(caps=frozenset({Capability("inspect", "read")}))
        store_token(db, t, runtime_clock=clock_fixed)
        # First revoke
        revoke_token(db, t.token_id, runtime_clock=clock_fixed)
        # Second revoke at a later clock (cardinality-one auto-retract
        # path through DB.transact would fail for retroactive corrections;
        # advancing the clock keeps valid_from monotonic).
        later_clock = _fixed_clock(_dt(2099, 1, 1, 13, 0, 0))
        revoke_token(db, t.token_id, runtime_clock=later_clock)
        # And validate still rejects.
        cs = validate_token(db, t.token_str, runtime_clock=later_clock)
        assert cs is None


# ===========================================================================
# 5. make_session + deterministic session_id (5)
# ===========================================================================
class TestSession:
    def test_session_id_deterministic_in_inputs(self):
        # Same (token_id, auth_clock_iso) → same session_id.
        sid1 = _derive_session_id("a1b2c3d4e5f67890", "2026-05-09T12:00:00+00:00")
        sid2 = _derive_session_id("a1b2c3d4e5f67890", "2026-05-09T12:00:00+00:00")
        assert sid1 == sid2
        assert len(sid1) == 16

    def test_session_id_deterministic_under_replay_clock(self):
        # The W1.E pin: replay clock at T → make_session twice → same
        # session_id (NOT uuid4, NOT wall-sampled).
        cs = CapabilitySet(
            caps=frozenset({Capability("inspect", "read")}),
        )
        clock = _fixed_clock(_dt(2099, 1, 1, 12, 0, 0))
        s1 = make_session("token-id-xx-1234", cs, runtime_clock=clock)
        s2 = make_session("token-id-xx-1234", cs, runtime_clock=clock)
        assert s1.session_id == s2.session_id
        assert s1.auth_clock_iso == s2.auth_clock_iso

    def test_session_clock_returns_datetime(self):
        cs = CapabilitySet(caps=frozenset())
        t = _dt(2099, 1, 1, 12, 0, 0)
        clock = _fixed_clock(t)
        s = make_session("token-id-xx-5678", cs, runtime_clock=clock)
        assert callable(s.clock)
        assert s.clock() == t
        assert isinstance(s.clock(), datetime)

    def test_session_parent_chain_depth_defaults_zero(self):
        cs = CapabilitySet(caps=frozenset())
        clock = _fixed_clock(_dt(2099, 1, 1, 12, 0, 0))
        s = make_session("token-id-xx-aaaa", cs, runtime_clock=clock)
        assert s.parent_chain_depth == 0

    def test_session_view_cursors_default_none(self):
        cs = CapabilitySet(caps=frozenset())
        clock = _fixed_clock(_dt(2099, 1, 1, 12, 0, 0))
        s = make_session("token-id-xx-bbbb", cs, runtime_clock=clock)
        assert s.view_cursor_tx_time_iso is None
        assert s.view_cursor_vt_iso is None


# ===========================================================================
# 6. list_tokens (4)
# ===========================================================================
class TestListTokens:
    def test_empty_store_returns_empty_list(self, db, clock_fixed):
        rows = list_tokens(db, runtime_clock=clock_fixed)
        assert rows == []

    def test_single_token_visible_after_store(self, db, clock_fixed):
        t = mint_token(
            caps=frozenset({Capability("inspect", "read")}),
            expires_at=_dt(2100, 1, 1),
            label="ops",
        )
        store_token(db, t, runtime_clock=clock_fixed)
        rows = list_tokens(db, runtime_clock=clock_fixed)
        assert len(rows) == 1
        row = rows[0]
        assert row["token_id"] == t.token_id
        assert row["label"] == "ops"
        assert row["expires_at_iso"] == _dt(2100, 1, 1).isoformat()
        assert "inspect:read" in row["caps_summary"]

    def test_revoked_token_not_in_list_output(self, db, clock_fixed):
        t = mint_token(caps=frozenset({Capability("inspect", "read")}))
        store_token(db, t, runtime_clock=clock_fixed)
        revoke_token(db, t.token_id, runtime_clock=clock_fixed)
        rows = list_tokens(db, runtime_clock=clock_fixed)
        assert rows == []

    def test_expired_token_still_in_list_output(self, db):
        # Expiry is a validation concern, not a listing concern. The
        # caller can spot past-expiry rows by inspecting expires_at_iso.
        t0 = _dt(2026, 1, 1)
        expires = _dt(2026, 6, 1)
        now_clock = _fixed_clock(_dt(2027, 1, 1))  # past the expiry
        t = mint_token(
            caps=frozenset({Capability("inspect", "read")}),
            expires_at=expires,
        )
        store_token(db, t, runtime_clock=_fixed_clock(t0))
        rows = list_tokens(db, runtime_clock=now_clock)
        assert len(rows) == 1
        assert rows[0]["expires_at_iso"] == expires.isoformat()
