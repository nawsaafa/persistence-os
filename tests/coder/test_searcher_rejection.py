"""T8/G6+G7 — rejection-path tests with spy double-protection.

G6: G-TRIGGER+PAYLOAD acceptance signal — non-EDN-shape payloads
NEVER reach `mcts_search`; payload-stage rejection short-circuits
before any expander/evaluator is constructed.

G7: ``_should_escalate_branch`` LD1 gate — non-`branch` decisions
never reach `_escalate_branch_body`; the gate is `kind == "branch"`
ONLY (confidence-based half deferred to 2.4a per LD1 R0 codex finding).

Falsifiability discipline (2.3a precedent — held in Impl R1 review):
the spy on `s.plan.mcts_search` has BOTH safety layers —
  - `side_effect=AssertionError(...)` so any invocation raises,
  - `assert_not_called()` at the end of the test verifies no call.
This double-protection catches reject-path leaks via either layer.
"""
from __future__ import annotations

import dataclasses as dc
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from persistence.coder._searcher import _escalate_branch_body
from persistence.coder._searcher_errors import BranchPayloadValidation
from persistence.coder._types import LLMDecision
from persistence.effect.handlers.fs import make_fs_handler
from persistence.sdk import Substrate


@dc.dataclass
class _CoderStub:
    substrate: Substrate
    model: str = "claude-test-2.3b"


def _spy_mcts_search() -> MagicMock:
    """Spy that fails fast if invoked. Use as patch target."""
    return MagicMock(side_effect=AssertionError("mcts_search must NOT be called"))


@pytest.fixture
def s(tmp_path: Path):
    """Real substrate so payload-stage validators can run; fs handler
    installed for parity with happy-path test substrates."""
    with Substrate.open("memory") as substrate:
        fs_handler = make_fs_handler(project_root=tmp_path, scratch_dir=tmp_path)
        substrate.effect.install_handler(fs_handler, position="bottom")
        yield substrate


# --------------------------------------------------------------------------- #
# G6 — payload-stage rejection (Stage 1 byte budget, Stage 2 parse,
#     Stage 3 semantic, Stage 4 mcts_config bounds)
# --------------------------------------------------------------------------- #


def test_g6_missing_seed_plan_edn_rejects_before_mcts_search(s):
    """G6: payload missing required `seed_plan_edn` → reject pre-search."""
    decision = LLMDecision(kind="branch", confidence=1.0, payload={})

    spy = _spy_mcts_search()
    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = spy  # type: ignore[method-assign]

    with pytest.raises(BranchPayloadValidation) as excinfo:
        _escalate_branch_body(coder, decision)

    assert excinfo.value.field == "seed_plan_edn"
    spy.assert_not_called()  # double-protection alongside side_effect


def test_g6_byte_budget_exceeded_rejects_before_mcts_search(s, tmp_path):
    """G6: payload `seed_plan_edn` over 8192-byte cap → reject pre-search."""
    long_path = "x" * 8200
    edn = f'[:seq {{}} [:fs/read {{:path "{long_path}"}}]]'
    decision = LLMDecision(
        kind="branch", confidence=1.0, payload={"seed_plan_edn": edn},
    )

    spy = _spy_mcts_search()
    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = spy  # type: ignore[method-assign]

    with pytest.raises(BranchPayloadValidation):
        _escalate_branch_body(coder, decision)

    spy.assert_not_called()


def test_g6_branch_leaf_in_seed_rejects_before_mcts_search(s):
    """G6: `:branch` leaf in seed plan → semantic validator rejects
    pre-search (re-raised from PlanPayloadValidation as
    BranchPayloadValidation per LD2)."""
    edn = '[:seq {} [:branch {}]]'
    decision = LLMDecision(
        kind="branch", confidence=1.0, payload={"seed_plan_edn": edn},
    )

    spy = _spy_mcts_search()
    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = spy  # type: ignore[method-assign]

    with pytest.raises(BranchPayloadValidation):
        _escalate_branch_body(coder, decision)

    spy.assert_not_called()


def test_g7_unsupported_mcts_config_field_rejects_before_mcts_search(s, tmp_path):
    """G7 (LD6): closed-set narrowing — a real MCTSConfig field that's
    deliberately NOT in `_PAYLOAD_OVERRIDABLE_FIELDS` is rejected."""
    edn = f'[:seq {{}} [:fs/read {{:path "{tmp_path / "x.txt"}"}}]]'
    decision = LLMDecision(
        kind="branch",
        confidence=1.0,
        payload={
            "seed_plan_edn": edn,
            "mcts_config": {"simple_regret_threshold": 0.1},  # not overridable
        },
    )

    spy = _spy_mcts_search()
    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = spy  # type: ignore[method-assign]

    with pytest.raises(BranchPayloadValidation):
        _escalate_branch_body(coder, decision)

    spy.assert_not_called()


def test_g7_max_iter_over_cap_rejects_before_mcts_search(s, tmp_path):
    """G7 (LD6): `max_iter > MAX_BRANCH_MAX_ITER` (50) is rejected."""
    edn = f'[:seq {{}} [:fs/read {{:path "{tmp_path / "x.txt"}"}}]]'
    decision = LLMDecision(
        kind="branch",
        confidence=1.0,
        payload={
            "seed_plan_edn": edn,
            "mcts_config": {"max_iter": 51},
        },
    )

    spy = _spy_mcts_search()
    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = spy  # type: ignore[method-assign]

    with pytest.raises(BranchPayloadValidation) as excinfo:
        _escalate_branch_body(coder, decision)

    assert "max_iter" in excinfo.value.field
    spy.assert_not_called()
