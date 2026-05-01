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
      tuple of the wrong arity; ``fn`` returned a non-numeric score.
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
# Implementation (filled in C2)
# ---------------------------------------------------------------------------


def fold_into(
    db: "DB",
    seed: Any,
    items: Iterable[Any],
    fn: Callable[[Any, Any, "DB"], tuple[Any, list[dict], Any]],
    choose: Callable[[list[FoldBranchScore]], int],
    *,
    on_error: Literal["abort", "skip", "checkpoint"] = "abort",
    checkpoint_every: int = 0,
    provenance: Optional[dict] = None,
) -> FoldIntoResult:
    """`s.txn.fold_into` impl. See module docstring for the full contract.

    Filled in commit C2; raises ``NotImplementedError`` until then.
    """
    raise NotImplementedError(
        "fold_into impl lands in commit C2 — see "
        "docs/plans/2026-05-01-phase-2.0c-fold-sdk-surface-impl.md"
    )
