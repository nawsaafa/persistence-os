"""T5/G3 — `_make_branch_evaluator` provider-closure construction.

LD3: provider closure routes through substrate.effect.perform(":llm/call", ...).
Score range: [0.0, 1.0]; out-of-range responses get clamped (rather than
raising — the engine absorbs evaluator exceptions as phase="reject"
which we don't want for off-by-epsilon JSON parsing edge cases).

FD4: LLMJudgeEvaluator.provider signature is Callable[[Node], float],
single positional arg.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from persistence.coder._searcher import _make_branch_evaluator
from persistence.plan import LLMJudgeEvaluator, parse


def test_make_branch_evaluator_returns_LLMJudgeEvaluator():
    coder = MagicMock()
    evaluator = _make_branch_evaluator(coder)
    assert isinstance(evaluator, LLMJudgeEvaluator)


def test_evaluator_provider_routes_through_llm_call():
    coder = MagicMock()
    coder.model = "claude-test"
    coder.substrate.effect.perform.return_value = {
        "tool_calls": [{"input": {"score": 0.85}}],
        "text": "",
    }
    evaluator = _make_branch_evaluator(coder)
    plan = parse('[:seq {} [:fs/read {:path "y.txt"}]]', strict=False)
    score = evaluator.evaluate(plan)
    assert score == 0.85
    coder.substrate.effect.perform.assert_called_once()
    request = coder.substrate.effect.perform.call_args.args[1]
    assert request["model"] == "claude-test"
    assert request["messages"][0]["role"] == "system"
    assert "tools" in request


def test_evaluator_clamps_out_of_range_score():
    """Score returned by LLM > 1.0 -> clamp to 1.0; < 0.0 -> clamp to 0.0.
    Avoids EvaluatorContractError for off-by-epsilon JSON parsing."""
    coder = MagicMock()
    coder.model = "claude-test"
    coder.substrate.effect.perform.return_value = {
        "tool_calls": [{"input": {"score": 1.5}}],
        "text": "",
    }
    evaluator = _make_branch_evaluator(coder)
    plan = parse('[:seq {} [:fs/read {:path "y.txt"}]]', strict=False)
    assert evaluator.evaluate(plan) == 1.0

    coder.substrate.effect.perform.return_value = {
        "tool_calls": [{"input": {"score": -0.3}}],
        "text": "",
    }
    assert evaluator.evaluate(plan) == 0.0


def test_evaluator_returns_zero_when_response_has_no_tool_calls():
    """I2: LLM may emit text without invoking the tool → 0.0 default.

    The engine's _is_finite_score accepts 0.0; this is "no signal" not
    "evaluator exception" so phase!=reject.
    """
    coder = MagicMock()
    coder.model = "claude-test"
    coder.substrate.effect.perform.return_value = {
        "tool_calls": [],
        "text": "I cannot score this plan right now.",
    }
    evaluator = _make_branch_evaluator(coder)
    plan = parse('[:seq {} [:fs/read {:path "y.txt"}]]', strict=False)
    assert evaluator.evaluate(plan) == 0.0


def test_evaluator_returns_zero_for_nan_or_inf_score():
    """I1: NaN/Inf must be guarded — both comparisons against 0.0/1.0
    return False for NaN, so without `math.isfinite()` guard NaN leaks
    to the engine."""
    import math as _math   # local alias to avoid shadowing if any
    coder = MagicMock()
    coder.model = "claude-test"

    # NaN
    coder.substrate.effect.perform.return_value = {
        "tool_calls": [{"input": {"score": _math.nan}}],
        "text": "",
    }
    evaluator = _make_branch_evaluator(coder)
    plan = parse('[:seq {} [:fs/read {:path "y.txt"}]]', strict=False)
    assert evaluator.evaluate(plan) == 0.0

    # +Inf
    coder.substrate.effect.perform.return_value = {
        "tool_calls": [{"input": {"score": _math.inf}}],
        "text": "",
    }
    assert evaluator.evaluate(plan) == 0.0

    # -Inf
    coder.substrate.effect.perform.return_value = {
        "tool_calls": [{"input": {"score": -_math.inf}}],
        "text": "",
    }
    assert evaluator.evaluate(plan) == 0.0


@pytest.mark.parametrize("bad_score", [
    True,           # bool (FD2 trap — isinstance(True, int) is True)
    False,          # bool
    "high",         # string
    None,           # missing-via-None
    [0.5],          # non-numeric container
])
def test_evaluator_returns_zero_for_malformed_score(bad_score):
    """I3: non-numeric / bool / missing score → 0.0 (no signal).
    Each case exercises a different filter branch in _parse_evaluator_tool_response.
    """
    coder = MagicMock()
    coder.model = "claude-test"
    coder.substrate.effect.perform.return_value = {
        "tool_calls": [{"input": {"score": bad_score}}],
        "text": "",
    }
    evaluator = _make_branch_evaluator(coder)
    plan = parse('[:seq {} [:fs/read {:path "y.txt"}]]', strict=False)
    assert evaluator.evaluate(plan) == 0.0


def test_evaluator_returns_zero_when_score_field_missing():
    """I3: tool_calls[0]["input"] has no "score" key → raw is None → 0.0."""
    coder = MagicMock()
    coder.model = "claude-test"
    coder.substrate.effect.perform.return_value = {
        "tool_calls": [{"input": {}}],   # no "score" key
        "text": "",
    }
    evaluator = _make_branch_evaluator(coder)
    plan = parse('[:seq {} [:fs/read {:path "y.txt"}]]', strict=False)
    assert evaluator.evaluate(plan) == 0.0


def test_evaluator_handles_non_mapping_tool_call_gracefully():
    """I4: if tool_calls[0] is a string / int / list (not a Mapping),
    the current code does .get() which would AttributeError. Verify
    behavior: defensive guard at top of _parse_evaluator_tool_response
    returns 0.0.
    """
    coder = MagicMock()
    coder.model = "claude-test"
    coder.substrate.effect.perform.return_value = {
        "tool_calls": ["not a dict"],   # string, not Mapping
        "text": "",
    }
    evaluator = _make_branch_evaluator(coder)
    plan = parse('[:seq {} [:fs/read {:path "y.txt"}]]', strict=False)
    assert evaluator.evaluate(plan) == 0.0
