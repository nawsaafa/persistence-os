"""B4 — ``LLMExpander`` is pure delegation to a provider closure.

Per design §8 + §17 ADR-5, ``LLMExpander.propose`` is exactly
``return self._provider(plan, k)`` — no MCTS-owned registry indirection,
no in-class prior-sum-to-1.0 enforcement, no truncation. The MCTS loop
(B6) owns the cache-miss assertion that raises ``ExpanderContractError``;
this module only verifies the byte-identical pass-through and Protocol
satisfaction. End-to-end "priors don't sum to 1.0 → ``ExpanderContract
Error``" coverage lands in B6's loop tests.

A separate ``test_expander_contract_error_class`` test verifies the
error class is wired (it ships in B4 even though the raise-site is B6).
"""
from __future__ import annotations

from collections.abc import Sequence

import pytest

from persistence.plan import (
    Action,
    Expander,
    ExpanderContractError,
    LLMExpander,
    Node,
    SubstituteLeafAction,
)


# --- Fixtures ------------------------------------------------------------ #


def _leaf(tag: str = ":leaf/predict", prompt: str = "x") -> Node:
    return Node(tag=tag, attrs={"prompt": prompt})


@pytest.fixture
def plan() -> Node:
    return Node(tag=":plan/root", attrs={"k": 1}, children=(_leaf(),))


@pytest.fixture
def action_alpha() -> SubstituteLeafAction:
    return SubstituteLeafAction(target_path=(0,), new_leaf=_leaf(":leaf/alpha"))


@pytest.fixture
def action_beta() -> SubstituteLeafAction:
    return SubstituteLeafAction(target_path=(0,), new_leaf=_leaf(":leaf/beta"))


# --- Protocol satisfaction ----------------------------------------------- #


def test_llm_expander_satisfies_expander_protocol(action_alpha):
    """``LLMExpander`` is structurally an ``Expander`` (``@runtime_checkable``)."""

    def provider(_plan: Node, _k: int) -> Sequence[tuple[Action, float]]:
        return [(action_alpha, 1.0)]

    expander = LLMExpander(provider=provider)
    assert isinstance(expander, Expander)


# --- Pass-through behaviour --------------------------------------------- #


def test_propose_returns_provider_output_byte_identically(
    plan, action_alpha, action_beta
):
    """``LLMExpander.propose`` is pure delegation; no in-class transform."""
    pinned: Sequence[tuple[Action, float]] = [
        (action_alpha, 0.5),
        (action_beta, 0.5),
    ]

    def provider(_plan: Node, _k: int) -> Sequence[tuple[Action, float]]:
        return pinned

    expander = LLMExpander(provider=provider)
    result = expander.propose(plan, k=4)
    # Byte-identity: same object reference (delegation, not copy).
    assert result is pinned


def test_propose_passes_plan_and_k_to_provider(plan, action_alpha):
    """``LLMExpander.propose`` forwards ``plan`` and ``k`` to the provider."""
    captured: dict[str, object] = {}

    def provider(p: Node, k: int) -> Sequence[tuple[Action, float]]:
        captured["plan"] = p
        captured["k"] = k
        return [(action_alpha, 1.0)]

    LLMExpander(provider=provider).propose(plan, k=7)
    assert captured["plan"] is plan
    assert captured["k"] == 7


def test_propose_empty_provider_output_passes_through(plan):
    """Empty provider output flows through (terminal-node signal; design §10)."""

    def provider(_plan: Node, _k: int) -> Sequence[tuple[Action, float]]:
        return ()

    result = LLMExpander(provider=provider).propose(plan, k=4)
    assert result == ()


def test_propose_does_not_truncate_provider_output(plan):
    """``LLMExpander`` does NOT truncate; the provider owns ``k`` semantics.

    The MCTS loop (B6) treats the provider's output as the contract
    surface — if the provider returns more than ``k``, that's the
    provider's choice and MCTS validates the prior-sum on whatever the
    provider returned.
    """
    actions = [
        SubstituteLeafAction(target_path=(0,), new_leaf=_leaf(f":leaf/{i}"))
        for i in range(5)
    ]
    pinned: Sequence[tuple[Action, float]] = [(a, 0.2) for a in actions]

    def provider(_plan: Node, _k: int) -> Sequence[tuple[Action, float]]:
        return pinned

    result = LLMExpander(provider=provider).propose(plan, k=2)
    # Pure delegation: the 5 proposals come back even though k=2.
    assert len(result) == 5


# --- Error class wired -------------------------------------------------- #


def test_expander_contract_error_subclasses_value_error():
    """``ExpanderContractError`` is a ``ValueError`` (catchable as such)."""
    err = ExpanderContractError("priors sum to 0.5, not 1.0 ± _PRIOR_TOL")
    assert isinstance(err, ValueError)
    assert str(err) == "priors sum to 0.5, not 1.0 ± _PRIOR_TOL"
