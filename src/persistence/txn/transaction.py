"""Transaction — the object passed into a dosync body.

Captures read_set, write_set, effect_intent_log, and metadata
(t_start snapshot, attempt count, commit_id allocated at success).

The retry loop and commit gate live alongside this class and are wired
in via ``Transaction._run`` (added in Task B7).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from persistence.txn.errors import RefValueNotImmutable
from persistence.txn.intents import EffectIntent
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

        Raises ``RefValueNotImmutable`` if ``value`` is mutable.
        """
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
        return entity_attrs.get(":value") if entity_attrs else None

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


__all__ = ["Transaction"]
