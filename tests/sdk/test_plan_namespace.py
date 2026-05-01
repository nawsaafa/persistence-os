"""``s.plan`` SDK namespace unit tests — Phase 2.0c-prime #147.

The curated SDK surface ``s.plan.*`` is a thin pass-through to
:mod:`persistence.plan`. Tests here exercise the surface binding
(stability metadata, namespace exposure, identity stability, dir()
contract, lifecycle gating) and a handful of integration smoke cases
to confirm the pass-through forwards arguments cleanly. Comprehensive
plan-module-level coverage lives in ``tests/plan/``.

Test groups:

1. Namespace existence + identity stability + lifecycle gate.
2. Frozen dir() surface — ``"plan"`` appears once in the contract list.
3. Smoke — parse / unparse round-trip.
4. Smoke — walk over a 3-node plan.
5. Smoke — execute a one-step plan via a registered dispatcher handler.
6. Smoke — edit_step inside ``s.txn.dosync()``.
7. Stability metadata — every curated method carries
   ``__sdk_stability__["level"] == "experimental"`` with a Phase
   2.0c-prime-shaped reason string.
8. Closed-substrate — ``s.plan.parse(...)`` after ``s.close()`` raises
   ``RuntimeError``.
9. Re-exports — value-shape types are reachable via
   ``from persistence.sdk import …`` without touching
   :mod:`persistence.plan`.
"""
from __future__ import annotations

import pytest

from persistence.sdk import (
    ExecutionResult,
    FailureInfo,
    LeafResult,
    Node,
    OptimizedPlan,
    PromotionRecord,
    Substrate,
    TrainingExample,
)
from persistence.sdk._facade import _PlanNamespace, _SUBSTRATE_PUBLIC_DIR


# ---------------------------------------------------------------------------
# 1. Namespace existence + identity stability + lifecycle gate
# ---------------------------------------------------------------------------


def test_plan_namespace_exists_on_substrate():
    """Substrate.open('memory').plan returns a _PlanNamespace; identity
    is stable across re-access (same instance bound at __init__).
    """
    with Substrate.open("memory") as s:
        ns1 = s.plan
        ns2 = s.plan
        assert isinstance(ns1, _PlanNamespace)
        assert ns1 is ns2  # identity stable for adapter-author caching


# ---------------------------------------------------------------------------
# 2. Frozen dir() — "plan" appears once in lexical position
# ---------------------------------------------------------------------------


def test_plan_in_dir():
    """`"plan"` appears in `dir(s)` exactly once, in expected lexical
    position (between `open` and `replay`).
    """
    with Substrate.open("memory") as s:
        names = dir(s)
        assert names.count("plan") == 1
        # Lexical: 'open' < 'plan' < 'replay' < 'repl' (Python sort)
        plan_idx = names.index("plan")
        assert names[plan_idx - 1] == "open"
        # 'plan' < 'replay' lexically
        assert "replay" in names[plan_idx + 1 :]


def test_plan_in_substrate_public_dir():
    """`"plan"` is in the closed `_SUBSTRATE_PUBLIC_DIR` tuple."""
    assert "plan" in _SUBSTRATE_PUBLIC_DIR


# ---------------------------------------------------------------------------
# 3. Smoke — parse / unparse round-trip
# ---------------------------------------------------------------------------


def test_plan_namespace_smoke_parse_unparse():
    """Round-trip a small EDN plan through s.plan.parse and s.plan.unparse."""
    edn = '[:seq {} [:llm-call {:prompt "hi"}]]'
    with Substrate.open("memory") as s:
        node = s.plan.parse(edn)
        assert isinstance(node, Node)
        assert node.tag == ":seq"
        assert len(node.children) == 1
        assert node.children[0].tag == ":llm-call"

        # Round-trip back to EDN; re-parse must produce the same Node id.
        edn_round = s.plan.unparse(node)
        assert isinstance(edn_round, str)
        node_round = s.plan.parse(edn_round)
        assert node_round.id == node.id


# ---------------------------------------------------------------------------
# 4. Smoke — walk a 3-node plan, count nodes
# ---------------------------------------------------------------------------


def test_plan_namespace_smoke_walk():
    """Walk a 3-node plan; trace count matches DFS pre-order."""
    plan = Node(
        tag=":seq",
        attrs={},
        children=(
            Node(tag=":llm-call", attrs={"prompt": "first"}, children=()),
            Node(tag=":llm-call", attrs={"prompt": "second"}, children=()),
        ),
    )
    with Substrate.open("memory") as s:
        trace = s.plan.walk(plan)
        assert isinstance(trace, list)
        assert len(trace) == 3  # root + 2 children
        assert trace[0] == plan.id


# ---------------------------------------------------------------------------
# 5. Smoke — execute a one-step plan via a registered dispatcher handler
# ---------------------------------------------------------------------------


def test_plan_namespace_smoke_execute():
    """Register a trivial dispatcher, execute a one-step plan, assert
    ExecutionResult shape.
    """
    # Dispatcher type is intentionally NOT re-exported on s.plan — the
    # type-vocabulary stays in persistence.plan per the SDK split. Adapter
    # authors who need it import directly.
    from persistence.plan import Dispatcher

    plan = Node(
        tag=":llm-call",
        attrs={"prompt": "hi"},
        children=(),
    )
    dispatcher = Dispatcher()
    dispatcher.register(":llm-call", lambda node, env: "answer")

    with Substrate.open("memory") as s:
        result = s.plan.execute(plan, dispatcher=dispatcher)

    assert isinstance(result, ExecutionResult)
    assert result.status == "ok"
    assert len(result.leaf_results) == 1
    leaf = result.leaf_results[0]
    assert isinstance(leaf, LeafResult)
    assert leaf.tag == ":llm-call"
    assert leaf.result == "answer"


# ---------------------------------------------------------------------------
# 6. Smoke — s.plan.edit_step inside s.txn.dosync()
# ---------------------------------------------------------------------------


def test_plan_namespace_smoke_edit():
    """edit_step inside s.txn.dosync() round-trips and returns a new
    Node with the substituted subtree.
    """
    inner = Node(tag=":llm-call", attrs={"prompt": "hi"}, children=())
    plan = Node(tag=":seq", attrs={}, children=(inner,))
    new_op = Node(tag=":llm-call", attrs={"prompt": "bye"}, children=())

    with Substrate.open("memory") as s:
        new_plan: Node | None = None
        with s.txn.dosync() as tx:
            new_plan = s.plan.edit_step(plan, inner.id, new_op, tx=tx)

        assert new_plan is not None
        assert new_plan.children[0].id == new_op.id
        assert new_plan.children[0].attrs["prompt"] == "bye"


# ---------------------------------------------------------------------------
# 7. Stability metadata — every curated method carries @experimental
# ---------------------------------------------------------------------------


# The full inventory of curated methods on _PlanNamespace that wrap a
# persistence.plan callable. Each one MUST carry the @experimental shape.
_CURATED_METHODS: tuple[str, ...] = (
    "parse",
    "unparse",
    "walk",
    "execute",
    "edit_step",
    "insert_step_after",
    "insert_step_before",
    "delete_step",
    "optimize",
    "promote",
    "gate_g1_replay_byte_identity",
    "gate_g2_audit_chain",
    "gate_g3_score_delta",
    "gate_g4_stub",
    "mcts_search",
    "mcts_promote",
    "apply_action",
    "register_metric",
    "unregister_metric",
    "lookup_metric",
    "register_coercion",
    "unregister_coercion",
    "lookup_coercion",
    "skill_library",
)


@pytest.mark.parametrize("method_name", _CURATED_METHODS)
def test_plan_namespace_methods_are_experimental(method_name: str):
    """Each curated s.plan.<method> carries @experimental metadata
    matching the s.txn.fork / s.txn.fold_into precedent: level is
    ``"experimental"`` and reason mentions Phase 2.0c-prime / #147.
    """
    with Substrate.open("memory") as s:
        method = getattr(s.plan, method_name)
        underlying = getattr(method, "__func__", method)
        metadata = getattr(underlying, "__sdk_stability__", None)
        assert metadata is not None, (
            f"s.plan.{method_name} missing __sdk_stability__ attribute; "
            f"@experimental decorator was not applied"
        )
        assert metadata.get("level") == "experimental", (
            f"s.plan.{method_name} stability level is "
            f"{metadata.get('level')!r}, expected 'experimental'"
        )
        reason = metadata.get("reason") or ""
        assert "Phase 2.0c-prime" in reason or "#147" in reason, (
            f"s.plan.{method_name} reason string does not carry the "
            f"phase tag; got: {reason!r}"
        )


# ---------------------------------------------------------------------------
# 8. Closed-substrate gate
# ---------------------------------------------------------------------------


def test_plan_namespace_after_close_raises():
    """Calling s.plan after s.close() raises RuntimeError via
    _check_open('plan').
    """
    s = Substrate.open("memory")
    s.close()
    with pytest.raises(RuntimeError, match="closed"):
        _ = s.plan


# ---------------------------------------------------------------------------
# 9. Re-exports — value-shape types reach via persistence.sdk
# ---------------------------------------------------------------------------


def test_plan_re_exports_from_sdk_init():
    """from persistence.sdk import Node, ExecutionResult, ... works
    without reaching into persistence.plan.
    """
    # All seven value-shape re-exports are accessible at the SDK
    # top-level. The imports above this test would have failed at
    # collection time if any were missing; here we assert identity
    # against persistence.plan so the curated re-export does not
    # silently drift to a copy / wrapper.
    from persistence.plan import (
        ExecutionResult as _PlanExecutionResult,
        FailureInfo as _PlanFailureInfo,
        LeafResult as _PlanLeafResult,
        Node as _PlanNode,
        OptimizedPlan as _PlanOptimizedPlan,
        PromotionRecord as _PlanPromotionRecord,
        TrainingExample as _PlanTrainingExample,
    )

    assert ExecutionResult is _PlanExecutionResult
    assert FailureInfo is _PlanFailureInfo
    assert LeafResult is _PlanLeafResult
    assert Node is _PlanNode
    assert OptimizedPlan is _PlanOptimizedPlan
    assert PromotionRecord is _PlanPromotionRecord
    assert TrainingExample is _PlanTrainingExample


def test_plan_re_exports_in_sdk_all():
    """Each re-exported name appears in persistence.sdk.__all__ for the
    spec-doc generator (G7 / SDK5).
    """
    import persistence.sdk as sdk_mod

    for name in (
        "ExecutionResult",
        "FailureInfo",
        "LeafResult",
        "Node",
        "OptimizedPlan",
        "PromotionRecord",
        "TrainingExample",
    ):
        assert name in sdk_mod.__all__, (
            f"{name!r} is re-exported but not in persistence.sdk.__all__"
        )
