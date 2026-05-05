"""Phase 2.1b — _types.py LLMDecision shape tests (design § 3.2)."""
from __future__ import annotations

import dataclasses

import pytest

from persistence.coder._types import LLMDecision, Observation


def test_llmdecision_has_three_fields():
    fields = {f.name for f in dataclasses.fields(LLMDecision)}
    assert fields == {"kind", "confidence", "payload"}


def test_llmdecision_is_frozen():
    d = LLMDecision(kind="act", confidence=0.9, payload={})
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.kind = "plan"  # type: ignore[misc]


def test_llmdecision_kind_typing_act():
    d = LLMDecision(kind="act", confidence=0.9, payload={"tool": "fs/write"})
    assert d.kind == "act"


def test_llmdecision_kind_typing_plan():
    d = LLMDecision(kind="plan", confidence=0.7, payload={"steps": [...]})
    assert d.kind == "plan"


def test_llmdecision_kind_typing_branch():
    d = LLMDecision(kind="branch", confidence=0.4, payload={})
    assert d.kind == "branch"


def test_observation_has_2_2a_fields():
    obs = Observation()
    fields = {f.name for f in dataclasses.fields(Observation)}
    assert fields == {"iter_count", "recent_decisions", "recent_actions"}  # Phase 2.2a T4
