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

import pytest

from persistence.fact.db import DB
from persistence.txn import AuditStackMissing


def test_intent_log_without_runtime_raises() -> None:
    """When ``tx.effect()`` queues an intent and no effect runtime is
    active at commit time, ``_replay_effect_intents`` raises
    :class:`AuditStackMissing` rather than silently dropping the
    intent.

    This is the canonical fail-fast trip — it triggers regardless of
    which op was queued (``:plan/edit``, ``:fork/*``, ``:code/exec``,
    ``:fold/chosen``, or any user-queued op).
    """
    db = DB()
    r = db.ref("v")

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
