"""Canonical JSON encoding tests — sorted keys, stable repr, deterministic hash."""
import hashlib

from persistence.effect.canonical import canonical_dumps, canonical_hash


def test_sorts_keys_for_stable_repr():
    """Dict key ordering must not affect output."""
    a = {"b": 1, "a": 2, "c": {"z": 1, "a": 2}}
    b = {"c": {"a": 2, "z": 1}, "a": 2, "b": 1}
    assert canonical_dumps(a) == canonical_dumps(b)


def test_canonical_hash_is_sha256_hex_prefix():
    """canonical_hash returns 'sha256:<64-hex>'."""
    h = canonical_hash({"op": "llm/call", "args": {"model": "x"}})
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64


def test_different_values_hash_differently():
    assert canonical_hash({"x": 1}) != canonical_hash({"x": 2})


def test_handles_nested_lists_and_primitives():
    val = {"list": [1, "two", None, True, 3.14], "nested": {"k": [1, 2]}}
    out = canonical_dumps(val)
    # round-trip is not required but must be stable
    assert canonical_dumps(val) == out


def test_rejects_non_jsonable_objects():
    """Canonical encoder must refuse things it cannot round-trip (e.g. sets, bytes)."""
    import pytest as _pt

    with _pt.raises(TypeError):
        canonical_dumps({"s": {1, 2, 3}})


def test_hash_matches_manual_sha256():
    """canonical_hash must equal sha256 of the canonical bytes."""
    obj = {"z": 1, "a": [2, 3]}
    expected = "sha256:" + hashlib.sha256(canonical_dumps(obj).encode("utf-8")).hexdigest()
    assert canonical_hash(obj) == expected
