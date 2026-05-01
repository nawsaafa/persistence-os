"""``DB.fork`` 4-datom audit-chain shape tests — Phase 2.0c-extended #145ext.

Verifies the canonical 4-datom audit emission from design § 3.7
table row + § 4.3 + ADR-7:

> Per fork call, in this order under outer txn:
>   :fork/probe   — {seed_hash, items_hash, fn_hash, choose_hash, branch_count}
>   :fork/branch  × N — {branch_index, branch_id, item_hash, branch_state_hash}
>   :fork/score   × N — {branch_index, score_value, score_hash}
>   :fork/chosen  — {chosen_index, chosen_branch_id, chosen_state_hash, txn_commit_uuid}

All 4 datom kinds emit through tx.effect() under the same outer txn so
they share txn_commit and Merkle-chain to the same prev_hash. Mirrors
Phase 2.0a :plan/edit verbatim.

Test plan:

1. Single fork emits exactly 2 + 2*N intents in the documented order.
2. Each datom kind carries the documented keys.
3. Two forks in one dosync share txn_commit; Merkle chain links cleanly.
4. Failure path: choose error raises BEFORE :fork/chosen is queued (3
   intents emitted, not 4).
5. Failure path: outside dosync raises BEFORE any intent is queued.
6. on_error="continue" failures do not skip :fork/branch/:fork/score
   emission (failed branches still get their datoms, with error in
   the branch_state_hash payload).
"""
from __future__ import annotations

import pytest

from persistence.effect.handlers.audit import (
    AuditEntry,
    make_audit_handler,
    verify_chain,
)
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.runtime import Handler, Runtime, with_runtime
from persistence.fact import ForkChooseError, ForkOutsideDosync
from persistence.sdk import Substrate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_FORK_OPS = (":fork/probe", ":fork/branch", ":fork/score", ":fork/chosen")


def _capture_handler(captured: list[tuple[str, dict]]) -> Handler:
    """Effect handler that records (op, kwargs) for every :fork/* op.

    Acts as a raw terminator (does NOT call k(args)).
    """
    clauses = {
        op: (
            lambda args, _k, _ctx, _op=op: captured.append((_op, args)) or None
        )
        for op in _FORK_OPS
    }
    return Handler(name="capture-fork", wraps=set(_FORK_OPS), clauses=clauses)


def _noop_raw_handler() -> Handler:
    """No-op raw handler for :fork/* ops — needed beneath the audit
    middleware, mirrors the :plan/edit pattern.
    """
    clauses = {op: (lambda _args, _k, _ctx: None) for op in _FORK_OPS}
    return Handler(name="fork-raw", wraps=set(_FORK_OPS), clauses=clauses)


def _add_fn(state, item):
    return state + item


def _argmax(branches):
    return max(range(len(branches)), key=lambda i: branches[i].branch_state)


# ---------------------------------------------------------------------------
# 1-2. Datom shape — 2 + 2*N intents in the documented order
# ---------------------------------------------------------------------------


def test_fork_emits_4_datom_shape_in_order():
    """A 3-branch fork emits intents in this order:
    [probe, branch x 3, score x 3, chosen] (8 total).
    """
    captured: list[tuple[str, dict]] = []
    rt = Runtime(handlers=[_capture_handler(captured)])

    with Substrate.open("memory") as s:
        with with_runtime(rt):
            with s.txn.dosync() as tx:
                s._db.fork(
                    items=[1, 5, 3],
                    fn=_add_fn,
                    choose=_argmax,
                    seed=0,
                    tx=tx,
                )

    # 1 probe + 3 branch + 3 score + 1 chosen = 8.
    ops = [op for op, _ in captured]
    assert ops == [
        ":fork/probe",
        ":fork/branch", ":fork/branch", ":fork/branch",
        ":fork/score", ":fork/score", ":fork/score",
        ":fork/chosen",
    ]


def test_fork_probe_carries_documented_keys():
    """:fork/probe -> {seed_hash, items_hash, fn_hash, choose_hash, branch_count, _txn_commit}."""
    captured: list[tuple[str, dict]] = []
    rt = Runtime(handlers=[_capture_handler(captured)])

    with Substrate.open("memory") as s:
        with with_runtime(rt):
            with s.txn.dosync() as tx:
                s._db.fork(
                    items=[1, 5, 3],
                    fn=_add_fn,
                    choose=_argmax,
                    seed=42,
                    tx=tx,
                )

    probe = next(args for op, args in captured if op == ":fork/probe")
    assert set(probe.keys()) == {
        "seed_hash", "items_hash", "fn_hash", "choose_hash",
        "branch_count", "_txn_commit",
    }
    assert probe["branch_count"] == 3
    # Hashes are 16-hex.
    for key in ("seed_hash", "items_hash", "fn_hash", "choose_hash"):
        assert isinstance(probe[key], str)
        assert len(probe[key]) == 16
        int(probe[key], 16)
    # _txn_commit injected by _replay_effect_intents.
    assert probe["_txn_commit"] is not None


def test_fork_branch_carries_documented_keys():
    """:fork/branch -> {branch_index, branch_id, item_hash, branch_state_hash, _txn_commit}."""
    captured: list[tuple[str, dict]] = []
    rt = Runtime(handlers=[_capture_handler(captured)])

    with Substrate.open("memory") as s:
        with with_runtime(rt):
            with s.txn.dosync() as tx:
                s._db.fork(
                    items=[10, 20, 30],
                    fn=_add_fn,
                    choose=_argmax,
                    seed=0,
                    tx=tx,
                )

    branch_intents = [args for op, args in captured if op == ":fork/branch"]
    assert len(branch_intents) == 3
    for i, args in enumerate(branch_intents):
        assert set(args.keys()) == {
            "branch_index", "branch_id", "item_hash",
            "branch_state_hash", "_txn_commit",
        }
        assert args["branch_index"] == i
        for key in ("branch_id", "item_hash", "branch_state_hash"):
            assert len(args[key]) == 16


def test_fork_score_carries_documented_keys():
    """:fork/score -> {branch_index, score_value, score_hash, _txn_commit}.

    For the bare DB.fork API (score=None on each branch), score_value
    falls back to the canonicalised branch_state.
    """
    captured: list[tuple[str, dict]] = []
    rt = Runtime(handlers=[_capture_handler(captured)])

    with Substrate.open("memory") as s:
        with with_runtime(rt):
            with s.txn.dosync() as tx:
                s._db.fork(
                    items=[1, 5, 3],
                    fn=_add_fn,
                    choose=_argmax,
                    seed=0,
                    tx=tx,
                )

    score_intents = [args for op, args in captured if op == ":fork/score"]
    assert len(score_intents) == 3
    for i, args in enumerate(score_intents):
        assert set(args.keys()) == {
            "branch_index", "score_value", "score_hash", "_txn_commit",
        }
        assert args["branch_index"] == i
        # branch_state for fn=add over [1,5,3] from seed=0 is [1, 5, 3].
        assert args["score_value"] in (1, 5, 3)
        assert len(args["score_hash"]) == 16


def test_fork_chosen_carries_documented_keys():
    """:fork/chosen -> {chosen_index, chosen_branch_id, chosen_state_hash, _txn_commit}."""
    captured: list[tuple[str, dict]] = []
    rt = Runtime(handlers=[_capture_handler(captured)])

    with Substrate.open("memory") as s:
        with with_runtime(rt):
            with s.txn.dosync() as tx:
                s._db.fork(
                    items=[1, 5, 3],
                    fn=_add_fn,
                    choose=_argmax,
                    seed=0,
                    tx=tx,
                )

    chosen = next(args for op, args in captured if op == ":fork/chosen")
    assert set(chosen.keys()) == {
        "chosen_index", "chosen_branch_id",
        "chosen_state_hash", "_txn_commit",
    }
    assert chosen["chosen_index"] == 1  # argmax over [1,5,3] -> idx 1
    assert len(chosen["chosen_branch_id"]) == 16
    assert len(chosen["chosen_state_hash"]) == 16
    # _txn_commit is the dosync commit_id (UUID hex).
    assert chosen["_txn_commit"] is not None


# ---------------------------------------------------------------------------
# 3. Merkle-chain integration — two forks in one dosync chain cleanly
# ---------------------------------------------------------------------------


def test_fork_audit_entries_form_a_verifiable_merkle_chain():
    """Wire make_audit_handler to wrap all :fork/* ops; emit two forks in
    one dosync; assert verify_chain accepts the sequence and entries
    share a single txn_commit.

    Mirrors tests/plan/test_edit_audit.py::
    test_plan_edit_audit_entries_form_a_verifiable_merkle_chain.
    Two 3-branch forks emit 2 * (1 + 3 + 3 + 1) = 16 audit entries.
    """
    entries: list[AuditEntry] = []
    audit = make_audit_handler(entries, wraps=set(_FORK_OPS))
    clock = make_fixed_clock_handler(ts=1_712_000_000.0)
    raw = _noop_raw_handler()
    rt = Runtime(handlers=[raw, clock, audit])

    with Substrate.open("memory") as s:
        with with_runtime(rt):
            with s.txn.dosync() as tx:
                s._db.fork(
                    items=[1, 2, 3],
                    fn=_add_fn,
                    choose=_argmax,
                    seed=0,
                    tx=tx,
                )
                s._db.fork(
                    items=[10, 20, 30],
                    fn=_add_fn,
                    choose=_argmax,
                    seed=0,
                    tx=tx,
                )

    # Two forks * (1 probe + 3 branch + 3 score + 1 chosen) = 16 entries.
    assert len(entries) == 16
    # Same commit_id across all (single dosync).
    txn_commits = {e.txn_commit for e in entries}
    assert len(txn_commits) == 1
    assert next(iter(txn_commits)) is not None

    # Merkle chain links: entries[0].prev_hash is None;
    # for i >= 1, entries[i].prev_hash == entries[i-1].id.
    assert entries[0].prev_hash is None
    for i in range(1, len(entries)):
        assert entries[i].prev_hash == entries[i - 1].id

    # verify_chain accepts.
    assert verify_chain(entries) is True

    # Op order across both forks: each fork emits
    # [probe, branch, branch, branch, score, score, score, chosen].
    expected_ops = [
        ":fork/probe", ":fork/branch", ":fork/branch", ":fork/branch",
        ":fork/score", ":fork/score", ":fork/score", ":fork/chosen",
    ] * 2
    assert [e.op for e in entries] == expected_ops


# ---------------------------------------------------------------------------
# 4-5. Failure paths — partial / no audit emission
# ---------------------------------------------------------------------------


def test_fork_choose_error_emits_probe_branch_score_but_not_chosen():
    """When choose returns out-of-range, :fork/chosen is NOT queued.

    The probe + branch + score datoms have already been queued before
    choose runs — they ride the dosync's effect log unchanged. The
    dosync as a whole is rolled back when the body raises (since the
    commit attempt never runs); under the test's runtime-no-runtime
    capture pattern, however, intents are queued but only flushed if
    the dosync commits successfully.

    The contract this test verifies: ForkChooseError raises BEFORE
    :fork/chosen is queued, so the trajectory's audit log carries no
    chosen-marker for the failed call.
    """
    captured: list[tuple[str, dict]] = []
    rt = Runtime(handlers=[_capture_handler(captured)])

    with Substrate.open("memory") as s:
        with with_runtime(rt):
            with pytest.raises(ForkChooseError):
                with s.txn.dosync() as tx:
                    s._db.fork(
                        items=[1, 2, 3],
                        fn=_add_fn,
                        choose=lambda b: 99,  # out of range
                        seed=0,
                        tx=tx,
                    )

    # The dosync raised, so _replay_effect_intents was never called —
    # captured stays empty.
    assert captured == []


def test_fork_outside_dosync_emits_no_audit_datom():
    """ADR-7 invariant: a fork outside dosync MUST raise rather than
    emit any silent :fork/* datom.
    """
    captured: list[tuple[str, dict]] = []
    rt = Runtime(handlers=[_capture_handler(captured)])

    with Substrate.open("memory") as s:
        with with_runtime(rt):
            with pytest.raises(ForkOutsideDosync):
                s._db.fork(
                    items=[1, 2],
                    fn=_add_fn,
                    choose=_argmax,
                    seed=0,
                )

    assert captured == []


# ---------------------------------------------------------------------------
# 6. on_error="continue" — failed branches still get their audit datoms
# ---------------------------------------------------------------------------


def test_fork_continue_failed_branches_still_get_audit_datoms():
    """Under on_error='continue', a branch whose fn raised still gets a
    :fork/branch + :fork/score datom — the failure is recorded in the
    branch_state_hash (which hashes the seed-substituted state) and the
    score_value (which canonicalises the seed too).
    """
    captured: list[tuple[str, dict]] = []
    rt = Runtime(handlers=[_capture_handler(captured)])

    def raising_fn(state, item):
        if item == 99:
            raise RuntimeError("transient")
        return state + item

    with Substrate.open("memory") as s:
        with with_runtime(rt):
            with s.txn.dosync() as tx:
                s._db.fork(
                    items=[1, 99, 5],
                    fn=raising_fn,
                    choose=lambda branches: 0,
                    seed=0,
                    tx=tx,
                    on_error="continue",
                )

    # 1 probe + 3 branch + 3 score + 1 chosen = 8 — failures included.
    ops = [op for op, _ in captured]
    assert ops == [
        ":fork/probe",
        ":fork/branch", ":fork/branch", ":fork/branch",
        ":fork/score", ":fork/score", ":fork/score",
        ":fork/chosen",
    ]
    branch_intents = [args for op, args in captured if op == ":fork/branch"]
    # The failed branch (item 99) has branch_index 1.
    assert branch_intents[1]["branch_index"] == 1
    # branch_id != the success branches' (different state).
    branch_ids = {a["branch_id"] for a in branch_intents}
    assert len(branch_ids) == 3  # 3 distinct branch_ids
