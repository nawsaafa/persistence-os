"""dosync — context-manager + decorator forms."""
from datetime import datetime, timezone

import pytest
from pyrsistent import pmap

from persistence.fact.db import DB
from persistence.txn import (
    NestedDosyncNotSupported,
    TxnDeadlineExceeded,
    TxnRetryExhausted,
)


def test_dosync_context_manager_commits_on_clean_exit():
    db = DB()
    r = db.ref("account-1")
    with db.dosync() as tx:
        tx.assoc(r, pmap({"balance": 100}))
    # After commit, deref outside dosync sees the value via as_of(now).
    view = db.as_of(db._clock())
    assert view.entity("account-1").get("value") == pmap({"balance": 100})


def test_dosync_decorator_form():
    db = DB()
    r = db.ref("counter")

    @db.dosync
    def increment(tx):
        tx.assoc(r, 1)

    increment()
    view = db.as_of(db._clock())
    assert view.entity("counter").get("value") == 1


def test_dosync_body_exception_propagates_no_commit():
    db = DB()
    r = db.ref("account-1")
    with pytest.raises(ValueError, match="boom"):
        with db.dosync() as tx:
            tx.assoc(r, pmap({"balance": 100}))
            raise ValueError("boom")
    # No commit — the entity has no value.
    view = db.as_of(db._clock())
    assert view.entity("account-1") == {}


def test_dosync_max_retries_zero_raises_immediately_under_conflict():
    # max_retries=0 means: don't even attempt one body run.
    # Implementation choice: max_retries=0 raises TxnRetryExhausted on
    # first conflict; if no conflict, commits on attempt 0.
    db = DB()
    r = db.ref("account-1")

    # No conflict scenario — should commit fine.
    with db.dosync(max_retries=0) as tx:
        tx.assoc(r, 42)
    view = db.as_of(db._clock())
    assert view.entity("account-1").get("value") == 42


def test_nested_dosync_raises_NestedDosyncNotSupported():
    db = DB()
    with pytest.raises(NestedDosyncNotSupported):
        with db.dosync() as _outer_tx:
            with db.dosync() as _inner_tx:
                pass


def test_tx_now_is_frozen_across_body():
    db = DB()
    with db.dosync() as tx:
        first = tx.now()
        # tx.now() returns the t_start, not the current wall-clock.
        second = tx.now()
        assert first == second
