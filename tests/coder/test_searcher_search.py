"""T6/G3+G4 — `_escalate_branch_body` happy path + LD0 single-execution invariant.

LD0 single-execution invariant: losers' leaves are STRUCTURAL Plan-AST
nodes inside the search tree; ONLY the winner's plan is dispatched
through `_escalate_plan_body`. The disk-marker test pins this: a winner
that writes a file gets exactly one file on disk; if losers also ran,
multiple files would appear.

LD3: providers route via substrate.effect.perform(":llm/call", ...) —
verified end-to-end in T4/T5 unit tests; T6 verifies the bridge layer
delegates correctly.

Note on mcts_search mocking: T6 deliberately mocks `s.plan.mcts_search`
to a MagicMock returning a hand-crafted MCTSResult. The full real-MCTS
audit-chain G4 acceptance signal (`:mcts/iteration` chain shape) is
queued as part of T7 / T9.1 (see /tmp/2.3b-nice-residuals.md). Reasons:

  1. T6 isolates BRIDGE behavior from ENGINE behavior; engine correctness
     is the responsibility of `tests/plan/test_mcts.py` (substrate-side).
  2. Real MCTS with fixed-proposal mocks deadlocks on transposition
     deduplication (zero new uniques per iter blocks sane termination
     under bridge-default `max_iter=50`).
  3. The LD0 invariant — what _escalate_branch_body does with the
     winner — is fully testable at the bridge layer.

FD1 (T2 cascade): all EDN strings use canonical bare-keyword form
`[:seq {} ...]` (NOT quoted-keyword).
"""
from __future__ import annotations

import dataclasses as dc
import os
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from persistence.coder._searcher import _escalate_branch_body
from persistence.coder._searcher_errors import BranchPayloadValidation
from persistence.coder._types import LLMDecision
from persistence.effect.handlers.fs import make_fs_handler
from persistence.plan import parse, unparse
from persistence.plan._mcts import MCTSResult
from persistence.sdk import Substrate


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


@dc.dataclass
class _CoderStub:
    """Minimal Coder-shaped stub. _escalate_branch_body reads .substrate
    and .model. Mirrors test_planner_execute.py:_CoderStub."""

    substrate: Substrate
    model: str = "claude-test-2.3b"


def _make_mcts_result(winner_node, *, terminated_by="max_iter") -> MCTSResult:
    """Hand-crafted MCTSResult with all 9 frozen-dataclass fields populated."""
    return MCTSResult(
        winner=winner_node,
        winner_plan_id="0" * 64,
        initial_plan_id="1" * 64,
        search_id="2" * 64,
        iter_count=3,
        unique_plans_visited=4,
        terminated_by=terminated_by,
        root_q=0.5,
        tree_dump=(),
    )


def _branch_decision(seed_edn: str, mcts_config: dict | None = None) -> LLMDecision:
    payload: dict[str, Any] = {"seed_plan_edn": seed_edn}
    if mcts_config is not None:
        payload["mcts_config"] = mcts_config
    return LLMDecision(kind="branch", confidence=0.9, payload=payload)


@pytest.fixture
def s(tmp_path: Path):
    """Real in-memory substrate with `:fs/*` handlers installed so
    `_escalate_plan_body` can dispatch the winner's leaves end-to-end.
    Pattern mirrors `tests/coder/test_loop_replay.py:137` (2.2a).
    Reads + writes are confined to `tmp_path` for the test."""
    project_root = tmp_path
    scratch_dir = tmp_path
    project_root.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    with Substrate.open("memory") as substrate:
        fs_handler = make_fs_handler(
            project_root=project_root, scratch_dir=scratch_dir
        )
        substrate.effect.install_handler(fs_handler, position="bottom")
        yield substrate


# --------------------------------------------------------------------------- #
# G-SCOPE / LD0 — disk-marker single-execution invariant (load-bearing)
# --------------------------------------------------------------------------- #


def test_only_winner_writes_marker_to_disk(s, tmp_path):
    """LD0: only the winner's plan is dispatched. The winner writes
    `winner.txt`; losers exist only as structural search-tree nodes
    inside the (mocked) MCTSResult and NEVER reach _escalate_plan_body.

    Falsifiability: if the impl ever called _escalate_plan_body for
    losers, additional files would appear in `tmp_path`.
    """
    winner_path = str(tmp_path / "winner.txt")
    seed_edn = f'[:seq {{}} [:fs/write {{:path "{tmp_path / "seed.txt"}" :bytes_or_text "seed"}}]]'

    winner_plan = parse(
        f'[:seq {{}} [:fs/write {{:path "{winner_path}" :bytes_or_text "winner-content"}}]]',
        strict=False,
    )

    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = MagicMock(  # type: ignore[method-assign]
        return_value=_make_mcts_result(winner_plan),
    )

    decision = _branch_decision(seed_edn)
    _escalate_branch_body(coder, decision)

    files_in_tmp = sorted(os.listdir(tmp_path))
    assert files_in_tmp == ["winner.txt"], (
        f"LD0 broken — expected only winner.txt, got {files_in_tmp!r}"
    )
    assert Path(winner_path).read_text() == "winner-content"
    assert not Path(tmp_path / "seed.txt").exists(), (
        "LD0 broken — seed plan was dispatched (it should be replaced by winner)"
    )


# --------------------------------------------------------------------------- #
# G3 — bridge wiring: kwargs, return shape, delegation
# --------------------------------------------------------------------------- #


def test_mcts_search_called_with_expected_kwargs(s, tmp_path):
    """LD3+LD5: bridge calls mcts_search with seed_plan + expander +
    evaluator + started_at_ms (positive int) + config + db kwargs."""
    seed_edn = f'[:seq {{}} [:fs/write {{:path "{tmp_path / "ok.txt"}" :bytes_or_text "x"}}]]'
    winner_plan = parse(seed_edn, strict=False)

    mock_search = MagicMock(return_value=_make_mcts_result(winner_plan))
    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = mock_search  # type: ignore[method-assign]

    _escalate_branch_body(coder, _branch_decision(seed_edn))

    assert mock_search.call_count == 1
    _, kwargs = mock_search.call_args
    # The seed plan is passed positionally; remaining are kwargs.
    assert "expander" in kwargs
    assert "evaluator" in kwargs
    assert "config" in kwargs
    assert "db" in kwargs
    # FD1: started_at_ms is a positive int (not bool, not float).
    started = kwargs["started_at_ms"]
    assert isinstance(started, int) and not isinstance(started, bool)
    assert started > 0


def test_mcts_search_receives_resolved_bridge_default_config(s, tmp_path):
    """LD6: with no payload override, bridge passes _BRANCH_BRIDGE_DEFAULT_CONFIG
    (max_iter=50, expander_k=4) — distinct from engine default max_iter=200."""
    seed_edn = f'[:seq {{}} [:fs/write {{:path "{tmp_path / "ok.txt"}" :bytes_or_text "x"}}]]'
    winner_plan = parse(seed_edn, strict=False)

    mock_search = MagicMock(return_value=_make_mcts_result(winner_plan))
    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = mock_search  # type: ignore[method-assign]

    _escalate_branch_body(coder, _branch_decision(seed_edn))

    config = mock_search.call_args.kwargs["config"]
    assert config.max_iter == 50
    assert config.expander_k == 4


def test_mcts_search_receives_payload_override_config(s, tmp_path):
    """LD6: payload `mcts_config` override is honored end-to-end."""
    seed_edn = f'[:seq {{}} [:fs/write {{:path "{tmp_path / "ok.txt"}" :bytes_or_text "x"}}]]'
    winner_plan = parse(seed_edn, strict=False)

    mock_search = MagicMock(return_value=_make_mcts_result(winner_plan))
    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = mock_search  # type: ignore[method-assign]

    decision = _branch_decision(seed_edn, mcts_config={"max_iter": 12, "expander_k": 3})
    _escalate_branch_body(coder, decision)

    config = mock_search.call_args.kwargs["config"]
    assert config.max_iter == 12
    assert config.expander_k == 3


def test_winner_executes_via_escalate_plan_body(s, tmp_path, monkeypatch):
    """LD0: bridge synthesizes LLMDecision(kind='plan',
    payload={'plan_edn': unparse(winner)}) and calls 2.3a's
    _escalate_plan_body exactly once."""
    seed_edn = f'[:seq {{}} [:fs/write {{:path "{tmp_path / "seed.txt"}" :bytes_or_text "s"}}]]'
    winner_path = str(tmp_path / "winner.txt")
    winner_plan = parse(
        f'[:seq {{}} [:fs/write {{:path "{winner_path}" :bytes_or_text "w"}}]]',
        strict=False,
    )

    captured: list[Any] = []

    import persistence.coder._searcher as searcher_mod

    def spy_escalate_plan_body(coder_arg, decision_arg):
        captured.append((coder_arg, decision_arg))

    monkeypatch.setattr(searcher_mod, "_escalate_plan_body", spy_escalate_plan_body)

    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = MagicMock(  # type: ignore[method-assign]
        return_value=_make_mcts_result(winner_plan),
    )

    _escalate_branch_body(coder, _branch_decision(seed_edn))

    assert len(captured) == 1, "LD0: _escalate_plan_body must be called exactly once"
    _, synthesized = captured[0]
    assert synthesized.kind == "plan"
    assert synthesized.confidence == 1.0
    assert synthesized.payload["plan_edn"] == unparse(winner_plan)


# --------------------------------------------------------------------------- #
# Payload-stage rejection happens BEFORE mcts_search dispatch
# --------------------------------------------------------------------------- #


def test_byte_budget_rejection_short_circuits_before_search(s):
    """Stage 1: byte-budget rejection short-circuits — mcts_search never
    called. (Mirror of T2 G1; here we verify the integration path.)"""
    long_path = "x" * 8200
    seed_edn = f'[:seq {{}} [:fs/write {{:path "{long_path}" :bytes_or_text "x"}}]]'

    mock_search = MagicMock()
    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = mock_search  # type: ignore[method-assign]

    with pytest.raises(BranchPayloadValidation) as excinfo:
        _escalate_branch_body(coder, _branch_decision(seed_edn))

    assert "byte" in excinfo.value.reason.lower()
    mock_search.assert_not_called()


def test_semantic_rejection_short_circuits_before_search(s):
    """Stage 3: semantic-validator rejection short-circuits.
    `:par`-rooted seed → BranchPayloadValidation; mcts_search never called.

    Also verifies the LD2 contract: 2.3a's PlanPayloadValidation (raised
    by validate_plan_for_2_3a) is re-raised as BranchPayloadValidation
    in the bridge — uniform error contract."""
    seed_edn = '[:par {} [:fs/read {:path "x.txt"}]]'

    mock_search = MagicMock()
    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = mock_search  # type: ignore[method-assign]

    with pytest.raises(BranchPayloadValidation):
        _escalate_branch_body(coder, _branch_decision(seed_edn))

    mock_search.assert_not_called()
