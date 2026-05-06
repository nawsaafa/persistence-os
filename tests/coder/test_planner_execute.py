"""T4/G4 — `_escalate_plan_body` happy path: walk-order :act/result emission,
:plan/done provenance, terminal mode-switch (returns None), zero additional
:llm/decision datoms, NOT in CANONICAL_AUDIT_WRAPPED_OPS.

Forced spec deviation vs impl plan:
  FD1 (T2 cascade): leaf.tag is already keyword-form (":fs/read" etc.);
    `_escalate_plan_body` MUST use leaf.tag directly — NOT f":{leaf.tag}".
    Tests assert :act/result.op == ":fs/read" (not "::fs/read").
  FD2 (T2): parse(strict=False) used for 2.3a coder-specific tags.
  FD3 (T2): walk() returns list[str]; visitor callback used for id→Node.
  LD2: :plan/done emitted via s.fact.transact (NOT audit op — NOT in
    CANONICAL_AUDIT_WRAPPED_OPS).
  LD3: result_summary inside :act/result carries {plan_id, node_id, tag,
    handler_id} PLUS the substrate op's own result (or None).
  latency_ms=0 for plan leaves at 2.3a (wall-clock deferred to 2.4a).
"""
from __future__ import annotations

import json
import uuid
import datetime as dt

import pytest

from persistence.coder._planner import (
    _build_plan_from_payload,
    _escalate_plan_body,
    validate_plan_for_2_3a,
)
from persistence.effect._audit_stack import CANONICAL_AUDIT_WRAPPED_OPS
from persistence.plan import Dispatcher
from persistence.sdk import Substrate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIMPLE_PLAN_EDN = '[:seq {} [:fs/read {:path "x.txt"}] [:code/run {:source "x=1"}] [:git/diff {}]]'


def _build_and_validate(edn: str):
    plan = _build_plan_from_payload({"plan_edn": edn})
    validate_plan_for_2_3a(plan)
    return plan


def _act_result_datoms(s: Substrate, session_start: dt.datetime):
    """Return :act/result datoms in tx order from since(session_start)."""
    view = s.fact.since(session_start)
    return sorted(
        [d for d in view.datoms if d.a == "act/result" and d.op == "assert"],
        key=lambda d: d.tx,
    )


def _plan_done_datoms(s: Substrate, session_start: dt.datetime):
    """Return :plan/done datoms in tx order from since(session_start)."""
    view = s.fact.since(session_start)
    return sorted(
        [d for d in view.datoms if d.a == "plan/done" and d.op == "assert"],
        key=lambda d: d.tx,
    )


def _llm_decision_datoms(s: Substrate, session_start: dt.datetime):
    """Return :llm/decision datoms from since(session_start)."""
    view = s.fact.since(session_start)
    return [d for d in view.datoms if d.a == "llm/decision" and d.op == "assert"]


@pytest.fixture
def s():
    with Substrate.open("memory") as substrate:
        yield substrate


# ---------------------------------------------------------------------------
# G4 — Test 1: walk-order :act/result emission with correct op (keyword-form)
# ---------------------------------------------------------------------------

def test_escalate_plan_emits_act_result_per_leaf_in_walk_order(s):
    """Three-leaf :seq plan emits 3 :act/result datoms in walk order.

    Key assertions:
      - Exactly 3 :act/result datoms emitted (one per leaf).
      - op field is keyword-form (:fs/read, :code/run, :git/diff) — NOT double-colon.
      - T2 FD1 proof: leaf.tag used directly without f":{leaf.tag}" prepend.
    """
    plan = _build_and_validate(SIMPLE_PLAN_EDN)
    session_start = dt.datetime.now(dt.timezone.utc)

    # Stub effect.perform to return predictable results.
    _call_log: list[str] = []

    def fake_perform(op, args=None):
        _call_log.append(op)
        return {"stubbed": True, "op": op}

    s.effect.perform = fake_perform  # type: ignore[method-assign]

    dispatcher = s.plan.new_dispatcher()
    result = _escalate_plan_body(plan, dispatcher, substrate=s)

    assert result is None, "_escalate_plan_body must return None on success (terminal mode-switch)"

    act_datoms = _act_result_datoms(s, session_start)
    assert len(act_datoms) == 3, f"expected 3 :act/result datoms, got {len(act_datoms)}"

    payloads = [json.loads(d.v) for d in act_datoms]
    ops = [p["op"] for p in payloads]
    # Walk order: :fs/read → :code/run → :git/diff
    assert ops == [":fs/read", ":code/run", ":git/diff"], (
        f"expected keyword-form ops in walk order, got {ops!r} — "
        "check leaf.tag is used DIRECTLY (not f':{leaf.tag}')"
    )


# ---------------------------------------------------------------------------
# G4 — Test 2: plan-context keys inside result_summary (LD3)
# ---------------------------------------------------------------------------

def test_escalate_plan_act_result_carries_plan_context_keys(s):
    """result_summary inside :act/result carries LD3 plan-context keys.

    Keys: plan_id, node_id, tag, handler_id — INSIDE result_summary field,
    not at top-level of the :act/result v-dict.
    """
    plan = _build_and_validate(SIMPLE_PLAN_EDN)
    session_start = dt.datetime.now(dt.timezone.utc)

    def fake_perform(op, args=None):
        return {"file_content": "hello"}

    s.effect.perform = fake_perform  # type: ignore[method-assign]

    dispatcher = s.plan.new_dispatcher()
    _escalate_plan_body(plan, dispatcher, substrate=s)

    act_datoms = _act_result_datoms(s, session_start)
    first = json.loads(act_datoms[0].v)

    # Top-level envelope keys (mirroring _act's shape)
    assert "op" in first
    assert "args_hash" in first
    assert "result_summary" in first
    assert "error" in first
    assert "latency_ms" in first

    # Plan-context keys INSIDE result_summary (LD3)
    rs = first["result_summary"]
    assert isinstance(rs, dict), f"result_summary must be dict, got {type(rs)}"
    assert "plan_id" in rs, f"result_summary missing plan_id: {rs}"
    assert "node_id" in rs, f"result_summary missing node_id: {rs}"
    assert "tag" in rs, f"result_summary missing tag: {rs}"
    assert "handler_id" in rs, f"result_summary missing handler_id: {rs}"

    # op at top-level matches the leaf's keyword-form (FD1 proof)
    assert first["op"] == ":fs/read", (
        f"expected ':fs/read' at first['op'], got {first['op']!r} — "
        "check leaf.tag is used DIRECTLY (not f':{leaf.tag}')"
    )
    # tag inside result_summary matches keyword-form (LD3 plan-context key)
    assert rs["tag"] == ":fs/read", f"result_summary.tag wrong: {rs['tag']!r}"

    # plan_id matches the plan root node id
    assert rs["plan_id"] == plan.id, (
        f"plan_id mismatch: {rs['plan_id']!r} != {plan.id!r}"
    )

    # latency_ms=0 at 2.3a (wall-clock deferred to 2.4a)
    assert first["latency_ms"] == 0


# ---------------------------------------------------------------------------
# G4 — Test 3: :plan/done provenance datom shape
# ---------------------------------------------------------------------------

def test_escalate_plan_emits_plan_done_provenance_datom(s):
    """:plan/done emitted as provenance datom via s.fact.transact after
    all leaves execute. Shape: {plan_id, status, leaf_count}.
    """
    plan = _build_and_validate(SIMPLE_PLAN_EDN)
    session_start = dt.datetime.now(dt.timezone.utc)

    def fake_perform(op, args=None):
        return None

    s.effect.perform = fake_perform  # type: ignore[method-assign]

    dispatcher = s.plan.new_dispatcher()
    _escalate_plan_body(plan, dispatcher, substrate=s)

    done_datoms = _plan_done_datoms(s, session_start)
    assert len(done_datoms) == 1, f"expected exactly 1 :plan/done datom, got {len(done_datoms)}"

    payload = json.loads(done_datoms[0].v)
    assert "plan_id" in payload, f":plan/done missing plan_id: {payload}"
    assert payload["plan_id"] == plan.id
    assert "status" in payload, f":plan/done missing status: {payload}"
    assert payload["status"] == "ok"
    assert "leaf_count" in payload, f":plan/done missing leaf_count: {payload}"
    assert payload["leaf_count"] == 3  # three leaves in SIMPLE_PLAN_EDN


# ---------------------------------------------------------------------------
# G4 — Test 4: :plan/done NOT in CANONICAL_AUDIT_WRAPPED_OPS (LD2)
# ---------------------------------------------------------------------------

def test_plan_done_not_in_canonical_audit_wrapped_ops():
    """:plan/done is a provenance datom (s.fact.transact), NOT an audit op.

    LD2 design decision: :plan/done must NOT appear in CANONICAL_AUDIT_WRAPPED_OPS
    to keep the audit chain clean (plan provenance != effect-level audit).
    """
    assert ":plan/done" not in CANONICAL_AUDIT_WRAPPED_OPS, (
        ":plan/done must NOT be in CANONICAL_AUDIT_WRAPPED_OPS — it is "
        "a provenance datom emitted via s.fact.transact, not an audit op"
    )
    assert "plan/done" not in CANONICAL_AUDIT_WRAPPED_OPS, (
        "bare 'plan/done' also absent"
    )


# ---------------------------------------------------------------------------
# G4 — Test 5: zero additional :llm/decision datoms
# ---------------------------------------------------------------------------

def test_escalate_plan_emits_zero_llm_decision_datoms(s):
    """Plan execution must NOT emit any :llm/decision datoms.

    The LLM was invoked BEFORE escalation (in _decide); _escalate_plan_body
    is effect-only and must not trigger any LLM calls or decision datoms.
    """
    plan = _build_and_validate(SIMPLE_PLAN_EDN)
    session_start = dt.datetime.now(dt.timezone.utc)

    def fake_perform(op, args=None):
        return {"ok": True}

    s.effect.perform = fake_perform  # type: ignore[method-assign]

    dispatcher = s.plan.new_dispatcher()
    _escalate_plan_body(plan, dispatcher, substrate=s)

    llm_datoms = _llm_decision_datoms(s, session_start)
    assert len(llm_datoms) == 0, (
        f"_escalate_plan_body must emit zero :llm/decision datoms, got {len(llm_datoms)}"
    )


# ---------------------------------------------------------------------------
# G4 — Test 6: returns None on success (terminal mode-switch LD0)
# ---------------------------------------------------------------------------

def test_escalate_plan_returns_none_on_success(s):
    """_escalate_plan_body returns None on success (LD0: terminal mode-switch).

    Coder.run() uses the early-return contract: after _escalate_plan_body
    returns (without raising), run() exits via `return` at _session.py:79.
    """
    plan = _build_and_validate(
        '[:seq {} [:fs/read {:path "readme.md"}]]'
    )
    session_start = dt.datetime.now(dt.timezone.utc)

    def fake_perform(op, args=None):
        return {"content": "# README"}

    s.effect.perform = fake_perform  # type: ignore[method-assign]

    dispatcher = s.plan.new_dispatcher()
    result = _escalate_plan_body(plan, dispatcher, substrate=s)

    assert result is None, (
        f"_escalate_plan_body must return None on success (got {result!r})"
    )
    # Also confirm at least 1 :act/result and 1 :plan/done were emitted
    assert len(_act_result_datoms(s, session_start)) == 1
    assert len(_plan_done_datoms(s, session_start)) == 1
