"""T4/G3 — `_make_branch_expander` + provider-closure construction.

LD3: provider closure routes through substrate.effect.perform(":llm/call", ...)
with request shape {model, messages, tools} per _session.py:134-139.
EMIT_BRANCH_PROPOSAL_TOOL_SCHEMA is the JSON-mode contract for action
proposals. Bridge-side post-processing: parse tool-use response into
[(Action, prior)] sequence + softmax-normalize priors so sum ≈ 1.0
(within _PRIOR_TOL).

ComposeWithSkillAction proposals are dropped at the wrapper layer per
LD3 (deferred to 2.3c when SkillLibrary lands).
"""
from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest

from persistence.coder._searcher import (
    _make_branch_expander,
    _softmax_normalize,
)
from persistence.plan import LLMExpander, Node, parse
from persistence.plan._mcts import (
    AddStepAction,
    ComposeWithSkillAction,
    SubstituteLeafAction,
    _PRIOR_TOL,
)


def test_make_branch_expander_returns_LLMExpander_instance():
    coder = MagicMock()
    expander = _make_branch_expander(coder)
    assert isinstance(expander, LLMExpander)


def test_softmax_normalize_emits_priors_summing_to_one():
    raw_logits = [1.0, 2.0, 3.0]
    normalized = _softmax_normalize(raw_logits)
    assert abs(sum(normalized) - 1.0) < _PRIOR_TOL
    # Rank-preserving: highest logit gets highest prior.
    assert normalized[2] > normalized[1] > normalized[0]


def test_softmax_normalize_handles_uniform_logits():
    raw_logits = [1.0, 1.0, 1.0]
    normalized = _softmax_normalize(raw_logits)
    assert abs(sum(normalized) - 1.0) < _PRIOR_TOL
    assert all(abs(p - 1.0/3) < _PRIOR_TOL for p in normalized)


def test_expander_provider_routes_through_llm_call():
    """The provider closure dispatches via substrate.effect.perform(":llm/call", ...)
    with shape {model, messages, tools}. Verify by spying on the perform call.

    Use canonical bare-keyword EDN form per FD1 (T2): [:seq {} [:fs/read {:path "x"}]].
    """
    coder = MagicMock()
    coder.model = "claude-test"
    coder.substrate.effect.perform.return_value = {
        "tool_calls": [{"input": {"proposals": [
            {
                "kind": "SubstituteLeafAction",
                "target_path": [0],
                "new_leaf_edn": '[:fs/read {:path "x.txt"}]',
                "logit": 1.0,
            },
        ]}}],
        "text": "",
    }
    expander = _make_branch_expander(coder)
    plan = parse('[:seq {} [:fs/read {:path "y.txt"}]]', strict=False)
    proposals = expander.propose(plan, k=4)

    # Verify :llm/call was dispatched with the expected request shape.
    coder.substrate.effect.perform.assert_called_once()
    call_args = coder.substrate.effect.perform.call_args
    assert call_args.args[0] == ":llm/call"
    request = call_args.args[1]
    assert "model" in request
    assert "messages" in request
    assert "tools" in request
    # No `system` or `response_format` keys per FD: actual :llm/call shape.
    assert "system" not in request
    assert "response_format" not in request
    # System message embedded in messages[0].
    assert request["messages"][0]["role"] == "system"


def test_expander_drops_compose_with_skill_action():
    """LD3: ComposeWithSkillAction proposals are dropped at the wrapper
    layer (deferred to 2.3c). The closure returns 1 valid + 1
    ComposeWithSkill -> only the valid one survives."""
    coder = MagicMock()
    coder.model = "claude-test"
    coder.substrate.effect.perform.return_value = {
        "tool_calls": [{"input": {"proposals": [
            {
                "kind": "SubstituteLeafAction",
                "target_path": [0],
                "new_leaf_edn": '[:fs/read {:path "ok.txt"}]',
                "logit": 1.0,
            },
            {
                "kind": "ComposeWithSkillAction",
                "target_path": [0],
                "skill_id": "deferred-to-2.3c",
                "logit": 5.0,
            },
        ]}}],
        "text": "",
    }
    expander = _make_branch_expander(coder)
    plan = parse('[:seq {} [:fs/read {:path "y.txt"}]]', strict=False)
    proposals = expander.propose(plan, k=4)

    # ComposeWithSkillAction was dropped; only SubstituteLeafAction remains.
    assert len(proposals) == 1
    action, prior = proposals[0]
    assert isinstance(action, SubstituteLeafAction)
    assert not isinstance(action, ComposeWithSkillAction)
