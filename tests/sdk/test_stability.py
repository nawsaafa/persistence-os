"""Tests for ``persistence.sdk._stability`` (SDK1).

Per the design doc ``docs/plans/2026-04-29-adapter-sdk-contract-design.md``
ADR-5 + ADR-16, the three stability decorators (``stable`` /
``experimental`` / ``deprecated``) attach machine-readable metadata to the
wrapped object via ``__sdk_stability__``. SDK1 only needs to assert:

1. each decorator attaches the correct metadata shape;
2. ``functools.wraps`` properties (``__name__``, ``__doc__``, signature)
   are preserved on wrapped callables;
3. ``@deprecated`` callables emit ``DeprecationWarning`` at call time;
4. argument validation rejects empty / wrong-typed parameters;
5. conflicting double-decoration raises loudly at import time.

The spec-generator-side assertions (CI-gated drift detection) ship in
SDK5 and are out of scope here.
"""
from __future__ import annotations

import functools
import inspect
import warnings

import pytest

from persistence.sdk import deprecated, experimental, stable


# ---------------------------------------------------------------------------
# 1. @stable
# ---------------------------------------------------------------------------
class TestStable:
    def test_attaches_level_version_and_default_note(self):
        @stable("v0.8")
        def f() -> int:
            return 1

        assert f.__sdk_stability__ == {
            "level": "stable",
            "version": "v0.8",
            "note": None,
        }

    def test_attaches_optional_note(self):
        @stable("v0.8", note="substring+tag mode")
        def recall() -> list[str]:
            return []

        assert recall.__sdk_stability__["note"] == "substring+tag mode"
        assert recall.__sdk_stability__["level"] == "stable"

    def test_does_not_change_runtime_behaviour(self):
        @stable("v0.8")
        def add(a: int, b: int) -> int:
            return a + b

        assert add(2, 3) == 5

    def test_preserves_name_doc_and_signature(self):
        @stable("v0.8")
        def my_func(x: int, *, y: str = "hi") -> str:
            """Docstring sentinel."""
            return f"{x}{y}"

        assert my_func.__name__ == "my_func"
        assert my_func.__doc__ == "Docstring sentinel."
        sig = inspect.signature(my_func)
        assert list(sig.parameters) == ["x", "y"]
        assert sig.parameters["y"].default == "hi"

    def test_works_on_classes(self):
        @stable("v0.8")
        class Foo:
            """A class."""

        assert Foo.__sdk_stability__["level"] == "stable"
        assert Foo.__name__ == "Foo"
        # Class can still be instantiated normally.
        assert isinstance(Foo(), Foo)

    def test_rejects_empty_version(self):
        with pytest.raises(ValueError, match="non-empty"):
            stable("")

    def test_rejects_non_string_version(self):
        with pytest.raises(ValueError, match="non-empty"):
            stable(8)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. @experimental
# ---------------------------------------------------------------------------
class TestExperimental:
    def test_attaches_level_with_no_reason(self):
        @experimental()
        def f() -> int:
            return 1

        assert f.__sdk_stability__ == {
            "level": "experimental",
            "reason": None,
        }

    def test_attaches_reason(self):
        @experimental(reason="escape hatch — see ADR-1")
        def f() -> int:
            return 1

        assert f.__sdk_stability__["reason"] == "escape hatch — see ADR-1"

    def test_does_not_emit_warning_on_call(self):
        @experimental(reason="any reason")
        def f() -> int:
            return 42

        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning becomes an error
            assert f() == 42

    def test_preserves_name_and_doc(self):
        @experimental()
        def my_exp() -> None:
            """Experimental doc."""

        assert my_exp.__name__ == "my_exp"
        assert my_exp.__doc__ == "Experimental doc."


# ---------------------------------------------------------------------------
# 3. @deprecated
# ---------------------------------------------------------------------------
class TestDeprecated:
    def test_attaches_full_metadata(self):
        @deprecated("use Substrate.fact.transact", since="v0.8", removal="v0.9")
        def old_fn() -> int:
            return 1

        assert old_fn.__sdk_stability__ == {
            "level": "deprecated",
            "replacement": "use Substrate.fact.transact",
            "since": "v0.8",
            "removal": "v0.9",
        }

    def test_removal_is_optional(self):
        @deprecated("use new_fn", since="v0.8")
        def old_fn() -> int:
            return 1

        assert old_fn.__sdk_stability__["removal"] is None

    def test_emits_deprecation_warning_on_call(self):
        @deprecated("use new_fn", since="v0.8", removal="v0.9")
        def old_fn() -> int:
            return 7

        with pytest.warns(DeprecationWarning, match="old_fn is deprecated"):
            assert old_fn() == 7

    def test_warning_message_includes_replacement(self):
        @deprecated("use new_fn instead", since="v0.8")
        def old_fn() -> int:
            return 1

        with pytest.warns(DeprecationWarning) as record:
            old_fn()

        assert len(record) == 1
        msg = str(record[0].message)
        assert "use new_fn instead" in msg
        assert "v0.8" in msg

    def test_warning_message_includes_removal_when_set(self):
        @deprecated("use new_fn", since="v0.8", removal="v0.9")
        def old_fn() -> int:
            return 1

        with pytest.warns(DeprecationWarning) as record:
            old_fn()

        assert "v0.9" in str(record[0].message)

    def test_preserves_name_doc_and_signature(self):
        @deprecated("use new_fn", since="v0.8")
        def old_fn(x: int, y: str = "z") -> str:
            """Old docstring."""
            return f"{x}{y}"

        assert old_fn.__name__ == "old_fn"
        assert old_fn.__doc__ == "Old docstring."
        sig = inspect.signature(old_fn)
        assert list(sig.parameters) == ["x", "y"]

    def test_preserves_wrapped_via_functools(self):
        # functools.wraps should populate __wrapped__ to the original.
        def the_real_fn() -> int:
            """Original."""
            return 9

        wrapped = deprecated("see new", since="v0.8")(the_real_fn)
        assert wrapped.__wrapped__ is the_real_fn  # type: ignore[attr-defined]

    def test_rejects_empty_replacement(self):
        with pytest.raises(ValueError, match="non-empty"):
            deprecated("", since="v0.8")

    def test_rejects_empty_since(self):
        with pytest.raises(ValueError, match="non-empty"):
            deprecated("use new_fn", since="")

    def test_call_returns_original_value_with_warning_filtered(self):
        @deprecated("use new_fn", since="v0.8")
        def old_fn(x: int) -> int:
            return x * 2

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            assert old_fn(21) == 42


# ---------------------------------------------------------------------------
# 4. Conflict detection — double-decoration is a loud import-time error.
# ---------------------------------------------------------------------------
class TestConflictDetection:
    def test_stable_then_experimental_raises(self):
        with pytest.raises(TypeError, match="sdk-stability conflict"):

            @experimental()
            @stable("v0.8")
            def f() -> int:
                return 1

    def test_stable_with_different_version_raises(self):
        with pytest.raises(TypeError, match="sdk-stability conflict"):

            @stable("v0.9")
            @stable("v0.8")
            def f() -> int:
                return 1

    def test_idempotent_same_metadata_is_allowed(self):
        # Stamping the SAME metadata twice is harmless (e.g. import cycles
        # that re-execute a decorated symbol). The conflict detector only
        # fires on an actual mismatch.
        def raw() -> int:
            return 1

        once = stable("v0.8")(raw)
        twice = stable("v0.8")(once)
        assert twice.__sdk_stability__ == {
            "level": "stable",
            "version": "v0.8",
            "note": None,
        }


# ---------------------------------------------------------------------------
# 5. Public surface — re-exports from persistence.sdk are the same objects.
# ---------------------------------------------------------------------------
class TestPublicReexport:
    def test_decorators_reachable_from_top_level(self):
        from persistence.sdk import deprecated as d_top
        from persistence.sdk import experimental as e_top
        from persistence.sdk import stable as s_top
        from persistence.sdk._stability import (
            deprecated as d_priv,
            experimental as e_priv,
            stable as s_priv,
        )

        assert d_top is d_priv
        assert e_top is e_priv
        assert s_top is s_priv

    def test_functools_wraps_is_used(self):
        # Sanity: deprecated wrapper is via functools.wraps so the
        # underlying inspect.unwrap chain reaches the original.
        @deprecated("use new_fn", since="v0.8")
        def f() -> int:
            return 1

        assert inspect.unwrap(f) is not f  # wrapper is distinct
        # __module__ propagated via functools.wraps
        assert f.__module__ == __name__
