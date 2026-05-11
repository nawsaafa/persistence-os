"""T5/G5 — `_escalate_plan_body` failure path: partial-trace :act/result
emission, failure-shaped :act/result for the failing leaf, final
:plan/execute summary, re-raise PlanExecutionFailed (NO :plan/done),
plus T3-residual adapter-failure-propagation guard.

LD4 (R0-fold B1): On status=failed:
  1. Emit :act/result per leaf in result.leaf_results (the SUCCESSFUL
     prefix; execute() bails BEFORE appending the failing leaf).
  2. Emit failure-shaped :act/result for the failing leaf — attrs + handler-id
     recovered via the pre-walked id→Node map stored in _escalate_plan_body.
  3. Emit final :act/result with op=":plan/execute" summarising the failure.
  4. Re-raise PlanExecutionFailed carrying FailureInfo.

Forced spec deviation vs impl plan:
  FD1 (T2 cascade): failed_tag is already keyword-form after execute()
    captures it via `failed_tag=node.tag` (_execute.py:201). Assertions
    use keyword-form (":code/run") — NOT bare ("code/run").
  LD4: the failing leaf is NOT in result.leaf_results — execute() bails
    BEFORE appending it (confirmed at _execute.py:193-205). attrs +
    handler-id for the failing leaf recovered via id_to_node map.
  latency_ms=0 for all failure-path datoms at 2.3a.
"""
from __future__ import annotations

import json
import datetime as dt
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from persistence.coder._planner import _escalate_plan_body, _make_adapter
from persistence.coder._planner_errors import PlanExecutionFailed
from persistence.coder._types import LLMDecision
from persistence.plan import Node
from persistence.sdk import Substrate


# ---------------------------------------------------------------------------
# Helpers — mirrors test_planner_execute.py style
# ---------------------------------------------------------------------------

@dataclass
class _CoderStub:
    """Minimal Coder-shaped stub. _escalate_plan_body only reads .substrate."""
    substrate: Substrate
    _session_start_dt: dt.datetime | None = None


def _make_coder_stub(s: Substrate) -> _CoderStub:
    return _CoderStub(
        substrate=s,
        _session_start_dt=dt.datetime.now(dt.timezone.utc),
    )


def _make_plan_decision(plan_edn: str) -> LLMDecision:
    return LLMDecision(
        kind="plan",
        confidence=0.9,
        payload={"plan_edn": plan_edn},
    )


def _act_result_datoms(s: Substrate, session_start: dt.datetime):
    """Return :act/result datoms in tx order."""
    view = s.fact.since(session_start)
    return sorted(
        [d for d in view.datoms if d.a == "act/result" and d.op == "assert"],
        key=lambda d: d.tx,
    )


def _plan_done_datoms(s: Substrate, session_start: dt.datetime):
    """Return :plan/done datoms in tx order."""
    view = s.fact.since(session_start)
    return sorted(
        [d for d in view.datoms if d.a == "plan/done" and d.op == "assert"],
        key=lambda d: d.tx,
    )


@pytest.fixture
def s():
    with Substrate.open("memory") as substrate:
        yield substrate


# ---------------------------------------------------------------------------
# Two-leaf plan: first leaf succeeds, second (code/run) raises.
# [:seq {} [:fs/read {:path "x.txt"}] [:code/run {:source "boom()"}]]
# ---------------------------------------------------------------------------

_TWO_LEAF_EDN = '[:seq {} [:fs/read {:path "x.txt"}] [:code/run {:source "boom()"}]]'


def _make_perform_with_failure(failing_op: str, exc: Exception):
    """Return fake perform that succeeds for all ops EXCEPT failing_op.

    Phase 2.4b LD-4: ``_escalate_plan_body`` now reads ``valid_from`` via
    ``substrate.effect.perform(":sys/now", {})``. Special-case the op so
    a wall-clock ``dt.datetime`` is returned regardless of the failure
    target — the test cares about handler-failure shape, not the time
    read.
    """
    def perform(op, args=None):
        if op == ":sys/now":
            return dt.datetime.now(dt.timezone.utc)
        if op == failing_op:
            raise exc
        return {"stubbed": True, "op": op}
    return perform


# ---------------------------------------------------------------------------
# G5 — Test 1: raises PlanExecutionFailed on handler raise
# ---------------------------------------------------------------------------

def test_escalate_plan_raises_plan_execution_failed_on_handler_raise(s):
    """_escalate_plan_body raises PlanExecutionFailed when a leaf handler raises.

    The raised exception must carry FailureInfo with the correct failed_tag
    (keyword-form per FD1) and a non-empty error_repr.
    """
    s.effect.perform = _make_perform_with_failure(  # type: ignore[method-assign]
        ":code/run", RuntimeError("BOOM_G5_TEST1")
    )

    coder = _make_coder_stub(s)
    decision = _make_plan_decision(_TWO_LEAF_EDN)

    with pytest.raises(PlanExecutionFailed) as exc_info:
        _escalate_plan_body(coder, decision)

    failure = exc_info.value.failure
    # FD1: failed_tag is keyword-form (":code/run"), NOT bare "code/run"
    assert failure.failed_tag == ":code/run", (
        f"expected ':code/run' (keyword-form), got {failure.failed_tag!r}"
    )
    assert "BOOM_G5_TEST1" in failure.error_repr, (
        f"error_repr should contain exception message: {failure.error_repr!r}"
    )
    assert failure.error_class == "RuntimeError"


# ---------------------------------------------------------------------------
# G5 — Test 2: partial-trace :act/result emitted for the successful prefix
# ---------------------------------------------------------------------------

def test_escalate_plan_emits_partial_trace_for_successful_prefix(s):
    """Exactly one :act/result emitted for the successful prefix leaf (:fs/read).

    The failing leaf (:code/run) must NOT appear in partial-trace :act/result
    datoms — execute() bails BEFORE appending it to result.leaf_results.
    """
    session_start = dt.datetime.now(dt.timezone.utc)

    s.effect.perform = _make_perform_with_failure(  # type: ignore[method-assign]
        ":code/run", RuntimeError("BOOM_PARTIAL_TRACE")
    )

    coder = _make_coder_stub(s)
    decision = _make_plan_decision(_TWO_LEAF_EDN)

    with pytest.raises(PlanExecutionFailed):
        _escalate_plan_body(coder, decision)

    act_datoms = _act_result_datoms(s, session_start)
    # 3 total: 1 partial-trace + 1 failure-shaped + 1 :plan/execute summary
    assert len(act_datoms) == 3, (
        f"expected 3 :act/result datoms (1 partial + 1 failure + 1 summary), "
        f"got {len(act_datoms)}"
    )
    # First datom = partial-trace for the succeeding :fs/read leaf
    first = json.loads(act_datoms[0].v)
    assert first["op"] == ":fs/read", (
        f"partial-trace first datom op must be ':fs/read', got {first['op']!r}"
    )
    assert first["error"] is None, (
        f"partial-trace leaf must have error=None, got {first['error']!r}"
    )


# ---------------------------------------------------------------------------
# G5 — Test 3: failure-shaped :act/result for the failing leaf with id-map context
# ---------------------------------------------------------------------------

def test_escalate_plan_emits_failure_shaped_act_result_for_failing_leaf(s):
    """The second :act/result datom is the failure-shaped entry for the failing leaf.

    op = failing leaf tag (keyword-form per FD1).
    error = failure.error_repr (class-prefixed repr).
    result_summary contains plan-context keys recovered via id_to_node map.
    """
    session_start = dt.datetime.now(dt.timezone.utc)

    boom_exc = RuntimeError("BOOM_FAILURE_SHAPED")
    s.effect.perform = _make_perform_with_failure(  # type: ignore[method-assign]
        ":code/run", boom_exc
    )

    coder = _make_coder_stub(s)
    decision = _make_plan_decision(_TWO_LEAF_EDN)

    with pytest.raises(PlanExecutionFailed) as exc_info:
        _escalate_plan_body(coder, decision)

    act_datoms = _act_result_datoms(s, session_start)
    # Second datom = failure-shaped for the failing :code/run leaf
    failure_datom = json.loads(act_datoms[1].v)

    # op = keyword-form failed tag (FD1 proof)
    assert failure_datom["op"] == ":code/run", (
        f"failure-shaped datom op must be ':code/run' (keyword-form), "
        f"got {failure_datom['op']!r}"
    )
    # error = failure.error_repr (non-None, class-prefixed)
    assert failure_datom["error"] is not None, "failure-shaped :act/result must have non-None error"
    assert "BOOM_FAILURE_SHAPED" in failure_datom["error"], (
        f"failure-shaped error must contain exception message: {failure_datom['error']!r}"
    )
    # result_summary carries plan-context keys (recovered via id→Node map)
    rs = failure_datom["result_summary"]
    assert isinstance(rs, dict), f"result_summary must be dict, got {type(rs)}"
    assert "plan_id" in rs, f"failure result_summary missing plan_id: {rs}"
    assert "node_id" in rs, f"failure result_summary missing node_id: {rs}"
    # tag must be keyword-form (FD1) — the failing leaf's tag
    assert rs["tag"] == ":code/run", (
        f"result_summary tag must be ':code/run' (keyword-form), got {rs['tag']!r}"
    )
    assert "handler_id" in rs, f"failure result_summary missing handler_id: {rs}"

    # Confirm error_repr matches what FailureInfo carries
    failure = exc_info.value.failure
    assert failure_datom["error"] == failure.error_repr


# ---------------------------------------------------------------------------
# G5 — Test 4: final :plan/execute summary :act/result emitted
# ---------------------------------------------------------------------------

def test_escalate_plan_emits_plan_execute_summary_on_failure(s):
    """The third :act/result datom is the final :plan/execute summary.

    op = ":plan/execute" (synthesized name, NOT a leaf tag).
    error = failure.error_repr.
    result_summary contains plan_id.
    """
    session_start = dt.datetime.now(dt.timezone.utc)

    boom_exc = ValueError("BOOM_SUMMARY")
    s.effect.perform = _make_perform_with_failure(  # type: ignore[method-assign]
        ":code/run", boom_exc
    )

    coder = _make_coder_stub(s)
    decision = _make_plan_decision(_TWO_LEAF_EDN)

    with pytest.raises(PlanExecutionFailed) as exc_info:
        _escalate_plan_body(coder, decision)

    act_datoms = _act_result_datoms(s, session_start)
    assert len(act_datoms) == 3, f"expected 3 :act/result datoms, got {len(act_datoms)}"

    # Third datom = :plan/execute summary
    summary = json.loads(act_datoms[2].v)
    assert summary["op"] == ":plan/execute", (
        f"final summary op must be ':plan/execute', got {summary['op']!r}"
    )
    assert summary["error"] is not None, "final summary must have non-None error"
    assert "BOOM_SUMMARY" in summary["error"], (
        f"summary error must contain exception message: {summary['error']!r}"
    )
    assert isinstance(summary["result_summary"], dict), (
        f"summary result_summary must be dict, got {type(summary['result_summary'])}"
    )
    assert "plan_id" in summary["result_summary"], (
        f"summary result_summary missing plan_id: {summary['result_summary']}"
    )
    failure = exc_info.value.failure
    assert summary["error"] == failure.error_repr


# ---------------------------------------------------------------------------
# G5 — Test 5: ZERO :plan/done datoms on failure
# ---------------------------------------------------------------------------

def test_escalate_plan_emits_zero_plan_done_on_failure(s):
    """:plan/done must NOT be emitted when execution fails.

    Per LD2 design: :plan/done presence implies success. Failure path
    raises PlanExecutionFailed BEFORE :plan/done is written.
    """
    session_start = dt.datetime.now(dt.timezone.utc)

    s.effect.perform = _make_perform_with_failure(  # type: ignore[method-assign]
        ":code/run", RuntimeError("BOOM_NO_DONE")
    )

    coder = _make_coder_stub(s)
    decision = _make_plan_decision(_TWO_LEAF_EDN)

    with pytest.raises(PlanExecutionFailed):
        _escalate_plan_body(coder, decision)

    done_datoms = _plan_done_datoms(s, session_start)
    assert len(done_datoms) == 0, (
        f"failure path must emit ZERO :plan/done datoms, got {len(done_datoms)}: {done_datoms}"
    )


# ---------------------------------------------------------------------------
# G5 — Test 6: native traceback unavailable (error_repr is string-only)
# ---------------------------------------------------------------------------

def test_native_traceback_unavailable_in_failure_info(s):
    """FailureInfo.error_repr is string-only (repr(exc)); native __traceback__
    is UNAVAILABLE because execute() captures into a string-only dataclass.

    Per design § 7 + LD4 documented limitation. Queued v0.9.x.
    This test documents the current contract — if native traceback becomes
    available in a future phase, this test should be updated.
    """
    s.effect.perform = _make_perform_with_failure(  # type: ignore[method-assign]
        ":code/run", RuntimeError("BOOM_TRACEBACK_CHECK")
    )

    coder = _make_coder_stub(s)
    decision = _make_plan_decision(_TWO_LEAF_EDN)

    with pytest.raises(PlanExecutionFailed) as exc_info:
        _escalate_plan_body(coder, decision)

    failure = exc_info.value.failure

    # error_repr is a string (repr of exc)
    assert isinstance(failure.error_repr, str), (
        f"error_repr must be str, got {type(failure.error_repr)}"
    )
    # error_repr looks like repr(RuntimeError(...)) — class-prefixed
    assert failure.error_repr.startswith("RuntimeError("), (
        f"error_repr should start with class name: {failure.error_repr!r}"
    )
    # PlanExecutionFailed itself does NOT carry __cause__ or __context__
    # pointing at the original exception — it's raised fresh with no chain.
    assert exc_info.value.__cause__ is None, (
        "PlanExecutionFailed must NOT have __cause__ — no native traceback in 2.3a"
    )


# ---------------------------------------------------------------------------
# T3-residual (coderabbit IMPORTANT): adapter propagates perform exception
# ---------------------------------------------------------------------------

def test_adapter_propagates_substrate_effect_perform_exception(s):
    """Regression for T3 IMPORTANT (coderabbit): when substrate.effect.perform
    raises, the adapter MUST propagate the exception (no swallowing) so
    s.plan.execute can capture it into FailureInfo. The adapter has no
    try/except — verifies the contract via direct invocation."""
    fake_perform = MagicMock(side_effect=RuntimeError("ADAPTER_PROP_SENTINEL"))
    s.effect.perform = fake_perform  # type: ignore[method-assign]
    adapter = _make_adapter(s, ":fs/read")
    node = Node(tag=":fs/read", attrs={"path": "x.txt"}, children=())
    with pytest.raises(RuntimeError, match="ADAPTER_PROP_SENTINEL"):
        adapter(node, {})
    fake_perform.assert_called_once_with(":fs/read", {"path": "x.txt"})
