"""Fold-into SDK surface — `s.txn.fold_into` convenience (#145, Phase 2.0c).

Phase 2.0c-extended (#145ext, folds in carryover #201): rewired on top
of :meth:`persistence.fact.DB.fork` to deliver the substrate-true
speculate-rollback-pick semantics from design doc § 3.7 + § 4.3.

See ``docs/plans/2026-04-30-phase-2-persistence-coder-design.md`` § 4.3
+ ADR-7 for the design ground truth, and
``docs/plans/2026-05-01-phase-2.0c-ext-fork-primitive-impl.md`` for the
Phase 2.0c-extended impl-level decisions.

## Public surface

- :class:`FoldBranchScore`        — per-branch (item, score, accumulator) tuple
- :class:`FoldIntoResult`         — `fold_into` return value (frozen dataclass)
- :func:`fold_into`               — `s.txn.fold_into` impl, callable from
                                    `_TxnNamespace.fold_into`
- :class:`FoldIntoOutsideDosync`  — raised when called outside `db.dosync`
- :class:`FoldIntoChooseError`    — raised when `choose` callback or `fn`
                                    contract is violated

## Architecture (post-2.0c-extended)

`fold_into` is now a **thin adapter on top of** :meth:`DB.fork`:

1. Build a wrapper ``fn`` that calls the user's 3-tuple
   ``(acc, item, db) -> (new_acc, facts, score)`` reducer, captures
   ``facts`` and ``score`` in per-branch closure state, and returns
   ``new_acc`` as the branch's terminal state.
2. Build a wrapper ``choose`` that lifts each :class:`ForkBranchResult`
   into a :class:`FoldBranchScore` (with the captured per-branch
   score + accumulator), invokes the user's choose, and returns the
   index.
3. ``DB.fork`` queues the canonical 4-datom audit shape
   (``:fork/probe`` + ``:fork/branch`` × N + ``:fork/score`` × N +
   ``:fork/chosen``) under the enclosing dosync, with rollback
   semantics: non-chosen branches' state is just discarded Python
   objects. Their facts are NOT committed.
4. **Only the chosen branch's facts** are committed via
   :meth:`DB.transact_batch` AFTER ``DB.fork`` returns. This is the
   substrate-true rollback shape from § 3.7 — non-chosen branches
   never reach ``db.history()`` post-commit.

The Path-A foldl-with-`:fold/chosen`-marker impl shipped in v0.8.0a1
is **superseded within Phase 2.0c**. The legacy `:fold/chosen` audit
op is no longer emitted by `fold_into` — the `:fork/*` 4-datom shape
is the canonical contract. `DB.fold` (the foldl/reduce primitive)
keeps the 2-tuple `fn` shape and is unchanged.

## on_error semantics (Phase 2.0c-extended)

- ``"abort"`` (default): if any branch's ``fn`` raises, the
  underlying ``DB.fork`` re-raises immediately; ``choose`` is never
  called; no chosen branch's facts are committed; no audit datoms
  flushed.
- ``"skip"``: maps to ``DB.fork``'s ``on_error="continue"`` —
  failed branches are recorded with ``score=None``,
  ``branch_state=seed``. The wrapper drops them from the score list
  passed to the user's ``choose`` (so callers retain the v0.7 +
  v0.8.0a1 semantic where ``choose`` only sees successful branches).
- ``"checkpoint"``: same as ``"abort"`` from `fold_into`'s
  perspective.

Contract violations (wrong return arity, non-numeric or non-finite
score) raise :class:`FoldIntoChooseError` regardless of ``on_error``
— programming bugs are not transient per-item failures.

## Score coercion

Scores from ``fn`` are coerced to ``float`` for both the audit datom
and the ``FoldIntoResult.all_scores`` field, even when ``fn``
returned ``int`` or ``bool``. Keeps the audit-datom JSON
canonicalization stable across replays.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal, Optional

from persistence.fact._fork import (
    ForkBranchResult,
    ForkChooseError,
    ForkOutsideDosync,
)
from persistence.txn.intents import is_in_dosync

if TYPE_CHECKING:
    from persistence.fact import DB
    from persistence.txn.transaction import Transaction


__all__ = [
    "FoldBranchScore",
    "FoldIntoResult",
    "FoldIntoOutsideDosync",
    "FoldIntoChooseError",
    "fold_into",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class FoldIntoOutsideDosync(RuntimeError):
    """`fold_into` was called outside an active ``db.dosync`` body.

    Mirrors :class:`persistence.plan._errors.PlanEditOutsideDosync` and
    :class:`persistence.fact._fork.ForkOutsideDosync`. The ``:fork/*``
    audit datoms MUST ride the same Merkle chain as the rest of the
    trajectory, which requires an enclosing transaction.

    Outside dosync, the audit datoms would be silent (no
    ``txn_commit`` to chain into) — that violates the deterministic-
    replay invariant from § 3.7 of the Phase-2 design doc. The gate
    trips upfront before any branch is run, so no work is wasted.
    """


class FoldIntoChooseError(RuntimeError):
    """Raised when the ``choose`` callback or the ``fn`` reducer
    violates its contract.

    Wraps the underlying ``TypeError`` / ``ValueError`` / arbitrary
    exception via ``__cause__`` so callers can ``except FoldIntoChooseError``
    once and inspect the cause for the specific violation.

    Single-classed (rather than two siblings for fn-error vs
    choose-error) because both manifest as "the agent's speculation
    contract is broken" — callers want one ``except`` block, not two.
    """


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldBranchScore:
    """One branch's (item, score, accumulator-after) triple, passed to
    the ``choose`` callback as the i-th element of the score list.

    Frozen so the ``choose`` callback cannot mutate the score list and
    the audit datom can quote the score back deterministically.

    The ``score`` field is always ``float``.
    """

    item: Any
    score: float
    accumulator_after: Any


@dataclass(frozen=True)
class FoldIntoResult:
    """Return value of :func:`fold_into`.

    The result captures both the ``chosen_*`` fields (what ``choose``
    picked) and the ``final_*`` fields (what the last branch's
    accumulator was). For an argmax-style ``choose`` over an arbitrary
    score sequence, the two will diverge whenever the chosen branch is
    not the last one.

    Phase 2.0c-extended: ``total_datoms_committed`` now reflects ONLY
    the chosen branch's facts (rollback semantics — non-chosen
    branches' facts never reach the substrate). Path-A's all-branches-
    committed semantic is superseded.
    """

    chosen_index: int
    chosen_score: float
    all_scores: tuple[float, ...]
    chosen_accumulator: Any
    final_accumulator: Any
    total_datoms_committed: int


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


def _coerce_score(raw: Any) -> float:
    """Coerce a raw ``fn``-returned score to ``float`` with shape checks.

    int / float / bool inputs are accepted; everything else (str,
    complex, None, dataclass, etc.) raises ``TypeError``. Non-finite
    floats (NaN / +Inf / -Inf) raise ``ValueError``. The caller wraps
    these in ``FoldIntoChooseError``.
    """
    if isinstance(raw, bool):
        # bool is a subclass of int, so this guard runs first.
        return float(raw)
    if isinstance(raw, (int, float)):
        coerced = float(raw)
        if not isfinite(coerced):
            raise ValueError(
                f"fold_into: fn returned non-finite score {raw!r}; "
                f"NaN / +Inf / -Inf are forbidden because they break "
                f"audit-datom byte-identity across replays"
            )
        return coerced
    raise TypeError(
        f"fold_into: fn must return a numeric score (int / float / bool); "
        f"got {type(raw).__name__}"
    )


def fold_into(
    db: "DB",
    seed: Any,
    items: Iterable[Any],
    fn: Callable[[Any, Any, "DB"], tuple[Any, list[dict], Any]],
    choose: Callable[[list[FoldBranchScore]], int],
    *,
    tx: Optional["Transaction"] = None,
    on_error: Literal["abort", "skip", "checkpoint"] = "abort",
    checkpoint_every: int = 0,
    provenance: Optional[dict] = None,
) -> FoldIntoResult:
    """`s.txn.fold_into` impl — Phase 2.0c-extended.

    Routes through :meth:`persistence.fact.DB.fork` for the substrate-
    true speculate-rollback-pick primitive, then commits ONLY the
    chosen branch's facts via :meth:`DB.transact_batch`. Audit shape
    is the canonical 4-datom emission (``:fork/probe`` +
    ``:fork/branch`` × N + ``:fork/score`` × N + ``:fork/chosen``) —
    no separate ``:fold/chosen`` datom (that op is reserved for
    ``DB.fold`` users who want the foldl-with-marker pattern).

    Args:
        db: the substrate's underlying ``DB`` (from ``Substrate._db``).
        seed: initial accumulator passed to ``fn`` on every branch.
        items: branch candidates; materialised once at the top.
        fn: ``(acc, item, db) -> (new_acc, facts, score)`` reducer.
            ``facts`` is the same shape ``DB.transact_batch`` accepts
            (a list of fact dicts); ``score`` is coerced to ``float``.
        choose: ``(branches: list[FoldBranchScore]) -> int`` policy
            that picks one branch's index. Pure / deterministic for
            byte-identity replay.
        tx: the active :class:`persistence.txn.Transaction` (passed in
            from the dosync body's ``with db.dosync() as tx:``
            binding). Required keyword-only — used by ``DB.fork`` to
            queue the 4 audit datoms on the transaction's effect-
            intent log.
        on_error: ``"abort"`` (default; any branch failure aborts
            the fork), ``"skip"`` (failed branches dropped from the
            score list shown to ``choose``), or ``"checkpoint"``
            (alias for ``"abort"`` from this surface's perspective).
        checkpoint_every: kept for v0.8.0a1 API compatibility; no
            longer functional under the rewire (DB.fork does not
            checkpoint — it speculates over an isolated branch per
            item). Documented as deprecated; raises ``ValueError``
            if non-zero so callers don't get silent semantic drift.
        provenance: forwarded to ``transact_batch`` when the chosen
            branch's facts are committed. Defaults to a
            ``{"source": "fold_into"}`` tag on the chosen branch's
            commit.

    Returns:
        :class:`FoldIntoResult` with the chosen branch's index +
        score + accumulator state, the full score tuple, and the
        chosen branch's datom commit count.

    Raises:
        FoldIntoOutsideDosync: not in an active dosync body, or
            ``tx`` is None (the dosync gate trips up-front).
        ValueError: ``items`` is empty; ``checkpoint_every`` non-zero
            (no longer supported); ``on_error`` invalid.
        FoldIntoChooseError: ``fn`` or ``choose`` violated its
            contract; original exception in ``__cause__``.
        Exception (any): forwarded from ``fn`` when ``on_error="abort"``
            and a branch raises. ``choose`` was never called.
    """
    # Dosync gate up-front. Both the ContextVar guard AND the explicit
    # `tx` argument must be present.
    if not is_in_dosync() or tx is None:
        raise FoldIntoOutsideDosync(
            "s.txn.fold_into must run inside a db.dosync(...) body and "
            "be passed the active Transaction via the tx= keyword. "
            "Without an active txn the :fork/* audit datoms would be "
            "silent and the deterministic-replay invariant from "
            "design § 3.7 would break."
        )

    if on_error not in ("abort", "skip", "checkpoint"):
        raise ValueError(
            f"fold_into: on_error must be one of 'abort'/'skip'/"
            f"'checkpoint', got {on_error!r}"
        )

    if checkpoint_every:
        raise ValueError(
            f"fold_into: checkpoint_every is no longer supported under "
            f"the Phase 2.0c-extended rewire — DB.fork speculates over "
            f"an isolated branch per item rather than buffering "
            f"checkpoints. Pass checkpoint_every=0 (default). "
            f"Got {checkpoint_every!r}."
        )

    materialised_items = list(items)
    if not materialised_items:
        raise ValueError(
            "fold_into: items must be non-empty — there are no "
            "branches to choose between"
        )

    # Side-state captured by the wrappers. Indexed by branch_index so
    # the reorder-from-fork-back-to-fold-shape is unambiguous.
    per_branch_facts: dict[int, list[dict]] = {}
    per_branch_score: dict[int, float] = {}
    per_branch_acc: dict[int, Any] = {}
    wrapper_violation: list[BaseException] = []

    # Pre-compute the branch indices so the wrapper_fn knows which idx
    # it's currently producing (DB.fork iterates `items` in order, but
    # doesn't surface idx into fn). We track via a counter on the
    # closure side.
    next_idx_holder = [0]

    def wrapper_fn(_branch_state: Any, item: Any) -> Any:
        """Adapter from fold_into's 3-tuple `fn` to DB.fork's 2-arg shape.

        ``_branch_state`` is always the seed — DB.fork passes seed as
        the initial state per branch. We re-invoke the user's
        3-tuple fn with the seed-as-accumulator and capture facts +
        score + new_acc in per-branch dicts keyed by branch index.

        Returns the user's ``new_acc`` as the branch's terminal state
        (which becomes ``ForkBranchResult.branch_state``).
        """
        idx = next_idx_holder[0]
        next_idx_holder[0] += 1

        result = fn(seed, item, db)
        # Shape check.
        if not isinstance(result, tuple) or len(result) != 3:
            err = TypeError(
                f"fold_into: fn must return a 3-tuple "
                f"(new_acc, facts, score); got "
                f"{type(result).__name__}"
                + (
                    f" of length {len(result)}"
                    if isinstance(result, tuple)
                    else ""
                )
            )
            wrapper_violation.append(err)
            raise err
        new_acc, facts, raw_score = result
        if not isinstance(facts, list):
            err = TypeError(
                f"fold_into: fn must return (acc, list[dict], score); "
                f"facts element is {type(facts).__name__}"
            )
            wrapper_violation.append(err)
            raise err
        try:
            score = _coerce_score(raw_score)
        except (TypeError, ValueError) as score_exc:
            wrapper_violation.append(score_exc)
            raise

        per_branch_facts[idx] = facts
        per_branch_score[idx] = score
        per_branch_acc[idx] = new_acc
        return new_acc

    # Map fold_into's on_error onto DB.fork's on_error.
    fork_on_error: Literal["stop", "continue"] = (
        "continue" if on_error == "skip" else "stop"
    )

    # Capture state for `choose` translation. Under "skip", the user's
    # choose only sees successful branches (their indices in the
    # successful list, NOT in the original items list). The chosen_index
    # FoldIntoResult.chosen_index reflects that successful-list index.
    # We track which fork-branch-indices were successful so we can map
    # the user's chosen idx -> fork's branch-index when needed.
    successful_fork_indices: list[int] = []

    # Sentinel class for the "all branches failed under skip" case so
    # fold_into can re-raise as ValueError (matching v0.8.0a1 contract)
    # rather than as FoldIntoChooseError.
    class _AllSkippedSentinel(Exception):
        pass

    # Sentinel for transporting the user's original choose-callback
    # exception (e.g. ZeroDivisionError) cleanly through fork_impl's
    # ForkChooseError wrapper.
    class _UserChooseRaised(Exception):
        def __init__(self, original: BaseException) -> None:
            super().__init__(str(original))
            self.original = original

    def choose_wrapper(fork_branches: list[ForkBranchResult]) -> int:
        """Adapter from DB.fork's branches list to user's choose.

        Builds a list of FoldBranchScore from the captured per-branch
        state (only successful branches under "skip"); calls user's
        choose; returns the WINNING fork branch_index (DB.fork wants
        an index into its own all_branches list, not the successful
        list).

        Raises vanilla TypeError / ValueError so ``fork_impl`` wraps
        them into ``ForkChooseError`` consistently; ``fold_into``
        catches ``ForkChooseError`` and unwraps the cause back into
        ``FoldIntoChooseError``. User-callback exceptions are
        transported via ``_UserChooseRaised`` so the original is
        preserved as ``__cause__``.
        """
        successful_fork_indices.clear()
        score_list: list[FoldBranchScore] = []
        for fb in fork_branches:
            # Failed branches (under "continue") have error populated.
            if fb.error is not None:
                continue
            successful_fork_indices.append(fb.branch_index)
            score_list.append(
                FoldBranchScore(
                    item=fb.item,
                    score=per_branch_score[fb.branch_index],
                    accumulator_after=per_branch_acc[fb.branch_index],
                )
            )

        if not score_list:
            raise _AllSkippedSentinel(
                "fold_into: every branch was skipped under "
                "on_error='skip'; no successful branch to choose"
            )

        try:
            user_chosen = choose(score_list)
        except BaseException as ce:
            # Transport original; fold_into unwraps via .original.
            raise _UserChooseRaised(ce) from ce

        # Validate user's choose return — raise vanilla TypeError /
        # ValueError. fork_impl will wrap into ForkChooseError; the
        # caller (fold_into) inspects the wrapped __cause__.
        if isinstance(user_chosen, bool) or not isinstance(user_chosen, int):
            raise TypeError(
                f"choose callback must return int; got "
                f"{type(user_chosen).__name__}"
            )
        if user_chosen < 0 or user_chosen >= len(score_list):
            raise ValueError(
                f"choose callback returned index {user_chosen} which "
                f"is out of range for {len(score_list)} branches "
                f"(valid: 0..{len(score_list) - 1})"
            )

        # Map user_chosen (index into successful list) -> DB.fork's
        # all_branches index.
        return successful_fork_indices[user_chosen]

    # Run DB.fork. fold_into-wrapper-violation cases bubble up as the
    # underlying TypeError/ValueError; we rewrap as
    # FoldIntoChooseError. fork-internal failures (under "abort") raise
    # the user's fn exception directly.
    try:
        fork_result = db.fork(
            items=materialised_items,
            fn=wrapper_fn,
            choose=choose_wrapper,
            seed=seed,
            tx=tx,
            on_error=fork_on_error,
            provenance=provenance,
        )
    except ForkOutsideDosync:
        # Re-classify under fold_into's exception type for backward-
        # compat — callers expect FoldIntoOutsideDosync.
        raise FoldIntoOutsideDosync(
            "s.txn.fold_into must run inside a db.dosync(...) body and "
            "be passed the active Transaction via the tx= keyword."
        )
    except ForkChooseError as fce:
        # ``fork_impl`` wraps the exception raised inside ``choose_wrapper``
        # as ``ForkChooseError(... from cause)``. Unwrap and re-classify:
        # - _AllSkippedSentinel -> ValueError (matches v0.8.0a1 contract
        #   for "every branch was skipped").
        # - _UserChooseRaised -> FoldIntoChooseError with the original
        #   user exception as __cause__.
        # - vanilla TypeError / ValueError from choose-validation ->
        #   FoldIntoChooseError with the cause preserved.
        # - wrapper-fn contract violation (recorded in
        #   wrapper_violation) -> FoldIntoChooseError.
        cause = fce.__cause__
        if isinstance(cause, _AllSkippedSentinel):
            raise ValueError(str(cause)) from None
        if isinstance(cause, _UserChooseRaised):
            raise FoldIntoChooseError(
                f"choose callback raised "
                f"{type(cause.original).__name__}: {cause.original}"
            ) from cause.original
        if wrapper_violation:
            raise FoldIntoChooseError(
                str(wrapper_violation[0])
            ) from wrapper_violation[0]
        if isinstance(cause, (TypeError, ValueError)):
            raise FoldIntoChooseError(str(cause)) from cause
        # Fallback — preserve the cause we have.
        raise FoldIntoChooseError(str(fce)) from cause
    except BaseException:
        # Wrapper-fn contract violation (wrong arity / non-numeric
        # score) -> wrap in FoldIntoChooseError.
        if wrapper_violation:
            raise FoldIntoChooseError(
                str(wrapper_violation[0])
            ) from wrapper_violation[0]
        # User fn legitimately raised under "abort" -> propagate as-is.
        raise

    # ---- Stage chosen branch's facts onto the outer txn. ---------------
    # Phase 2.0d W1 (M3): pre-W1 we called ``db.transact_batch`` here,
    # which committed immediately mid-dosync — that broke atomicity:
    # if the outer body raised after fold_into returned, the chosen-
    # branch facts were already committed and survived the rollback.
    # Now we queue them onto ``tx.staged_facts`` so they ride the
    # outer dosync's single atomic transact_batch (alongside the
    # write_set + commute reapply + commit datom). An outer raise
    # discards the whole staged log along with the rest of the txn.
    # The :fork/* audit datoms already ride the outer commit via
    # tx.effect (Phase 2.0a precedent).
    #
    # Phase 2.0d W2 (m6): the pre-W2 implementation constructed a
    # ``prov_for_batch`` dict (``{"source": "fold_into", ...}``) and
    # bound it to ``_`` — the value was constructed and then dropped.
    # That was dead code. ``transact_batch`` accepts ONE provenance
    # dict per call, and the outer dosync's commit path already
    # supplies ``_build_commit_provenance(tx, commit_id)`` for the
    # whole batch (write_set + commute reapply + staged_facts +
    # commit datom). Per-staged-fact provenance cannot be expressed
    # at the ``transact_batch`` layer without substrate-level work
    # (out of scope for the fix-pass).
    #
    # The ``provenance`` argument is still accepted on the public
    # ``fold_into`` signature for API forward-compat, and is forwarded
    # to ``db.fork`` (line 473) — though ``DB.fork`` itself does not
    # currently consume it (see ``_fork.py:436`` "not used by DB.fork
    # itself"). Replay-debug consumers identify fold_into-origin
    # chosen-branch facts via the ``:fork/chosen`` audit datom's
    # ``txn_commit`` field, which rides the existing Merkle chain at
    # ``effect/handlers/audit.py``; the chosen ``staged_facts`` share
    # that commit_id by construction (single ``transact_batch`` call).
    chosen_fork_idx = fork_result.chosen_index
    chosen_facts = per_branch_facts.get(chosen_fork_idx, [])

    if chosen_facts:
        tx.add_facts(chosen_facts)
    committed_count = len(chosen_facts)

    # Assemble FoldIntoResult.
    successful_indices = sorted(per_branch_score.keys())
    all_scores: tuple[float, ...] = tuple(
        per_branch_score[i] for i in successful_indices
    )
    # The successful-list index that the user's choose returned. We
    # need to map fork_result.chosen_index back to its position in the
    # successful_indices list.
    user_chosen_idx = successful_indices.index(chosen_fork_idx)
    chosen_score = per_branch_score[chosen_fork_idx]
    chosen_accumulator = per_branch_acc[chosen_fork_idx]
    # final_accumulator = the last *successful* branch's accumulator
    # (matches v0.8.0a1 semantics where DB.fold returned its last-
    # iterated acc; under skip, that's the last successful one).
    final_accumulator = per_branch_acc[successful_indices[-1]]

    return FoldIntoResult(
        chosen_index=user_chosen_idx,
        chosen_score=chosen_score,
        all_scores=all_scores,
        chosen_accumulator=chosen_accumulator,
        final_accumulator=final_accumulator,
        total_datoms_committed=committed_count,
    )
