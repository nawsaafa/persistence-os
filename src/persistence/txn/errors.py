"""Error hierarchy for persistence.txn.

All exceptions raised by the transactional module subclass :class:`TxnError`.
This lets callers ``except TxnError`` for any txn-related failure while
still being able to discriminate by specific subclass when needed.
"""
from __future__ import annotations


class TxnError(Exception):
    """Base class for all transactional errors raised by persistence.txn."""


class TxnRetryExhausted(TxnError):
    """Raised when a dosync block exceeds its bounded retry budget.

    Default budget is ``max_retries=256``. Pass a larger value to
    ``db.dosync(max_retries=...)`` if a transaction legitimately needs more
    attempts under heavy contention; consider whether ``tx.commute`` (v0.5.1)
    or restructuring the body would be a better fix.
    """


class TxnDeadlineExceeded(TxnError):
    """Raised when a dosync block with ``deadline=...`` exceeds its
    wall-clock budget. Deadline-using transactions are tagged
    ``:persistence.txn/non-deterministic-retry true`` in their commit
    datom because wall-clock timing is not deterministic across replays.
    """


class RefBranchMismatch(TxnError):
    """A Ref obtained from one DB was used inside a dosync against another
    DB (typically a branched copy). Refs are bound to the DB they were
    constructed against; cross-DB use is forbidden.
    """


class RefValueNotImmutable(TxnError):
    """Raised by ``tx.assoc`` / ``tx.alter`` / ``db.new_ref(initial=)`` when
    the value is a mutable type that bypasses ref isolation.

    Accepted: pyrsistent.PMap/PVector/PSet, frozenset, tuple, str, int,
    bool, float, Decimal, bytes, None, frozen dataclass instances.
    Rejected: dict, list, set, mutable dataclass instances, arbitrary
    objects with mutable __dict__.

    Use ``persistence.txn.freeze(value)`` to convert dict→PMap and
    list→PVector.
    """


class EffectInIoBlock(TxnError):
    """Raised when raw ``effect.perform()`` is called inside a dosync body.

    Use ``tx.effect(op, **kwargs)`` instead — effects-as-intents are
    queued during the body and atomically replayed at commit time, so
    they're safe under retry.
    """


class NestedDosyncNotSupported(TxnError):
    """v0.5.0a1 forbids nested dosync. The semantics question (flatten
    into outer vs preserve inner-rollback-on-failure) is deferred to
    v0.5.4. Restructure the calling code to do all transactional work
    in one outermost block.
    """


__all__ = [
    "TxnError",
    "TxnRetryExhausted",
    "TxnDeadlineExceeded",
    "RefBranchMismatch",
    "RefValueNotImmutable",
    "EffectInIoBlock",
    "NestedDosyncNotSupported",
]
