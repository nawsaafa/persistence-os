"""B6 — Load-bearing determinism pin for ``mcts_search`` (design §10, §15).

Two ``mcts_search`` invocations with identical
``(initial_plan, config, started_at_ms, expander_responses,
evaluator_responses)`` produce byte-identical ``MCTSResult.tree_dump``
+ ``search_id`` + ``winner_plan_id``. This is the load-bearing property
the Stream B paper claim (Prop 6) rests on — replay-from-datoms-alone
in B-INT is downstream of this pin.

B6 runs with ``db=None``: no provenance is emitted; determinism is
exercised purely against the in-process search machinery.
"""
from __future__ import annotations

from collections.abc import Sequence

from persistence.plan import (
    Action,
    MCTSConfig,
    Node,
    SubstituteLeafAction,
    mcts_search,
)
from persistence.plan._mcts import _StaticEvaluator, _StaticExpander


# --- Fixtures ------------------------------------------------------------ #


def _leaf(tag: str = ":leaf/predict", prompt: str = "x") -> Node:
    return Node(tag=tag, attrs={"prompt": prompt})


def _build_initial() -> Node:
    """Root plan with two leaves; expander substitutes leaves under it."""
    return Node(
        tag=":plan/seq",
        attrs={"version": 1},
        children=(_leaf(prompt="A"), _leaf(prompt="B")),
    )


def _build_substitute_pair(initial: Node) -> tuple[
    SubstituteLeafAction, SubstituteLeafAction, Node, Node
]:
    """Build two substitute-leaf actions reaching distinct child plans."""
    new_a = _leaf(prompt="A_substituted")
    new_b = _leaf(prompt="B_substituted")
    act_a = SubstituteLeafAction(target_path=(0,), new_leaf=new_a)
    act_b = SubstituteLeafAction(target_path=(1,), new_leaf=new_b)
    # Compute the resulting plan ids
    from persistence.plan import apply_action

    plan_a = apply_action(initial, act_a)
    plan_b = apply_action(initial, act_b)
    return act_a, act_b, plan_a, plan_b


# --- Tests --------------------------------------------------------------- #


def test_determinism_pin_two_runs_byte_identical_tree_dump():
    """Two runs with identical inputs produce byte-identical ``tree_dump``."""
    initial = _build_initial()
    act_a, act_b, plan_a, plan_b = _build_substitute_pair(initial)

    expander_proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act_a, 0.6), (act_b, 0.4)],
    }
    evaluator_scores = {
        initial.id: 0.5,
        plan_a.id: 0.7,
        plan_b.id: 0.3,
    }
    config = MCTSConfig(max_iter=20, max_unique_plans=64)

    result1 = mcts_search(
        initial,
        expander=_StaticExpander(expander_proposals),
        evaluator=_StaticEvaluator(evaluator_scores),
        started_at_ms=1_000_000,
        config=config,
    )
    result2 = mcts_search(
        initial,
        expander=_StaticExpander(expander_proposals),
        evaluator=_StaticEvaluator(evaluator_scores),
        started_at_ms=1_000_000,
        config=config,
    )

    assert result1.tree_dump == result2.tree_dump
    assert result1.search_id == result2.search_id
    assert result1.winner_plan_id == result2.winner_plan_id
    assert result1.iter_count == result2.iter_count
    assert result1.terminated_by == result2.terminated_by
    assert result1.root_q == result2.root_q


def test_determinism_pin_search_id_content_addressed():
    """``search_id`` is a deterministic function of inputs (design §13).

    ``"mcts/" + 16-hex sha256 prefix`` of canonical-JSON of
    ``{initial_plan_id, config_hash, started_at}``."""
    initial = _build_initial()
    act_a, _act_b, plan_a, plan_b = _build_substitute_pair(initial)

    config = MCTSConfig(max_iter=10)
    proposals_one: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act_a, 1.0)],
    }
    result = mcts_search(
        initial,
        expander=_StaticExpander(proposals_one),
        evaluator=_StaticEvaluator({initial.id: 0.5, plan_a.id: 0.6}),
        started_at_ms=1_234_567,
        config=config,
    )
    assert result.search_id.startswith("mcts/")
    assert len(result.search_id) == len("mcts/") + 16
    # Different started_at_ms changes search_id
    result2 = mcts_search(
        initial,
        expander=_StaticExpander(proposals_one),
        evaluator=_StaticEvaluator({initial.id: 0.5, plan_a.id: 0.6}),
        started_at_ms=2_345_678,
        config=config,
    )
    assert result2.search_id != result.search_id


def test_determinism_pin_tree_dump_lex_sorted_string():
    """``tree_dump`` is lex-sorted on (parent_id, child_id, action_hash) — string lex."""
    initial = _build_initial()
    act_a, act_b, plan_a, plan_b = _build_substitute_pair(initial)
    expander_proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act_a, 0.5), (act_b, 0.5)],
    }
    result = mcts_search(
        initial,
        expander=_StaticExpander(expander_proposals),
        evaluator=_StaticEvaluator(
            {initial.id: 0.5, plan_a.id: 0.7, plan_b.id: 0.3}
        ),
        started_at_ms=1_000_000,
        config=MCTSConfig(max_iter=10),
    )
    # Each row is (parent_plan_id, child_plan_id, action_hash, visits, q)
    for prev, nxt in zip(result.tree_dump, result.tree_dump[1:]):
        # Sort key is (row[0], row[1], row[2]) — every element a hex str.
        assert (prev[0], prev[1], prev[2]) < (nxt[0], nxt[1], nxt[2])


def test_determinism_pin_5x_no_flake():
    """Run 5 times back-to-back; assert all tree_dumps identical."""
    initial = _build_initial()
    act_a, act_b, plan_a, plan_b = _build_substitute_pair(initial)
    proposals: dict[str, Sequence[tuple[Action, float]]] = {
        initial.id: [(act_a, 0.5), (act_b, 0.5)]
    }
    scores = {initial.id: 0.5, plan_a.id: 0.7, plan_b.id: 0.3}

    dumps: list[tuple[tuple[str, str, str, int, float], ...]] = []
    for _ in range(5):
        result = mcts_search(
            initial,
            expander=_StaticExpander(proposals),
            evaluator=_StaticEvaluator(scores),
            started_at_ms=1_000_000,
            config=MCTSConfig(max_iter=20),
        )
        dumps.append(result.tree_dump)

    first = dumps[0]
    for d in dumps[1:]:
        assert d == first
