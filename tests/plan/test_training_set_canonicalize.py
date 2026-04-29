"""A2 — `_canonicalize_training_set` deterministic SHA-256.

The canonicalizer turns a list of `TrainingExample` dicts into a stable
hex digest. Stability is the reproducibility hook for paper §6 numeric
tables: same inputs → same hash → same provenance, regardless of run,
machine, or example ordering. Per design §5 rule (1), only canonical-JSON
bytes feed sha256 — no Python-callable hashing, no float drift.

Tests pin the canonicalizer's contract per
docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md §5 rule (1)
and docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md §2.A2.
"""
from __future__ import annotations

import hashlib

import pytest

from persistence.plan import TrainingExample
from persistence.plan._execute import _canonicalize_training_set

# Pinned hex of the empty-byte sha256. Matches `hashlib.sha256(b"").hexdigest()`.
_EMPTY_SHA256_HEX = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


# --- Edge case 1: empty training set returns the pinned empty-bytes hash - #


def test_empty_training_set_returns_known_fixed_hash():
    """Empty list → sha256 of `b""`. Pinned across runs and machines.

    Rationale: this is the cheapest deterministic anchor for the
    canonicalizer's correctness contract. If the implementation ever
    starts seeding with a non-empty prefix, this test catches it.
    """
    digest = _canonicalize_training_set([])
    assert digest == _EMPTY_SHA256_HEX
    # Defensive: matches Python's stdlib answer too.
    assert digest == hashlib.sha256(b"").hexdigest()


# --- Edge case 2: single example deterministic across calls -------------- #


def test_single_example_deterministic_repeat_call():
    """Two calls on the same input must produce byte-identical hashes."""
    examples: list[TrainingExample] = [
        {"inputs": {"q": "what is 2+2?"}, "expected": 4},
    ]
    h1 = _canonicalize_training_set(examples)
    h2 = _canonicalize_training_set(examples)
    assert h1 == h2
    # And it's a 64-char (256-bit) lowercase hex string.
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)


# --- Edge case 3: reordering same examples → same hash ------------------ #


def test_reorder_examples_same_hash():
    """Examples are sorted internally, so caller ordering must not matter."""
    a: TrainingExample = {"inputs": {"q": "alpha"}, "expected": "A"}
    b: TrainingExample = {"inputs": {"q": "beta"}, "expected": "B"}
    c: TrainingExample = {"inputs": {"q": "gamma"}, "expected": "C"}
    h_abc = _canonicalize_training_set([a, b, c])
    h_cba = _canonicalize_training_set([c, b, a])
    h_bac = _canonicalize_training_set([b, a, c])
    assert h_abc == h_cba == h_bac


# --- Edge case 4: dict-key ordering inside `inputs` doesn't matter ------ #


def test_inputs_dict_key_order_does_not_affect_hash():
    """Canonical-JSON sorts keys, so {a:1,b:2} == {b:2,a:1} for hashing."""
    ex1: TrainingExample = {"inputs": {"a": 1, "b": 2}, "expected": "ok"}
    ex2: TrainingExample = {"inputs": {"b": 2, "a": 1}, "expected": "ok"}
    assert _canonicalize_training_set([ex1]) == _canonicalize_training_set([ex2])


# --- Edge case 5: extra key in example dict → ValueError ---------------- #


def test_extra_key_in_example_raises_value_error_with_index():
    """Schema is closed: only `inputs` + `expected`. Extras must blow up."""
    bad: dict = {"inputs": {"q": "x"}, "expected": "y", "extra": 1}
    with pytest.raises(ValueError, match=r"index 0"):
        _canonicalize_training_set([bad])  # type: ignore[list-item]


def test_extra_key_at_index_2_reports_correct_index():
    """Error message must point to the offending example position."""
    good: TrainingExample = {"inputs": {"q": "ok"}, "expected": "ok"}
    bad: dict = {"inputs": {"q": "x"}, "expected": "y", "metadata": "drop"}
    with pytest.raises(ValueError, match=r"index 2"):
        _canonicalize_training_set([good, good, bad])  # type: ignore[list-item]


# --- Edge case 6: `inputs` not a dict → ValueError ---------------------- #


def test_inputs_must_be_dict():
    """`inputs` carries the model-facing kwargs; lists/strings are not valid."""
    bad: dict = {"inputs": [1, 2, 3], "expected": "ok"}
    with pytest.raises(ValueError, match=r"inputs"):
        _canonicalize_training_set([bad])  # type: ignore[list-item]


def test_inputs_string_is_invalid():
    """Defensive: strings would JSON-serialize but the schema requires dict."""
    bad: dict = {"inputs": "raw text", "expected": "ok"}
    with pytest.raises(ValueError, match=r"inputs"):
        _canonicalize_training_set([bad])  # type: ignore[list-item]


# --- Edge case 7: missing required keys → ValueError -------------------- #


def test_missing_inputs_raises_value_error():
    """Both keys are required — drop one and it must fail loudly."""
    bad: dict = {"expected": "ok"}
    with pytest.raises(ValueError, match=r"inputs"):
        _canonicalize_training_set([bad])  # type: ignore[list-item]


def test_missing_expected_raises_value_error():
    """Symmetric: `expected` is required even when value is None."""
    bad: dict = {"inputs": {"q": "x"}}
    with pytest.raises(ValueError, match=r"expected"):
        _canonicalize_training_set([bad])  # type: ignore[list-item]


def test_expected_none_is_valid():
    """`expected=None` is legal — the schema only requires the KEY to exist."""
    ex: TrainingExample = {"inputs": {"q": "x"}, "expected": None}
    # Must not raise; result is a deterministic hex string.
    h = _canonicalize_training_set([ex])
    assert len(h) == 64


# --- Edge case 8: NaN / Inf in inputs → ValueError ---------------------- #


def test_nan_in_inputs_raises_value_error():
    """allow_nan=False on json.dumps propagates — content-addressing
    requires reflexive equality, which NaN breaks (NaN != NaN)."""
    bad: TrainingExample = {"inputs": {"score": float("nan")}, "expected": 1}
    with pytest.raises(ValueError):
        _canonicalize_training_set([bad])


def test_positive_infinity_in_inputs_raises_value_error():
    """Inf cannot round-trip through strict JSON."""
    bad: TrainingExample = {"inputs": {"score": float("inf")}, "expected": 1}
    with pytest.raises(ValueError):
        _canonicalize_training_set([bad])


def test_negative_infinity_in_inputs_raises_value_error():
    """Symmetric with +Inf — strict JSON rejects either pole."""
    bad: TrainingExample = {"inputs": {"score": float("-inf")}, "expected": 1}
    with pytest.raises(ValueError):
        _canonicalize_training_set([bad])


def test_tuple_and_list_inside_inputs_canonicalize_identically():
    """json.dumps converts tuple to list, so {"a":(1,2)} and {"a":[1,2]}
    canonicalize to the same bytes. Pin this to catch any future
    refactor that adds tuple-vs-list normalization upstream."""
    a: TrainingExample = {"inputs": {"a": (1, 2, 3)}, "expected": 1}
    b: TrainingExample = {"inputs": {"a": [1, 2, 3]}, "expected": 1}
    assert _canonicalize_training_set([a]) == _canonicalize_training_set([b])


def test_non_json_serializable_inputs_raises_value_error():
    """Non-JSON types (e.g. set) raise TypeError from json.dumps; we
    wrap and re-raise as ValueError uniformly so callers match one class."""
    bad: TrainingExample = {"inputs": {"a": {1, 2, 3}}, "expected": 1}  # set, not list
    with pytest.raises(ValueError, match="index 0"):
        _canonicalize_training_set([bad])
