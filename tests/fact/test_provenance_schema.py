"""Phase D — Provenance TypedDict shape + back-compat with free-form dict."""
from __future__ import annotations

from persistence.fact import Provenance


def test_provenance_typeddict_exposed_from_persistence_fact():
    """Provenance is exported from persistence.fact (or a submodule)."""
    # TypedDict at runtime is a dict subclass via typing; both shapes accepted
    assert Provenance is not None


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
