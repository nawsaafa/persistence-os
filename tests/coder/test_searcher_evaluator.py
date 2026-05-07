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
