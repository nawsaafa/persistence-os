"""B2 — ``MCTSConfig`` ``__post_init__`` validation contract.

Covers design §11 / §17 ADR-8: every numeric field is guarded by a
**bool-isinstance check FIRST**, then a numeric type check, then a
positivity check. This pre-empts Stream A's W1.B / G4 anti-pattern
where ``isinstance(True, int) is True`` lets ``bool`` slip past a naïve
``> 0`` check (``True > 0`` is ``True`` accidentally; ``False > 0`` is
``False`` so a threshold-never-fires).

Optional fields (``simple_regret_threshold``, ``wall_clock_budget_ms``)
accept ``None``; non-``None`` values run the same triple-check.

``seed`` is validated as ``int`` (rejecting ``bool``); reserved for
v0.7+ Dirichlet noise / shuffle tie-break per design §10.
"""
from __future__ import annotations

import dataclasses

import pytest

from persistence.plan import MCTSConfig


# --- Default construction ------------------------------------------------- #


def test_default_construction_succeeds():
    """``MCTSConfig()`` builds with the design §11 defaults — no raise."""
    cfg = MCTSConfig()
    assert cfg.c_puct == 1.4
    assert cfg.max_iter == 200
    assert cfg.max_unique_plans == 64
    assert cfg.expander_k == 4
    assert cfg.simple_regret_threshold is None
    assert cfg.simple_regret_window == 5
    assert cfg.wall_clock_budget_ms is None
    assert cfg.seed == 0


# --- c_puct boundaries ---------------------------------------------------- #


def test_c_puct_false_rejected():
    """``c_puct=False`` fails the bool-isinstance guard (design §11)."""
    with pytest.raises(ValueError, match="c_puct"):
        MCTSConfig(c_puct=False)  # type: ignore[arg-type]


def test_c_puct_true_rejected():
    """``c_puct=True`` fails the bool-isinstance guard."""
    with pytest.raises(ValueError, match="c_puct"):
        MCTSConfig(c_puct=True)  # type: ignore[arg-type]


def test_c_puct_zero_rejected():
    """``c_puct=0`` fails the positivity check (must be ``> 0``)."""
    with pytest.raises(ValueError, match="c_puct"):
        MCTSConfig(c_puct=0)


def test_c_puct_negative_rejected():
    """``c_puct=-1.0`` fails the positivity check."""
    with pytest.raises(ValueError, match="c_puct"):
        MCTSConfig(c_puct=-1.0)


def test_c_puct_string_rejected():
    """``c_puct='1.4'`` fails the numeric type check."""
    with pytest.raises(ValueError, match="c_puct"):
        MCTSConfig(c_puct="1.4")  # type: ignore[arg-type]


# --- max_iter boundaries -------------------------------------------------- #


def test_max_iter_false_rejected():
    """``max_iter=False`` fails the bool-isinstance guard."""
    with pytest.raises(ValueError, match="max_iter"):
        MCTSConfig(max_iter=False)  # type: ignore[arg-type]


def test_max_iter_zero_rejected():
    """``max_iter=0`` would produce a vacuous search — rejected."""
    with pytest.raises(ValueError, match="max_iter"):
        MCTSConfig(max_iter=0)


# --- max_unique_plans boundaries ----------------------------------------- #


def test_max_unique_plans_zero_rejected():
    """``max_unique_plans=0`` rejected."""
    with pytest.raises(ValueError, match="max_unique_plans"):
        MCTSConfig(max_unique_plans=0)


# --- expander_k boundaries ----------------------------------------------- #


def test_expander_k_zero_rejected():
    """``expander_k=0`` rejected (technically allowed by math, always-pointless)."""
    with pytest.raises(ValueError, match="expander_k"):
        MCTSConfig(expander_k=0)


def test_expander_k_false_rejected():
    """``expander_k=False`` fails the bool-isinstance guard."""
    with pytest.raises(ValueError, match="expander_k"):
        MCTSConfig(expander_k=False)  # type: ignore[arg-type]


# --- simple_regret_threshold (Optional[float]) --------------------------- #


def test_simple_regret_threshold_false_rejected():
    """``simple_regret_threshold=False`` slipping past ``>= 0.0`` rejected (design §11)."""
    with pytest.raises(ValueError, match="simple_regret_threshold"):
        MCTSConfig(simple_regret_threshold=False)  # type: ignore[arg-type]


def test_simple_regret_threshold_negative_rejected():
    """``simple_regret_threshold=-0.1`` fails the non-negative check."""
    with pytest.raises(ValueError, match="simple_regret_threshold"):
        MCTSConfig(simple_regret_threshold=-0.1)


def test_simple_regret_threshold_none_ok():
    """``simple_regret_threshold=None`` is the off-state — accepted."""
    cfg = MCTSConfig(simple_regret_threshold=None)
    assert cfg.simple_regret_threshold is None


def test_simple_regret_threshold_zero_ok():
    """``simple_regret_threshold=0.0`` is non-negative — accepted (design §11)."""
    cfg = MCTSConfig(simple_regret_threshold=0.0)
    assert cfg.simple_regret_threshold == 0.0


# --- simple_regret_window boundaries ------------------------------------- #


def test_simple_regret_window_zero_rejected():
    """``simple_regret_window=0`` rejected."""
    with pytest.raises(ValueError, match="simple_regret_window"):
        MCTSConfig(simple_regret_window=0)


# --- wall_clock_budget_ms (Optional[int], must be int not float) --------- #


def test_wall_clock_budget_ms_zero_rejected():
    """``wall_clock_budget_ms=0`` rejected."""
    with pytest.raises(ValueError, match="wall_clock_budget_ms"):
        MCTSConfig(wall_clock_budget_ms=0)


def test_wall_clock_budget_ms_float_rejected():
    """``wall_clock_budget_ms=1.5`` fails — must be int, not float."""
    with pytest.raises(ValueError, match="wall_clock_budget_ms"):
        MCTSConfig(wall_clock_budget_ms=1.5)  # type: ignore[arg-type]


def test_wall_clock_budget_ms_none_ok():
    """``wall_clock_budget_ms=None`` is the off-state — accepted."""
    cfg = MCTSConfig(wall_clock_budget_ms=None)
    assert cfg.wall_clock_budget_ms is None


# --- seed boundaries (int, rejecting bool) ------------------------------- #


def test_seed_true_rejected():
    """``seed=True`` fails the bool-isinstance guard (design §10)."""
    with pytest.raises(ValueError, match="seed"):
        MCTSConfig(seed=True)  # type: ignore[arg-type]


def test_seed_string_rejected():
    """``seed='0'`` fails the int type check."""
    with pytest.raises(ValueError, match="seed"):
        MCTSConfig(seed="0")  # type: ignore[arg-type]


def test_seed_zero_ok():
    """``seed=0`` is the default — accepted."""
    cfg = MCTSConfig(seed=0)
    assert cfg.seed == 0


# --- Frozen + slots checks ----------------------------------------------- #


def test_config_is_frozen():
    """Mutating a frozen-dataclass attr raises ``FrozenInstanceError``."""
    cfg = MCTSConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.c_puct = 2.0  # type: ignore[misc]


def test_config_uses_slots():
    """Slots forbid setting unknown attrs and remove ``__dict__``.

    Direct ``__slots__`` introspection is the byte-stable contract;
    assignment-time error type varies (``AttributeError`` on the
    instance is the canonical signal, but Python 3.14's ``frozen=True``
    + ``slots=True`` combo can surface ``TypeError`` from the frozen
    ``__setattr__`` machinery when the slot itself is missing — both
    are valid)."""
    assert hasattr(MCTSConfig, "__slots__")
    cfg = MCTSConfig()
    assert not hasattr(cfg, "__dict__")
    with pytest.raises((AttributeError, TypeError)):
        cfg.foo = 1  # type: ignore[attr-defined]
