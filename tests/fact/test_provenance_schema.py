"""Phase D — Provenance TypedDict shape + back-compat with free-form dict."""
from __future__ import annotations

from datetime import datetime, timezone

from persistence.effect.handlers.audit import AuditEntry, audit_entry_to_datom
from persistence.fact import Datom, Provenance
from persistence.fact.datom import provenance_from_dict
from persistence.fact.wire import (
    _provenance_from_wire,
    _provenance_to_wire,
    datom_to_wire,
    wire_to_datom,
)

#: Canonical example UUID (RFC 4122) for tests that construct a Datom and
#: invoke datom_to_wire — the wire boundary enforces a UUID-shaped entity id.
_TEST_ENTITY_UUID = "550e8400-e29b-41d4-a716-446655440000"


def test_provenance_typeddict_exposed_from_persistence_fact():
    """Provenance is exported from persistence.fact and is a dict at runtime."""
    # The import at module level already proves exposure; this assertion
    # pins the load-bearing TypedDict-is-a-dict-subclass property at runtime
    # so existing free-form callers remain valid (the docstring's claim).
    assert issubclass(Provenance, dict)


def test_provenance_typeddict_accepts_known_keys():
    """Construct a Provenance with all named keys; runtime accepts as dict."""
    p: Provenance = {
        "source": "test",
        "tx_time": "2026-04-25T00:00:00+00:00",
        "handler_id": "h-1",
        "canonical_call": "abc123",
        "parent_provenance_hash": "p-hash-1",
        "superseded_by_tx": 5,
        "extra": {"my_extra_key": "value"},
    }
    # TypedDict is a dict at runtime
    assert p["source"] == "test"
    assert p["extra"]["my_extra_key"] == "value"


def test_provenance_typeddict_total_false_allows_partial():
    """Provenance is total=False — instances may omit fields."""
    # Just source — valid
    p: Provenance = {"source": "test"}
    assert p == {"source": "test"}

    # Empty — valid for total=False
    empty: Provenance = {}
    assert empty == {}


def test_provenance_from_dict_lifts_known_keys():
    """from_dict lifts known keys to top level; unknown keys land in extra."""
    raw = {
        "source": "test",
        "handler_id": "h-1",
        "my_custom_key": 42,                # unknown
        "another_extra": "value",            # unknown
    }
    p = provenance_from_dict(raw)
    assert p["source"] == "test"
    assert p["handler_id"] == "h-1"
    assert "extra" in p
    assert p["extra"]["my_custom_key"] == 42
    assert p["extra"]["another_extra"] == "value"


def test_provenance_from_dict_preserves_existing_extra():
    """If raw already has 'extra', from_dict merges into it (not overwrites)."""
    raw = {
        "source": "test",
        "extra": {"pre_existing": "yes"},
        "uncategorized_top_level": "lifted",
    }
    p = provenance_from_dict(raw)
    assert p["source"] == "test"
    assert p["extra"]["pre_existing"] == "yes"
    assert p["extra"]["uncategorized_top_level"] == "lifted"


def test_provenance_from_dict_empty_input():
    """Empty dict → empty Provenance (no extra key needed)."""
    assert provenance_from_dict({}) == {}


def test_datom_accepts_typed_provenance_construction():
    """Datom can be constructed with a Provenance TypedDict in provenance arg."""
    ts = datetime(2026, 4, 25, tzinfo=timezone.utc)
    p: Provenance = {"source": "test", "handler_id": "h-1"}

    d = Datom(
        e="e-1", a="x", v=1,
        tx=1, tx_time=ts, valid_from=ts, valid_to=None, op="assert",
        provenance=p,
    )
    assert d.provenance["source"] == "test"
    assert d.provenance["handler_id"] == "h-1"


def test_provenance_wire_roundtrip_preserves_typed_shape():
    """Provenance survives wire-out / wire-in roundtrip without losing keys."""
    p = {
        "source": "test",
        "handler_id": "h-1",
        "extra": {"custom_field": 42},
    }
    wire = _provenance_to_wire(p)
    back = _provenance_from_wire(wire)
    # Every key on the way in is on the way out
    assert back.get("source") == "test"
    assert back.get("handler_id") == "h-1"
    # Custom field stays nested under "extra" — the wire roundtrip must not
    # hoist it to top-level (which would silently re-shape future Provenance).
    assert back.get("extra", {}).get("custom_field") == 42


def test_provenance_wire_roundtrip_preserves_canonical_hash():
    """A Datom hashes to the same id before and after wire roundtrip."""
    ts = datetime(2026, 4, 25, tzinfo=timezone.utc)
    d_before = Datom(
        e=_TEST_ENTITY_UUID, a="x", v=42,
        tx=1, tx_time=ts, valid_from=ts, valid_to=None, op="assert",
        provenance={"source": "test", "handler_id": "h-1", "extra": {"k": "v"}},
    )

    wire = datom_to_wire(d_before)
    d_after = wire_to_datom(wire)

    # Wire round-trip is the identity on canonical content
    assert d_before == d_after


def test_audit_provenance_carries_parent_provenance_hash_alias():
    """The audit handler's emitted datom carries both :prev-hash and
    parent_provenance_hash with the same value, so D2 typed Provenance
    readers find the chain pointer alongside legacy :prev-hash readers.
    """
    entry = AuditEntry(
        id="sha256:" + "a" * 64,
        prev_hash="parent-hash-abc",
        op=":raw",
        args_hash="arg-h",
        verdict="ok",
        latency_ms=0,
        recorded_at=1745539200.0,  # 2026-04-25 00:00:00 UTC as float
    )
    datom = audit_entry_to_datom(entry)
    prov = datom[":datom/provenance"]

    # Legacy reader (audit.verify_chain) keeps working
    assert prov.get(":prev-hash") == "parent-hash-abc"
    # Typed-Provenance reader (D2 readers, D5 causal_history) sees the same value
    assert prov.get("parent_provenance_hash") == "parent-hash-abc"
