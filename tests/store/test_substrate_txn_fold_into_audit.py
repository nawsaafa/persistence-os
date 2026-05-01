"""``s.txn.fold_into`` audit-chain reconstruction (#145 / ADR-7 / § 4.3).

Verifies the load-bearing audit invariants from ADR-7 + § 4.3:

> Datom shape: :fold/probe :fold/branch :fold/score :fold/chosen
> (Phase 2.0c ships :fold/chosen only — see scratch impl plan
> carryover backlog #201 for the full 4-datom shape).

> Falsifiable acceptance gate: property test — for N branches with
> deterministic probe, s.txn.fold returns scores in the same order
> across two replays; fold_into chosen-branch commit is byte-identity.
> (Byte-identity property lives in test_fold_byte_identity.py;
> THIS file covers Merkle-chain integration.)

Three cases:

1. The :fold/chosen effect intent fires with the expected key set.
2. Two fold_into calls in one dosync produce a verifiable Merkle chain
   (verify_chain True; entries[1].prev_hash == entries[0].id).
3. fold_into outside dosync raises BEFORE any tx.effect call —
   no audit datom emitted on the failure path.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from persistence.effect.handlers.audit import (
    AuditEntry,
    make_audit_handler,
    verify_chain,
)
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.runtime import Handler, Runtime, with_runtime
from persistence.fact.db import DB
from persistence.sdk import Substrate
from persistence.sdk._fold_into import (
    FoldIntoOutsideDosync,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


def _scoring_fn(acc, item, db):
    fact = {
        "e": f"branch-{item}",
        "a": "fold/value",
        "v": item,
        "valid_from": _now(),
    }
    return acc + item, [fact], float(item)


def _argmax(branches):
    return max(range(len(branches)), key=lambda i: branches[i].score)


def _capture_fold_chosen_handler(captured: list[dict]) -> Handler:
    """Effect handler that records every :fold/chosen kwargs payload.

    Acts as a raw terminator (does NOT call ``k(args)``) — same shape
    as ``test_edit_audit._capture_plan_edit_handler``.
    """
    return Handler(
        name="capture-fold-chosen",
        wraps={":fold/chosen"},
        clauses={
            ":fold/chosen": (
                lambda args, *_: captured.append(args) or None
            ),
        },
    )


def _noop_fold_chosen_raw_handler() -> Handler:
    """Raw (terminator) handler for :fold/chosen — no-op.

    Needed under the audit handler because the audit handler is a
    middleware that calls ``k(args)``; without a raw handler beneath
    it the runtime hits 'reached the bottom of the stack'.

    For :fold/chosen the request datom IS the audit signal — there is
    no downstream side effect to perform — so the raw handler is a
    no-op by construction (same pattern as :plan/edit's raw handler).
    """
    return Handler(
        name="fold-chosen-raw",
        wraps={":fold/chosen"},
        clauses={
            ":fold/chosen": lambda _args, _k, _ctx: None,
        },
    )


# ---------------------------------------------------------------------------
# 1. Datom shape — :fold/chosen carries the documented keys
# ---------------------------------------------------------------------------


def test_fold_into_emits_fold_chosen_datom_with_expected_keys():
    """A single fold_into call queues exactly one :fold/chosen intent
    with {chosen_index, chosen_score, all_scores, branch_count,
    _txn_commit}.
    """
    captured: list[dict] = []
    rt = Runtime(handlers=[_capture_fold_chosen_handler(captured)])

    with Substrate.open("memory") as s:
        with with_runtime(rt):
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=0,
                    items=[1, 5, 3],
                    fn=_scoring_fn,
                    choose=_argmax,
                    tx=tx,
                )

    assert len(captured) == 1
    intent = captured[0]
    assert set(intent.keys()) == {
        "chosen_index",
        "chosen_score",
        "all_scores",
        "branch_count",
        "_txn_commit",
    }
    assert intent["chosen_index"] == 1
    assert intent["chosen_score"] == 5.0
    assert intent["all_scores"] == (1.0, 5.0, 3.0)
    assert intent["branch_count"] == 3
    # _txn_commit injected by _replay_effect_intents at commit time.
    assert intent["_txn_commit"] is not None
    assert isinstance(intent["_txn_commit"], str)


# ---------------------------------------------------------------------------
# 2. Merkle-chain integration — two fold_intos in one dosync chain
# ---------------------------------------------------------------------------


def test_fold_into_audit_entries_form_a_verifiable_merkle_chain():
    """Wire make_audit_handler to wrap :fold/chosen; emit two
    fold_intos in one dosync; assert verify_chain accepts the
    sequence and entries share a single txn_commit.

    Mirrors tests/plan/test_edit_audit.py::
    test_plan_edit_audit_entries_form_a_verifiable_merkle_chain
    exactly — :fold/chosen rides the same chain as :plan/edit.
    """
    entries: list[AuditEntry] = []
    audit = make_audit_handler(entries, wraps={":fold/chosen"})
    clock = make_fixed_clock_handler(ts=1_712_000_000.0)
    raw = _noop_fold_chosen_raw_handler()
    rt = Runtime(handlers=[raw, clock, audit])

    with Substrate.open("memory") as s:
        with with_runtime(rt):
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=0,
                    items=[1, 2, 3],
                    fn=_scoring_fn,
                    choose=_argmax,
                    tx=tx,
                )
                s.txn.fold_into(
                    seed=10,
                    items=[20, 30, 40],
                    fn=_scoring_fn,
                    choose=_argmax,
                    tx=tx,
                )

    assert len(entries) == 2
    for e in entries:
        assert e.op == ":fold/chosen"
        assert e.txn_commit is not None

    # Same commit_id across both (single dosync).
    assert entries[0].txn_commit == entries[1].txn_commit

    # Merkle chain links: entry[1].prev_hash == entry[0].id;
    # entry[0] is the head of the test-local chain.
    assert entries[0].prev_hash is None
    assert entries[1].prev_hash == entries[0].id

    # verify_chain passes.
    assert verify_chain(entries) is True


# ---------------------------------------------------------------------------
# 3. Failure path — outside dosync emits no audit datom
# ---------------------------------------------------------------------------


def test_fold_into_outside_dosync_emits_no_audit_datom():
    """ADR-7 + § 4.3 invariant: a fold_into outside dosync MUST raise
    rather than emit a silent :fold/chosen. The
    FoldIntoOutsideDosync exception is the enforcement mechanism —
    no :fold/chosen datom should ever exist without an enclosing
    txn_commit.
    """
    captured: list[dict] = []
    rt = Runtime(handlers=[_capture_fold_chosen_handler(captured)])

    with Substrate.open("memory") as s:
        with with_runtime(rt):
            with pytest.raises(FoldIntoOutsideDosync):
                s.txn.fold_into(
                    seed=0,
                    items=[1, 2],
                    fn=_scoring_fn,
                    choose=_argmax,
                )

    # Nothing was captured — gate trips before tx.effect runs.
    assert captured == []


def test_fold_into_with_failure_path_does_not_emit_chosen_datom():
    """If fn raises under on_error='abort', choose is never called and
    no :fold/chosen datom is queued. The trajectory's audit log carries
    no fold-decision record, which is the desired contract: only
    successful decisions are auditable.
    """
    captured: list[dict] = []
    rt = Runtime(handlers=[_capture_fold_chosen_handler(captured)])

    def raising_fn(acc, item, db):
        raise RuntimeError("boom")

    with Substrate.open("memory") as s:
        with with_runtime(rt):
            with pytest.raises(Exception):  # FoldError
                with s.txn.dosync() as tx:
                    s.txn.fold_into(
                        seed=0,
                        items=[1, 2],
                        fn=raising_fn,
                        choose=_argmax,
                        tx=tx,
                    )

    assert captured == []
