"""A2 — `MetricRef` + metric registry.

The registry maps `(metric_id, version)` pairs to scalar-emitting
callables that consume an `ExecutionResult` plus the per-example
`expected` dict. The registry is the canonical identity of a metric
under design §5 rule (2): callers cannot pass raw callables into
`optimize()`, and the `(id, version)` pair becomes part of the
provenance hash so changes to the metric are explicit.

Tests pin the public API per
docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md §5 rule (2)
and docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md §2.A2.
"""
from __future__ import annotations

import pytest

from persistence.plan import (
    ExecutionResult,
    MetricRef,
    lookup_metric,
    register_metric,
    unregister_metric,
)
from persistence.plan._errors import MetricNotRegistered


# --- Helpers --------------------------------------------------------------- #


def _ok_result() -> ExecutionResult:
    """Trivial ExecutionResult fixture for registry round-trip tests."""
    return ExecutionResult(
        plan_id="0" * 32,
        status="ok",
        leaf_results=(),
        failure=None,
    )


def _make_metric(value: float):
    """Factory for closures returning a fixed scalar — pins identity."""

    def _metric(result: ExecutionResult, expected: dict) -> float:
        return value

    return _metric


# --- Public API surface ---------------------------------------------------- #


def test_metric_ref_is_namedtuple_with_id_and_version():
    """MetricRef ships as a NamedTuple keyed by (id, version)."""
    ref = MetricRef(id="exact-match", version="v1")
    assert ref.id == "exact-match"
    assert ref.version == "v1"
    # NamedTuple → tuple semantics: positional unpacking works
    rid, rver = ref
    assert (rid, rver) == ("exact-match", "v1")


# --- Edge case 1: register → lookup round-trip ---------------------------- #


def test_register_then_lookup_returns_same_callable():
    """Round-trip: registered fn is what lookup returns (identity, not copy)."""
    ref = MetricRef(id="round-trip", version="v1")
    fn = _make_metric(0.42)
    try:
        register_metric(ref, fn)
        retrieved = lookup_metric(ref)
        assert retrieved is fn
        # And the callable is invokable with the documented signature.
        assert retrieved(_ok_result(), {"any": "expected"}) == 0.42
    finally:
        unregister_metric(ref)


# --- Edge case 2: re-register without replace=True raises ValueError ----- #


def test_re_register_same_ref_without_replace_raises_value_error():
    """Collision protection: same (id, version) must not silently overwrite."""
    ref = MetricRef(id="collision", version="v1")
    register_metric(ref, _make_metric(1.0))
    try:
        with pytest.raises(ValueError, match="collision"):
            register_metric(ref, _make_metric(2.0))
        # Original fn still wins on lookup — failed register is no-op.
        assert lookup_metric(ref)(_ok_result(), {}) == 1.0
    finally:
        unregister_metric(ref)


# --- Edge case 3: re-register with replace=True returns new fn ----------- #


def test_re_register_with_replace_true_overwrites():
    """replace=True is the explicit override path (test fixtures use it)."""
    ref = MetricRef(id="replace-me", version="v1")
    register_metric(ref, _make_metric(1.0))
    try:
        register_metric(ref, _make_metric(2.0), replace=True)
        assert lookup_metric(ref)(_ok_result(), {}) == 2.0
    finally:
        unregister_metric(ref)


# --- Edge case 4: lookup on missing ref raises MetricNotRegistered ------- #


def test_lookup_missing_ref_raises_metric_not_registered():
    """Missing registration is a domain error, not KeyError leakage."""
    ref = MetricRef(id="never-registered", version="v1")
    # Defensive: ensure registry truly does not contain this key.
    with pytest.raises(MetricNotRegistered):
        lookup_metric(ref)


def test_metric_not_registered_subclasses_key_error():
    """Per _errors.py: MetricNotRegistered is a KeyError subclass."""
    ref = MetricRef(id="never-registered-2", version="v1")
    with pytest.raises(KeyError):
        lookup_metric(ref)


# --- Edge case 5: unregister then lookup raises MetricNotRegistered ------ #


def test_unregister_then_lookup_raises_metric_not_registered():
    """unregister() drops the entry; subsequent lookup must miss."""
    ref = MetricRef(id="ephemeral", version="v1")
    register_metric(ref, _make_metric(1.0))
    unregister_metric(ref)
    with pytest.raises(MetricNotRegistered):
        lookup_metric(ref)


# --- Edge case 6: unregister on missing raises MetricNotRegistered ------- #


def test_unregister_missing_ref_raises_metric_not_registered():
    """Symmetric with lookup miss: unregister of an unknown ref is a domain error."""
    ref = MetricRef(id="never-here", version="v1")
    with pytest.raises(MetricNotRegistered):
        unregister_metric(ref)


# --- Edge case 7: same id, different version → distinct entries ---------- #


def test_same_id_different_version_are_distinct_entries():
    """Versioning is part of the key. Bumping version is an explicit switch."""
    ref_v1 = MetricRef(id="exact-match", version="v1")
    ref_v2 = MetricRef(id="exact-match", version="v2")
    register_metric(ref_v1, _make_metric(1.0))
    register_metric(ref_v2, _make_metric(2.0))
    try:
        assert lookup_metric(ref_v1)(_ok_result(), {}) == 1.0
        assert lookup_metric(ref_v2)(_ok_result(), {}) == 2.0
        # Unregistering v1 leaves v2 intact.
        unregister_metric(ref_v1)
        assert lookup_metric(ref_v2)(_ok_result(), {}) == 2.0
    finally:
        # Defensive cleanup: ref_v1 may already be gone — match the
        # registry's miss semantics rather than swallowing.
        try:
            unregister_metric(ref_v1)
        except MetricNotRegistered:
            pass
        unregister_metric(ref_v2)
