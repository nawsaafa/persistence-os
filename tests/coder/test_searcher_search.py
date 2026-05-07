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

import datetime as _dt

from persistence.coder._searcher import _escalate_branch_body
from persistence.coder._searcher_errors import BranchPayloadValidation
from persistence.coder._types import LLMDecision
from persistence.effect.handlers.callable import make_callable_llm_handler
from persistence.effect.handlers.fs import make_fs_handler
from persistence.plan import parse, unparse
from persistence.plan._mcts import MCTSResult
from persistence.sdk import Substrate

_EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)


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


# --------------------------------------------------------------------------- #
# G4 / LD0 acceptance signal — REAL `s.plan.mcts_search` end-to-end
#
# The tests above mock `mcts_search` to isolate BRIDGE behavior from
# ENGINE behavior. Codex Impl R1 (T9.1) flagged that the frozen-doc
# G4/LD0 signal explicitly calls for a "scripted K=2 real-MCTS" run
# proving losers DON'T dispatch handlers under real engine control.
#
# These tests close that gap with a smart `:llm/call` handler that
# distinguishes expander vs evaluator by tool name, runs real
# `s.plan.mcts_search` with tight bounds, and asserts:
#   - LD0: only the winner's plan executes (disk-marker invariant)
#   - LD5: ZERO `:fork/*` datoms in the audit chain (mcts_search uses
#          in-memory dict transposition, NOT s.txn.fork)
#   - LD5: `:plan/done` provenance datom emitted exactly once on winner
# --------------------------------------------------------------------------- #


def _make_real_mcts_call_fn(*, winner_path: str, loser_path: str):
    """Smart `:llm/call` mock that distinguishes expander vs evaluator
    by tool name. Returns 2 SubstituteLeafActions per expansion (winner
    leaf vs loser leaf); evaluator scores plans containing 'winner'
    substring at 0.95, 'loser' at 0.30, seed at 0.05 — deterministic
    winner under PUCT selection."""

    def call_fn(*, model, messages, tools=None, temperature=None, max_tokens=None):
        tool_name = tools[0]["name"] if tools else ""

        if tool_name == "emit_branch_proposals":
            return {
                "tool_calls": [{
                    "input": {
                        "proposals": [
                            {
                                "kind": "SubstituteLeafAction",
                                "target_path": [0],
                                "new_leaf_edn": (
                                    f'[:fs/write {{:path "{winner_path}" '
                                    f':bytes_or_text "winner_content"}}]'
                                ),
                                "logit": 2.0,
                            },
                            {
                                "kind": "SubstituteLeafAction",
                                "target_path": [0],
                                "new_leaf_edn": (
                                    f'[:fs/write {{:path "{loser_path}" '
                                    f':bytes_or_text "loser_content"}}]'
                                ),
                                "logit": 1.0,
                            },
                        ]
                    }
                }],
                "text": "",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "fingerprint": "test",
            }

        if tool_name == "emit_branch_score":
            user_content = messages[1]["content"]
            if "winner" in user_content:
                score = 0.95
            elif "loser" in user_content:
                score = 0.30
            else:
                score = 0.05
            return {
                "tool_calls": [{"input": {"score": score}}],
                "text": "",
                "usage": {"input_tokens": 5, "output_tokens": 1},
                "fingerprint": "test",
            }

        return {"tool_calls": [], "text": "unknown tool"}

    return call_fn


def test_real_mcts_ld0_disk_marker_invariant_under_engine_control(s, tmp_path):
    """G4/LD0 — REAL `s.plan.mcts_search` proves losers DON'T dispatch.

    Tight bounds (`max_iter=2, expander_k=2`) keep the search small
    while still exercising the engine's expand/evaluate/select cycle.
    The smart call_fn produces winner/loser SubstituteLeafActions; the
    engine selects the highest-q child (winner) and the bridge executes
    it once via 2.3a's `_escalate_plan_body`.

    Falsifiability: if the bridge accidentally dispatched the loser
    branch's leaves (or the seed's), `loser.txt` or `seed.txt` would
    appear on disk → assertion fails.
    """
    seed_path = str(tmp_path / "seed.txt")
    winner_path = str(tmp_path / "winner.txt")
    loser_path = str(tmp_path / "loser.txt")

    seed_edn = (
        f'[:seq {{}} [:fs/write {{:path "{seed_path}" :bytes_or_text "seed"}}]]'
    )

    s.effect.install_handler(
        make_callable_llm_handler(
            call_fn=_make_real_mcts_call_fn(
                winner_path=winner_path, loser_path=loser_path,
            )
        ),
        position="bottom",
    )

    coder = _CoderStub(substrate=s)
    decision = LLMDecision(
        kind="branch",
        confidence=0.9,
        payload={
            "seed_plan_edn": seed_edn,
            "mcts_config": {"max_iter": 2, "expander_k": 2},
        },
    )

    _escalate_branch_body(coder, decision)

    files = sorted(os.listdir(tmp_path))
    assert "winner.txt" in files, f"winner missing! files={files}"
    assert "loser.txt" not in files, (
        f"LD0 broken — loser.txt exists, search dispatched losers; files={files}"
    )
    assert "seed.txt" not in files, (
        f"LD0 broken — seed.txt exists, seed plan dispatched; files={files}"
    )


def test_real_mcts_emits_zero_fork_datoms(s, tmp_path):
    """LD5 / R0-fold B1: `mcts_search` uses in-memory dict
    transposition (`_mcts.py:881-882`), NOT `s.txn.fork`. The audit
    chain MUST contain ZERO `:fork/*` datoms after a real-MCTS run."""
    seed_path = str(tmp_path / "seed.txt")
    winner_path = str(tmp_path / "winner.txt")
    loser_path = str(tmp_path / "loser.txt")
    seed_edn = (
        f'[:seq {{}} [:fs/write {{:path "{seed_path}" :bytes_or_text "seed"}}]]'
    )

    s.effect.install_handler(
        make_callable_llm_handler(
            call_fn=_make_real_mcts_call_fn(
                winner_path=winner_path, loser_path=loser_path,
            )
        ),
        position="bottom",
    )
    coder = _CoderStub(substrate=s)
    decision = LLMDecision(
        kind="branch",
        confidence=0.9,
        payload={
            "seed_plan_edn": seed_edn,
            "mcts_config": {"max_iter": 2, "expander_k": 2},
        },
    )

    _escalate_branch_body(coder, decision)

    view = s.fact.since(_EPOCH)
    fork_datoms = [d for d in view.datoms if d.a.startswith("fork/")]
    assert fork_datoms == [], (
        f"LD5 broken — mcts_search must NOT emit :fork/* datoms; got "
        f"{[d.a for d in fork_datoms]!r}"
    )


def test_real_mcts_emits_exactly_one_plan_done_on_winner(s, tmp_path):
    """LD5: `:plan/done` provenance datom emitted exactly once via 2.3a's
    `_escalate_plan_body` for the winner. Losers never reach
    `_escalate_plan_body` so they emit zero `:plan/done`."""
    seed_path = str(tmp_path / "seed.txt")
    winner_path = str(tmp_path / "winner.txt")
    loser_path = str(tmp_path / "loser.txt")
    seed_edn = (
        f'[:seq {{}} [:fs/write {{:path "{seed_path}" :bytes_or_text "seed"}}]]'
    )

    s.effect.install_handler(
        make_callable_llm_handler(
            call_fn=_make_real_mcts_call_fn(
                winner_path=winner_path, loser_path=loser_path,
            )
        ),
        position="bottom",
    )
    coder = _CoderStub(substrate=s)
    decision = LLMDecision(
        kind="branch",
        confidence=0.9,
        payload={
            "seed_plan_edn": seed_edn,
            "mcts_config": {"max_iter": 2, "expander_k": 2},
        },
    )

    _escalate_branch_body(coder, decision)

    view = s.fact.since(_EPOCH)
    plan_done_datoms = [
        d for d in view.datoms if d.a == "plan/done" and d.op == "assert"
    ]
    assert len(plan_done_datoms) == 1, (
        f"LD5 broken — expected exactly 1 :plan/done from winner execution; "
        f"got {len(plan_done_datoms)}"
    )
