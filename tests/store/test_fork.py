"""``DB.fork`` substrate-primitive unit tests — Phase 2.0c-extended #145ext.

See ``docs/plans/2026-04-30-phase-2-persistence-coder-design.md`` § 3.7
+ § 4.3 + ADR-7, and ``src/persistence/fact/_fork.py`` for the impl.
Folds in carryover #201 from the 2.0c carryover backlog.

Coverage in this file is for the **bare** ``DB.fork`` primitive
(speculate / score / pick / rollback over opaque Python state). The
``fold_into`` rewire-on-top-of-fork tests live in
``test_substrate_txn_fold_into.py``; the SDK surface ``s.txn.fork``
tests live in ``test_substrate_txn_fork.py``; the 4-datom audit-chain
shape tests live in ``test_fork_audit.py``.

Test plan:

1.  Happy path — argmax over 3 branches; ForkResult shape.
2.  ForkResult is frozen.
3.  Empty items -> ValueError.
4.  Outside dosync -> ForkOutsideDosync.
5.  Inside dosync without tx kwarg -> ForkOutsideDosync.
6.  choose returns out-of-range -> ForkChooseError(ValueError).
7.  choose returns negative -> ForkChooseError(ValueError).
8.  choose returns non-int -> ForkChooseError(TypeError).
9.  choose returns bool -> ForkChooseError(TypeError).
10. choose raises arbitrary exception -> ForkChooseError with cause.
11. fn raises under on_error="stop" -> exception propagates; choose never called.
12. fn raises under on_error="continue" -> failed branch recorded with
    score=None, error="<TypeName>: <msg>", branch_state=seed.
13. on_error="continue" with all branches failing — choose still runs,
    sees all-failed list (no auto-skip).
14. Invalid on_error value -> ValueError.
15. branch_id is content-addressed (same item + state -> same id).
16. branch_state can be any Python value (dict, tuple, etc).
17. **No facts committed by DB.fork itself** — only audit datoms emitted.
"""
from __future__ import annotations

import pytest

from persistence.fact import (
    DB,
    ForkBranchResult,
    ForkChooseError,
    ForkOutsideDosync,
    ForkResult,
)
from persistence.sdk import Substrate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_fn(state, item):
    """Trivial reducer: state + item."""
    return state + item


def _argmax(branches):
    """Pick the branch with the largest numeric branch_state."""
    return max(range(len(branches)), key=lambda i: branches[i].branch_state)


# ---------------------------------------------------------------------------
# 1-2. Happy path
# ---------------------------------------------------------------------------


def test_fork_happy_path_argmax():
    """3 items [1, 5, 3]; fn=add to seed=0; argmax picks index 1."""
    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            result = s._db.fork(
                items=[1, 5, 3],
                fn=_add_fn,
                choose=_argmax,
                seed=0,
                tx=tx,
            )
    assert isinstance(result, ForkResult)
    assert result.chosen_index == 1
    assert result.chosen_state == 5
    assert len(result.all_branches) == 3
    # Each branch is a ForkBranchResult with the expected shape.
    for i, b in enumerate(result.all_branches):
        assert isinstance(b, ForkBranchResult)
        assert b.branch_index == i
        assert b.score is None  # bare DB.fork doesn't populate score
        assert b.error is None
    # branch_state values match fn(seed, item).
    states = [b.branch_state for b in result.all_branches]
    assert states == [1, 5, 3]


def test_fork_result_is_frozen():
    """ForkResult is a frozen dataclass — caller cannot mutate."""
    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            result = s._db.fork(
                items=[1],
                fn=_add_fn,
                choose=lambda b: 0,
                seed=0,
                tx=tx,
            )
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        result.chosen_index = 99  # type: ignore[misc]


def test_fork_branch_result_is_frozen():
    """ForkBranchResult is a frozen dataclass too."""
    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            result = s._db.fork(
                items=[1],
                fn=_add_fn,
                choose=lambda b: 0,
                seed=0,
                tx=tx,
            )
    branch = result.all_branches[0]
    with pytest.raises(Exception):
        branch.branch_index = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 3-5. Empty items + dosync gate
# ---------------------------------------------------------------------------


def test_fork_empty_items_raises():
    """Empty items -> ValueError before any fn / choose runs."""
    with Substrate.open("memory") as s:
        with pytest.raises(ValueError, match="non-empty"):
            with s.txn.dosync() as tx:
                s._db.fork(
                    items=[],
                    fn=_add_fn,
                    choose=_argmax,
                    seed=0,
                    tx=tx,
                )


def test_fork_outside_dosync_raises():
    """Calling DB.fork outside dosync -> ForkOutsideDosync."""
    with Substrate.open("memory") as s:
        with pytest.raises(ForkOutsideDosync, match="dosync"):
            s._db.fork(
                items=[1, 2],
                fn=_add_fn,
                choose=_argmax,
                seed=0,
            )


def test_fork_dosync_without_tx_raises():
    """Inside dosync but tx=None -> ForkOutsideDosync.

    The dosync ContextVar guard says "in dosync" but we still require
    explicit tx so the audit datoms never silently drop.
    """
    with Substrate.open("memory") as s:
        with pytest.raises(ForkOutsideDosync, match="dosync"):
            with s.txn.dosync():
                s._db.fork(
                    items=[1, 2],
                    fn=_add_fn,
                    choose=_argmax,
                    seed=0,
                    # tx omitted -> default None
                )


# ---------------------------------------------------------------------------
# 6-10. choose-callback validation
# ---------------------------------------------------------------------------


def test_fork_choose_returns_out_of_range_raises():
    """choose returns 5 for 3-branch input -> ForkChooseError(ValueError)."""
    with Substrate.open("memory") as s:
        with pytest.raises(ForkChooseError) as exc_info:
            with s.txn.dosync() as tx:
                s._db.fork(
                    items=[1, 2, 3],
                    fn=_add_fn,
                    choose=lambda b: 5,
                    seed=0,
                    tx=tx,
                )
        assert isinstance(exc_info.value.__cause__, ValueError)


def test_fork_choose_returns_negative_raises():
    """choose returns -1 -> ForkChooseError(ValueError)."""
    with Substrate.open("memory") as s:
        with pytest.raises(ForkChooseError) as exc_info:
            with s.txn.dosync() as tx:
                s._db.fork(
                    items=[1, 2],
                    fn=_add_fn,
                    choose=lambda b: -1,
                    seed=0,
                    tx=tx,
                )
        assert isinstance(exc_info.value.__cause__, ValueError)


def test_fork_choose_returns_non_int_raises():
    """choose returns "first" -> ForkChooseError(TypeError)."""
    with Substrate.open("memory") as s:
        with pytest.raises(ForkChooseError) as exc_info:
            with s.txn.dosync() as tx:
                s._db.fork(
                    items=[1, 2],
                    fn=_add_fn,
                    choose=lambda b: "first",  # type: ignore[return-value,arg-type]
                    seed=0,
                    tx=tx,
                )
        assert isinstance(exc_info.value.__cause__, TypeError)


def test_fork_choose_returns_bool_raises():
    """choose returns True (an int subclass) -> ForkChooseError(TypeError).

    Treating True as 1 silently is a footgun; the contract requires
    a clean int, same as fold_into.
    """
    with Substrate.open("memory") as s:
        with pytest.raises(ForkChooseError) as exc_info:
            with s.txn.dosync() as tx:
                s._db.fork(
                    items=[1, 2],
                    fn=_add_fn,
                    choose=lambda b: True,  # type: ignore[return-value]
                    seed=0,
                    tx=tx,
                )
        assert isinstance(exc_info.value.__cause__, TypeError)


def test_fork_choose_raises_arbitrary_exception_wrapped():
    """choose raises ZeroDivisionError -> ForkChooseError with __cause__."""
    def bad_choose(branches):
        return 1 // 0  # raises

    with Substrate.open("memory") as s:
        with pytest.raises(ForkChooseError) as exc_info:
            with s.txn.dosync() as tx:
                s._db.fork(
                    items=[1, 2],
                    fn=_add_fn,
                    choose=bad_choose,
                    seed=0,
                    tx=tx,
                )
        assert isinstance(exc_info.value.__cause__, ZeroDivisionError)


# ---------------------------------------------------------------------------
# 11-13. fn-failure handling under on_error
# ---------------------------------------------------------------------------


def test_fork_fn_raises_under_stop_propagates():
    """fn raises under on_error='stop' -> exception propagates; choose never called."""
    choose_calls = []

    def raising_fn(state, item):
        if item == 99:
            raise RuntimeError("boom")
        return state + item

    def tracking_choose(branches):
        choose_calls.append(len(branches))
        return 0

    with Substrate.open("memory") as s:
        with pytest.raises(RuntimeError, match="boom"):
            with s.txn.dosync() as tx:
                s._db.fork(
                    items=[1, 99, 3],
                    fn=raising_fn,
                    choose=tracking_choose,
                    seed=0,
                    tx=tx,
                    on_error="stop",
                )
    assert choose_calls == []  # choose never invoked


def test_fork_fn_raises_under_continue_records_failure():
    """fn raises on item 99 with on_error='continue' -> branch recorded with
    score=None, error populated, branch_state=seed."""
    def raising_fn(state, item):
        if item == 99:
            raise RuntimeError("boom")
        return state + item

    seen = []

    def picky_choose(branches):
        seen.append(list(branches))
        # Pick the branch with max numeric state, treating None error as ok.
        scoreable = [b for b in branches if b.error is None]
        if not scoreable:
            return 0
        winner = max(scoreable, key=lambda b: b.branch_state)
        return winner.branch_index

    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            result = s._db.fork(
                items=[1, 99, 5, 3],
                fn=raising_fn,
                choose=picky_choose,
                seed=0,
                tx=tx,
                on_error="continue",
            )
    # All 4 branches present in the result list (failures included).
    assert len(result.all_branches) == 4
    # The failing branch has error populated, branch_state=seed=0.
    failed = result.all_branches[1]
    assert failed.error is not None
    assert "RuntimeError" in failed.error
    assert "boom" in failed.error
    assert failed.branch_state == 0  # seed
    # The other branches have error=None.
    for i, b in enumerate(result.all_branches):
        if i == 1:
            continue
        assert b.error is None
    # choose saw all 4 branches.
    assert len(seen) == 1
    assert len(seen[0]) == 4
    # Picked the highest-state successful branch (index 2, value 5).
    assert result.chosen_index == 2
    assert result.chosen_state == 5


def test_fork_continue_all_failed_choose_still_runs():
    """All branches fail under on_error='continue' — choose still runs
    against the failed list (no auto-skip).

    This contract differs from fold_into's "every branch was skipped ->
    ValueError" — DB.fork is a lower-level primitive, and the caller's
    choose may legitimately want to pick from a list of all-failed
    branches (e.g. retry the least-broken-looking error).
    """
    def all_raise(state, item):
        raise RuntimeError(f"item {item} failed")

    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            result = s._db.fork(
                items=[1, 2, 3],
                fn=all_raise,
                choose=lambda branches: 0,
                seed=0,
                tx=tx,
                on_error="continue",
            )
    # All 3 branches in the result list, all with error populated.
    assert len(result.all_branches) == 3
    for b in result.all_branches:
        assert b.error is not None
        assert "RuntimeError" in b.error
    # choose picked index 0; that's the legitimate winner.
    assert result.chosen_index == 0


# ---------------------------------------------------------------------------
# 14. Invalid on_error
# ---------------------------------------------------------------------------


def test_fork_invalid_on_error_raises():
    """on_error='abort' (a fold-style value) -> ValueError on entry."""
    with Substrate.open("memory") as s:
        with pytest.raises(ValueError, match="on_error"):
            with s.txn.dosync() as tx:
                s._db.fork(
                    items=[1],
                    fn=_add_fn,
                    choose=lambda b: 0,
                    seed=0,
                    tx=tx,
                    on_error="abort",  # type: ignore[arg-type]
                )


# ---------------------------------------------------------------------------
# 15. branch_id is content-addressed
# ---------------------------------------------------------------------------


def test_fork_branch_id_content_addressed():
    """Same (item, branch_state) -> same branch_id across runs."""
    with Substrate.open("memory") as s1:
        with s1.txn.dosync() as tx1:
            r1 = s1._db.fork(
                items=[10, 20, 30],
                fn=_add_fn,
                choose=lambda b: 0,
                seed=0,
                tx=tx1,
            )

    with Substrate.open("memory") as s2:
        with s2.txn.dosync() as tx2:
            r2 = s2._db.fork(
                items=[10, 20, 30],
                fn=_add_fn,
                choose=lambda b: 0,
                seed=0,
                tx=tx2,
            )

    # Same inputs -> same branch_ids.
    ids1 = [b.branch_id for b in r1.all_branches]
    ids2 = [b.branch_id for b in r2.all_branches]
    assert ids1 == ids2
    # Each branch_id is 16-hex.
    for bid in ids1:
        assert len(bid) == 16
        int(bid, 16)  # raises if not hex


def test_fork_different_states_yield_different_branch_ids():
    """Same item, different fn -> different branch_ids."""
    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            r_add = s._db.fork(
                items=[5],
                fn=lambda state, item: state + item,
                choose=lambda b: 0,
                seed=0,
                tx=tx,
            )
            r_mul = s._db.fork(
                items=[5],
                fn=lambda state, item: state * item,
                choose=lambda b: 0,
                seed=2,
                tx=tx,
            )
    assert r_add.all_branches[0].branch_id != r_mul.all_branches[0].branch_id


# ---------------------------------------------------------------------------
# 16. branch_state can be any Python value
# ---------------------------------------------------------------------------


def test_fork_branch_state_can_be_dict():
    """branch_state is opaque to the substrate — dict / tuple / custom OK."""
    def make_dict(state, item):
        return {"item": item, "doubled": item * 2}

    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            result = s._db.fork(
                items=[1, 2, 3],
                fn=make_dict,
                choose=lambda b: max(
                    range(len(b)), key=lambda i: b[i].branch_state["doubled"]
                ),
                tx=tx,
            )
    assert result.chosen_index == 2
    assert result.chosen_state == {"item": 3, "doubled": 6}


def test_fork_branch_state_can_be_tuple():
    """branch_state can be a structural tuple — choose reads the score
    from a tuple position; the audit datom canonicalises the structural
    state via canonical-JSON."""
    def make_tuple(state, item):
        return (item, item ** 2, "tag")

    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            result = s._db.fork(
                items=[1, 2, 3],
                fn=make_tuple,
                choose=lambda b: max(
                    range(len(b)), key=lambda i: b[i].branch_state[1]
                ),
                tx=tx,
            )
    # branch_state for chosen is the squared-largest item.
    assert result.chosen_index == 2
    # tuple may be coerced to list at the audit layer; compare via
    # accessor on the result struct (not via canonical JSON):
    assert tuple(result.chosen_state) == (3, 9, "tag")


# ---------------------------------------------------------------------------
# 17. DB.fork does not commit any facts at the bare layer
# ---------------------------------------------------------------------------


def test_fork_emits_no_substrate_facts_at_bare_layer():
    """The bare DB.fork primitive emits ONLY audit datoms (via tx.effect),
    NOT substrate facts. Adapters that want fact-side commits thread
    them through the closure (see fold_into in C5).

    This test verifies the rollback contract at the substrate-fact
    layer: even though fn executes against 3 branches, no fact
    datoms appear in db.history() post-commit.
    """
    def stateful_fn(state, item):
        return {"derived": item * 100}

    with Substrate.open("memory") as s:
        # Capture the pre-fork log length.
        pre_facts = list(s.escape.fact.store.all_datoms())
        pre_count = len(pre_facts)

        with s.txn.dosync() as tx:
            result = s._db.fork(
                items=[1, 2, 3],
                fn=stateful_fn,
                choose=lambda b: 0,
                tx=tx,
            )

        # Post-fork log: exactly one new datom (the dosync commit datom),
        # not 3 per-branch facts. The bare DB.fork primitive commits NO
        # substrate facts itself.
        post_facts = list(s.escape.fact.store.all_datoms())
        new_facts = post_facts[pre_count:]

        # The only new datom is the dosync commit fact (a == ":persistence.txn/commit-id").
        commit_facts = [
            d for d in new_facts
            if d.a == "persistence.txn/commit-id"
        ]
        non_commit_facts = [
            d for d in new_facts
            if d.a != "persistence.txn/commit-id"
        ]
        assert len(commit_facts) == 1
        assert len(non_commit_facts) == 0, (
            f"DB.fork bare layer must not emit substrate facts; "
            f"got: {[(d.e, d.a, d.v) for d in non_commit_facts]}"
        )

    # Result still came back fine; the chosen branch is index 0.
    assert result.chosen_index == 0
