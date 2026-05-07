"""T6/G5b — winner-execution failure inherits 2.3a's PlanExecutionFailed.

LD4 invariant: when the WINNER plan's leaf raises during execution
(after MCTS picked it cleanly), `_escalate_plan_body` re-raises
`PlanExecutionFailed` per 2.3a. The 2.3b bridge MUST NOT wrap this
in `BranchSearchFailed` — the search succeeded; only the post-search
execution failed. `BranchSearchFailed` is reserved for *search-layer*
failures (T7 path-1 bubble-out + path-2 all-evaluations-failed).

Falsifiability: if `_escalate_branch_body` ever wrapped winner-execution
failures in `BranchSearchFailed`, the test below would catch it
(asserts the exception type is `PlanExecutionFailed`, NOT
`BranchSearchFailed`).
"""
from __future__ import annotations

import dataclasses as dc
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from persistence.coder._planner_errors import PlanExecutionFailed
from persistence.coder._searcher import _escalate_branch_body
from persistence.coder._searcher_errors import BranchSearchFailed
from persistence.coder._types import LLMDecision
from persistence.effect.handlers.fs import make_fs_handler
from persistence.plan import parse
from persistence.plan._mcts import MCTSResult
from persistence.sdk import Substrate


@dc.dataclass
class _CoderStub:
    substrate: Substrate
    model: str = "claude-test-2.3b"


def _make_mcts_result(winner_node) -> MCTSResult:
    return MCTSResult(
        winner=winner_node,
        winner_plan_id="0" * 64,
        initial_plan_id="1" * 64,
        search_id="2" * 64,
        iter_count=3,
        unique_plans_visited=4,
        terminated_by="max_iter",
        root_q=0.5,
        tree_dump=(),
    )


@pytest.fixture
def s(tmp_path: Path):
    """Real substrate with `:fs/*` capability-confined to `tmp_path`.
    Writes outside `tmp_path` are rejected by `make_fs_handler` —
    that's the failure-induction mechanism for these G5b tests."""
    with Substrate.open("memory") as substrate:
        fs_handler = make_fs_handler(project_root=tmp_path, scratch_dir=tmp_path)
        substrate.effect.install_handler(fs_handler, position="bottom")
        yield substrate


def _branch_decision(seed_edn: str) -> LLMDecision:
    return LLMDecision(
        kind="branch", confidence=0.9, payload={"seed_plan_edn": seed_edn},
    )


# --------------------------------------------------------------------------- #
# G5b — winner-execution failure propagates as PlanExecutionFailed
# --------------------------------------------------------------------------- #


def test_winner_execution_failure_raises_plan_execution_failed(s, tmp_path):
    """Winner's `:fs/write` targets a path OUTSIDE the scratch_dir
    capability. The fs handler raises; `_escalate_plan_body` wraps as
    `PlanExecutionFailed`; `_escalate_branch_body` lets it propagate.
    """
    seed_edn = f'[:seq {{}} [:fs/write {{:path "{tmp_path / "ok.txt"}" :bytes_or_text "x"}}]]'
    # Winner writes OUTSIDE the scratch_dir — fs handler rejects.
    winner_outside_path = "/tmp/persistence-os-2.3b-must-not-exist.txt"
    winner_plan = parse(
        f'[:seq {{}} [:fs/write {{:path "{winner_outside_path}" :bytes_or_text "x"}}]]',
        strict=False,
    )

    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = MagicMock(  # type: ignore[method-assign]
        return_value=_make_mcts_result(winner_plan),
    )

    with pytest.raises(PlanExecutionFailed):
        _escalate_branch_body(coder, _branch_decision(seed_edn))


def test_winner_execution_failure_is_not_wrapped_in_branch_search_failed(s, tmp_path):
    """LD4: `BranchSearchFailed` is NEVER raised for post-search
    execution failures. The bridge MUST let `PlanExecutionFailed`
    propagate unchanged."""
    seed_edn = f'[:seq {{}} [:fs/write {{:path "{tmp_path / "ok.txt"}" :bytes_or_text "x"}}]]'
    winner_outside_path = "/tmp/persistence-os-2.3b-must-not-exist.txt"
    winner_plan = parse(
        f'[:seq {{}} [:fs/write {{:path "{winner_outside_path}" :bytes_or_text "x"}}]]',
        strict=False,
    )

    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = MagicMock(  # type: ignore[method-assign]
        return_value=_make_mcts_result(winner_plan),
    )

    raised: Exception | None = None
    try:
        _escalate_branch_body(coder, _branch_decision(seed_edn))
    except Exception as e:  # noqa: BLE001
        raised = e

    assert raised is not None, "expected the winner-execution failure to raise"
    assert isinstance(raised, PlanExecutionFailed), (
        f"LD4 broken — winner-execution failure must propagate as "
        f"PlanExecutionFailed, got {type(raised).__name__}"
    )
    assert not isinstance(raised, BranchSearchFailed), (
        "LD4 broken — winner-execution failure must NOT be wrapped in BranchSearchFailed"
    )


def test_mcts_search_was_actually_invoked_before_winner_execution(s, tmp_path):
    """G5b sanity: confirm the failure path runs AFTER mcts_search
    succeeds (i.e. we're testing post-search winner-execution failure,
    not pre-search payload-validation failure)."""
    seed_edn = f'[:seq {{}} [:fs/write {{:path "{tmp_path / "ok.txt"}" :bytes_or_text "x"}}]]'
    winner_outside_path = "/tmp/persistence-os-2.3b-must-not-exist.txt"
    winner_plan = parse(
        f'[:seq {{}} [:fs/write {{:path "{winner_outside_path}" :bytes_or_text "x"}}]]',
        strict=False,
    )

    mock_search = MagicMock(return_value=_make_mcts_result(winner_plan))
    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = mock_search  # type: ignore[method-assign]

    with pytest.raises(PlanExecutionFailed):
        _escalate_branch_body(coder, _branch_decision(seed_edn))

    assert mock_search.call_count == 1, (
        "mcts_search must have been invoked exactly once — the failure "
        "must be in post-search winner execution, not pre-search payload"
    )
