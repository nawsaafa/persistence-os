"""ARIS R3 F6 — unified verdict vocabulary reconciler.

The spec uses EDN keywords (``":allow"``); the runtime uses bare strings
(``"allow"``). :mod:`persistence.effect.verdicts` is the single source of
truth for translating between them.
"""
from __future__ import annotations

import pytest

from persistence.effect.verdicts import (
    EDN_VERDICTS,
    PYTHON_VERDICTS,
    as_edn,
    as_python,
)


class TestVocabulary:
    def test_every_policy_verdict_in_python_set(self):
        for v in ("allow", "deny", "deny-silently", "require-approval"):
            assert v in PYTHON_VERDICTS

    def test_every_audit_result_path_in_python_set(self):
        # audit handler records "ok" / "error" for success / exception paths
        assert "ok" in PYTHON_VERDICTS
        assert "error" in PYTHON_VERDICTS

    def test_edn_set_is_python_set_with_colons(self):
        assert EDN_VERDICTS == {":" + v for v in PYTHON_VERDICTS}


class TestAsEdn:
    @pytest.mark.parametrize("v", sorted(PYTHON_VERDICTS))
    def test_python_to_edn_prepends_colon(self, v):
        assert as_edn(v) == ":" + v

    def test_idempotent_on_keyworded_input(self):
        assert as_edn(":allow") == ":allow"

    def test_unknown_verdict_raises(self):
        with pytest.raises(ValueError, match="unknown"):
            as_edn("banana")

    def test_unknown_edn_verdict_raises(self):
        with pytest.raises(ValueError, match="unknown"):
            as_edn(":banana")


class TestAsPython:
    @pytest.mark.parametrize("v", sorted(EDN_VERDICTS))
    def test_edn_to_python_strips_colon(self, v):
        assert as_python(v) == v[1:]

    def test_idempotent_on_python_input(self):
        assert as_python("allow") == "allow"

    def test_unknown_verdict_raises(self):
        with pytest.raises(ValueError, match="unknown"):
            as_python("banana")


class TestRoundTrip:
    @pytest.mark.parametrize("v", sorted(PYTHON_VERDICTS))
    def test_python_edn_python(self, v):
        assert as_python(as_edn(v)) == v

    @pytest.mark.parametrize("v", sorted(EDN_VERDICTS))
    def test_edn_python_edn(self, v):
        assert as_edn(as_python(v)) == v
