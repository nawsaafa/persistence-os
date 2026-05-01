"""``s.txn.fold_into`` unit tests — Phase 2.0c-extended #145ext.

See ``docs/plans/2026-04-30-phase-2-persistence-coder-design.md`` § 4.3
+ ADR-7, and ``src/persistence/sdk/_fold_into.py`` for the impl
(rewired on top of ``DB.fork`` in Phase 2.0c-extended).

**Phase 2.0c-extended supersedes the Path-A foldl-with-marker impl
shipped at v0.8.0a1.** The substrate-true semantics are now:

- Each branch starts from ``seed`` (NOT from the previous branch's
  accumulator) — ``DB.fork`` runs branches in isolation, not as a
  foldl.
- **Only the chosen branch's facts are committed** to the substrate;
  non-chosen branches' facts are rolled back (never reach
  ``db.history()``).
- Audit shape is the canonical 4-datom emission (``:fork/probe`` +
  ``:fork/branch`` × N + ``:fork/score`` × N + ``:fork/chosen``);
  the legacy ``:fold/chosen`` op is no longer emitted by
  ``fold_into``.
- ``fn`` raising under ``on_error="abort"`` re-raises the original
  exception directly (no ``FoldError`` wrapper) — fold_into is no
  longer routed through ``DB.fold``.

Tests that assertted Path-A semantics (all-branches-committed,
foldl-style accumulator carry-over, ``FoldError`` wrapping) are
updated. Tests that asserted contract invariants (dosync gate, choose
validation, fn-shape validation, byte-identity) keep their original
shape and stay green.

Test plan:

1. Happy path — argmax over 3 branches, FoldIntoResult shape under
   fork-isolation semantics (each branch starts from seed).
2. **NEW** — only chosen branch's facts persist post-commit (rollback
   verification at the substrate-fact layer).
3. choose returns out-of-range -> FoldIntoChooseError(ValueError)
4. choose returns negative -> FoldIntoChooseError(ValueError)
5. choose returns non-int -> FoldIntoChooseError(TypeError)
6. choose returns bool -> FoldIntoChooseError(TypeError)
7. Empty items -> ValueError
8. Outside dosync -> FoldIntoOutsideDosync
9. fn returns 2-tuple -> FoldIntoChooseError(TypeError)
10. fn returns non-finite score (NaN) -> FoldIntoChooseError(ValueError)
11. fn raises under on_error="abort" -> original exception propagates
    (NO FoldError wrapper); choose never called; no facts committed.
12. fn raises under on_error="skip" -> choose sees only successful
    branches; chosen_index is into the SUCCESSFUL list.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import nan

import pytest

from persistence.sdk import Substrate
from persistence.sdk._fold_into import (
    FoldBranchScore,
    FoldIntoChooseError,
    FoldIntoOutsideDosync,
    FoldIntoResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


def _scoring_fn(acc, item, db):
    """Reducer that emits one fact per item with score = item value."""
    fact = {
        "e": f"branch-{item}",
        "a": "fold/value",
        "v": item,
        "valid_from": _now(),
    }
    return acc + item, [fact], float(item)


def _argmax(branches: list[FoldBranchScore]) -> int:
    return max(range(len(branches)), key=lambda i: branches[i].score)


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


def test_fold_into_happy_path_argmax():
    """3 branches with scores 1, 5, 3; argmax picks index 1 (score 5).

    **Phase 2.0c-extended**: each branch starts from ``seed`` in
    isolation (DB.fork-backed), so ``chosen_accumulator`` is
    ``seed + chosen_item`` (NOT the foldl-accumulated sum across all
    items up to and including the chosen one). Only the chosen
    branch's facts persist; non-chosen branches' facts are rolled
    back (verified separately by
    ``test_fold_into_rolls_back_non_chosen_branch_facts``).
    """
    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            result = s.txn.fold_into(
                seed=0,
                items=[1, 5, 3],
                fn=_scoring_fn,
                choose=_argmax,
                tx=tx,
            )
        assert isinstance(result, FoldIntoResult)
        assert result.chosen_index == 1
        assert result.chosen_score == 5.0
        assert result.all_scores == (1.0, 5.0, 3.0)
        # chosen_accumulator is seed + chosen_item = 0 + 5 = 5 (each
        # branch is isolated under fork-rollback semantics).
        assert result.chosen_accumulator == 5
        # final_accumulator is the LAST branch's accumulator: seed + 3 = 3.
        assert result.final_accumulator == 3
        # **Rollback semantics**: only the chosen branch's facts are
        # committed (1 datom), NOT all 3 branches' facts.
        assert result.total_datoms_committed == 1


def test_fold_into_rolls_back_non_chosen_branch_facts():
    """Phase 2.0c-extended substrate-true rollback: only the chosen
    branch's facts persist post-commit; non-chosen branches' facts
    NEVER reach ``db.history()``.

    This is the wedge-story test: rewind/branch/replay needs per-branch
    rollback to be real, not just an audit-marker over a foldl.
    """
    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            result = s.txn.fold_into(
                seed=0,
                items=[10, 50, 30],  # branch values 10, 50, 30
                fn=_scoring_fn,
                choose=_argmax,  # picks branch 1 (value 50)
                tx=tx,
            )
        assert result.chosen_index == 1
        # Only branch-50's fact should be in the substrate.
        committed = [
            d for d in s.escape.fact.store.all_datoms()
            if d.a == "fold/value"
        ]
        assert len(committed) == 1
        assert committed[0].e == "branch-50"
        assert committed[0].v == 50
        # Branches 10 and 30 should NOT exist anywhere in the log.
        all_eids = {d.e for d in s.escape.fact.store.all_datoms()}
        assert "branch-10" not in all_eids
        assert "branch-30" not in all_eids


def test_fold_into_rollback_preserves_pre_fork_substrate_state():
    """Facts written BEFORE the fork_into call survive — only the
    non-chosen branches' tentative facts are rolled back.
    """
    with Substrate.open("memory") as s:
        # Stage 1: commit a "control" fact outside any fork.
        s._db.transact(
            [{"e": "control", "a": "marker", "v": "stays"}],
        )

        # Stage 2: fold_into with rollback semantics.
        with s.txn.dosync() as tx:
            result = s.txn.fold_into(
                seed=0,
                items=[1, 5, 3],
                fn=_scoring_fn,
                choose=_argmax,
                tx=tx,
            )
        assert result.chosen_index == 1
        # Both the control fact AND the chosen branch's fact persist.
        eids = {d.e for d in s.escape.fact.store.all_datoms()}
        assert "control" in eids
        assert "branch-5" in eids
        # Non-chosen branches did NOT leak in.
        assert "branch-1" not in eids
        assert "branch-3" not in eids


def test_fold_into_returns_frozen_dataclass():
    """FoldIntoResult is frozen — caller cannot mutate after return."""
    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            result = s.txn.fold_into(
                seed=0,
                items=[1],
                fn=_scoring_fn,
                choose=lambda b: 0,
                tx=tx,
            )
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        result.chosen_index = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2-4. choose-callback error paths
# ---------------------------------------------------------------------------


def test_fold_into_choose_returns_out_of_range_raises():
    """choose returns len(branches) -> FoldIntoChooseError(ValueError)."""
    with Substrate.open("memory") as s:
        with pytest.raises(FoldIntoChooseError) as exc_info:
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=0,
                    items=[1, 2],
                    fn=_scoring_fn,
                    choose=lambda b: 5,  # out of range
                    tx=tx,
                )
        assert isinstance(exc_info.value.__cause__, ValueError)


def test_fold_into_choose_returns_negative_raises():
    """choose returns -1 -> FoldIntoChooseError(ValueError)."""
    with Substrate.open("memory") as s:
        with pytest.raises(FoldIntoChooseError) as exc_info:
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=0,
                    items=[1, 2],
                    fn=_scoring_fn,
                    choose=lambda b: -1,
                    tx=tx,
                )
        assert isinstance(exc_info.value.__cause__, ValueError)


def test_fold_into_choose_returns_non_int_raises():
    """choose returns 'first' -> FoldIntoChooseError(TypeError)."""
    with Substrate.open("memory") as s:
        with pytest.raises(FoldIntoChooseError) as exc_info:
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=0,
                    items=[1, 2],
                    fn=_scoring_fn,
                    choose=lambda b: "first",  # type: ignore[return-value,arg-type]
                    tx=tx,
                )
        assert isinstance(exc_info.value.__cause__, TypeError)


def test_fold_into_choose_returns_bool_raises():
    """choose returns True (a bool) -> FoldIntoChooseError(TypeError).

    bool is technically an int subclass but treating True as 1 silently
    is a footgun; the contract requires a clean int.
    """
    with Substrate.open("memory") as s:
        with pytest.raises(FoldIntoChooseError) as exc_info:
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=0,
                    items=[1, 2],
                    fn=_scoring_fn,
                    choose=lambda b: True,  # type: ignore[return-value]
                    tx=tx,
                )
        assert isinstance(exc_info.value.__cause__, TypeError)


def test_fold_into_choose_raises_arbitrary_exception_wrapped():
    """choose raises ZeroDivisionError -> FoldIntoChooseError with __cause__."""
    def bad_choose(branches):
        return 1 // 0  # raises

    with Substrate.open("memory") as s:
        with pytest.raises(FoldIntoChooseError) as exc_info:
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=0,
                    items=[1, 2],
                    fn=_scoring_fn,
                    choose=bad_choose,
                    tx=tx,
                )
        assert isinstance(exc_info.value.__cause__, ZeroDivisionError)


# ---------------------------------------------------------------------------
# 5. Empty items
# ---------------------------------------------------------------------------


def test_fold_into_empty_items_raises():
    """Empty items list -> ValueError before any fn / choose runs."""
    with Substrate.open("memory") as s:
        with pytest.raises(ValueError, match="non-empty"):
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=0,
                    items=[],
                    fn=_scoring_fn,
                    choose=_argmax,
                    tx=tx,
                )


# ---------------------------------------------------------------------------
# 6. Outside dosync gate
# ---------------------------------------------------------------------------


def test_fold_into_outside_dosync_raises():
    """Calling fold_into outside dosync -> FoldIntoOutsideDosync.

    Trips upfront before any branch is processed.
    """
    with Substrate.open("memory") as s:
        with pytest.raises(FoldIntoOutsideDosync, match="dosync"):
            s.txn.fold_into(
                seed=0,
                items=[1, 2],
                fn=_scoring_fn,
                choose=_argmax,
            )


def test_fold_into_dosync_without_tx_raises():
    """Inside dosync but tx=None -> FoldIntoOutsideDosync.

    The dosync ContextVar guard would say 'in dosync', but we still
    require the explicit `tx` so the audit datom never silently drops.
    """
    with Substrate.open("memory") as s:
        with pytest.raises(FoldIntoOutsideDosync, match="dosync"):
            with s.txn.dosync():
                s.txn.fold_into(
                    seed=0,
                    items=[1, 2],
                    fn=_scoring_fn,
                    choose=_argmax,
                    # tx omitted -> None default
                )


# ---------------------------------------------------------------------------
# 7-8. fn-contract error paths
# ---------------------------------------------------------------------------


def test_fold_into_fn_returns_2_tuple_raises():
    """fn forgets the score -> FoldIntoChooseError(TypeError)."""
    def bad_fn(acc, item, db):
        return acc + item, []  # 2-tuple instead of 3

    with Substrate.open("memory") as s:
        with pytest.raises(FoldIntoChooseError) as exc_info:
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=0,
                    items=[1],
                    fn=bad_fn,  # type: ignore[arg-type]
                    choose=_argmax,
                    tx=tx,
                )
        assert isinstance(exc_info.value.__cause__, TypeError)


def test_fold_into_fn_returns_4_tuple_raises():
    """fn returns 4-tuple -> FoldIntoChooseError(TypeError)."""
    def bad_fn(acc, item, db):
        return acc + item, [], 1.0, "extra"  # 4-tuple

    with Substrate.open("memory") as s:
        with pytest.raises(FoldIntoChooseError) as exc_info:
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=0,
                    items=[1],
                    fn=bad_fn,  # type: ignore[arg-type]
                    choose=_argmax,
                    tx=tx,
                )
        assert isinstance(exc_info.value.__cause__, TypeError)


def test_fold_into_score_non_finite_raises():
    """fn returns NaN score -> FoldIntoChooseError(ValueError)."""
    def nan_fn(acc, item, db):
        return acc + item, [], nan

    with Substrate.open("memory") as s:
        with pytest.raises(FoldIntoChooseError) as exc_info:
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=0,
                    items=[1],
                    fn=nan_fn,
                    choose=_argmax,
                    tx=tx,
                )
        assert isinstance(exc_info.value.__cause__, ValueError)


def test_fold_into_score_non_numeric_raises():
    """fn returns string score -> FoldIntoChooseError(TypeError)."""
    def str_fn(acc, item, db):
        return acc + item, [], "high"

    with Substrate.open("memory") as s:
        with pytest.raises(FoldIntoChooseError) as exc_info:
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=0,
                    items=[1],
                    fn=str_fn,  # type: ignore[arg-type]
                    choose=_argmax,
                    tx=tx,
                )
        assert isinstance(exc_info.value.__cause__, TypeError)


def test_fold_into_score_int_coerced_to_float():
    """fn returns int score -> coerced to float in result + audit datom."""
    def int_fn(acc, item, db):
        return acc + item, [], 42  # int score

    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            result = s.txn.fold_into(
                seed=0,
                items=[1],
                fn=int_fn,
                choose=lambda b: 0,
                tx=tx,
            )
    assert isinstance(result.chosen_score, float)
    assert result.chosen_score == 42.0
    # all_scores carries floats too
    assert all(isinstance(s, float) for s in result.all_scores)


# ---------------------------------------------------------------------------
# 9-10. on_error propagation
# ---------------------------------------------------------------------------


def test_fold_into_propagates_original_exception_under_abort():
    """fn raises on item 99 with on_error='abort' -> original exception
    propagates directly (NO FoldError wrapper under Phase 2.0c-extended);
    choose never called; no :fork/chosen datom emitted; no facts
    committed (rollback)."""
    choose_calls = []

    def raising_fn(acc, item, db):
        if item == 99:
            raise RuntimeError("boom")
        return acc + item, [], float(item)

    def tracking_choose(branches):
        choose_calls.append(len(branches))
        return 0

    with Substrate.open("memory") as s:
        # Phase 2.0c-extended: DB.fork re-raises the user's fn exception
        # directly under on_error='stop'/'abort'. No FoldError wrapper
        # (that was Path-A's DB.fold-routed shape, now superseded).
        with pytest.raises(RuntimeError, match="boom"):
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=0,
                    items=[1, 99, 3],
                    fn=raising_fn,
                    choose=tracking_choose,
                    tx=tx,
                    on_error="abort",
                )
    assert choose_calls == []  # choose never invoked


def test_fold_into_skip_passes_only_successful_branches_to_choose():
    """4 items, item 99 raises with on_error='skip'. choose sees the 3
    successful branches (indices 0, 2, 3 in items) renumbered to
    [0, 1, 2] in the score list. chosen_index is the score-list index.
    """
    def skipping_fn(acc, item, db):
        if item == 99:
            raise RuntimeError("transient")
        return acc + item, [], float(item)

    seen_branches: list[list[FoldBranchScore]] = []

    def picky_choose(branches):
        seen_branches.append(list(branches))
        # pick the one with highest score
        return max(range(len(branches)), key=lambda i: branches[i].score)

    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            result = s.txn.fold_into(
                seed=0,
                items=[1, 99, 5, 3],  # item index 1 (value 99) raises
                fn=skipping_fn,
                choose=picky_choose,
                tx=tx,
                on_error="skip",
            )
    # Only 3 branches reached choose (the successful ones).
    assert len(seen_branches) == 1
    assert len(seen_branches[0]) == 3
    # Their scores are 1.0, 5.0, 3.0 (item 99 dropped).
    assert [b.score for b in seen_branches[0]] == [1.0, 5.0, 3.0]
    # Argmax picks index 1 in the SUCCESSFUL list (score 5.0).
    assert result.chosen_index == 1
    assert result.chosen_score == 5.0
    # branch_count reflects successful-only.
    assert len(result.all_scores) == 3


def test_fold_into_skip_all_branches_raises():
    """Every branch raises under skip -> ValueError (no successful
    branch to choose between)."""
    def all_raise(acc, item, db):
        raise RuntimeError("always fails")

    with Substrate.open("memory") as s:
        with pytest.raises(ValueError, match="every branch was skipped"):
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=0,
                    items=[1, 2, 3],
                    fn=all_raise,
                    choose=_argmax,
                    tx=tx,
                    on_error="skip",
                )


# ---------------------------------------------------------------------------
# Backwards compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    """fold_into is a NEW method; existing s.txn.* surface unchanged."""

    def test_fold_still_callable_with_2_tuple_fn(self):
        """s.txn.fold (existing surface) still takes 2-tuple fn."""
        def fn_2tuple(acc, item, db):
            fact = {
                "e": f"i-{item}",
                "a": "fold/value",
                "v": item,
                "valid_from": _now(),
            }
            return acc + item, [fact]  # 2-tuple

        with Substrate.open("memory") as s:
            acc, n = s.txn.fold(seed=0, items=[1, 2, 3], fn=fn_2tuple)
            assert acc == 6
            assert n == 3

    def test_fold_into_is_marked_experimental(self):
        """fold_into carries @experimental metadata for the spec gen."""
        with Substrate.open("memory") as s:
            method = s.txn.fold_into
            underlying = getattr(method, "__func__", method)
            metadata = getattr(underlying, "__sdk_stability__", None)
            assert metadata is not None
            assert metadata.get("level") == "experimental"
            reason = metadata.get("reason") or ""
            assert "Phase 2.0c" in reason or "#145" in reason
