"""Tests for :mod:`persistence.store._pool_config` (PG5).

Pure-data helper: no live Postgres needed. Pins the option-string shape
so future drift is caught at unit-test time rather than via an opaque
"why are my timeouts not applied" production-side puzzle.
"""

from __future__ import annotations

import pytest

from persistence.store._pool_config import recommended_pool_kwargs


def test_default_kwargs_match_documented_table() -> None:
    """Defaults match the table in postgres-deployment.md.

    statement_timeout=5000, lock_timeout=2000,
    idle_in_transaction_session_timeout=10000.
    """
    out = recommended_pool_kwargs()

    assert out == {
        "kwargs": {
            "options": (
                "-c statement_timeout=5000 "
                "-c lock_timeout=2000 "
                "-c idle_in_transaction_session_timeout=10000"
            )
        }
    }


def test_returned_dict_is_splat_ready_for_connection_pool() -> None:
    """``ConnectionPool(**recommended_pool_kwargs())`` is the documented call shape.

    Asserting the top-level key is exactly ``"kwargs"`` so future
    refactors don't silently break the splat contract.
    """
    out = recommended_pool_kwargs()
    assert list(out.keys()) == ["kwargs"]
    inner = out["kwargs"]
    assert isinstance(inner, dict)
    assert list(inner.keys()) == ["options"]


def test_each_timeout_parameter_is_honoured() -> None:
    """Custom values for all three timeouts flow through verbatim."""
    out = recommended_pool_kwargs(
        statement_timeout_ms=60_000,
        lock_timeout_ms=15_000,
        idle_in_transaction_timeout_ms=120_000,
    )

    options = out["kwargs"]["options"]  # type: ignore[index]
    assert "-c statement_timeout=60000" in options
    assert "-c lock_timeout=15000" in options
    assert "-c idle_in_transaction_session_timeout=120000" in options


def test_option_order_is_stable() -> None:
    """Option order is statement → lock → idle_in_transaction.

    The string is reproducible across calls so test assertions don't
    flap on dict-iteration order changes (psycopg + libpq don't care
    about order, but stable output makes diffs grep-clean).
    """
    a = recommended_pool_kwargs()["kwargs"]["options"]  # type: ignore[index]
    b = recommended_pool_kwargs()["kwargs"]["options"]  # type: ignore[index]
    assert a == b
    # All three keys appear in the documented order.
    s_idx = a.index("statement_timeout")
    l_idx = a.index("lock_timeout")
    i_idx = a.index("idle_in_transaction_session_timeout")
    assert s_idx < l_idx < i_idx


@pytest.mark.parametrize(
    "kwarg",
    ["statement_timeout_ms", "lock_timeout_ms", "idle_in_transaction_timeout_ms"],
)
def test_zero_timeout_raises_value_error(kwarg: str) -> None:
    """Zero is rejected — Postgres would treat it as "no timeout"."""
    with pytest.raises(ValueError, match=kwarg):
        recommended_pool_kwargs(**{kwarg: 0})


@pytest.mark.parametrize(
    "kwarg",
    ["statement_timeout_ms", "lock_timeout_ms", "idle_in_transaction_timeout_ms"],
)
def test_negative_timeout_raises_value_error(kwarg: str) -> None:
    """Negative values are rejected as obviously wrong."""
    with pytest.raises(ValueError, match=kwarg):
        recommended_pool_kwargs(**{kwarg: -1})


def test_helper_is_importable_from_canonical_path() -> None:
    """The module path documented in postgres-deployment.md actually resolves.

    Guards against accidental rename / move that would break the
    deployment doc's import example.
    """
    from persistence.store import _pool_config

    assert hasattr(_pool_config, "recommended_pool_kwargs")
    assert _pool_config.recommended_pool_kwargs is recommended_pool_kwargs


def test_helper_does_not_open_pool_or_touch_postgres() -> None:
    """Smoke: pure-data helper must not import psycopg or psycopg_pool.

    The deployment doc promises this is a recipe builder, callable in
    any Python environment regardless of whether the [postgres] extra
    is installed. We confirm it returns successfully without any
    networking and without surfacing any psycopg ImportError.
    """
    # No DSN, no pool, no network — just data.
    out1 = recommended_pool_kwargs()
    out2 = recommended_pool_kwargs(statement_timeout_ms=1)
    assert out1 != out2
    assert "statement_timeout=1 " in out2["kwargs"]["options"]  # type: ignore[index]
