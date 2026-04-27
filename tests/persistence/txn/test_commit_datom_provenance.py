"""v0.5.1 N1 — read-set + intent-log on commit datom provenance.

The commit datom (a == "persistence.txn/commit-id" — leading colon stripped
by ``Datom.__post_init__``) now carries two additional provenance keys:

- ``:persistence.txn/read-set`` — sorted list of eids the body read.
- ``:persistence.txn/intent-log`` — list of ``{":op", ":kwargs"}`` items
  in the order ``tx.effect()`` was called.

The intent-log is conformed against the registered
``:persistence.txn/intent-log`` spec at commit time (option (3) — strict
validation at commit, NOT at ``tx.effect()`` call site). Non-EDN-conformant
kwargs (e.g. raw ``datetime``) cause ``SpecError`` to be raised on dosync
exit, before any datom is written.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from persistence.fact.db import DB
from persistence.spec._registry import SpecError


def _commit_datom(db):
    """Pick the single commit datom out of the store.

    Recurring gotcha (pinned in CHANGELOG-txn.md): ``Datom.__post_init__``
    strips a leading colon from ``a``, so the registered attribute name
    ``:persistence.txn/commit-id`` lands on the datom as the bare string
    ``"persistence.txn/commit-id"``.
    """
    return next(
        d for d in db.store.all_datoms()
        if d.a == "persistence.txn/commit-id"
    )


def test_commit_datom_carries_read_set_as_sorted_eids():
    db = DB()
    r1 = db.ref("z-account")
    r2 = db.ref("a-account")

    @db.dosync
    def body(tx):
        # Deref both refs in non-sorted insertion order — the commit
        # datom must record them sorted alphabetically by eid.
        tx.deref(r1)
        tx.deref(r2)
        tx.assoc(r1, 1)

    body()
    prov = _commit_datom(db).provenance
    assert prov[":persistence.txn/read-set"] == sorted([r1.eid, r2.eid])


def test_commit_datom_carries_intent_log_in_call_order():
    db = DB()
    r = db.ref("v")

    @db.dosync
    def body(tx):
        tx.effect(":log/write", message="A")
        tx.effect(":log/write", message="B")
        tx.effect(":log/write", message="C")
        tx.assoc(r, 1)

    body()
    prov = _commit_datom(db).provenance
    intent_log = prov[":persistence.txn/intent-log"]
    assert [item[":kwargs"]["message"] for item in intent_log] == ["A", "B", "C"]
    assert all(item[":op"] == ":log/write" for item in intent_log)


def test_intent_log_op_and_kwargs_round_trip():
    db = DB()
    r = db.ref("v")

    @db.dosync
    def body(tx):
        tx.effect(":log/write", message="hi", level=2)
        tx.assoc(r, 1)

    body()
    prov = _commit_datom(db).provenance
    intent_log = prov[":persistence.txn/intent-log"]
    assert intent_log == [
        {":op": ":log/write", ":kwargs": {"message": "hi", "level": 2}}
    ]


def test_non_conformant_intent_kwargs_raises_at_commit_not_at_call():
    """v0.5.1 W1 fix-pass — R1 MINOR 5: extended with three assertions
    pinning the "clean unwind" promise from the design doc.

    On ``SpecError`` from ``_build_commit_provenance`` (intent-log
    conformance), the failure path must:

    (a) NOT allocate a ``commit_id`` on the captured Transaction —
        ``tx.commit_id`` stays None even though ``commit_id`` was bound
        as a local in ``_commit_attempt`` before the conformance call.
    (b) NOT fire any effect-runtime side effects — ``_replay_effect_intents``
        runs only on ``_commit_attempt`` returning True; SpecError aborts
        before that.
    (c) Clear the ``ContextVar`` dosync guard — ``_run`` pairs
        ``set_dosync_guard()`` with a ``finally: clear_dosync_guard()``,
        so a subsequent ``dosync`` from the same task doesn't trip
        ``NestedDosyncNotSupported``.
    """
    from persistence.effect.runtime import Handler, Runtime, with_runtime
    from persistence.txn.intents import is_in_dosync

    db = DB()
    r = db.ref("v")

    # Capture pre-commit datom count to confirm nothing was written.
    pre_count = sum(1 for _ in db.store.all_datoms())

    # (b) tracking handler — records every ``:log/write`` perform call
    # so we can assert empty after SpecError.
    log_calls: list[dict] = []

    def tracker_clause(args, k, ctx):
        log_calls.append(dict(args))
        return None

    tracker = Handler(
        name="log-tracker",
        wraps={":log/write"},
        clauses={":log/write": tracker_clause},
    )
    rt = Runtime([tracker])

    captured: dict[str, object] = {}

    @db.dosync
    def body(tx):
        # Capture the tx in a closure so we can assert ``tx.commit_id is None``
        # from outside the body after the SpecError.
        captured["tx"] = tx
        # tx.effect() succeeds — kwargs validation is deferred to commit time.
        tx.effect(":log/write", ts=datetime.now(timezone.utc))
        tx.assoc(r, 1)
        # Body completes cleanly; SpecError raises on dosync exit when
        # _build_commit_provenance conforms the intent-log.

    with with_runtime(rt):
        with pytest.raises(SpecError):
            body()

    # No commit datom was written — the lock-held transact_batch never ran.
    post_count = sum(1 for _ in db.store.all_datoms())
    assert post_count == pre_count
    assert not any(
        d.a == "persistence.txn/commit-id" for d in db.store.all_datoms()
    )
    # (a) clean unwind: commit_id was never assigned to the tx —
    # ``_commit_attempt`` allocated a local ``commit_id`` UUID but the
    # SpecError raised before ``tx.commit_id = commit_id`` ran.
    tx_ref = captured["tx"]
    assert tx_ref.commit_id is None  # type: ignore[union-attr]
    # (b) clean unwind: no effect intents replayed through the runtime.
    assert log_calls == []
    # (c) clean unwind: the ContextVar dosync guard was cleared, so a
    # follow-up dosync from this task does not trip
    # NestedDosyncNotSupported.
    assert not is_in_dosync()


def test_empty_read_set_emits_empty_list():
    db = DB()
    r = db.ref("v")

    @db.dosync
    def body(tx):
        # Write only — no deref calls. read_set stays empty.
        tx.assoc(r, 1)

    body()
    prov = _commit_datom(db).provenance
    assert prov[":persistence.txn/read-set"] == []
