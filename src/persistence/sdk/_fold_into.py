"""Fold-into SDK surface — `s.txn.fold_into` convenience (#145, Phase 2.0c).

See ``docs/plans/2026-04-30-phase-2-persistence-coder-design.md`` § 4.3 +
ADR-7 for the design ground truth, and
``docs/plans/2026-05-01-phase-2.0c-fold-sdk-surface-impl.md`` for the
impl-level decisions.

## Public surface

- :class:`FoldBranchScore`        — per-branch (item, score, accumulator) tuple
- :class:`FoldIntoResult`         — `fold_into` return value (frozen dataclass)
- :func:`fold_into`               — `s.txn.fold_into` impl, callable from
                                    `_TxnNamespace.fold_into`
- :class:`FoldIntoOutsideDosync`  — raised when called outside `db.dosync`
- :class:`FoldIntoChooseError`    — raised when `choose` callback or `fn`
                                    contract is violated

## Architecture

`fold_into` is a **convenience method** that runs `DB.fold` (which already
commits facts as it iterates) under the agent-extended `(acc, item, db)
-> (new_acc, facts, score)` reducer signature, then calls a user-supplied
`choose` callback over the per-branch scores and emits a single
`:fold/chosen` audit datom marking the winning branch.

Per Decision 1 in the impl plan: `DB.fold` keeps its 2-tuple `fn` contract.
`fold_into` wraps the 3-tuple `fn` into a 2-tuple wrapper while collecting
`(item, score, new_acc)` triples for `choose`.

Per Decision 5 in the impl plan: `fold_into` REQUIRES the caller to be
inside a `db.dosync(...)` body — outside dosync, `tx.effect()` has no
transaction to attach the `:fold/chosen` audit datom to, and the
deterministic-replay invariant breaks. The dosync gate is enforced
upfront via :func:`persistence.txn.intents.is_in_dosync`.

## Audit invariant

Every successful ``fold_into`` call emits a ``:fold/chosen`` effect
intent via ``tx.effect()`` with kwargs::

    {
      "chosen_index": <0-based index in the score list passed to `choose`>,
      "chosen_score": <float; the winning branch's score>,
      "all_scores": <tuple[float, ...]; per-branch scores>,
      "branch_count": <int; len(score list passed to `choose`)>,
    }

The ``_txn_commit`` (commit_id) is supplied automatically by
``persistence.txn.transaction._replay_effect_intents`` at commit time;
the audit handler at ``effect/handlers/audit.py`` chains the
``:fold/chosen`` request datom into the same Merkle chain as
``:plan/edit`` / ``:code/exec`` / etc.

## on_error semantics (Decision 7)

- ``"abort"`` (default): if any branch's ``fn`` raises, ``DB.fold``
  raises ``FoldError``; ``choose`` is never called; no ``:fold/chosen``
  datom is emitted.
- ``"skip"``: skipped branches are dropped from the score list passed
  to ``choose``. The ``chosen_index`` recorded in the audit datom is
  the index into the **successful-branches list**, not the original
  ``items`` list. ``branch_count`` reflects the successful count.
  Note: branches that violate the ``fn`` contract (wrong return arity,
  non-numeric or non-finite score) are NEVER silently skipped — they
  raise :class:`FoldIntoChooseError` regardless of ``on_error``,
  because contract violations are programming bugs, not transient
  per-item failures.
- ``"checkpoint"``: same as ``"abort"`` from `fold_into`'s perspective
  (fold raises, choose not called).

## Score coercion (Decision 6)

Scores from ``fn`` are coerced to ``float`` for both the audit datom
and the ``FoldIntoResult.all_scores`` field, even when ``fn`` returned
``int`` or ``bool``. This keeps the audit-datom JSON canonicalization
stable across replays (``json.dumps(1)`` vs ``json.dumps(1.0)`` differ
at byte level — ``1`` vs ``1.0``).
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal, Optional

from persistence.fact import FoldError
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

    Mirrors :class:`persistence.plan._errors.PlanEditOutsideDosync`:
    `fold_into` emits a ``:fold/chosen`` audit datom that MUST ride the
    same Merkle chain as the rest of the trajectory, which requires an
    enclosing transaction.

    Outside dosync, the audit datom would be silent (no ``txn_commit``
    to chain into) — that violates the deterministic-replay invariant
    from § 3.7 of the Phase-2 design doc. The gate trips upfront before
    any branch is scored, so no work is wasted.
    """


class FoldIntoChooseError(RuntimeError):
    """Raised when the ``choose`` callback or the ``fn`` reducer
    violates its contract.

    Wraps the underlying ``TypeError`` / ``ValueError`` / arbitrary
    exception via ``__cause__`` so callers can ``except FoldIntoChooseError``
    once and inspect the cause for the specific violation:

    - ``TypeError``: ``choose`` returned a non-int; ``fn`` returned a
      tuple of the wrong arity or shape; ``fn`` returned a non-numeric
      score.
    - ``ValueError``: ``choose`` returned an int outside
      ``[0, branch_count)``; ``fn`` returned a non-finite score
      (NaN / +Inf / -Inf).
    - Anything else: ``choose`` itself raised arbitrarily; the original
      is the ``__cause__``.

    The constraint is single-classed (rather than two siblings for fn-
    error vs choose-error) because both manifest as "the agent's
    speculation contract is broken" — callers want one ``except`` block,
    not two.
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

    The ``score`` field is always ``float`` — see Decision 6 in the
    impl plan for the coercion rationale.
    """

    item: Any
    score: float
    accumulator_after: Any


@dataclass(frozen=True)
class FoldIntoResult:
    """Return value of :func:`fold_into`.

    Per Decision 4 in the impl plan, the result captures both the
    ``chosen_*`` fields (what ``choose`` picked) and the ``final_*``
    fields (what `DB.fold` returned at the end of the iteration). For
    an argmax-style ``choose`` over an arbitrary score sequence, the
    two will diverge whenever the chosen branch is not the last one.
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

    Per Decision 6: int / float / bool inputs are accepted; everything
    else (str, complex, None, dataclass, etc.) raises ``TypeError``.
    Non-finite floats (NaN / +Inf / -Inf) raise ``ValueError``.

    The caller wraps these in ``FoldIntoChooseError``.
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
    """`s.txn.fold_into` impl. See module docstring for the full contract.

    Routes through :meth:`persistence.fact.DB.fold` for the actual
    transactional accumulation; collects per-branch scores into a
    side-list; applies the ``choose`` callback; emits the
    ``:fold/chosen`` audit datom via the supplied ``Transaction``'s
    effect-intent log.

    Args:
        db: the substrate's underlying ``DB`` (from ``Substrate._db``).
        seed: initial accumulator passed to ``fn`` on iteration 0.
        items: branch candidates; materialised once at the top.
        fn: ``(acc, item, db) -> (new_acc, facts, score)`` reducer.
            ``facts`` is the same shape ``DB.fold`` accepts (a list
            of fact dicts); ``score`` is coerced to ``float``.
        choose: ``(branches: list[FoldBranchScore]) -> int`` policy
            that picks one branch's index. Pure / deterministic for
            byte-identity replay.
        tx: the active :class:`persistence.txn.Transaction` (passed in
            from the dosync body's ``with db.dosync() as tx:`` binding).
            Required keyword-only — used to queue the ``:fold/chosen``
            audit datom on the transaction's effect-intent log.
            Mirrors :func:`persistence.plan.edit_step`'s ``tx`` param.
        on_error: forwarded to ``DB.fold`` per Decision 7.
        checkpoint_every: forwarded to ``DB.fold``.
        provenance: forwarded to ``DB.fold``.

    Returns:
        :class:`FoldIntoResult` with the chosen branch's index +
        score + accumulator state, the full score tuple, and the
        total datom count from ``DB.fold``.

    Raises:
        FoldIntoOutsideDosync: not in an active dosync body, or ``tx``
            is None (the dosync gate trips up-front).
        ValueError: ``items`` is empty.
        FoldIntoChooseError: ``fn`` or ``choose`` violated its
            contract; original exception in ``__cause__``.
        FoldError: forwarded from ``DB.fold`` when a branch's ``fn``
            raises and ``on_error`` is ``"abort"`` /
            ``"checkpoint"``. ``choose`` was never called.
    """
    # Decision 5: dosync gate up-front. Both the ContextVar guard AND
    # the explicit `tx` argument must be present — the guard catches
    # call-site-outside-dosync; the `tx is None` check catches "user
    # forgot to thread tx through" so the audit datom never silently
    # drops.
    if not is_in_dosync() or tx is None:
        raise FoldIntoOutsideDosync(
            "s.txn.fold_into must run inside a db.dosync(...) body and "
            "be passed the active Transaction via the tx= keyword. "
            "Without an active txn the :fold/chosen audit datom would "
            "be silent and the deterministic-replay invariant from "
            "design § 3.7 would break."
        )

    # Decision 1 + Decision 7 prelim: items materialised once, validated
    # non-empty before any work is done.
    materialised_items = list(items)
    if not materialised_items:
        raise ValueError(
            "fold_into: items must be non-empty — there are no "
            "branches to choose between"
        )

    # Side-state captured by the wrapper closure. `branches` collects
    # successful (item, score, new_acc) triples; `wrapper_violation`
    # captures the first contract violation we saw (so we can re-raise
    # FoldIntoChooseError after fold completes — even under "skip" mode
    # where DB.fold would otherwise silently drop the failing item).
    branches: list[FoldBranchScore] = []
    wrapper_violation: list[BaseException] = []

    def wrapper_fn(
        acc: Any, item: Any, inner_db: "DB"
    ) -> tuple[Any, list[dict]]:
        """Adapter from 3-tuple `fn` to 2-tuple shape `DB.fold` expects.

        Validates the return shape; on contract violation, records the
        cause in ``wrapper_violation`` and re-raises so ``DB.fold``
        either propagates (under "abort") or drops (under "skip"). In
        both cases the post-fold check sees ``wrapper_violation`` and
        surfaces ``FoldIntoChooseError`` to the caller.
        """
        result = fn(acc, item, inner_db)
        # Shape check: must be a 3-tuple.
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
        # Score validation + coercion (Decision 6).
        try:
            score = _coerce_score(raw_score)
        except (TypeError, ValueError) as score_exc:
            wrapper_violation.append(score_exc)
            raise
        branches.append(
            FoldBranchScore(
                item=item,
                score=score,
                accumulator_after=new_acc,
            )
        )
        return new_acc, facts

    # Run the underlying fold. If a wrapper violation surfaces under
    # "abort", DB.fold wraps it in FoldError(__cause__=violation); we
    # unwrap below. Under "skip", DB.fold silently drops the branch but
    # we still see the violation in `wrapper_violation`.
    try:
        final_acc, total_datoms = db.fold(
            seed,
            materialised_items,
            wrapper_fn,
            on_error=on_error,
            checkpoint_every=checkpoint_every,
            provenance=provenance,
        )
    except FoldError as fe:
        # Contract violations bypass on_error: the user's contract is
        # broken regardless of failure-handling discipline.
        if wrapper_violation:
            raise FoldIntoChooseError(
                str(wrapper_violation[0])
            ) from wrapper_violation[0]
        # Genuine fold error from user fn — propagate as-is so the
        # caller can `except FoldError`.
        raise

    # Even when DB.fold completed (e.g. under on_error="skip"), a
    # contract violation in the wrapper means we should not proceed to
    # `choose` — the agent's score list would be silently incomplete.
    if wrapper_violation:
        raise FoldIntoChooseError(
            str(wrapper_violation[0])
        ) from wrapper_violation[0]

    # If on_error="skip" dropped every branch, there is nothing to
    # choose between. Treat as ValueError (mirrors the empty-items
    # gate), since the score list `choose` would see is empty.
    if not branches:
        raise ValueError(
            "fold_into: every branch was skipped under on_error='skip'; "
            "no successful branch to choose"
        )

    # Decision 2: invoke `choose` with the score list. Wrap any
    # exception from `choose` itself in FoldIntoChooseError.
    try:
        chosen_index = choose(branches)
    except BaseException as ce:
        raise FoldIntoChooseError(
            f"choose callback raised {type(ce).__name__}: {ce}"
        ) from ce

    # Validate `choose` return shape.
    if isinstance(chosen_index, bool) or not isinstance(chosen_index, int):
        # bool is an int subclass — exclude it explicitly so True/False
        # do not silently route to index 1/0.
        err: BaseException = TypeError(
            f"choose callback must return int; got "
            f"{type(chosen_index).__name__}"
        )
        raise FoldIntoChooseError(str(err)) from err
    if chosen_index < 0 or chosen_index >= len(branches):
        err = ValueError(
            f"choose callback returned index {chosen_index} which is "
            f"out of range for {len(branches)} branches "
            f"(valid: 0..{len(branches) - 1})"
        )
        raise FoldIntoChooseError(str(err)) from err

    chosen = branches[chosen_index]
    all_scores: tuple[float, ...] = tuple(b.score for b in branches)

    # Decision 3: emit the :fold/chosen audit datom via the active
    # transaction's effect-intent queue. The _txn_commit (commit_id)
    # is auto-injected at intent-replay time by
    # persistence.txn.transaction._replay_effect_intents — same path
    # as :plan/edit and :code/exec. No new chain code: :fold/chosen
    # rides the existing Merkle chain at effect/handlers/audit.py.
    _emit_chosen_datom(
        tx,
        chosen_index=chosen_index,
        chosen_score=chosen.score,
        all_scores=all_scores,
        branch_count=len(branches),
    )

    return FoldIntoResult(
        chosen_index=chosen_index,
        chosen_score=chosen.score,
        all_scores=all_scores,
        chosen_accumulator=chosen.accumulator_after,
        final_accumulator=final_acc,
        total_datoms_committed=total_datoms,
    )


def _emit_chosen_datom(
    tx: "Transaction",
    *,
    chosen_index: int,
    chosen_score: float,
    all_scores: tuple[float, ...],
    branch_count: int,
) -> None:
    """Queue the :fold/chosen audit datom on the Transaction's
    effect-intent log.

    The actual emission to the effect runtime (and Merkle-chain hook
    in ``effect/handlers/audit.py``) happens at commit time via
    ``persistence.txn.transaction._replay_effect_intents``, which
    injects the ``txn_commit`` (commit_id) alongside these kwargs.

    Mirrors :func:`persistence.plan._edit._emit_edit_datom`.
    """
    tx.effect(
        ":fold/chosen",
        chosen_index=chosen_index,
        chosen_score=chosen_score,
        all_scores=all_scores,
        branch_count=branch_count,
    )
