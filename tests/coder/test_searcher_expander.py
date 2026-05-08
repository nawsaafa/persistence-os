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


def test_expander_drops_compose_with_skill_action_when_skill_library_absent():
    """Phase 2.3c.2 LD6 option (b) — recast as a NEGATIVE case.

    Original 2.3b LD3: ComposeWithSkillAction proposals were dropped at
    the wrapper layer (kind-string + isinstance belt-and-braces, FD7).
    2.3c.2 LIFTS those wrapper-layer drops; rejection now happens at the
    dry-run layer when (a) skill_library is None or (b) skill_id is
    unregistered. This test pins case (a): a coder without a
    skill_library still rejects ComposeWithSkillAction proposals — but
    via the dry-run path (``_apply_compose_with_skill`` raises
    ``_SkillNotRegistered``), not the wrapper-layer pre-decode drops.

    The closure returns 1 valid SubstituteLeafAction + 1
    ComposeWithSkillAction. With ``coder.skill_library is None``, only
    the valid action survives (compose drops at dry-run).
    """
    coder = MagicMock()
    coder.model = "claude-test"
    coder.skill_library = None  # 2.3c.2 — explicit absent skill_library
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
                "skill_id": "skill/never-registered",
                "logit": 5.0,
            },
        ]}}],
        "text": "",
    }
    expander = _make_branch_expander(coder)
    plan = parse('[:seq {} [:fs/read {:path "y.txt"}]]', strict=False)
    proposals = expander.propose(plan, k=4)

    # ComposeWithSkillAction was dropped at the dry-run layer (no skill
    # library → _apply_compose_with_skill raises _SkillNotRegistered).
    assert len(proposals) == 1
    action, prior = proposals[0]
    assert isinstance(action, SubstituteLeafAction)
    assert not isinstance(action, ComposeWithSkillAction)


def test_expander_returns_empty_when_response_has_no_tool_calls():
    """LLM may emit text without invoking the tool. Wrapper drops to ()."""
    coder = MagicMock()
    coder.model = "claude-test"
    coder.substrate.effect.perform.return_value = {
        "tool_calls": [],
        "text": "I cannot propose any expansions right now.",
    }
    expander = _make_branch_expander(coder)
    plan = parse('[:seq {} [:fs/read {:path "y.txt"}]]', strict=False)
    proposals = expander.propose(plan, k=4)
    assert proposals == ()


def test_expander_returns_empty_when_proposals_is_none():
    """I1 (codex robustness): proposals=None must drop to () not raise TypeError."""
    coder = MagicMock()
    coder.model = "claude-test"
    coder.substrate.effect.perform.return_value = {
        "tool_calls": [{"input": {"proposals": None}}],
        "text": "",
    }
    expander = _make_branch_expander(coder)
    plan = parse('[:seq {} [:fs/read {:path "y.txt"}]]', strict=False)
    proposals = expander.propose(plan, k=4)
    assert proposals == ()


def test_expander_drops_proposals_failing_dry_run_validator():
    """LD2: proposals whose apply_action result would fail
    validate_plan_for_2_3a are silently dropped at the wrapper layer.

    A proposal substituting a leaf with an invalid tag (`:branch` is
    banned per validate_plan_for_2_3a) MUST be dropped, not surfaced.
    """
    coder = MagicMock()
    coder.model = "claude-test"
    coder.substrate.effect.perform.return_value = {
        "tool_calls": [{"input": {"proposals": [
            # SubstituteLeafAction with a :branch leaf — validator rejects.
            {
                "kind": "SubstituteLeafAction",
                "target_path": [0],
                "new_leaf_edn": '[:branch {}]',
                "logit": 1.0,
            },
            # Another with a valid :fs/read leaf.
            {
                "kind": "SubstituteLeafAction",
                "target_path": [0],
                "new_leaf_edn": '[:fs/read {:path "ok.txt"}]',
                "logit": 1.0,
            },
        ]}}],
        "text": "",
    }
    expander = _make_branch_expander(coder)
    plan = parse('[:seq {} [:fs/read {:path "y.txt"}]]', strict=False)
    proposals = expander.propose(plan, k=4)

    # Only the valid one survives.
    assert len(proposals) == 1
    action, _prior = proposals[0]
    assert isinstance(action, SubstituteLeafAction)


def test_expander_decodes_add_step_action():
    """I5: AddStepAction happy path. Validates `at: int + bool-isinstance`
    branch + new_child_edn parse + isinstance(action, AddStepAction)."""
    coder = MagicMock()
    coder.model = "claude-test"
    coder.substrate.effect.perform.return_value = {
        "tool_calls": [{"input": {"proposals": [
            {
                "kind": "AddStepAction",
                "target_path": [],   # root
                "at": 1,
                "new_child_edn": '[:fs/read {:path "added.txt"}]',
                "logit": 2.0,
            },
        ]}}],
        "text": "",
    }
    expander = _make_branch_expander(coder)
    plan = parse('[:seq {} [:fs/read {:path "y.txt"}]]', strict=False)
    proposals = expander.propose(plan, k=4)

    assert len(proposals) == 1
    action, prior = proposals[0]
    assert isinstance(action, AddStepAction)
    assert action.at == 1
    # Single-survivor softmax → 1.0
    assert abs(prior - 1.0) < _PRIOR_TOL


@pytest.mark.parametrize("bad_proposal", [
    # Non-Mapping entry.
    "not a dict",
    # Missing 'kind'.
    {"target_path": [0], "logit": 1.0},
    # Unknown 'kind'.
    {"kind": "FooAction", "target_path": [0], "logit": 1.0},
    # Non-numeric logit.
    {"kind": "SubstituteLeafAction", "target_path": [0],
     "new_leaf_edn": '[:fs/read {:path "x.txt"}]', "logit": "high"},
    # Bool logit (FD2-style: bool-isinstance-int trap).
    {"kind": "SubstituteLeafAction", "target_path": [0],
     "new_leaf_edn": '[:fs/read {:path "x.txt"}]', "logit": True},
    # Non-list target_path (I2 — string would silently char-split before fix).
    {"kind": "SubstituteLeafAction", "target_path": "abc",
     "new_leaf_edn": '[:fs/read {:path "x.txt"}]', "logit": 1.0},
])
def test_expander_drops_malformed_proposal_silently(bad_proposal):
    """I6 + I2: malformed proposals are dropped silently. The wrapper's
    contract is "drop bad, don't raise". Each filter branch has its own
    skip-path; this parametrize asserts ALL of them drop correctly."""
    coder = MagicMock()
    coder.model = "claude-test"
    coder.substrate.effect.perform.return_value = {
        "tool_calls": [{"input": {"proposals": [bad_proposal]}}],
        "text": "",
    }
    expander = _make_branch_expander(coder)
    plan = parse('[:seq {} [:fs/read {:path "y.txt"}]]', strict=False)
    proposals = expander.propose(plan, k=4)

    assert proposals == ()
