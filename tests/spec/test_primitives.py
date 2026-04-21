"""Test primitive scalar specs and the Conformed/ConformError contract."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest

from persistence import spec as S
from persistence.spec import ConformError, Conformed


class TestConformedContract:
    def test_conformed_unwrap_returns_value(self):
        c = Conformed(value=42, spec_key=":test/int")
        assert c.unwrap() == 42

    def test_conformed_is_ok_true(self):
        assert Conformed(value=42, spec_key=":x").is_ok is True

    def test_error_is_ok_false(self):
        err = ConformError(spec_key=":x", value=42, reason="bad")
        assert err.is_ok is False

    def test_conform_returns_conformed_on_success(self):
        result = S.int_().conform(42)
        assert isinstance(result, Conformed)
        assert result.value == 42

    def test_conform_returns_error_on_failure(self):
        result = S.int_().conform("nope")
        assert isinstance(result, ConformError)
        assert "int" in result.reason.lower()

    def test_conformed_and_error_are_distinct_types(self):
        # parse-don't-validate: downstream code must check the type,
        # not just truthiness
        ok = S.int_().conform(1)
        bad = S.int_().conform("x")
        assert type(ok) is not type(bad)


class TestScalarSpecs:
    def test_int_accepts_int(self):
        assert S.int_().conform(42).is_ok

    def test_int_rejects_str(self):
        assert not S.int_().conform("42").is_ok

    def test_int_rejects_bool(self):
        # bool is subclass of int in Python; spec must NOT silently accept it
        assert not S.int_().conform(True).is_ok

    def test_float_accepts_float(self):
        assert S.float_().conform(3.14).is_ok

    def test_float_rejects_int(self):
        # no silent coercion
        assert not S.float_().conform(42).is_ok

    def test_bool_accepts_bool(self):
        assert S.bool_().conform(True).is_ok
        assert S.bool_().conform(False).is_ok

    def test_bool_rejects_int_one(self):
        assert not S.bool_().conform(1).is_ok

    def test_str_accepts_str(self):
        assert S.str_().conform("hello").is_ok

    def test_str_rejects_bytes(self):
        assert not S.str_().conform(b"hello").is_ok

    def test_bytes_accepts_bytes(self):
        assert S.bytes_().conform(b"\\x00\\x01").is_ok

    def test_bytes_rejects_str(self):
        assert not S.bytes_().conform("str").is_ok

    def test_uuid_accepts_uuid_object(self):
        assert S.uuid_().conform(uuid.uuid4()).is_ok

    def test_uuid_accepts_uuid_string(self):
        s = str(uuid.uuid4())
        result = S.uuid_().conform(s)
        assert result.is_ok
        # conformed value is the canonical UUID object (refinement, not coercion:
        # conform narrows representation but does not change identity)
        assert isinstance(result.value, uuid.UUID)

    def test_uuid_rejects_random_string(self):
        assert not S.uuid_().conform("not-a-uuid").is_ok

    def test_inst_accepts_datetime(self):
        assert S.inst().conform(dt.datetime.now(dt.timezone.utc)).is_ok

    def test_inst_accepts_iso_string(self):
        result = S.inst().conform("2026-04-20T12:00:00+00:00")
        assert result.is_ok
        assert isinstance(result.value, dt.datetime)

    def test_inst_rejects_naive_datetime(self):
        # agents demand timezone-aware instants; naive is ambiguous and banned
        naive = dt.datetime(2026, 4, 20, 12, 0, 0)
        assert not S.inst().conform(naive).is_ok


class TestNoSilentCoercion:
    """Hard contract: conform never silently coerces."""

    def test_int_spec_does_not_coerce_string_to_int(self):
        assert not S.int_().conform("42").is_ok

    def test_float_spec_does_not_coerce_int_to_float(self):
        assert not S.float_().conform(1).is_ok

    def test_str_spec_does_not_stringify(self):
        assert not S.str_().conform(42).is_ok


class TestExplain:
    def test_explain_mentions_value_and_expected_type(self):
        explanation = S.int_().explain("not-an-int")
        assert "int" in explanation.lower()
        assert "not-an-int" in explanation or "str" in explanation

    def test_explain_on_valid_is_empty_or_none(self):
        # explain should be a no-op for valid values
        result = S.int_().explain(42)
        # allow either "" or a message saying it conforms
        assert result == "" or "ok" in result.lower() or "conform" in result.lower()


class TestGenerate:
    def test_int_generator_produces_int(self):
        val = S.int_().generate()
        assert isinstance(val, int) and not isinstance(val, bool)

    def test_str_generator_produces_str(self):
        val = S.str_().generate()
        assert isinstance(val, str)

    def test_uuid_generator_produces_uuid(self):
        val = S.uuid_().generate()
        assert isinstance(val, uuid.UUID)

    def test_inst_generator_produces_aware_datetime(self):
        val = S.inst().generate()
        assert isinstance(val, dt.datetime)
        assert val.tzinfo is not None

    def test_round_trip_generate_conform(self):
        """generate -> conform must always succeed for scalar specs."""
        for sp in [S.int_(), S.float_(), S.str_(), S.bytes_(),
                   S.bool_(), S.uuid_(), S.inst()]:
            val = sp.generate()
            result = sp.conform(val)
            assert result.is_ok, (
                f"generate/conform round trip failed for {sp!r}: "
                f"{val!r} -> {result}"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
