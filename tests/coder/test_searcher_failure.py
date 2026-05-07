"""T7/G5a — search-layer failure two-path funnel (LD4).

Path-1 (bubble-out): `ExpanderContractError` or any raw expander
provider exception bubbles out of `mcts_search`. The bridge wraps with
try/except, emits one `:act/result(op=":mcts/search", error=...)`
datom, and raises `BranchSearchFailed` with `search_id=None`,
`iter_count=None`, `terminated_by=None` (search never completed enough
to have a search_id).

Path-2 (post-search detection): `mcts_search` returns cleanly but
`MCTSResult.terminated_by == "all_evaluations_failed"`. The bridge
inspects post-search, emits the same `:act/result` shape (with
`search_id`/`iter_count`/`terminated_by` populated from the
`MCTSResult`), and raises `BranchSearchFailed`.

Both paths: ZERO `:plan/done` datoms (winner never executed).
"""
from __future__ import annotations

import datetime as dt
import dataclasses as dc
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)

from persistence.coder._searcher import _escalate_branch_body
from persistence.coder._searcher_errors import BranchSearchFailed
from persistence.coder._types import LLMDecision
from persistence.effect.canonical import canonical_dumps
from persistence.effect.handlers.fs import make_fs_handler
from persistence.plan import parse
from persistence.plan._errors import ExpanderContractError
from persistence.plan._mcts import MCTSResult
from persistence.sdk import Substrate


@dc.dataclass
class _CoderStub:
    substrate: Substrate
    model: str = "claude-test-2.3b"


def _make_mcts_result(
    winner_node, *, terminated_by="max_iter", iter_count=3
) -> MCTSResult:
    return MCTSResult(
        winner=winner_node,
        winner_plan_id="0" * 64,
        initial_plan_id="1" * 64,
        search_id="abc123" + "0" * 58,
        iter_count=iter_count,
        unique_plans_visited=4,
        terminated_by=terminated_by,
        root_q=0.0,
        tree_dump=(),
    )


@pytest.fixture
def s(tmp_path: Path):
    """Real substrate with `:fs/*` capability-confined to `tmp_path`."""
    with Substrate.open("memory") as substrate:
        fs_handler = make_fs_handler(project_root=tmp_path, scratch_dir=tmp_path)
        substrate.effect.install_handler(fs_handler, position="bottom")
        yield substrate


def _branch_decision(seed_edn: str) -> LLMDecision:
    return LLMDecision(
        kind="branch", confidence=0.9, payload={"seed_plan_edn": seed_edn},
    )


def _act_result_search_datoms(s: Substrate):
    """Filter fact-store for :act/result datoms whose `op` is :mcts/search."""
    rows = []
    view = s.fact.since(_EPOCH)
    for d in view.datoms:
        if d.a != "act/result" or d.op != "assert":
            continue
        try:
            import json
            v = json.loads(d.v) if isinstance(d.v, str) else d.v
        except Exception:
            continue
        if isinstance(v, dict) and v.get("op") == ":mcts/search":
            rows.append(v)
    return rows


# --------------------------------------------------------------------------- #
# Path-1 — bubble-out
# --------------------------------------------------------------------------- #


def test_path1_expander_contract_error_funnels_to_branch_search_failed(s, tmp_path):
    """ExpanderContractError bubbles out → BranchSearchFailed with all
    three optional fields None (search never completed)."""
    seed_edn = f'[:seq {{}} [:fs/write {{:path "{tmp_path / "ok.txt"}" :bytes_or_text "x"}}]]'

    bubble_exc = ExpanderContractError("prior sum 0.9 != 1.0 (test fixture)")
    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = MagicMock(  # type: ignore[method-assign]
        side_effect=bubble_exc,
    )

    with pytest.raises(BranchSearchFailed) as excinfo:
        _escalate_branch_body(coder, _branch_decision(seed_edn))

    assert "ExpanderContractError" in excinfo.value.error_repr
    assert "prior sum" in excinfo.value.error_repr
    assert excinfo.value.search_id is None
    assert excinfo.value.iter_count is None
    assert excinfo.value.terminated_by is None
    # __cause__ chain preserved (raise ... from exc).
    assert excinfo.value.__cause__ is bubble_exc


def test_path1_emits_one_act_result_datom_with_op_mcts_search(s, tmp_path):
    """Path-1 emits exactly ONE :act/result with op=':mcts/search' +
    error field. The fact-store row is the audit trail observers see."""
    seed_edn = f'[:seq {{}} [:fs/write {{:path "{tmp_path / "ok.txt"}" :bytes_or_text "x"}}]]'

    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = MagicMock(  # type: ignore[method-assign]
        side_effect=ExpanderContractError("test"),
    )

    with pytest.raises(BranchSearchFailed):
        _escalate_branch_body(coder, _branch_decision(seed_edn))

    rows = _act_result_search_datoms(s)
    assert len(rows) == 1
    row = rows[0]
    assert row["op"] == ":mcts/search"
    assert "ExpanderContractError" in row["error"]
    # Path-1 has all three optional fields None.
    assert row["result_summary"]["search_id"] is None
    assert row["result_summary"]["iter_count"] is None
    assert row["result_summary"]["terminated_by"] is None


def test_path1_raw_provider_exception_also_funnels(s, tmp_path):
    """LD4 Path-1 catches ANY exception that bubbles out of mcts_search,
    not just ExpanderContractError. A bare RuntimeError from a provider
    closure must be wrapped identically."""
    seed_edn = f'[:seq {{}} [:fs/write {{:path "{tmp_path / "ok.txt"}" :bytes_or_text "x"}}]]'

    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("provider blew up"),
    )

    with pytest.raises(BranchSearchFailed) as excinfo:
        _escalate_branch_body(coder, _branch_decision(seed_edn))

    assert "RuntimeError" in excinfo.value.error_repr
    assert excinfo.value.search_id is None


# --------------------------------------------------------------------------- #
# Path-2 — post-search detection (terminated_by == "all_evaluations_failed")
# --------------------------------------------------------------------------- #


def test_path2_all_evaluations_failed_funnels_to_branch_search_failed(s, tmp_path):
    """terminated_by="all_evaluations_failed" → BranchSearchFailed with
    populated search_id / iter_count / terminated_by."""
    seed_edn = f'[:seq {{}} [:fs/write {{:path "{tmp_path / "ok.txt"}" :bytes_or_text "x"}}]]'
    winner_plan = parse(seed_edn, strict=False)

    failed_result = _make_mcts_result(
        winner_plan, terminated_by="all_evaluations_failed", iter_count=7,
    )
    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = MagicMock(  # type: ignore[method-assign]
        return_value=failed_result,
    )

    with pytest.raises(BranchSearchFailed) as excinfo:
        _escalate_branch_body(coder, _branch_decision(seed_edn))

    assert excinfo.value.error_repr == "all_evaluations_failed"
    assert excinfo.value.terminated_by == "all_evaluations_failed"
    # Path-2 has populated fields from MCTSResult.
    assert excinfo.value.search_id == failed_result.search_id
    assert excinfo.value.iter_count == 7


def test_path2_emits_act_result_with_populated_search_id(s, tmp_path):
    """Path-2 emits :act/result with search_id/iter_count/terminated_by
    populated from MCTSResult (the bridge observed a real search that
    just couldn't score anything)."""
    seed_edn = f'[:seq {{}} [:fs/write {{:path "{tmp_path / "ok.txt"}" :bytes_or_text "x"}}]]'
    winner_plan = parse(seed_edn, strict=False)
    failed_result = _make_mcts_result(
        winner_plan, terminated_by="all_evaluations_failed", iter_count=7,
    )

    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = MagicMock(  # type: ignore[method-assign]
        return_value=failed_result,
    )

    with pytest.raises(BranchSearchFailed):
        _escalate_branch_body(coder, _branch_decision(seed_edn))

    rows = _act_result_search_datoms(s)
    assert len(rows) == 1
    row = rows[0]
    assert row["op"] == ":mcts/search"
    assert row["error"] == "all_evaluations_failed"
    assert row["result_summary"]["search_id"] == failed_result.search_id
    assert row["result_summary"]["iter_count"] == 7
    assert row["result_summary"]["terminated_by"] == "all_evaluations_failed"


# --------------------------------------------------------------------------- #
# Both paths — ZERO :plan/done (winner never executed)
# --------------------------------------------------------------------------- #


def test_path1_emits_zero_plan_done(s, tmp_path):
    """LD0+LD4 invariant: search-layer failure → winner NEVER executes.
    Therefore ZERO :plan/done datoms. (Compare: T6 happy-path emits
    exactly one :plan/done via _escalate_plan_body.)"""
    seed_edn = f'[:seq {{}} [:fs/write {{:path "{tmp_path / "ok.txt"}" :bytes_or_text "x"}}]]'

    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = MagicMock(  # type: ignore[method-assign]
        side_effect=ExpanderContractError("test"),
    )

    with pytest.raises(BranchSearchFailed):
        _escalate_branch_body(coder, _branch_decision(seed_edn))

    view = s.fact.since(_EPOCH)
    plan_done_datoms = [
        d for d in view.datoms if d.a == "plan/done" and d.op == "assert"
    ]
    assert plan_done_datoms == [], (
        f"LD0+LD4 broken — search failure must NOT execute winner; got "
        f"{len(plan_done_datoms)} :plan/done datoms"
    )


def test_path2_emits_zero_plan_done(s, tmp_path):
    """LD0+LD4: same invariant for path-2 (post-search detection)."""
    seed_edn = f'[:seq {{}} [:fs/write {{:path "{tmp_path / "ok.txt"}" :bytes_or_text "x"}}]]'
    winner_plan = parse(seed_edn, strict=False)
    failed_result = _make_mcts_result(
        winner_plan, terminated_by="all_evaluations_failed",
    )

    coder = _CoderStub(substrate=s)
    coder.substrate.plan.mcts_search = MagicMock(  # type: ignore[method-assign]
        return_value=failed_result,
    )

    with pytest.raises(BranchSearchFailed):
        _escalate_branch_body(coder, _branch_decision(seed_edn))

    view = s.fact.since(_EPOCH)
    plan_done_datoms = [
        d for d in view.datoms if d.a == "plan/done" and d.op == "assert"
    ]
    assert plan_done_datoms == []
