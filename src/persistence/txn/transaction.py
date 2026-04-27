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
        """Queue an effect intent to be replayed atomically at commit."""
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
        # Snapshot read at t_start. The convention for ref-as-value:
        # the entity's ``:value`` attribute holds the ref's value.
        view = self.db.as_of(self.t_start)
        entity_attrs = view.entity(ref.eid)
        return entity_attrs.get(WRITE_ATTR) if entity_attrs else None

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
WRITE_ATTR = "value"  # ref values stored under this attribute (Datom strips leading ":" from `a`)


def _spec_validate_writes(write_set: dict) -> None:
    """Run spec.conform over each write before tx-id allocation.

    For v0.5.0a1, the only spec we apply is the per-ref :value spec
    if registered. Calls without a registered spec pass through.
    Raises ``persistence.spec.SpecError`` on failure (caller propagates;
    no tx-id is burned).
    """
    keys = set(_registered_keys())
    spec_key = WRITE_ATTR
    if spec_key not in keys:
        return
    for _ref, value in write_set.items():
        result = _spec_conform(spec_key, value)
        if not result.is_ok:
            from persistence.spec import SpecError
            raise SpecError(result)


def _build_write_facts(tx: "Transaction") -> list[dict]:
    """Build the list-of-fact-dicts payload for db.transact()."""
    now = tx.db._clock()
    facts: list[dict] = []
    for ref, value in tx.write_set.items():
        facts.append({
            "e": ref.eid,
            "a": WRITE_ATTR,
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
        with db.store._lock:
            touched = {r.eid for r in tx.read_set} | {r.eid for r in tx.write_set}
            if any_datoms_since(db, t_start, touched):
                attempt += 1
                continue

            # Apply atomically as one db.transact() — write_set + commit datom.
            # `facts` always contains at least the commit datom, so no guard.
            db.transact_batch(
                facts,
                provenance={
                    ":persistence.txn/commit-id": commit_id,
                    ":persistence.txn/started-at": t_start.isoformat(),
                    ":persistence.txn/committed-at": db._clock().isoformat(),
                    ":persistence.txn/retry-count": attempt,
                    ":persistence.txn/non-deterministic-retry": deadline is not None,
                },
            )
        tx.commit_id = commit_id

        # Replay effect intents through the real handler stack.
        # Only meaningful if there's an active runtime; if not, intents
        # silently no-op (caller responsibility to set up runtime).
        from persistence.effect.runtime import _active as _effect_active
        rt = _effect_active.get()
        if rt is not None:
            for intent in tx.effect_intent_log:
                rt.perform(intent.op, {**intent.kwargs, "_txn_commit": commit_id})

        return commit_id

__all__ = [
    "Transaction",
    "DEFAULT_MAX_RETRIES",
    "WRITE_ATTR",
    "_spec_validate_writes",
    "_build_write_facts",
    "_build_commit_fact",
    "_run",
]
