"""``DB.fork`` — speculate / score / pick / rollback substrate primitive.

Phase 2.0c-extended (#145ext, folds in carryover #201). See
``docs/plans/2026-05-01-phase-2.0c-ext-fork-primitive-impl.md`` for the
impl-level decisions and ``docs/plans/2026-04-30-phase-2-persistence-coder-design.md``
§ 3.7 + § 4.3 + ADR-7 for the design ground truth.

## What this is, vs ``DB.fold``

``DB.fold`` (the foldl/reduce primitive shipped at v0.7.0a1) commits
**every** item's facts as it iterates — it's a reduce that happens to
be transactional and bitemporal. ``DB.fold_into`` (the v0.8.0a1 SDK
convenience) layered a chosen-marker on top of that, but the facts of
non-chosen branches still persisted in the substrate.

``DB.fork`` is the missing primitive: it runs ``fn`` against N
**isolated** child branches (one per item), scores them via a
``choose`` callback, and **commits only the chosen branch's facts**
within the outer transaction. Non-chosen branches' tentative state is
discarded — they never reach ``db.history()`` post-commit. This is
the rewind/branch/replay shape the persistence-coder wedge needs.

Both primitives stay in the API. ``fold`` is for "reduce + audit the
chosen one"; ``fork`` is for "speculate, pick, rollback the rest".

## Audit shape — the canonical 4-datom emission

Per ``DB.fork`` call, in this order under the outer dosync (so all share
``txn_commit`` and a stable Merkle prev-hash chain of ``2 + 2*N``
entries):

1. ``:fork/probe``  — one datom with
   ``{seed_hash, items_hash, fn_hash, choose_hash, branch_count}``.
2. ``:fork/branch`` — one datom per branch with
   ``{branch_index, branch_id, item_hash, branch_state_hash}``.
3. ``:fork/score``  — one datom per branch with
   ``{branch_index, score_value, score_hash}``.
4. ``:fork/chosen`` — one datom with
   ``{chosen_index, chosen_branch_id, chosen_state_hash, txn_commit_uuid}``.

All hashes are sha256 over canonical-JSON, first 16 hex chars (matching
the existing ``_hash_fact`` shape in ``db.py``). Callable hashes are
over ``(qualname, module)`` tuples — best-effort, since Python
callables are not byte-hashable in general.

The 4 datoms ride the existing Merkle chain at
``persistence.effect.handlers.audit`` via ``tx.effect()`` queueing —
**no new chain code**. ``_txn_commit`` is auto-injected at commit time
by ``persistence.txn.transaction._replay_effect_intents``.

## Public surface (this module)

- :class:`ForkBranchResult`  — per-branch outcome (frozen)
- :class:`ForkResult`        — fork-call return (frozen)
- :class:`ForkOutsideDosync` — raised when called outside dosync
- :class:`ForkChooseError`   — raised when choose / fn contract violated
- :func:`fork_impl`          — implementation imported by ``DB.fork``

## Why ``branch_state`` is opaque

``fn(branch_state, item) -> branch_state`` operates on a Python value,
not on a DB. The substrate does not know or care what the branch
state means. This keeps rollback trivial: non-chosen branches' state
is just discarded Python objects, nothing was ever written.

The SDK's ``fold_into`` layer threads facts through via a closure —
it collects per-branch fact lists from a 3-tuple ``fn`` and applies
only the chosen branch's facts via ``tx.db.transact_batch`` at the
choose step. From ``DB.fork``'s point of view, that wrapper is just
another opaque ``fn``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Literal, Optional, Sequence

from persistence.txn.intents import is_in_dosync

if TYPE_CHECKING:
    from persistence.fact.db import DB
    from persistence.txn.transaction import Transaction


__all__ = [
    "ForkBranchResult",
    "ForkResult",
    "ForkOutsideDosync",
    "ForkChooseError",
    "fork_impl",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ForkOutsideDosync(RuntimeError):
    """``DB.fork`` was called outside an active ``db.dosync`` body.

    Mirrors :class:`persistence.plan._errors.PlanEditOutsideDosync` and
    :class:`persistence.sdk._fold_into.FoldIntoOutsideDosync`. The
    ``:fork/*`` audit datoms MUST ride the same Merkle chain as the
    rest of the trajectory, which requires an enclosing transaction.

    Outside dosync the audit datoms would be silent (no ``txn_commit``
    to chain into) — that violates the deterministic-replay invariant
    from § 3.7 of the Phase-2 design doc. The gate trips upfront before
    any branch is run, so no work is wasted.
    """


class ForkChooseError(RuntimeError):
    """Raised when the ``choose`` callback or the ``fn`` reducer
    violates its contract.

    Wraps the underlying ``TypeError`` / ``ValueError`` / arbitrary
    exception via ``__cause__`` so callers can ``except ForkChooseError``
    once and inspect the cause for the specific violation:

    - ``TypeError``: ``choose`` returned a non-int (or a bool).
    - ``ValueError``: ``choose`` returned an int outside
      ``[0, branch_count)``.
    - Anything else: ``choose`` itself raised arbitrarily; the
      original is the ``__cause__``.

    Single-classed (rather than two siblings) because both manifest as
    "the agent's speculation contract is broken" — callers want one
    ``except`` block. Same posture as ``FoldIntoChooseError``.
    """


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForkBranchResult:
    """One branch's outcome after ``fn(branch_state, item)`` ran.

    Frozen so the ``choose`` callback cannot mutate the result list
    and the audit datom can quote values back deterministically.

    Attributes:
        branch_index:  0-based position in the input ``items``.
        branch_id:     16-hex content-hash of ``(item, branch_state)``;
                       lets the ``:fork/chosen`` datom point at the
                       winner unambiguously across replays.
        item:          the input item this branch was forked from.
        branch_state:  ``fn``'s return value for this branch, OR the
                       seed if ``fn`` raised under
                       ``on_error="continue"``.
        score:         optional numeric score the adapter layer
                       populated (e.g. ``fold_into`` extracts a
                       numeric score from the 3-tuple ``fn`` shape);
                       ``None`` for the bare ``DB.fork`` API or for
                       branches that failed under ``"continue"``.
        error:         ``"<TypeName>: <message>"`` if this branch
                       raised under ``on_error="continue"``; ``None``
                       otherwise. Audit-replay reads this to skip
                       failed branches deterministically.
    """

    branch_index: int
    branch_id: str
    item: Any
    branch_state: Any
    score: Optional[float] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class ForkResult:
    """Return value of ``DB.fork``.

    Attributes:
        chosen_index:     index in ``all_branches`` that ``choose``
                          picked.
        chosen_state:     ``all_branches[chosen_index].branch_state``,
                          surfaced for the common case where callers
                          want the winner directly without indexing.
        all_branches:     immutable tuple of every branch's outcome
                          (including failures under ``"continue"``).
                          Length equals ``len(items)``.
        txn_commit_uuid:  the enclosing dosync's commit_id; populated
                          AFTER commit via the ``:fork/chosen`` audit
                          datom replay path. Available on the returned
                          :class:`ForkResult` only after the dosync
                          completes.
    """

    chosen_index: int
    chosen_state: Any
    all_branches: tuple[ForkBranchResult, ...]
    txn_commit_uuid: Optional[str] = None


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------


def _hash16(payload: Any) -> str:
    """16-hex-char sha256 over canonical-JSON of ``payload``.

    Matches the shape of :func:`persistence.fact.db._hash_fact` so the
    ``:fork/*`` audit datoms have the same hash family as
    ``Datom.provenance.prompt_hash``.

    ``default=str`` handles datetime / UUID; ``sort_keys=True``
    canonicalises dict ordering. Unhashable payloads (e.g. raw class
    instances without ``__str__``) fall back to ``repr``.
    """
    try:
        blob = json.dumps(payload, default=str, sort_keys=True).encode()
    except (TypeError, ValueError):
        blob = repr(payload).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def _hash_callable(fn: Callable) -> str:
    """Best-effort 16-hex hash of a callable's identity.

    Python callables are not byte-hashable in general (closures, C
    extensions, etc.). We hash ``(qualname, module)`` so the same
    function-by-identity hashes the same across replays of the same
    process; replays in a different process / different versioned
    module produce a different hash, which is the documented
    audit-replay caveat.
    """
    qualname = getattr(fn, "__qualname__", repr(fn))
    module = getattr(fn, "__module__", "")
    return _hash16({"qualname": qualname, "module": module})


# ---------------------------------------------------------------------------
# Audit-datom emission helpers
# ---------------------------------------------------------------------------


def _emit_probe_datom(
    tx: "Transaction",
    *,
    seed_hash: str,
    items_hash: str,
    fn_hash: str,
    choose_hash: str,
    branch_count: int,
) -> None:
    tx.effect(
        ":fork/probe",
        seed_hash=seed_hash,
        items_hash=items_hash,
        fn_hash=fn_hash,
        choose_hash=choose_hash,
        branch_count=branch_count,
    )


def _emit_branch_datom(
    tx: "Transaction",
    *,
    branch_index: int,
    branch_id: str,
    item_hash: str,
    branch_state_hash: str,
) -> None:
    tx.effect(
        ":fork/branch",
        branch_index=branch_index,
        branch_id=branch_id,
        item_hash=item_hash,
        branch_state_hash=branch_state_hash,
    )


def _canonicalize_score_value(raw: Any) -> Any:
    """Coerce a score value to an EDN-conformant payload for the audit datom.

    The intent-log spec at ``:persistence.txn/edn-value`` accepts scalars
    (int/float/str/bool/None), lists/tuples, and str-keyed dicts — but
    rejects ``datetime``, dataclasses, custom classes. Adapter callers
    routing a numeric score (``fold_into``) hit the scalar fast-path;
    bare ``DB.fork`` callers passing arbitrary ``branch_state`` need a
    canonicalisation layer so the audit datom is always emittable.

    Numeric scalars + None pass through. Everything else is canonical-
    JSON-stringified so the wire form is byte-stable (sort_keys=True,
    default=str). The hash side already uses the same canonical form
    via :func:`_hash16`, so ``score_value`` and ``score_hash`` agree
    on what byte-identity means.
    """
    if raw is None or isinstance(raw, (bool, int, float, str)):
        return raw
    try:
        return json.dumps(raw, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return repr(raw)


def _emit_score_datom(
    tx: "Transaction",
    *,
    branch_index: int,
    score_value: Any,
    score_hash: str,
) -> None:
    tx.effect(
        ":fork/score",
        branch_index=branch_index,
        score_value=_canonicalize_score_value(score_value),
        score_hash=score_hash,
    )


def _emit_chosen_datom(
    tx: "Transaction",
    *,
    chosen_index: int,
    chosen_branch_id: str,
    chosen_state_hash: str,
) -> None:
    """Queue the :fork/chosen audit datom on the Transaction's intent log.

    ``txn_commit_uuid`` is auto-injected by
    ``persistence.txn.transaction._replay_effect_intents`` at commit
    time as the ``_txn_commit`` kwarg, mirroring :plan/edit.
    """
    tx.effect(
        ":fork/chosen",
        chosen_index=chosen_index,
        chosen_branch_id=chosen_branch_id,
        chosen_state_hash=chosen_state_hash,
    )


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


def _validate_choose_index(chosen_index: Any, branch_count: int) -> int:
    """Validate ``choose`` callback's return; raise ForkChooseError.

    Mirrors the validation block in
    :func:`persistence.sdk._fold_into.fold_into`.
    """
    if isinstance(chosen_index, bool) or not isinstance(chosen_index, int):
        # bool is an int subclass — exclude it explicitly so True/False
        # do not silently route to index 1/0.
        err: BaseException = TypeError(
            f"choose callback must return int; got "
            f"{type(chosen_index).__name__}"
        )
        raise ForkChooseError(str(err)) from err
    if chosen_index < 0 or chosen_index >= branch_count:
        err = ValueError(
            f"choose callback returned index {chosen_index} which is "
            f"out of range for {branch_count} branches "
            f"(valid: 0..{branch_count - 1})"
        )
        raise ForkChooseError(str(err)) from err
    return chosen_index


def fork_impl(
    db: "DB",
    items: Sequence[Any],
    fn: Callable[[Any, Any], Any],
    choose: Callable[[list[ForkBranchResult]], int],
    *,
    seed: Any = None,
    tx: Optional["Transaction"] = None,
    on_error: Literal["stop", "continue"] = "stop",
    provenance: Optional[dict] = None,
) -> ForkResult:
    """``DB.fork`` implementation.

    See module docstring for the contract; ``DB.fork`` is a thin
    pass-through that supplies ``self`` as ``db``.

    Per-branch isolation is structural: ``fn(branch_state, item)``
    operates on opaque Python state, not on the substrate. Branches
    that diverge into different states cannot interfere with each
    other (no shared mutable state on the substrate side). Rollback is
    a no-op — non-chosen branches' state is just discarded.

    Adapters that want substrate-level facts emitted per branch
    (``fold_into`` is the canonical example) thread fact lists through
    the closure used to wrap ``fn``, then commit only the chosen
    branch's facts via ``tx.db.transact_batch`` inside the outer txn
    AFTER ``choose`` returns. ``DB.fork`` itself does not commit any
    facts — its sole substrate-side effect is the 4 audit datoms
    queued via ``tx.effect``.

    Args:
        db: substrate DB (the ``self`` of ``DB.fork`` when called as
            a method).
        items: branch candidates; one item produces one branch.
            Materialised to a list at the top of the call.
        fn: ``(branch_state, item) -> branch_state``. Called once
            per branch with ``seed`` as the initial state. The
            return is the branch's terminal state.
        choose: ``(branches) -> int`` policy that picks the winning
            index. Pure / deterministic for byte-identity replay.
            Sees a list of :class:`ForkBranchResult`; under
            ``"continue"`` failed branches still appear in the list
            (with ``error`` populated and ``branch_state == seed``)
            so ``choose`` decides whether to skip them.
        seed: initial branch state passed to every branch's ``fn``
            call. Defaults to ``None``.
        tx: the active :class:`Transaction` (passed in from the
            ``with db.dosync() as tx:`` binding). Required keyword-
            only — used to queue the 4 ``:fork/*`` audit datoms.
            Mirrors :func:`persistence.plan.edit_step`'s ``tx``
            param.
        on_error: ``"stop"`` (default) — first ``fn`` failure
            aborts the whole fork (re-raise immediately).
            ``"continue"`` — failed branch is recorded with
            ``score=None`` and ``error="<TypeName>: <msg>"``;
            ``branch_state`` is set to ``seed``; ``choose`` sees
            the failure entry and may skip it.
        provenance: not used by ``DB.fork`` itself (no facts are
            committed at this layer). Kept on the signature for
            adapter compatibility — ``fold_into`` forwards its
            ``provenance`` through this slot.

    Returns:
        :class:`ForkResult` with ``chosen_index``, ``chosen_state``,
        and the full ``all_branches`` tuple. ``txn_commit_uuid`` is
        ``None`` at the moment ``DB.fork`` returns — it gets
        populated implicitly via the ``:fork/chosen`` audit datom's
        ``_txn_commit`` field at dosync commit time, but the
        :class:`ForkResult` value itself is captured pre-commit.

    Raises:
        ForkOutsideDosync: not in an active dosync body, or
            ``tx`` is None (the dosync gate trips up-front).
        ValueError: ``items`` is empty.
        ForkChooseError: ``choose`` violated its contract.
        Exception (any): forwarded from ``fn`` when ``on_error="stop"``
            and a branch raises. ``choose`` was never called.
    """
    # Dosync gate up-front (mirrors fold_into).
    if not is_in_dosync() or tx is None:
        raise ForkOutsideDosync(
            "DB.fork must run inside a db.dosync(...) body and be "
            "passed the active Transaction via the tx= keyword. "
            "Without an active txn the :fork/* audit datoms would be "
            "silent and the deterministic-replay invariant from "
            "design § 3.7 would break."
        )

    if on_error not in ("stop", "continue"):
        raise ValueError(
            f"DB.fork: on_error must be 'stop' or 'continue', got "
            f"{on_error!r}"
        )

    materialised_items = list(items)
    if not materialised_items:
        raise ValueError(
            "DB.fork: items must be non-empty — there are no "
            "branches to choose between"
        )

    # ---- Run each branch in isolation. ------------------------------------
    # ``fn`` operates on opaque Python state. Per-branch isolation is
    # structural: each branch starts from ``seed`` and the substrate
    # is not touched. Adapters that want fact-side isolation thread
    # facts through the closure (see ``_fold_into.py``).
    branches: list[ForkBranchResult] = []
    for idx, item in enumerate(materialised_items):
        try:
            branch_state = fn(seed, item)
            error_str: Optional[str] = None
        except BaseException as exc:  # noqa: BLE001 — caller policy decides
            if on_error == "stop":
                # No partial work to clean up; nothing has been
                # committed (fork only emits audit datoms, and we
                # haven't queued any yet).
                raise
            # "continue": record the failure, keep going.
            branch_state = seed
            error_str = f"{type(exc).__name__}: {exc}"

        # branch_id is content-addressed over (item, branch_state).
        branch_id = _hash16({"item": item, "state": branch_state})
        branches.append(
            ForkBranchResult(
                branch_index=idx,
                branch_id=branch_id,
                item=item,
                branch_state=branch_state,
                score=None,  # bare DB.fork does not score; adapters do.
                error=error_str,
            )
        )

    # ---- Emit :fork/probe (one). -----------------------------------------
    seed_hash = _hash16(seed)
    items_hash = _hash16(materialised_items)
    fn_hash = _hash_callable(fn)
    choose_hash = _hash_callable(choose)
    _emit_probe_datom(
        tx,
        seed_hash=seed_hash,
        items_hash=items_hash,
        fn_hash=fn_hash,
        choose_hash=choose_hash,
        branch_count=len(branches),
    )

    # ---- Emit :fork/branch (one per branch). -----------------------------
    for b in branches:
        _emit_branch_datom(
            tx,
            branch_index=b.branch_index,
            branch_id=b.branch_id,
            item_hash=_hash16(b.item),
            branch_state_hash=_hash16(b.branch_state),
        )

    # ---- Emit :fork/score (one per branch). ------------------------------
    # ``score_value`` is whatever ``fn`` returned as the branch's
    # terminal state — for the bare ``DB.fork`` API there is no
    # numeric score, so the audit datom carries the structural state.
    # Adapters that want a numeric score (``fold_into``) populate
    # ``ForkBranchResult.score`` via a wrapping ``fn``.
    for b in branches:
        # Prefer numeric score if the adapter populated one; else fall
        # back to the structural state. Either way, hash it canonically
        # so byte-identity replay holds.
        score_payload = b.score if b.score is not None else b.branch_state
        _emit_score_datom(
            tx,
            branch_index=b.branch_index,
            score_value=score_payload,
            score_hash=_hash16(score_payload),
        )

    # ---- Run choose; validate. ------------------------------------------
    try:
        chosen_index = choose(branches)
    except ForkChooseError:
        # Already a clean error; let it propagate without wrapping.
        raise
    except BaseException as ce:
        raise ForkChooseError(
            f"choose callback raised {type(ce).__name__}: {ce}"
        ) from ce

    chosen_index = _validate_choose_index(chosen_index, len(branches))
    chosen = branches[chosen_index]

    # ---- Emit :fork/chosen (one). ---------------------------------------
    _emit_chosen_datom(
        tx,
        chosen_index=chosen_index,
        chosen_branch_id=chosen.branch_id,
        chosen_state_hash=_hash16(chosen.branch_state),
    )

    return ForkResult(
        chosen_index=chosen_index,
        chosen_state=chosen.branch_state,
        all_branches=tuple(branches),
        txn_commit_uuid=None,  # populated at dosync commit time via :fork/chosen
    )
