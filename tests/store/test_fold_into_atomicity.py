"""``s.txn.fold_into`` chosen-branch atomicity tests — Phase 2.0d W1 / M3.

Phase 2.0d W1 (R2 MAJOR M3 fix): the chosen branch's facts now ride
the outer ``dosync``'s atomic ``transact_batch`` (via the new
``Transaction.staged_facts`` field + ``tx.add_facts`` API), instead
of calling ``db.transact_batch`` directly mid-dosync. Pre-W1 the
direct call committed immediately — an outer body raise after
``fold_into`` returned left chosen-branch facts in
``db.history()``, breaking the rollback contract from design § 4.3.

Cross-references: design § 3.7 + § 4.3 + ADR-7; the M3 finding in
``review-stage/aris-r2-v0.8.5a1-raw.txt``.
"""
from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from persistence.effect import canonical_audit_stack, with_runtime
from persistence.sdk import Substrate


def _emit_fact_fn(_acc, item, _db):
    """``fn`` that emits one fact per branch + carries item as score."""
    facts = [{"e": f"e-{item}", "a": "value", "v": int(item) * 10}]
    return _acc, facts, item


def _argmax(branches):
    return max(range(len(branches)), key=lambda i: branches[i].score)


# ---------------------------------------------------------------------------
# 1. Chosen facts atomic with outer commit — outer-raise rolls them back
# ---------------------------------------------------------------------------


def test_fold_into_chosen_facts_atomic_with_outer_commit() -> None:
    """If the outer ``dosync`` body raises AFTER ``fold_into`` returns,
    the chosen branch's staged facts are NOT committed — they were
    queued on ``tx.staged_facts`` and rolled back along with the rest
    of the transaction.
    """
    s = Substrate.open("memory")
    try:
        # Snapshot pre-fork log length.
        pre_facts = list(s._db.store.all_datoms())
        pre_count = len(pre_facts)

        with pytest.raises(RuntimeError, match="outer body raised"):
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=0,
                    items=[1, 2, 3],
                    fn=_emit_fact_fn,
                    choose=_argmax,
                    tx=tx,
                )
                # Outer body raises AFTER fold_into returned. Pre-W1
                # this raise would still leave the chosen-branch
                # facts in db.history(); post-W1 they roll back.
                raise RuntimeError("outer body raised")

        # No facts committed.
        post_facts = list(s._db.store.all_datoms())
        assert len(post_facts) == pre_count, (
            f"chosen-branch facts leaked across outer raise: "
            f"pre={pre_count} post={len(post_facts)}"
        )
        # Specifically, no e-3 (the chosen item under argmax-on-item)
        # value datom exists.
        for d in post_facts:
            assert d.e != "e-3", (
                f"chosen-branch fact for item=3 leaked: {d}"
            )
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 2. Chosen facts visible only after outer commit succeeds
# ---------------------------------------------------------------------------


def test_fold_into_chosen_facts_visible_after_outer_commit_only() -> None:
    """Inside a successful outer ``dosync`` body, the chosen-branch
    facts must NOT be visible via direct ``db.q`` mid-body — the
    M3 staging path defers their commit until the outer txn lands.
    They become visible only AFTER the dosync completes.
    """
    s = Substrate.open("memory")
    try:
        with s.txn.dosync() as tx:
            s.txn.fold_into(
                seed=0,
                items=[1, 2, 3],
                fn=_emit_fact_fn,
                choose=_argmax,
                tx=tx,
            )
            # Mid-dosync: query the snapshot at t_start. The chosen
            # branch's facts are not yet committed — they live on
            # tx.staged_facts and have not been transact_batch'd.
            mid_view = s._db.as_of(tx.t_start)
            assert mid_view.entity("e-3") == {} or mid_view.entity("e-3") is None, (
                "chosen-branch facts visible mid-dosync — staging "
                "path leaked"
            )
            # Confirm they are staged on the txn.
            assert len(tx.staged_facts) == 1
            assert tx.staged_facts[0]["e"] == "e-3"
            assert tx.staged_facts[0]["v"] == 30

        # After commit, the chosen-branch facts are visible.
        post_view = s._db.as_of(s._db._clock())
        assert post_view.entity("e-3") == {"value": 30}
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 3. Successful fold_into still commits chosen facts (regression check)
# ---------------------------------------------------------------------------


def test_fold_into_successful_run_commits_chosen_facts() -> None:
    """The W1 staging fix must NOT regress the happy path: a
    successful ``fold_into`` followed by a clean dosync exit commits
    exactly the chosen branch's facts (and only those).
    """
    s = Substrate.open("memory")
    try:
        with s.txn.dosync() as tx:
            result = s.txn.fold_into(
                seed=0,
                items=[1, 2, 3],
                fn=_emit_fact_fn,
                choose=_argmax,
                tx=tx,
            )

        assert result.chosen_index == 2  # argmax over [1, 2, 3] picks idx 2.
        assert result.total_datoms_committed == 1

        # Only e-3 committed; e-1 / e-2 are non-chosen branches and
        # never reached the substrate.
        view = s._db.as_of(s._db._clock())
        assert view.entity("e-3") == {"value": 30}
        assert view.entity("e-1") in (None, {})
        assert view.entity("e-2") in (None, {})
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 4. Hypothesis byte-identity property at @max_examples=200
# ---------------------------------------------------------------------------


@given(
    items=st.lists(
        st.integers(min_value=1, max_value=100),
        min_size=2,
        max_size=8,
        unique=True,
    )
)
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_property_rolled_back_fold_into_leaves_substrate_unchanged(
    items: list[int],
) -> None:
    """For any non-empty integer-item list, an outer-dosync raise after
    ``fold_into`` returns leaves ``db.history()`` identical to its
    pre-fold_into state. The chosen-branch facts staged on
    ``tx.staged_facts`` must roll back atomically with the rest of
    the txn.
    """
    s = Substrate.open("memory")
    try:
        pre_facts = list(s._db.store.all_datoms())
        pre_eids = sorted(d.e for d in pre_facts)

        try:
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=0,
                    items=items,
                    fn=_emit_fact_fn,
                    choose=_argmax,
                    tx=tx,
                )
                raise RuntimeError("force-rollback")
        except RuntimeError:
            pass

        post_facts = list(s._db.store.all_datoms())
        post_eids = sorted(d.e for d in post_facts)
        assert pre_eids == post_eids, (
            f"rolled-back fold_into changed db.history(): "
            f"pre={pre_eids} post={post_eids}"
        )
    finally:
        s.close()
