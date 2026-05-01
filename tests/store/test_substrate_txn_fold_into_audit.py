"""``s.txn.fold_into`` audit-chain reconstruction — Phase 2.0c-extended.

See ``docs/plans/2026-04-30-phase-2-persistence-coder-design.md`` § 4.3
+ ADR-7 for the design ground truth.

**Phase 2.0c-extended supersedes the Path-A `:fold/chosen` shape.**
``fold_into`` is rewired on top of ``DB.fork`` and emits the canonical
4-datom audit shape from § 3.7 + § 4.3:

> Datom shape (per fold_into call):
>   :fork/probe   — {seed_hash, items_hash, fn_hash, choose_hash, branch_count}
>   :fork/branch  × N — {branch_index, branch_id, item_hash, branch_state_hash}
>   :fork/score   × N — {branch_index, score_value, score_hash}
>   :fork/chosen  — {chosen_index, chosen_branch_id, chosen_state_hash, txn_commit_uuid}

> Falsifiable acceptance gate: property test — for N branches with
> deterministic probe, two replays of fold_into produce byte-identical
> 4-datom sequences in the same order. (The byte-identity property
> lives in ``test_fold_byte_identity.py``; this file covers the
> Merkle-chain integration.)

Cases:

1. fold_into emits 4-datom shape (1 + 3 + 3 + 1 = 8 effect intents).
2. Each datom kind carries the documented keys.
3. Two fold_intos in one dosync produce a verifiable Merkle chain
   (verify_chain True; entries chain with prev_hash linking) and
   share a single txn_commit.
4. fold_into outside dosync raises BEFORE any tx.effect call —
   no audit datom emitted on the failure path.
5. fold_into with fn raising under abort: nothing flushed because the
   dosync rolls back.
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


_FORK_OPS = (":fork/probe", ":fork/branch", ":fork/score", ":fork/chosen")


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
    middleware, mirrors :plan/edit pattern.
    """
    clauses = {op: (lambda _args, _k, _ctx: None) for op in _FORK_OPS}
    return Handler(name="fork-raw", wraps=set(_FORK_OPS), clauses=clauses)


# ---------------------------------------------------------------------------
# 1-2. Datom shape — 4-datom emission with documented keys
# ---------------------------------------------------------------------------


def test_fold_into_emits_4_datom_shape_in_order():
    """A 3-branch fold_into emits 1 + 3 + 3 + 1 = 8 intents in the
    canonical order [probe, branch x 3, score x 3, chosen].

    Phase 2.0c-extended supersedes the Path-A single-:fold/chosen
    emission; the new contract is the substrate-true 4-datom shape.
    """
    captured: list[tuple[str, dict]] = []
    rt = Runtime(handlers=[_capture_handler(captured)])

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

    ops = [op for op, _ in captured]
    assert ops == [
        ":fork/probe",
        ":fork/branch", ":fork/branch", ":fork/branch",
        ":fork/score", ":fork/score", ":fork/score",
        ":fork/chosen",
    ]


def test_fold_into_no_longer_emits_legacy_fold_chosen_op():
    """The legacy ``:fold/chosen`` op is reserved for ``DB.fold`` users
    who want the foldl-with-marker pattern. ``fold_into`` no longer
    emits it under the Phase 2.0c-extended rewire.
    """
    legacy_captured: list[dict] = []
    legacy_handler = Handler(
        name="legacy-capture",
        wraps={":fold/chosen"},
        clauses={
            ":fold/chosen": lambda args, *_: legacy_captured.append(args) or None,
        },
    )
    fork_captured: list[tuple[str, dict]] = []
    rt = Runtime(handlers=[legacy_handler, _capture_handler(fork_captured)])

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

    # No :fold/chosen captures — the rewire emits :fork/* exclusively.
    assert legacy_captured == []
    # Confirm :fork/* is what actually flowed.
    assert any(op == ":fork/chosen" for op, _ in fork_captured)


def test_fold_into_probe_carries_documented_keys():
    """:fork/probe -> {seed_hash, items_hash, fn_hash, choose_hash,
    branch_count, _txn_commit}."""
    captured: list[tuple[str, dict]] = []
    rt = Runtime(handlers=[_capture_handler(captured)])

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

    probe = next(args for op, args in captured if op == ":fork/probe")
    assert set(probe.keys()) == {
        "seed_hash", "items_hash", "fn_hash", "choose_hash",
        "branch_count", "_txn_commit",
    }
    assert probe["branch_count"] == 3
    assert probe["_txn_commit"] is not None


def test_fold_into_chosen_datom_carries_documented_keys():
    """:fork/chosen -> {chosen_index, chosen_branch_id,
    chosen_state_hash, _txn_commit}.

    Note: chosen_index in the audit datom is into the ALL-branches list
    (DB.fork's view), NOT the user-facing successful-only list — this is
    the substrate-truth contract. The FoldIntoResult.chosen_index
    surfaced to the caller IS into the successful list (matching v0.8.0a1
    semantics for backward-compat).
    """
    captured: list[tuple[str, dict]] = []
    rt = Runtime(handlers=[_capture_handler(captured)])

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

    chosen = next(args for op, args in captured if op == ":fork/chosen")
    assert set(chosen.keys()) == {
        "chosen_index", "chosen_branch_id",
        "chosen_state_hash", "_txn_commit",
    }
    assert chosen["chosen_index"] == 1  # argmax over scores [1,5,3]
    assert isinstance(chosen["chosen_branch_id"], str)
    assert len(chosen["chosen_branch_id"]) == 16


# ---------------------------------------------------------------------------
# 3. Merkle-chain integration — two fold_intos chain cleanly
# ---------------------------------------------------------------------------


def test_fold_into_audit_entries_form_a_verifiable_merkle_chain():
    """Wire make_audit_handler to wrap all :fork/* ops; emit two
    fold_intos in one dosync; assert verify_chain accepts the
    16-entry sequence and entries share a single txn_commit.

    Mirrors tests/plan/test_edit_audit.py::
    test_plan_edit_audit_entries_form_a_verifiable_merkle_chain.
    Two 3-branch fold_intos emit 2 * (1 + 3 + 3 + 1) = 16 entries.
    """
    entries: list[AuditEntry] = []
    audit = make_audit_handler(entries, wraps=set(_FORK_OPS))
    clock = make_fixed_clock_handler(ts=1_712_000_000.0)
    raw = _noop_raw_handler()
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

    # 2 * 8 = 16 audit entries.
    assert len(entries) == 16

    # Same commit_id across all (single dosync).
    txn_commits = {e.txn_commit for e in entries}
    assert len(txn_commits) == 1
    assert next(iter(txn_commits)) is not None

    # Merkle chain links: head has prev_hash=None; subsequent entries
    # link via prev_hash -> previous entry's id.
    assert entries[0].prev_hash is None
    for i in range(1, len(entries)):
        assert entries[i].prev_hash == entries[i - 1].id

    # verify_chain accepts the chain.
    assert verify_chain(entries) is True

    # Op order across both fold_intos: each emits 8 entries in canonical
    # 4-datom-shape order.
    expected_ops = [
        ":fork/probe", ":fork/branch", ":fork/branch", ":fork/branch",
        ":fork/score", ":fork/score", ":fork/score", ":fork/chosen",
    ] * 2
    assert [e.op for e in entries] == expected_ops


# ---------------------------------------------------------------------------
# 4-5. Failure paths — no audit emission on rollback
# ---------------------------------------------------------------------------


def test_fold_into_outside_dosync_emits_no_audit_datom():
    """ADR-7 + § 4.3 invariant: a fold_into outside dosync MUST raise
    rather than emit any silent :fork/* datom.
    """
    captured: list[tuple[str, dict]] = []
    rt = Runtime(handlers=[_capture_handler(captured)])

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


def test_fold_into_with_failure_path_does_not_emit_audit_datoms():
    """If fn raises under on_error='abort', the dosync raises before
    commit, so the queued :fork/probe + :fork/branch intents are never
    flushed via _replay_effect_intents. The trajectory's audit log
    carries no fork-decision record — only successful decisions are
    auditable.
    """
    captured: list[tuple[str, dict]] = []
    rt = Runtime(handlers=[_capture_handler(captured)])

    def raising_fn(acc, item, db):
        raise RuntimeError("boom")

    with Substrate.open("memory") as s:
        with with_runtime(rt):
            with pytest.raises(RuntimeError, match="boom"):
                with s.txn.dosync() as tx:
                    s.txn.fold_into(
                        seed=0,
                        items=[1, 2],
                        fn=raising_fn,
                        choose=_argmax,
                        tx=tx,
                    )

    # Body raised, dosync rolled back; intents never flushed.
    assert captured == []
