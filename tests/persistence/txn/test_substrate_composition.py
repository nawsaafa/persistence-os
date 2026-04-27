"""dosync composes correctly with branch / fork / causal_history."""
from datetime import datetime, timezone, timedelta

import pytest
from pyrsistent import pmap

from persistence.fact.db import DB
from persistence.fact.projection import DictProjection


def test_dosync_inside_branch_isolated_from_parent():
    """Writes inside a dosync against a branched DB must not leak into
    the parent's store.
    """
    parent = DB()
    r_parent = parent.ref("account")
    with parent.dosync() as tx:
        tx.assoc(r_parent, pmap({"balance": 100}))

    t_branch = parent._clock()
    branched = parent.branch(t_branch, [])

    r_branched = branched.ref("account")
    with branched.dosync() as tx:
        tx.assoc(r_branched, pmap({"balance": 999}))

    # Parent unchanged.
    parent_view = parent.as_of(parent._clock())
    assert parent_view.entity("account").get("value") == pmap({"balance": 100})

    # Branched sees the new value.
    branched_view = branched.as_of(branched._clock())
    assert branched_view.entity("account").get("value") == pmap({"balance": 999})


def test_as_of_before_commit_does_not_see_dosync_writes():
    db = DB()
    r = db.ref("v")
    t_before = db._clock() - timedelta(seconds=1)
    with db.dosync() as tx:
        tx.assoc(r, 42)
    # Snapshot at t_before sees no datoms for "v".
    pre_view = db.as_of(t_before)
    assert pre_view.entity("v") == {}
    # Snapshot at now sees the value.
    now_view = db.as_of(db._clock())
    assert now_view.entity("v").get("value") == 42


def test_fork_returns_fresh_projection_unaffected_by_dosync():
    db = DB()
    proj = DictProjection()
    forked = proj.fork("branch-1")
    # Forked is a fresh empty projection — even after dosync writes into db.
    with db.dosync() as tx:
        tx.assoc(db.ref("v"), 42)
    assert forked.as_dict() == {}


def test_PLAN_CANONICAL_VERSION_unchanged():
    from persistence.plan import PLAN_CANONICAL_VERSION
    assert PLAN_CANONICAL_VERSION == 1


def test_replay_byte_identity_across_two_runs():
    """Hypothesis-style property test, manually instantiated.

    Run the same body twice against fresh DBs with frozen clocks; the
    log datoms (excluding tx_time) should match byte-for-byte.
    """
    from datetime import timezone

    fixed_t = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    clock = lambda: fixed_t

    def run_once():
        db = DB(clock=clock)
        @db.dosync
        def body(tx):
            tx.assoc(db.ref("a"), 1)
            tx.assoc(db.ref("b"), 2)
            tx.alter(db.ref("a"), lambda v: (v or 0) + 10)
        body()
        # Strip uuid commit-id and ephemeral fields; compare structurally.
        # Note: Datom.__post_init__ strips leading colon from `a`, so commit
        # datoms have a="persistence.txn/commit-id", NOT ":persistence.txn/...".
        return [
            (d.e, d.a, d.v, d.op, d.valid_from)
            for d in db.log()
            if not d.a.startswith("persistence.txn/")  # commit datoms vary
        ]

    # The user-write datoms (a=value, b=value) should match byte-for-byte.
    log1 = run_once()
    log2 = run_once()
    assert log1 == log2, f"log1={log1}\nlog2={log2}"
