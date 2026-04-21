"""Test combinators: and, or, not, maybe, keys, map_of, seq_of, tuple_of,
enum, regex, ref."""
from __future__ import annotations

import pytest

from persistence import spec as S


class TestAnd:
    def test_and_accepts_if_all_pass(self):
        sp = S.and_(S.int_(), S.regex(r"^\d+$"))
        # but int and regex don't share a value shape; and_ is predicate-style —
        # real use is e.g. and_(int_(), predicate-that-checks-range).
        # Simpler test: and_ of two int variants both succeed.
        sp2 = S.and_(S.int_(), S.int_())
        assert sp2.conform(42).is_ok

    def test_and_rejects_if_any_fails(self):
        sp = S.and_(S.int_(), S.str_())
        assert not sp.conform(42).is_ok
        assert not sp.conform("x").is_ok

    def test_and_returns_conformed_value(self):
        sp = S.and_(S.int_(), S.int_())
        r = sp.conform(7)
        assert r.value == 7


class TestOr:
    def test_or_accepts_either(self):
        sp = S.or_(S.int_(), S.str_())
        assert sp.conform(42).is_ok
        assert sp.conform("x").is_ok

    def test_or_rejects_neither(self):
        sp = S.or_(S.int_(), S.str_())
        assert not sp.conform(3.14).is_ok

    def test_or_error_contains_sub_errors(self):
        sp = S.or_(S.int_(), S.str_())
        err = sp.conform(3.14)
        assert not err.is_ok
        assert len(err.sub_errors) == 2


class TestNot:
    def test_not_accepts_inverse(self):
        sp = S.not_(S.int_())
        assert sp.conform("x").is_ok
        assert sp.conform(3.14).is_ok

    def test_not_rejects_inner_match(self):
        sp = S.not_(S.int_())
        assert not sp.conform(42).is_ok


class TestMaybe:
    def test_maybe_accepts_none(self):
        sp = S.maybe(S.int_())
        assert sp.conform(None).is_ok

    def test_maybe_accepts_inner_value(self):
        sp = S.maybe(S.int_())
        assert sp.conform(42).is_ok

    def test_maybe_rejects_wrong_type(self):
        sp = S.maybe(S.int_())
        assert not sp.conform("x").is_ok


class TestKeys:
    def test_required_present(self):
        sp = S.keys(required={":a": S.int_(), ":b": S.str_()})
        r = sp.conform({":a": 1, ":b": "x"})
        assert r.is_ok
        assert r.value == {":a": 1, ":b": "x"}

    def test_required_missing(self):
        sp = S.keys(required={":a": S.int_()})
        err = sp.conform({})
        assert not err.is_ok
        assert ":a" in err.reason or any(":a" in (e.reason or "") for e in err.sub_errors)

    def test_optional_present_must_conform(self):
        sp = S.keys(required={}, optional={":b": S.int_()})
        assert sp.conform({}).is_ok
        assert sp.conform({":b": 7}).is_ok
        assert not sp.conform({":b": "x"}).is_ok

    def test_extra_keys_allowed(self):
        sp = S.keys(required={":a": S.int_()})
        r = sp.conform({":a": 1, ":extra": 99})
        assert r.is_ok  # extra keys are tolerated — Datomic-style open schema

    def test_rejects_non_dict(self):
        sp = S.keys(required={})
        assert not sp.conform(["not", "a", "map"]).is_ok

    def test_error_path_for_nested_failure(self):
        sp = S.keys(required={":a": S.int_()})
        err = sp.conform({":a": "bad"})
        # error path should include :a
        assert not err.is_ok
        # Either the top-level reason mentions :a, or a sub_error has path with :a
        paths = [e.path for e in err.sub_errors]
        assert any(":a" in p for p in paths) or ":a" in err.reason


class TestMapOf:
    def test_accepts_uniform_map(self):
        sp = S.map_of(S.str_(), S.int_())
        assert sp.conform({"a": 1, "b": 2}).is_ok

    def test_rejects_bad_key(self):
        sp = S.map_of(S.str_(), S.int_())
        assert not sp.conform({1: 1}).is_ok

    def test_rejects_bad_value(self):
        sp = S.map_of(S.str_(), S.int_())
        assert not sp.conform({"a": "bad"}).is_ok


class TestSeqOf:
    def test_accepts_list(self):
        sp = S.seq_of(S.int_())
        assert sp.conform([1, 2, 3]).is_ok

    def test_accepts_tuple(self):
        sp = S.seq_of(S.int_())
        assert sp.conform((1, 2, 3)).is_ok

    def test_rejects_bad_element(self):
        sp = S.seq_of(S.int_())
        assert not sp.conform([1, "bad", 3]).is_ok

    def test_min_length(self):
        sp = S.seq_of(S.int_(), min=2)
        assert not sp.conform([1]).is_ok
        assert sp.conform([1, 2]).is_ok

    def test_max_length(self):
        sp = S.seq_of(S.int_(), max=2)
        assert not sp.conform([1, 2, 3]).is_ok
        assert sp.conform([1, 2]).is_ok

    def test_rejects_str_even_though_iterable(self):
        # strings are iterable; must not be accepted as seq_of(str)
        sp = S.seq_of(S.str_())
        assert not sp.conform("abc").is_ok


class TestTupleOf:
    def test_accepts_fixed_tuple(self):
        sp = S.tuple_of(S.int_(), S.str_(), S.bool_())
        assert sp.conform((1, "x", True)).is_ok

    def test_rejects_wrong_length(self):
        sp = S.tuple_of(S.int_(), S.str_())
        assert not sp.conform((1,)).is_ok
        assert not sp.conform((1, "x", "extra")).is_ok

    def test_rejects_wrong_element_type(self):
        sp = S.tuple_of(S.int_(), S.str_())
        assert not sp.conform(("bad", "x")).is_ok

    def test_accepts_list_of_right_shape(self):
        sp = S.tuple_of(S.int_(), S.str_())
        assert sp.conform([1, "x"]).is_ok


class TestEnum:
    def test_accepts_member(self):
        sp = S.enum("a", "b", "c")
        assert sp.conform("a").is_ok

    def test_rejects_non_member(self):
        sp = S.enum("a", "b")
        assert not sp.conform("c").is_ok

    def test_enum_of_keywords(self):
        sp = S.enum(":assert", ":retract")
        assert sp.conform(":assert").is_ok
        assert not sp.conform(":foo").is_ok


class TestRegex:
    def test_accepts_match(self):
        sp = S.regex(r"^\d{3}$")
        assert sp.conform("123").is_ok

    def test_rejects_non_match(self):
        sp = S.regex(r"^\d{3}$")
        assert not sp.conform("12").is_ok

    def test_rejects_non_string(self):
        sp = S.regex(r".*")
        assert not sp.conform(123).is_ok


class TestRef:
    def test_ref_follows_registered_spec(self):
        S.register(":test.ref/int", S.int_())
        sp = S.ref(":test.ref/int")
        assert sp.conform(42).is_ok
        assert not sp.conform("x").is_ok

    def test_ref_errors_on_unknown_key(self):
        sp = S.ref(":nonexistent/key")
        err = sp.conform(42)
        assert not err.is_ok
        assert "nonexistent" in err.reason or "unknown" in err.reason.lower()


class TestGenerateOnCombinators:
    """Each combinator must produce a generator that round-trips."""

    def test_maybe_generates_valid(self):
        sp = S.maybe(S.int_())
        for _ in range(50):
            val = sp.generate()
            assert sp.conform(val).is_ok

    def test_keys_generates_valid(self):
        sp = S.keys(required={":a": S.int_(), ":b": S.str_()},
                    optional={":c": S.bool_()})
        for _ in range(20):
            val = sp.generate()
            assert sp.conform(val).is_ok

    def test_map_of_generates_valid(self):
        sp = S.map_of(S.str_(), S.int_())
        for _ in range(20):
            val = sp.generate()
            assert sp.conform(val).is_ok

    def test_seq_of_generates_valid(self):
        sp = S.seq_of(S.int_(), min=1, max=5)
        for _ in range(20):
            val = sp.generate()
            assert sp.conform(val).is_ok

    def test_tuple_of_generates_valid(self):
        sp = S.tuple_of(S.int_(), S.str_(), S.bool_())
        for _ in range(20):
            val = sp.generate()
            assert sp.conform(val).is_ok

    def test_or_generates_valid(self):
        sp = S.or_(S.int_(), S.str_())
        for _ in range(20):
            val = sp.generate()
            assert sp.conform(val).is_ok

    def test_enum_generates_member(self):
        sp = S.enum("a", "b", "c")
        for _ in range(20):
            val = sp.generate()
            assert val in ("a", "b", "c")
            assert sp.conform(val).is_ok

    def test_regex_generate_matches(self):
        sp = S.regex(r"^[a-z]{3,5}$")
        # note: regex generator is best-effort; we at least need a string that
        # matches in most cases.
        for _ in range(10):
            val = sp.generate()
            assert sp.conform(val).is_ok


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
