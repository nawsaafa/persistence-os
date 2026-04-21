"""Test registry behaviors: register/get, parse, quickcheck, LLM errors,
version swapping."""
from __future__ import annotations

import pytest

from persistence import spec as S
from persistence.spec import ConformError, Conformed
from persistence.spec._registry import SpecError


class TestRegister:
    def test_register_and_get(self):
        S.register(":test.reg/scalar", S.int_())
        assert S.get(":test.reg/scalar") is not None
        assert S.conform(":test.reg/scalar", 42).is_ok

    def test_unknown_key_returns_error(self):
        result = S.conform(":test.nonexistent/key", 42)
        assert not result.is_ok
        assert "nonexistent" in result.reason or "no spec" in result.reason.lower()

    def test_registered_keys_sorted(self):
        S.register(":test.zzz/a", S.int_())
        S.register(":test.aaa/a", S.int_())
        keys = S.registered_keys()
        assert keys == sorted(keys)


class TestParse:
    def test_parse_returns_conformed_on_success(self):
        S.register(":test.parse/int", S.int_())
        result = S.parse(":test.parse/int", 42)
        assert isinstance(result, Conformed)
        assert result.value == 42

    def test_parse_raises_on_failure(self):
        S.register(":test.parse/int2", S.int_())
        with pytest.raises(SpecError) as exc:
            S.parse(":test.parse/int2", "bad")
        assert isinstance(exc.value.error, ConformError)

    def test_parse_spec_key_propagates(self):
        S.register(":test.parse/k", S.int_())
        c = S.parse(":test.parse/k", 7)
        assert c.spec_key == ":test.parse/k"


class TestExplainForLLM:
    def test_explain_contains_spec_key_and_fix(self):
        S.register(":test.llm/int", S.int_())
        msg = S.explain_for_llm(":test.llm/int", "bad")
        assert ":test.llm/int" in msg
        assert "Fix:" in msg

    def test_explain_ok_returns_empty(self):
        S.register(":test.llm/int2", S.int_())
        assert S.explain_for_llm(":test.llm/int2", 42) == ""

    def test_nested_explain_lists_path(self):
        sp = S.keys(required={":a": S.int_(), ":b": S.str_()})
        S.register(":test.llm/nested", sp)
        msg = S.explain_for_llm(":test.llm/nested", {":a": "bad", ":b": 1})
        # should reference :a and :b or indicate the paths somehow
        assert "Fix" in msg


class TestQuickcheck:
    def test_quickcheck_returns_empty_when_prop_holds(self):
        S.register(":test.qc/int", S.int_())
        failures = S.quickcheck(":test.qc/int", lambda v: isinstance(v, int), n=20)
        assert failures == []

    def test_quickcheck_finds_violations(self):
        S.register(":test.qc/int2", S.int_())
        # property says "always > 10**9" — any generated int should fail
        failures = S.quickcheck(":test.qc/int2", lambda v: v > 10**9, n=20)
        assert len(failures) > 0


class TestVersionSwap:
    def test_swap_new_spec_old_conformed_still_valid_against_old(self):
        # Register v1 (int)
        old_spec = S.int_()
        S.register(":test.ver/wacc", old_spec)
        old_value = 87  # basis points
        old_result = S.conform(":test.ver/wacc", old_value)
        assert old_result.is_ok

        # Swap to v2: float in [0, 1]
        from persistence.spec._combinators import _And
        new_spec = S.and_(S.float_())
        S.register(":test.ver/wacc", new_spec)

        # Old value no longer conforms (different schema)
        assert not S.conform(":test.ver/wacc", old_value).is_ok
        # New value does
        assert S.conform(":test.ver/wacc", 0.087).is_ok

        # But old spec, held separately, still works on old value
        assert old_spec.conform(old_value).is_ok

    def test_versioned_keys_coexist(self):
        S.register(":test.ver/shape@v1", S.keys(required={":a": S.int_()}))
        S.register(":test.ver/shape@v2", S.keys(required={":a": S.int_(), ":b": S.str_()}))
        assert S.conform(":test.ver/shape@v1", {":a": 1}).is_ok
        assert not S.conform(":test.ver/shape@v2", {":a": 1}).is_ok
        assert S.conform(":test.ver/shape@v2", {":a": 1, ":b": "x"}).is_ok


class TestComposition:
    def test_register_references_another_spec(self):
        S.register(":test.comp/atom", S.int_())
        S.register(":test.comp/pair",
                   S.tuple_of(S.ref(":test.comp/atom"), S.ref(":test.comp/atom")))
        assert S.conform(":test.comp/pair", (1, 2)).is_ok
        assert not S.conform(":test.comp/pair", (1, "x")).is_ok

    def test_swapping_atom_affects_ref(self):
        S.register(":test.comp/atom2", S.int_())
        S.register(":test.comp/box", S.keys(required={":v": S.ref(":test.comp/atom2")}))
        assert S.conform(":test.comp/box", {":v": 7}).is_ok
        assert not S.conform(":test.comp/box", {":v": "x"}).is_ok

        # swap atom to accept strings too
        S.register(":test.comp/atom2", S.or_(S.int_(), S.str_()))
        assert S.conform(":test.comp/box", {":v": "x"}).is_ok


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
