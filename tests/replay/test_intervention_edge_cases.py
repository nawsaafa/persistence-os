"""ARIS Round 6 pre-Phase-2 cleanup — R2 R4-G3 intervention edge cases.

R2 flagged three untested intervention shapes that the engine already
handles one way or another, but whose behaviour was never pinned. Pin
them so future refactors don't silently change the contract:

1. **Empty intervention list** — ``replay(traj, [])`` already raises
   ``ValueError`` at ``engine.py:134`` ("replay requires at least one
   intervention"). Pin the exact error path.

2. **Out-of-order step numbers** — the engine uses
   ``interventions_by_step = {i["step"]: i for i in interventions}``
   at ``engine.py:159``, so list order doesn't affect the result.
   Assert the hash is identical regardless of the order the caller
   submits the interventions in.

3. **Duplicate step numbers** — the dict comprehension above means the
   *last* entry for a given step wins silently. Pin that behaviour so
   a Phase-2 contributor who tightens it to a ``ValueError`` has to
   actively update this test (the assumption is recorded in the test
   itself, not buried in code).
"""
from __future__ import annotations

import pytest

from persistence.replay.engine import record
from persistence.replay.trajectory import trajectory_hash


# ---------------------------------------------------------------------------
# 1. Empty intervention list — fail fast
# ---------------------------------------------------------------------------


def test_empty_intervention_list_raises(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    """An empty intervention list has no semantic meaning — replay must
    fail fast rather than silently emit a counterfactual identical to
    the factual (which would burn compute for zero value and muddy the
    lineage — the Trajectory would carry ``intervention=[]`` rather
    than a clear signal that no intervention was actually applied).
    """
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    with pytest.raises(ValueError, match="at least one intervention"):
        toy_replay(factual, [])


# ---------------------------------------------------------------------------
# 2. Out-of-order step numbers — order-independent by construction
# ---------------------------------------------------------------------------


def test_out_of_order_intervention_steps_produce_identical_hash(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    """The engine keys interventions by step, not by list position. A
    caller who submits ``[step=3, step=1]`` must get the same
    counterfactual (same hash, same facts, same branch_point) as a
    caller who submits ``[step=1, step=3]``.

    This pins the order-independence contract at the engine boundary —
    any future refactor that accidentally respects list order (e.g.
    replacing the dict with a list) would light this up.
    """
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)

    ordered = [
        {"step": 1, "field": "action", "new_value": {"type": "wait"}},
        {"step": 3, "field": "obs", "new_value": {"price": 999, "regime": "chop"}},
    ]
    reversed_ = list(reversed(ordered))

    cf_ordered = toy_replay(factual, ordered)
    cf_reversed = toy_replay(factual, reversed_)

    # Semantic: the two counterfactuals must be hash-identical.
    assert trajectory_hash(cf_ordered) == trajectory_hash(cf_reversed)

    # branch_point is ``min(step)`` — order-independent.
    assert cf_ordered.branch_point == cf_reversed.branch_point == 1

    # Facts are byte-identical because rng alignment depends only on
    # (seeds, step, intervention-at-step).
    assert len(cf_ordered.facts) == len(cf_reversed.facts)
    for a, b in zip(cf_ordered.facts, cf_reversed.facts):
        assert a.state == b.state
        assert a.action == b.action
        assert a.random_draws == b.random_draws


def test_out_of_order_intervention_steps_apply_at_correct_indices(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    """Not just hash-identical — the intervened facts land at their
    declared step indices even when submitted out-of-order.
    """
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    # Submit step=3 before step=1 deliberately.
    cf = toy_replay(
        factual,
        [
            {"step": 3, "field": "obs", "new_value": {"price": 999, "regime": "chop"}},
            {"step": 1, "field": "action", "new_value": {"type": "wait"}},
        ],
    )

    # Step 1's action was overridden.
    assert cf.facts[1].action == {"type": "wait"}
    # Step 3's obs was overridden.
    assert cf.facts[3].obs == {"price": 999, "regime": "chop"}


# ---------------------------------------------------------------------------
# 3. Duplicate step numbers — last-wins is the current contract
# ---------------------------------------------------------------------------


def test_duplicate_intervention_step_last_entry_wins(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    """Pin: ``{i["step"]: i for i in interventions}`` in ``engine.py``
    means a duplicated step keeps the *last* entry. Callers passing
    two interventions for the same step get the second one applied.

    This is a deliberate pin of current behaviour — the reviewer
    flagged the untested path, not the behaviour itself. Phase-2 may
    promote this to a ``ValueError`` (since duplicate-step usually
    signals a caller bug in programmatic intervention generation);
    at that point this test becomes a behaviour-change sentinel.
    """
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    cf = toy_replay(
        factual,
        [
            {"step": 1, "field": "action", "new_value": {"type": "wait"}},
            {"step": 1, "field": "action", "new_value": {"type": "sell"}},  # last
        ],
    )
    # Last entry wins.
    assert cf.facts[1].action == {"type": "sell"}


def test_duplicate_intervention_step_lineage_records_full_list(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    """Even though only the *last* intervention-per-step is applied, the
    ``Trajectory.intervention`` lineage surface stores the FULL submitted
    list verbatim (see ``engine.py:170`` —
    ``[copy.deepcopy(iv) for iv in interventions]``).

    This is intentional — the lineage is the caller's submitted
    intent; the effect is the deduplicated application. Keeping both
    visible makes it possible for regulator-replay / DPO consumers to
    detect the ambiguity in the original intervention set.
    """
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    submitted = [
        {"step": 1, "field": "action", "new_value": {"type": "wait"}},
        {"step": 1, "field": "action", "new_value": {"type": "sell"}},
    ]
    cf = toy_replay(factual, submitted)

    # Lineage records BOTH entries.
    assert isinstance(cf.intervention, list)
    assert len(cf.intervention) == 2
    assert cf.intervention[0]["new_value"] == {"type": "wait"}
    assert cf.intervention[1]["new_value"] == {"type": "sell"}


def test_out_of_range_step_still_fails_before_deduplication(
    toy_obs_stream, toy_seeds, toy_initial_state, toy_agent, toy_apply, toy_replay
):
    """Interaction of G3 (duplicate/OOO) with R2 F3 (out-of-range): an
    out-of-range step in the list must still raise even if a valid
    duplicate for the same step also appears. The range check runs
    before the dict-dedup.
    """
    factual = record(toy_obs_stream, toy_seeds, toy_agent, toy_apply, toy_initial_state)
    with pytest.raises(ValueError, match="out of range"):
        toy_replay(
            factual,
            [
                {"step": 1, "field": "action", "new_value": {"type": "wait"}},
                {"step": 99, "field": "action", "new_value": {"type": "sell"}},
            ],
        )
