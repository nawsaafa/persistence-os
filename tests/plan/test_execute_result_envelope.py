"""A1 — `execute()` core + result envelope.

Wraps Dispatcher.dispatch in an ExecutionResult envelope with
walk-order failure semantics. Tests pin the public API per
docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md §4
and docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md §2.A1.
"""
from __future__ import annotations

import pytest

from persistence.plan import (
    Dispatcher,
    ExecutionResult,
    FailureInfo,
    LeafResult,
    Node,
    UnimplementedNodeKindError,
    execute,
)


# --- Public API surface --------------------------------------------------- #


def test_execution_result_envelope_is_frozen_dataclass():
    """ExecutionResult / LeafResult / FailureInfo are immutable dataclasses."""
    res = ExecutionResult(
        plan_id="0" * 32,
        status="ok",
        leaf_results=(),
        failure=None,
    )
    with pytest.raises((AttributeError, TypeError)):
        res.status = "failed"  # type: ignore[misc]


def test_leaf_result_is_frozen_dataclass():
    """LeafResult is frozen + slotted."""
    leaf = LeafResult(node_id="abc", tag=":llm-call", handler_id="<default>", result=42)
    with pytest.raises((AttributeError, TypeError)):
        leaf.result = "mutated"  # type: ignore[misc]


def test_failure_info_is_frozen_dataclass():
    """FailureInfo is frozen + slotted."""
    f = FailureInfo(
        failed_node_id="abc",
        failed_tag=":llm-call",
        error_class="ValueError",
        error_repr="ValueError('boom')",
    )
    with pytest.raises((AttributeError, TypeError)):
        f.error_class = "RuntimeError"  # type: ignore[misc]


# --- Edge case 1: empty plan ---------------------------------------------- #


def test_execute_empty_plan_no_handler_returns_ok():
    """Edge 1: root with no children, no handler → status='ok', leaf_results=()."""
    plan = Node(tag=":seq")  # no children, dispatcher empty
    d = Dispatcher()
    res = execute(plan, d)
    assert res.status == "ok"
    assert res.leaf_results == ()
    assert res.failure is None
    assert res.plan_id == plan.id


def test_execute_root_with_no_handler_but_children_walks_silently():
    """Root + children without handlers → status='ok', no leaves recorded."""
    plan = Node(tag=":seq", children=(Node(tag=":a"), Node(tag=":b")))
    d = Dispatcher()
    res = execute(plan, d)
    assert res.status == "ok"
    assert res.leaf_results == ()
    assert res.failure is None


# --- Edge case 2: single :llm-call leaf ----------------------------------- #


def test_execute_single_llm_call_leaf_one_leaf_result():
    """Edge 2: single :llm-call leaf → leaf_results has 1 entry with right node_id."""
    leaf = Node(tag=":llm-call")
    d = Dispatcher()
    d.register(":llm-call", lambda n, env: "answer")
    res = execute(leaf, d)
    assert res.status == "ok"
    assert len(res.leaf_results) == 1
    only = res.leaf_results[0]
    assert only.node_id == leaf.id
    assert only.tag == ":llm-call"
    assert only.result == "answer"
    assert res.failure is None


def test_execute_leaf_results_is_a_tuple():
    """leaf_results is a tuple (frozen), not a list."""
    leaf = Node(tag=":llm-call")
    d = Dispatcher()
    d.register(":llm-call", lambda n, env: "answer")
    res = execute(leaf, d)
    assert isinstance(res.leaf_results, tuple)


def test_execute_handler_id_default_when_attr_missing():
    """handler_id defaults to '<default>' when node.attrs has no 'handler-id' key."""
    leaf = Node(tag=":llm-call")  # no handler-id in attrs
    d = Dispatcher()
    d.register(":llm-call", lambda n, env: None)
    res = execute(leaf, d)
    assert res.leaf_results[0].handler_id == "<default>"


def test_execute_handler_id_read_from_node_attrs():
    """handler_id read from node.attrs.get('handler-id', '<default>')."""
    leaf = Node(tag=":llm-call", attrs={"handler-id": "openai-gpt-4o"})
    d = Dispatcher()
    d.register(":llm-call", lambda n, env: None)
    res = execute(leaf, d)
    assert res.leaf_results[0].handler_id == "openai-gpt-4o"


# --- Edge case 3: nested DAG, walk order preserved ------------------------ #


def test_execute_nested_seq_two_llm_calls_walk_order_preserved():
    """Edge 3: :seq containing two :llm-call → walk-order preserved in leaf_results."""
    a = Node(tag=":llm-call", attrs={"handler-id": "h-a"})
    b = Node(tag=":llm-call", attrs={"handler-id": "h-b"})
    plan = Node(tag=":seq", children=(a, b))
    d = Dispatcher()

    counter = {"n": 0}

    def handler(n, env):
        counter["n"] += 1
        return f"result-{counter['n']}"

    d.register(":llm-call", handler)
    res = execute(plan, d)
    assert res.status == "ok"
    assert len(res.leaf_results) == 2
    # Walk order: a before b
    assert res.leaf_results[0].node_id == a.id
    assert res.leaf_results[0].handler_id == "h-a"
    assert res.leaf_results[0].result == "result-1"
    assert res.leaf_results[1].node_id == b.id
    assert res.leaf_results[1].handler_id == "h-b"
    assert res.leaf_results[1].result == "result-2"


def test_execute_records_leaf_for_every_dispatched_node_including_root():
    """Even the root, if it has a registered handler, contributes a LeafResult."""
    plan = Node(tag=":seq", children=(Node(tag=":a"),))
    d = Dispatcher()
    d.register(":seq", lambda n, env: "seq-result")
    d.register(":a", lambda n, env: "a-result")
    res = execute(plan, d)
    assert [lr.tag for lr in res.leaf_results] == [":seq", ":a"]
    assert [lr.result for lr in res.leaf_results] == ["seq-result", "a-result"]


# --- Edge case 4: handler raises ------------------------------------------ #


def test_execute_handler_raises_value_error_returns_failed_status():
    """Edge 4: handler raises ValueError → status='failed', error_class='ValueError'."""
    failing = Node(tag=":llm-call")
    plan = Node(tag=":seq", children=(failing,))
    d = Dispatcher()

    def boom(n, env):
        raise ValueError("oops")

    d.register(":llm-call", boom)
    res = execute(plan, d)
    assert res.status == "failed"
    assert res.failure is not None
    assert res.failure.failed_node_id == failing.id
    assert res.failure.failed_tag == ":llm-call"
    assert res.failure.error_class == "ValueError"
    # repr(ValueError('oops')) == "ValueError('oops')"
    assert "oops" in res.failure.error_repr
    assert res.failure.error_repr == repr(ValueError("oops"))


def test_execute_handler_raise_preserves_partial_leaf_results():
    """Partial leaf_results before the failing node are preserved."""
    a = Node(tag=":llm-call", attrs={"handler-id": "ok-handler"})
    boom_node = Node(tag=":tool-call")
    plan = Node(tag=":seq", children=(a, boom_node))

    d = Dispatcher()
    d.register(":llm-call", lambda n, env: "partial-result")

    def boom(n, env):
        raise RuntimeError("late failure")

    d.register(":tool-call", boom)

    res = execute(plan, d)
    assert res.status == "failed"
    # leaf_results captured BEFORE the failure
    assert len(res.leaf_results) == 1
    assert res.leaf_results[0].node_id == a.id
    assert res.leaf_results[0].result == "partial-result"
    assert res.failure is not None
    assert res.failure.failed_node_id == boom_node.id
    assert res.failure.error_class == "RuntimeError"


def test_execute_handler_raise_halts_walk_no_successors_executed():
    """Successor nodes after a failing node are NOT executed (early-stop)."""
    boom_node = Node(tag=":llm-call")
    after = Node(tag=":llm-call", attrs={"handler-id": "after"})
    plan = Node(tag=":seq", children=(boom_node, after))

    calls: list[str] = []

    def maybe_boom(n, env):
        if n is boom_node:
            calls.append("boom")
            raise ValueError("halt")
        calls.append("after")
        return "should-not-run"

    d = Dispatcher()
    d.register(":llm-call", maybe_boom)

    res = execute(plan, d)
    assert res.status == "failed"
    assert calls == ["boom"]
    # `after` handler never called
    assert len(res.leaf_results) == 0


def test_execute_failure_uses_repr_not_str():
    """error_repr is repr(exc), not str(exc) — type is encoded in the repr."""

    class CustomError(Exception):
        pass

    leaf = Node(tag=":llm-call")
    d = Dispatcher()

    def boom(n, env):
        raise CustomError("payload")

    d.register(":llm-call", boom)
    res = execute(leaf, d)
    assert res.status == "failed"
    assert res.failure is not None
    assert res.failure.error_class == "CustomError"
    # repr looks like CustomError('payload') — contains class name
    assert "CustomError" in res.failure.error_repr
    assert "payload" in res.failure.error_repr


# --- Edge case 5: walker raises UnimplementedNodeKindError ---------------- #


def test_execute_reraises_unimplemented_node_kind_for_code_leaf():
    """Edge 5: walker raises UnimplementedNodeKindError for :code leaf →
    execute() RE-RAISES (does NOT swallow into FailureInfo)."""
    plan = Node(tag=":code")  # leaf :code → walker raises
    d = Dispatcher()
    with pytest.raises(UnimplementedNodeKindError):
        execute(plan, d)


def test_execute_reraises_unimplemented_node_kind_for_branch_leaf():
    """Same for :branch leaf."""
    plan = Node(tag=":branch")
    d = Dispatcher()
    with pytest.raises(UnimplementedNodeKindError):
        execute(plan, d)


def test_execute_reraises_unimplemented_even_when_inside_seq():
    """:code leaf nested inside :seq still raises."""
    plan = Node(tag=":seq", children=(Node(tag=":code"),))
    d = Dispatcher()
    with pytest.raises(UnimplementedNodeKindError):
        execute(plan, d)


# --- env handling --------------------------------------------------------- #


def test_execute_env_defaults_to_empty_dict_when_none():
    """env=None → handler receives an empty dict."""
    leaf = Node(tag=":llm-call")
    seen: list[dict] = []

    def handler(n, env):
        seen.append(env)
        return None

    d = Dispatcher()
    d.register(":llm-call", handler)
    res = execute(leaf, d, env=None)
    assert res.status == "ok"
    assert seen == [{}]


def test_execute_env_passed_through_to_handlers():
    """When env is supplied, handlers see that exact dict (by reference)."""
    leaf = Node(tag=":llm-call")
    d = Dispatcher()
    seen_ids: list[int] = []
    d.register(":llm-call", lambda n, env: seen_ids.append(id(env)))
    env = {"trace_id": "xyz"}
    execute(leaf, d, env=env)
    assert seen_ids == [id(env)]


def test_execute_env_default_is_fresh_each_call():
    """Default env=None → new empty dict per call (no shared mutable default).

    Direct test of the no-shared-mutable-default contract: if the
    implementation used ``env={}`` as the default arg (a Python footgun),
    handler mutations would persist across calls. We mutate the handler-
    received env in call 1 and confirm call 2's handler sees an EMPTY env.
    """
    leaf = Node(tag=":llm-call")
    captured: list[dict] = []

    def handler(n, env):
        # snapshot what was visible BEFORE mutation, then mutate
        captured.append(dict(env))
        env["touched"] = True
        return None

    d = Dispatcher()
    d.register(":llm-call", handler)

    execute(leaf, d)  # first call, env=None → fresh dict
    execute(leaf, d)  # second call, env=None → another fresh dict

    # If the default were a shared `{}`, captured[1] would contain the
    # mutation from captured[0]'s call. Fresh-per-call → both empty.
    assert captured == [{}, {}]


# --- plan_id matches node.id of root -------------------------------------- #


def test_execute_plan_id_is_root_node_id():
    """ExecutionResult.plan_id == root Node.id (32-hex-char content address)."""
    plan = Node(tag=":seq", children=(Node(tag=":a"), Node(tag=":b")))
    d = Dispatcher()
    res = execute(plan, d)
    assert res.plan_id == plan.id
    assert len(res.plan_id) == 32  # ID_HEX_WIDTH


def test_execute_plan_id_present_on_failure_too():
    """plan_id is populated on both ok and failed results."""
    leaf = Node(tag=":llm-call")
    d = Dispatcher()
    d.register(":llm-call", lambda n, env: (_ for _ in ()).throw(ValueError("boom")))
    res = execute(leaf, d)
    assert res.status == "failed"
    assert res.plan_id == leaf.id
