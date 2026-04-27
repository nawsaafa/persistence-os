"""Transaction — the object passed into a dosync body.

Captures read_set, write_set, effect_intent_log, and metadata
(t_start snapshot, attempt count, commit_id allocated at success).

The retry loop and commit gate live alongside this class and are wired
in via ``Transaction._run`` (added in Task B7).
"""
from __future__ import annotations

import time
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from persistence.spec import conform as _spec_conform
from persistence.spec import registered_keys as _registered_keys
from persistence.txn.conflict import any_datoms_since
from persistence.txn.errors import (
    NestedDosyncNotSupported,
    RefValueNotImmutable,
    TxnDeadlineExceeded,
    TxnRetryExhausted,
)
from persistence.txn.intents import (
    EffectIntent,
    clear_dosync_guard,
    is_in_dosync,
    set_dosync_guard,
)
from persistence.txn.ref import Ref, is_immutable_value


@dataclass
class Transaction:
    """Mutable transaction state passed into a dosync body.

    Lives for one body invocation. On retry, a fresh Transaction is
    constructed; the previous one's read_set / write_set / intent_log
    are discarded.

    Fields are mutable on purpose — the body progressively builds the
    sets via ``deref``/``assoc``/``alter``/``effect``. The Transaction
    itself does not commit; ``_run`` (Task B7) drives commit and retry.
    """

    db: Any
    t_start: datetime
    attempt: int
    read_set: set[Ref] = field(default_factory=set)
    write_set: dict[Ref, Any] = field(default_factory=dict)
    effect_intent_log: list[EffectIntent] = field(default_factory=list)
    commit_id: str | None = None

    def now(self) -> datetime:
        """Frozen t_start. Use this instead of datetime.now() inside
        a dosync body so the value is deterministic across retries.
        """
        return self.t_start

    def assoc(self, ref: Ref, value: Any) -> None:
        """Queue a write of ``value`` to ``ref`` at commit time.

        Raises ``RefBranchMismatch`` if ``ref`` was constructed against
        a different DB. Raises ``RefValueNotImmutable`` if ``value`` is
        mutable.
        """
        from persistence.txn.errors import RefBranchMismatch
        from persistence.txn._db_extension import _get_db_id

        if ref.db_id != _get_db_id(self.db):
            raise RefBranchMismatch(
                f"ref {ref!r} belongs to a different DB than this dosync"
            )
        if not is_immutable_value(value):
            raise RefValueNotImmutable(
                f"tx.assoc value must be immutable; got {type(value).__name__!r}. "
                f"Use persistence.txn.freeze(...) or wrap in pyrsistent."
            )
        self.write_set[ref] = value

    def effect(self, op: str, **kwargs: Any) -> None:
        """Queue an effect intent to be replayed atomically at commit.

        Validation note: the kwargs are NOT validated at this call site.
        Validation against ``:persistence.txn/intent-log`` happens at commit
        time when the intent is serialised onto the commit datom's
        provenance. A ``tx.effect()`` call that succeeds may still cause
        the enclosing dosync to raise ``SpecError`` at commit if the kwargs
        aren't EDN-conformant (e.g., raw ``datetime`` objects, custom
        classes). Callers expecting eager validation must conform their
        kwargs themselves before calling ``tx.effect()``.

        See ``persistence.txn._specs._EdnValueSpec`` for the per-value rule
        (scalars + lists/tuples + str-keyed dicts; rejects datetime).
        """
        self.effect_intent_log.append(EffectIntent(op=op, kwargs=dict(kwargs)))

    def deref(self, ref: Ref) -> Any:
        """Snapshot read of ``ref`` at ``t_start``.

        Branch-checked: ``ref`` must belong to this transaction's DB.
        Adds ``ref`` to the read_set. If the body has already written
        to this ref via ``assoc`` or ``alter``, returns the pending
        write (read-your-own-writes); otherwise returns the value of
        the underlying entity at ``t_start``, or ``None`` if no
        ``:value`` attribute has been asserted yet.
        """
        from persistence.txn.errors import RefBranchMismatch
        from persistence.txn._db_extension import _get_db_id

        if ref.db_id != _get_db_id(self.db):
            raise RefBranchMismatch(
                f"ref {ref!r} belongs to a different DB than this dosync"
            )
        self.read_set.add(ref)
        if ref in self.write_set:
            return self.write_set[ref]
        # Snapshot read at t_start. The ref's value lives under the
        # ``ref.spec_attr`` attribute name (default ``"value"`` preserves
        # v0.5.0a1 behavior bit-for-bit). Two refs sharing an eid but
        # carrying different ``spec_attr`` resolve to different attribute
        # values on the same entity — this is the per-ref-attribute
        # contract introduced in v0.5.1 (rev O / N3).
        view = self.db.as_of(self.t_start)
        entity_attrs = view.entity(ref.eid)
        return entity_attrs.get(ref.spec_attr) if entity_attrs else None

    def alter(self, ref: Ref, fn: Callable, *args: Any) -> Any:
        """Read ``ref`` at snapshot, apply ``fn(snapshot_value, *args)``,
        queue the result as a write, return the new value.

        Adds ``ref`` to the read_set (so concurrent writes to ``ref``
        cause this transaction to retry). The new value must be
        immutable; ``RefValueNotImmutable`` is raised otherwise.
        """
        current = self.deref(ref)
        new_value = fn(current, *args)
        self.assoc(ref, new_value)
        return new_value



DEFAULT_MAX_RETRIES = 256
WRITE_ATTR = "value"  # default ``Ref.spec_attr`` (Datom strips leading ":" from `a`)


def _spec_validate_writes(write_set: dict) -> None:
    """Run spec.conform over each write before tx-id allocation.

    v0.5.1 (rev O / N3): each ref carries its own ``spec_attr`` (default
    ``"value"``); we look up that key in the registry and conform the
    write against it. Refs with no spec registered for their attr pass
    through untouched. This generalises the v0.5.0a1 single-global-spec
    behavior — the default ``spec_attr="value"`` makes unannotated
    callers identical to before.

    Raises ``persistence.spec.SpecError`` on failure (caller propagates;
    no tx-id is burned).
    """
    keys = set(_registered_keys())
    for ref, value in write_set.items():
        spec_key = ref.spec_attr
        if spec_key not in keys:
            continue
        result = _spec_conform(spec_key, value)
        if not result.is_ok:
            # ``SpecError`` lives in ``_registry`` and is not re-exported
            # through ``persistence.spec.__all__`` (see N1 commentary in
            # ``_build_commit_provenance``). Use the submodule path.
            from persistence.spec._registry import SpecError
            raise SpecError(result)  # type: ignore[arg-type]


def _build_write_facts(tx: "Transaction") -> list[dict]:
    """Build the list-of-fact-dicts payload for db.transact().

    v0.5.1 (rev O / N3): each fact's ``a`` is ``ref.spec_attr`` (default
    ``"value"``), so per-ref attribute names land on the Datom store
    directly. Datom strips a leading ``":"`` if present.
    """
    now = tx.db._clock()
    facts: list[dict] = []
    for ref, value in tx.write_set.items():
        facts.append({
            "e": ref.eid,
            "a": ref.spec_attr,
            "v": value,
            "valid_from": now,
        })
    return facts


def _build_commit_fact(tx: "Transaction", commit_id: str) -> dict:
    """Build the per-commit datom (one extra fact per dosync)."""
    now = tx.db._clock()
    return {
        "e": commit_id,
        "a": ":persistence.txn/commit-id",
        "v": commit_id,
        "valid_from": now,
    }


def _build_commit_provenance(tx: "Transaction", commit_id: str) -> dict:
    """Build the dict passed as ``transact_batch(provenance=...)``.

    Reads the optional private ``tx._deadline_set`` flag set by ``_run``
    once before the retry loop (defaults to ``False`` — the CM path
    never sets it).

    v0.5.1 (rev O / N1) emits two additional keys:

    - ``:persistence.txn/read-set`` — sorted list of eids the body
      read via ``tx.deref`` (or implicitly via ``tx.alter``). Empty
      list when the body wrote without reading.
    - ``:persistence.txn/intent-log`` — list of
      ``{":op": str, ":kwargs": dict}`` items in queue order, one per
      ``tx.effect()`` call. Each item is conformed against the
      registered ``:persistence.txn/intent-log`` element spec; on
      failure ``SpecError`` is raised here, before the commit datom
      is written. This is option (3) from the design doc — strict
      validation at commit time, not at ``tx.effect()`` call site.
      The lock-held ``transact_batch`` call below never runs when
      this raises, so no datoms or commit_id are burned and the
      enclosing ``with db.store._lock`` block unwinds cleanly.
    """
    intent_log_wire: list[dict] = [
        {":op": intent.op, ":kwargs": dict(intent.kwargs)}
        for intent in tx.effect_intent_log
    ]
    # Conform the whole list once: ``seq_of`` propagates per-index
    # paths in ``ConformError.sub_errors``, so a non-EDN kwarg at the
    # third intent reports as ``[2][":kwargs"][...]`` rather than
    # collapsing to index 0 (which would happen if we conformed each
    # item against the seq_of-wrapping registered key separately).
    result = _spec_conform(":persistence.txn/intent-log", intent_log_wire)
    if not result.is_ok:
        # ``SpecError`` lives in ``_registry`` and is not re-exported through
        # ``persistence.spec.__all__`` (latent gap in v0.5.0a1, also affects
        # ``_spec_validate_writes`` above — that path is dormant because it
        # only fires when a write spec is registered AND mismatches; nothing
        # exercises it in v0.5.0a1). The submodule path is what
        # ``tests/plan/test_parse.py`` uses.
        from persistence.spec._registry import SpecError
        raise SpecError(result)  # type: ignore[arg-type]

    return {
        ":persistence.txn/commit-id": commit_id,
        ":persistence.txn/started-at": tx.t_start.isoformat(),
        ":persistence.txn/committed-at": tx.db._clock().isoformat(),
        ":persistence.txn/retry-count": tx.attempt,
        ":persistence.txn/non-deterministic-retry": getattr(
            tx, "_deadline_set", False
        ),
        ":persistence.txn/read-set": sorted(r.eid for r in tx.read_set),
        ":persistence.txn/intent-log": intent_log_wire,
    }


def _replay_effect_intents(tx: "Transaction", commit_id: str) -> None:
    """Replay ``tx.effect_intent_log`` through the active effect runtime.

    No-op when no runtime is active (intents queue but never fire — the
    caller is responsible for setting up the runtime if it wants effects
    to run).

    v0.5.1 N2: passes ``commit_id`` via the typed ``txn_commit`` kwarg
    instead of stuffing ``"_txn_commit"`` into the intent's kwargs dict.
    The audit handler pops the sentinel before hashing args, so the
    same intent replayed across two different commits now produces the
    same ``args_hash`` (closes the v0.5.0a1 corruption where args_hash
    was polluted by commit_id). ``dict(intent.kwargs)`` defensively
    copies so the audit handler's in-place ``args.pop`` never reaches
    back to ``intent.kwargs``.
    """
    from persistence.effect.runtime import _active as _effect_active

    rt = _effect_active.get()
    if rt is None:
        return
    for intent in tx.effect_intent_log:
        rt.perform(intent.op, dict(intent.kwargs), txn_commit=commit_id)


def _commit_attempt(tx: "Transaction") -> bool:
    """Run the spec-validate / facts-build / lock+conflict-check / transact
    sequence atomically.

    Returns ``True`` if committed, ``False`` if the conflict check rejected
    it (caller decides retry vs raise). On success, sets ``tx.commit_id``
    and replays effect intents through the active runtime (if any).

    Both ``with db.dosync()`` and ``@db.dosync`` route their commit gate
    through this single helper.
    """
    # Commit gate 1: spec validation BEFORE allocating tx-id.
    _spec_validate_writes(tx.write_set)

    # Commit gates 2+3: conflict check AND transact must be atomic.
    # Without the lock, two threads can both pass the MVCC check before
    # either writes, then both commit — losing increments (B7 concurrency
    # bug). Holding store._lock across check+write collapses the window
    # to zero: a thread that passes the check is guaranteed to commit
    # before any other thread's check runs.
    commit_id = str(_uuid.uuid4())  # noqa: wall-clock
    facts = _build_write_facts(tx) + [_build_commit_fact(tx, commit_id)]
    with tx.db.store._lock:
        touched = {r.eid for r in tx.read_set} | {r.eid for r in tx.write_set}
        if any_datoms_since(tx.db, tx.t_start, touched):
            return False
        # Apply atomically as one db.transact() — write_set + commit datom.
        # `facts` always contains at least the commit datom, so no guard.
        tx.db.transact_batch(
            facts,
            provenance=_build_commit_provenance(tx, commit_id),
        )
    tx.commit_id = commit_id
    _replay_effect_intents(tx, commit_id)
    return True


def _run(
    db: Any,
    body: Callable[["Transaction"], Any],
    max_retries: int = DEFAULT_MAX_RETRIES,
    deadline: Optional[float] = None,
) -> str:
    """The retry loop that backs both ``with db.dosync()`` and
    ``@db.dosync``. Returns the commit_id on success.
    """
    if is_in_dosync():
        raise NestedDosyncNotSupported(
            "nested dosync is not supported in v0.5.0a1; restructure "
            "the calling code to do all transactional work in one "
            "outermost block (deferred to v0.5.4)."
        )

    deadline_at = time.monotonic() + deadline if deadline else None  # noqa: wall-clock
    attempt = 0
    while True:
        if deadline_at is not None and time.monotonic() >= deadline_at:  # noqa: wall-clock
            raise TxnDeadlineExceeded(
                f"dosync deadline of {deadline}s elapsed after {attempt} attempts"
            )
        if attempt > max_retries:
            raise TxnRetryExhausted(
                f"dosync exceeded max_retries={max_retries}"
            )

        t_start = db._clock()
        tx = Transaction(db=db, t_start=t_start, attempt=attempt)
        if deadline is not None:
            # Read by _build_commit_provenance; CM path leaves it absent.
            tx._deadline_set = True  # type: ignore[attr-defined]
        token = set_dosync_guard()
        try:
            try:
                body(tx)
            finally:
                clear_dosync_guard(token)
        except (TxnDeadlineExceeded, TxnRetryExhausted, NestedDosyncNotSupported):
            raise
        except Exception:
            # Body raised — propagate immediately, no commit, no retry.
            raise

        if _commit_attempt(tx):
            return tx.commit_id  # type: ignore[return-value]
        attempt += 1


__all__ = [
    "Transaction",
    "DEFAULT_MAX_RETRIES",
    "WRITE_ATTR",
    "_spec_validate_writes",
    "_build_write_facts",
    "_build_commit_fact",
    "_build_commit_provenance",
    "_replay_effect_intents",
    "_commit_attempt",
    "_run",
]
