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


class AtomCASExhausted(TxnError):
    """Raised when ``Atom.swap`` exhausts ``max_retries`` without committing.

    Under the writer-lock idiom (``with db.store._lock:`` held across
    read+write) this should never trigger — the lock guarantees no
    interleave between the read and the transact, so the first attempt
    always succeeds. The cap is defensive against future lock-free CAS
    fast paths and against pathological ``fn`` callbacks that themselves
    raise (in which case retry is the wrong remedy — ``swap`` propagates
    the body exception immediately rather than swallowing it under
    retry, but the bound is here as a backstop).
    """


class AtomInDosyncProhibited(TxnError):
    """Raised when any atom op is invoked under an active ``dosync`` body.

    persistence-os's atom writes are NOT in the ``dosync`` intent-queue
    + retry domain. Permitting them inside would punch a non-replayable
    hole in the audit chain — atom writes wouldn't appear in commit-id
    provenance, and dosync replay would not reproduce the same atom
    state. The user's in-txn read/write surface is ``tx.deref`` /
    ``tx.assoc`` / ``tx.alter``. See v0.5.2 design doc § F1
    "Intentional Clojure-parity deviation" block (this is a deliberate
    deviation from Clojure, where ``swap!`` inside ``dosync`` runs
    immediately).
    """


class AuditStackMissing(TxnError):
    """Raised when intent-replay finds a non-empty intent log but no
    active effect runtime.

    Phase 2.0d W1 (R2 MAJOR M2): the substrate-default audit stack
    installed by :meth:`persistence.sdk.Substrate.open` covers every
    canonical audit op (``:plan/edit`` / ``:fork/*`` / ``:code/exec`` /
    ``:fold/chosen``). If a caller subverts the default (constructs a
    raw ``DB`` directly, opens a substrate with ``audit=False``, or
    pops the runtime mid-flight) AND queues an audit-emitting intent,
    the commit-time replay would have silently dropped the intent under
    the v0.5.x "no-runtime → no-op" rule. That dropped silently audited
    work — the deterministic-replay invariant from design § 3.7 cannot
    hold. This error is the fail-fast guard.

    Adapter authors who legitimately need raw-DB-without-audit (sandbox
    tests, byte-identity drift fixtures) should pop a runtime with no
    audit middleware before the dosync runs — the runtime is non-empty,
    so the no-runtime guard does not trip; intents reach the (empty)
    handler stack and the standard ``Unhandled`` error surfaces if no
    raw terminator is installed for the queued op. The two errors are
    distinct in failure mode and remediation.
    """


__all__ = [
    "TxnError",
    "TxnRetryExhausted",
    "TxnDeadlineExceeded",
    "RefBranchMismatch",
    "RefValueNotImmutable",
    "EffectInIoBlock",
    "NestedDosyncNotSupported",
    "AtomCASExhausted",
    "AtomInDosyncProhibited",
    "AuditStackMissing",
]
