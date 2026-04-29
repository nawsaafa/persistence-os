"""B8 — ``MCTSReplayCacheMiss`` test-only stub contract (W2 MINOR-8).

Pins: ``MCTSReplayCacheMiss`` is **test-local**, never raised by the
production ``mcts_search()`` loop. Production cache-miss is the normal
path that calls the real LLM-backed expander/evaluator. The exception
class lives in this test module, NOT in ``_mcts.py``'s public surface
or any production module.

These stubs and the ``MCTSReplayCacheMiss`` class are reused by
B-INT's replay-from-datoms-alone integration test. B8 only verifies the
contract: stubs raise on cache miss, succeed on cache hit, and the
production module does not import the exception.
"""
from __future__ import annotations

from collections.abc import Sequence

from persistence.plan import (
    Action,
    MCTSConfig,
    Node,
    SubstituteLeafAction,
    apply_action,
    mcts_search,
)
from persistence.plan._mcts import _StaticEvaluator, _StaticExpander


# --- Test-only exception (NOT in _mcts.py / persistence.plan public) -- #


class MCTSReplayCacheMiss(RuntimeError):
    """Raised by replay-loud stubs when caches are insufficient.

    **Test-only.** Lives here, not in ``_mcts.py``. The production
    ``mcts_search()`` loop never raises this; production cache-miss is
    the normal path that calls the real expander/evaluator. Used by
    B-INT to assert "the populated caches were sufficient — no LLM
    re-call needed during replay".
    """


# --- Loud stubs that raise on any call ------------------------------- #


class _ReplayLoudExpander:
    """Expander that ALWAYS raises ``MCTSReplayCacheMiss`` on call.

    Used in replay tests after the expander cache has been populated;
    if the loop ever calls it, the cache was insufficient — replay
    fails loudly. Pinned in B-INT step 7 (design §13 replay procedure).
    """

    def propose(self, plan: Node, *, k: int) -> Sequence[tuple[Action, float]]:
        raise MCTSReplayCacheMiss(
            f"replay: expander called for plan_id={plan.id!r} k={k!r} "
            f"— cache should have hit"
        )


class _ReplayLoudEvaluator:
    """Evaluator that ALWAYS raises ``MCTSReplayCacheMiss`` on call."""

    def evaluate(self, plan: Node) -> float:
        raise MCTSReplayCacheMiss(
            f"replay: evaluator called for plan_id={plan.id!r} "
            f"— cache should have hit"
        )


# --- Test 1: stubs raise standalone --------------------------------- #


def test_replay_loud_expander_raises_on_call():
    """Stub expander raises ``MCTSReplayCacheMiss`` on first ``propose``."""
    stub = _ReplayLoudExpander()
    plan = Node(tag=":leaf/predict", attrs={"prompt": "x"})
    try:
        stub.propose(plan, k=4)
    except MCTSReplayCacheMiss as exc:
        assert "replay: expander called" in str(exc)
        assert plan.id in str(exc)
        return
    raise AssertionError("expander stub did not raise on call")


def test_replay_loud_evaluator_raises_on_call():
    """Stub evaluator raises ``MCTSReplayCacheMiss`` on first ``evaluate``."""
    stub = _ReplayLoudEvaluator()
    plan = Node(tag=":leaf/predict", attrs={"prompt": "x"})
    try:
        stub.evaluate(plan)
    except MCTSReplayCacheMiss as exc:
        assert "replay: evaluator called" in str(exc)
        return
    raise AssertionError("evaluator stub did not raise on call")


# --- Test 2: production loop does not import / raise the exception -- #


def test_mcts_module_does_not_export_replay_cache_miss():
    """``MCTSReplayCacheMiss`` is NOT in ``persistence.plan._mcts.__all__``."""
    from persistence.plan import _mcts as mcts_mod

    assert "MCTSReplayCacheMiss" not in mcts_mod.__all__, (
        "MCTSReplayCacheMiss must remain test-local "
        "(W2 MINOR-8); not part of _mcts.py's public surface"
    )
    # Also assert it's not even a name in the module.
    assert not hasattr(mcts_mod, "MCTSReplayCacheMiss"), (
        "_mcts.py must not import or define MCTSReplayCacheMiss"
    )


def test_mcts_module_does_not_export_via_persistence_plan_public():
    """The top-level ``persistence.plan`` package does not re-export the stub."""
    import persistence.plan as plan_pkg

    assert "MCTSReplayCacheMiss" not in plan_pkg.__all__
    assert not hasattr(plan_pkg, "MCTSReplayCacheMiss")


# --- Test 3: B-INT precursor — stubs work as drop-in for real search - #


def test_search_runs_clean_with_real_stubs_then_replay_loud_would_fail():
    """Forward search with real stubs runs cleanly; loud stubs would fail.

    Sanity check that ``_ReplayLoudExpander`` is a Protocol-compatible
    drop-in with the same surface as ``_StaticExpander``. We don't run
    the loud version end-to-end here (B-INT does) — that requires a
    populated cache. We just verify the type contract.
    """
    initial = Node(
        tag=":plan/seq",
        attrs={"v": 1},
        children=(Node(tag=":leaf/predict", attrs={"prompt": "x"}),),
    )
    act = SubstituteLeafAction(
        target_path=(0,),
        new_leaf=Node(tag=":leaf/predict", attrs={"prompt": "y"}),
    )
    plan_a = apply_action(initial, act)
    # Real stubs produce a clean run.
    real_result = mcts_search(
        initial,
        expander=_StaticExpander({initial.id: [(act, 1.0)]}),
        evaluator=_StaticEvaluator({initial.id: 0.5, plan_a.id: 0.7}),
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=2, max_unique_plans=8),
    )
    assert real_result.iter_count >= 1
    # Loud stubs satisfy the Protocol surface — they're callable on a
    # ``Node`` and would raise on any invocation. We don't actually
    # invoke ``mcts_search`` with them here (B-INT does, with populated
    # caches); we just check the structural compatibility.
    loud_exp: object = _ReplayLoudExpander()
    loud_eval: object = _ReplayLoudEvaluator()
    assert hasattr(loud_exp, "propose")
    assert hasattr(loud_eval, "evaluate")
