"""``AuditStackMissing`` fail-fast guard tests — Phase 2.0d W1 / M2.

Phase 2.0d W1 (R2 MAJOR M2 fix): :func:`persistence.txn.transaction._replay_effect_intents`
raises :class:`persistence.txn.AuditStackMissing` when intent log is
non-empty but no active effect runtime is installed. This module pins
that guard at the txn-module level (independent of the SDK's
``Substrate.open(audit=True)`` default install).

Cross-references: design § 3.7 + ADR-6 (audit datoms must ride the
Merkle chain); :class:`persistence.txn.AuditStackMissing` for the
exception's docstring on remediation patterns.
"""
from __future__ import annotations

from typing import Any

import pytest

from persistence.fact.db import DB
from persistence.txn import AuditStackMissing


def test_intent_log_without_runtime_raises() -> None:
    """When ``tx.effect()`` queues an intent and no effect runtime is
    active at commit time, ``_commit_attempt`` raises
    :class:`AuditStackMissing` BEFORE writing anything (Phase 2.0d W2
    M5 — pre-commit gate). The intent is rejected at the gate, not
    silently dropped after the facts have been committed.

    This is the canonical fail-fast trip — it triggers regardless of
    which op was queued (``:plan/edit``, ``:fork/*``, ``:code/exec``,
    ``:fold/chosen``, or any user-queued op).

    Phase 2.0d W2 (M5 atomicity): also asserts ``db.log()``
    length is unchanged after the failed dosync. The W1 implementation
    raised AFTER ``transact_batch`` completed, leaving the ref-write
    fact in the log; under the W2 fix the gate fires BEFORE
    ``transact_batch`` so no facts land.
    """
    db = DB()
    r = db.ref("v")

    log_before = len(list(db.log()))

    @db.dosync
    def body(tx):
        tx.effect(":plan/edit", plan_id="p", step_id="s",
                  before_op_hash="b", after_op_hash="a")
        tx.assoc(r, 1)

    with pytest.raises(AuditStackMissing) as exc_info:
        body()
    # The error message points the caller at the canonical
    # remediation: install via Substrate.open() OR wrap with
    # canonical_audit_stack(...).
    msg = str(exc_info.value)
    assert "canonical_audit_stack" in msg
    assert "Substrate.open" in msg

    # Phase 2.0d W2 / M5: no DB state change on AuditStackMissing.
    log_after = len(list(db.log()))
    assert log_after == log_before, (
        f"AuditStackMissing must rollback the dosync atomically — "
        f"log grew from {log_before} to {log_after} datoms"
    )
    # And the ref carries no value (the assoc was rolled back).
    view = db.as_of(db._clock())
    assert view.entity(r.eid) == {}, (
        f"ref.eid should carry no committed attrs after "
        f"AuditStackMissing; got {view.entity(r.eid)!r}"
    )


def test_empty_intent_log_without_runtime_is_a_no_op() -> None:
    """The fail-fast guard ONLY trips when the intent log is non-empty.

    The "raw fact-only dosync, no runtime needed" path remains a no-op
    — that is the v0.5.x default behaviour when no effects were
    queued, and it stays valid post-W1.
    """
    db = DB()
    r = db.ref("v")

    @db.dosync
    def body(tx):
        # No tx.effect() calls — only fact writes.
        tx.assoc(r, 1)

    # No AuditStackMissing.
    body()
    view = db.as_of(db._clock())
    assert view.entity(r.eid) == {"value": 1}


def test_audit_stack_missing_pre_gates_before_transact_batch() -> None:
    """Phase 2.0d W2 (M5): the ``AuditStackMissing`` raise happens
    BEFORE ``transact_batch`` is invoked.

    Spies on ``DB.transact_batch`` to prove call_count == 0 when the
    gate trips. Under W1 the call_count would have been 1 (commit
    happened, then ``_replay_effect_intents`` raised — atomicity bug).
    Under W2 the pre-gate in ``_commit_attempt`` short-circuits before
    the lock is acquired, so ``transact_batch`` is never reached.
    """
    db = DB()
    r = db.ref("v")

    call_log: list[tuple[Any, Any]] = []
    original_transact_batch = db.transact_batch

    def spy_transact_batch(facts, provenance=None, *, force_retroactive=False):
        call_log.append((tuple(facts), provenance))
        return original_transact_batch(
            facts, provenance, force_retroactive=force_retroactive
        )

    db.transact_batch = spy_transact_batch  # type: ignore[method-assign]

    @db.dosync
    def body(tx):
        tx.effect(":plan/edit", plan_id="p", step_id="s",
                  before_op_hash="b", after_op_hash="a")
        tx.assoc(r, 1)

    try:
        with pytest.raises(AuditStackMissing):
            body()
    finally:
        # Restore the spy (paranoia — DB is fresh per test, but be
        # tidy in case a follow-up assertion runs on db).
        db.transact_batch = original_transact_batch  # type: ignore[method-assign]

    # The pre-commit gate must have short-circuited before reaching
    # transact_batch.
    assert len(call_log) == 0, (
        f"transact_batch was called {len(call_log)} time(s) before the "
        f"AuditStackMissing raise — the W1 commit-then-raise atomicity "
        f"regression is back. Calls: {call_log!r}"
    )


def test_intent_log_with_active_runtime_does_not_raise() -> None:
    """Sanity check: when an effect runtime IS active and covers the
    queued op, the W1 guard does not trip — the intent flows through
    the runtime as before.
    """
    from persistence.effect import canonical_audit_stack, with_runtime

    db = DB()
    r = db.ref("v")

    @db.dosync
    def body(tx):
        tx.effect(":plan/edit", plan_id="p", step_id="s",
                  before_op_hash="b", after_op_hash="a")
        tx.assoc(r, 1)

    rt = canonical_audit_stack(entries=[])
    with with_runtime(rt):
        body()  # no AuditStackMissing.
