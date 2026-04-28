"""B5 — ``_is_finite_score`` boundaries + ``EvaluatorContractError`` shape.

The full loop-driven NaN/Inf reject path lives in B9; this module only
covers the helper's truth table (design §9 NaN/Inf defense) and the
error class wiring (raise-site is B9; ships in B5 alongside the
helper). Bool-isinstance reject is the Stream A W1.B / G4 anti-pattern
preempted at every numeric boundary in v0.6.5.
"""
from __future__ import annotations

import math

import pytest

from persistence.plan import EvaluatorContractError, LLMJudgeEvaluator, Node
from persistence.plan._mcts import _is_finite_score


# --- Finite floats accepted --------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    [0.0, -0.0, 0.5, -0.5, 1.0, -1.0, 1e-9, 1e9, 0, 1, -1],
)
def test_is_finite_score_accepts_finite_numerics(value):
    """Finite ``int`` and ``float`` values pass the guard."""
    assert _is_finite_score(value) is True


# --- NaN / Inf rejected ------------------------------------------------- #


def test_is_finite_score_rejects_nan():
    """NaN is rejected (``math.isfinite`` returns False)."""
    assert _is_finite_score(float("nan")) is False


def test_is_finite_score_rejects_positive_infinity():
    """+Inf is rejected (would poison PUCT arithmetic; design §9)."""
    assert _is_finite_score(float("inf")) is False


def test_is_finite_score_rejects_negative_infinity():
    """-Inf is rejected (would poison PUCT arithmetic; design §9)."""
    assert _is_finite_score(float("-inf")) is False


def test_is_finite_score_rejects_math_inf_and_nan():
    """Spot-check the canonical ``math`` constants — same posture."""
    assert _is_finite_score(math.inf) is False
    assert _is_finite_score(-math.inf) is False
    assert _is_finite_score(math.nan) is False


# --- Bool rejected (Stream A W1.B / G4) --------------------------------- #


def test_is_finite_score_rejects_true():
    """``True`` is an ``int`` subclass; reject explicitly (Stream A W1.B / G4)."""
    assert _is_finite_score(True) is False


def test_is_finite_score_rejects_false():
    """``False`` is an ``int`` subclass; reject explicitly (vacuous-truth defense)."""
    assert _is_finite_score(False) is False


# --- Non-numeric types rejected ----------------------------------------- #


@pytest.mark.parametrize(
    "value",
    ["0.5", "nan", "", b"0.5", None, [0.5], (0.5,), {"x": 0.5}],
)
def test_is_finite_score_rejects_non_numeric(value):
    """Strings, bytes, None, containers — all non-numeric, all reject."""
    assert _is_finite_score(value) is False


# --- EvaluatorContractError wiring -------------------------------------- #


def test_evaluator_contract_error_subclasses_value_error():
    """``EvaluatorContractError`` is a ``ValueError`` (catchable as such)."""
    err = EvaluatorContractError("score 'nan' is not finite")
    assert isinstance(err, ValueError)
    assert str(err) == "score 'nan' is not finite"


# --- LLMJudgeEvaluator pure-delegation pin ------------------------------ #


def test_llm_judge_evaluator_returns_provider_value_byte_identically():
    """``LLMJudgeEvaluator.evaluate`` is pure delegation; B9 owns finite-check."""
    plan = Node(tag=":plan/x", attrs={"k": 1})
    captured: dict[str, Node] = {}

    def provider(p: Node) -> float:
        captured["plan"] = p
        return 0.123456789

    evaluator = LLMJudgeEvaluator(provider=provider)
    score = evaluator.evaluate(plan)
    assert score == 0.123456789
    assert captured["plan"] is plan


def test_llm_judge_evaluator_passes_through_non_finite_scores():
    """Pure delegation: the evaluator does NOT enforce finite-score; B9 does.

    This pins design §9 + impl plan §B5: the helper exists, but the
    raise-site is the MCTS loop reject path (B9). ``LLMJudgeEvaluator``
    itself returns whatever the provider returned — even ``nan``.
    """
    plan = Node(tag=":plan/x", attrs={"k": 1})

    def nan_provider(_p: Node) -> float:
        return float("nan")

    evaluator = LLMJudgeEvaluator(provider=nan_provider)
    score = evaluator.evaluate(plan)
    assert math.isnan(score)
