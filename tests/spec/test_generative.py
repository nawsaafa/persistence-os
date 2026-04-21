"""Hypothesis-driven generative tests.

We use hypothesis to stress the spec contract at scale. These tests assert:
1. Every spec.generate() output is accepted by spec.conform().
2. quickcheck finds violations when the property is false.
3. quickcheck returns empty when the property follows from the spec.
"""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from persistence import spec as S
from persistence.spec._canonical import CANONICAL_SPECS


# ---------------------------------------------------------------------------
# hypothesis strategy: a registered spec key, any of our canonical 10
# ---------------------------------------------------------------------------

canonical_key = st.sampled_from(CANONICAL_SPECS)


@given(canonical_key)
@settings(max_examples=50, deadline=None)
def test_every_canonical_spec_round_trips(key):
    val = S.generate_example(key)
    result = S.conform(key, val)
    assert result.is_ok, (
        f"round-trip failed for {key}:\n"
        f"  {S.explain_for_llm(key, val)}"
    )


# ---------------------------------------------------------------------------
# hypothesis on primitives — no silent coercion, ever
# ---------------------------------------------------------------------------

@given(st.text())
def test_int_spec_never_accepts_string(s):
    assert not S.int_().conform(s).is_ok


@given(st.integers())
def test_int_spec_always_accepts_int(n):
    assert S.int_().conform(n).is_ok


@given(st.integers(min_value=0, max_value=1))
def test_int_spec_rejects_bool_even_though_python_subclasses(n):
    # only actual int; not True/False which are bool
    assert S.int_().conform(bool(n)).is_ok is False


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_float_spec_accepts_all_finite_floats(f):
    assert S.float_().conform(f).is_ok


# ---------------------------------------------------------------------------
# quickcheck harness
# ---------------------------------------------------------------------------

def test_quickcheck_int_always_is_int():
    S.register(":test.gen/int", S.int_())
    assert S.quickcheck(":test.gen/int", lambda v: isinstance(v, int), n=100) == []


def test_quickcheck_confidence_in_range():
    # Use the canonical domain/decision spec: every generated decision should
    # have confidence in [0, 1].
    failures = S.quickcheck(
        ":persistence.domain/decision",
        lambda d: 0.0 <= d[":confidence"] <= 1.0,
        n=50,
    )
    assert failures == []


def test_quickcheck_false_property_surfaces_counterexamples():
    # All confidences are < 10, so the property "confidence > 10" should fail on
    # every single example — we expect the failure list to contain ~n entries.
    failures = S.quickcheck(
        ":persistence.domain/decision",
        lambda d: d[":confidence"] > 10.0,
        n=30,
    )
    assert len(failures) == 30


def test_quickcheck_on_unknown_key_raises():
    with pytest.raises(KeyError):
        S.quickcheck(":nonexistent/key", lambda v: True, n=5)


# ---------------------------------------------------------------------------
# Spec-as-value: equality and hashing
# ---------------------------------------------------------------------------

def test_two_int_specs_are_equal():
    assert S.int_() == S.int_()


def test_two_enums_equal_iff_same_members():
    assert S.enum("a", "b") == S.enum("a", "b")
    assert S.enum("a", "b") != S.enum("a", "c")


def test_two_keys_specs_equal_iff_same_shape():
    a = S.keys(required={":x": S.int_()})
    b = S.keys(required={":x": S.int_()})
    c = S.keys(required={":y": S.int_()})
    assert a == b
    assert a != c


def test_specs_are_hashable():
    assert len({S.int_(), S.int_()}) == 1
    assert len({S.enum("a"), S.enum("b")}) == 2
