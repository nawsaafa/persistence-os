"""Hypothesis byte-identity properties for s.txn.fold + s.txn.fold_into +
s.txn.fork — Phase 2.0c-extended.

This file ships the § 4.3 falsifiable acceptance gate from the Phase-2
design doc:

> Falsifiable acceptance gate: property test — for N branches with
> deterministic probe, s.txn.fold returns scores in the same order
> across two replays; fold_into chosen-branch commit is byte-identity.

Phase 2.0c-extended adds:

> For any fixed (seed, items, deterministic fn, deterministic choose)
> input, two replays of fold_into produce byte-identical 4-datom
> sequences (:fork/probe + :fork/branch x N + :fork/score x N +
> :fork/chosen) in the same order.

> Rollback verification: non-chosen branches' facts MUST NOT appear
> in db.history() post-commit, for any input distribution.

Properties at @max_examples=200:

1. ``test_s_txn_fold_byte_identity_across_replays`` — for any
   (seed, items, deterministic fn) input, two independent runs of
   ``s.txn.fold`` on fresh substrates produce identical accumulator,
   identical datom-count, byte-identical projected fact stream.

2. ``test_s_txn_fold_into_chosen_datom_byte_identity`` — Path-A
   single-datom byte-identity property, retained for backward
   regression coverage. Captures the ``:fork/chosen`` payload (the
   final datom of the new 4-datom shape) and asserts byte-identity
   modulo _txn_commit.

3. ``test_s_txn_fold_into_4_datom_shape_byte_identity`` — Phase
   2.0c-extended property: the FULL 4-datom sequence
   (probe + branch*N + score*N + chosen) is byte-identical across
   two replays of the same fold_into input. Captures all 8 intents
   (for N=3) and JSON-canonicalises modulo _txn_commit.

4. ``test_s_txn_fold_into_rolls_back_non_chosen_branches`` — Phase
   2.0c-extended rollback property: for any (seed, items, fn,
   choose) input, ONLY the chosen branch's facts appear in
   db.history() post-commit. Non-chosen branches' facts MUST be
   absent.

The tests use Hypothesis to generate input distributions; deterministic
``fn`` and ``choose`` are MODULE-LEVEL (no closures over draw'd state)
so byte-identity is rooted in the inputs alone.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import hypothesis.strategies as st
from hypothesis import HealthCheck, given, settings

from persistence.effect.handlers.audit import (
    AuditEntry,
    make_audit_handler,
)
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.runtime import Handler, Runtime, with_runtime
from persistence.sdk import Substrate
from persistence.sdk._fold_into import FoldBranchScore


# ---------------------------------------------------------------------------
# Module-level deterministic helpers (no closures)
# ---------------------------------------------------------------------------


_FIXED_TS = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
_FORK_OPS = (":fork/probe", ":fork/branch", ":fork/score", ":fork/chosen")


def _det_fold_fn(acc, item, db):
    """Deterministic 2-tuple fn for s.txn.fold byte-identity property.

    No closure state, no clock/wall-time reads, no UUIDs.
    """
    fact = {
        "e": f"item-{item}",
        "a": "fold/value",
        "v": item,
        "valid_from": _FIXED_TS,
    }
    return acc + item, [fact]


def _det_fold_into_fn(acc, item, db):
    """Deterministic 3-tuple fn for s.txn.fold_into byte-identity.

    Score is item*item — fully determined by the items strategy.
    """
    fact = {
        "e": f"branch-{item}",
        "a": "fold/value",
        "v": item,
        "valid_from": _FIXED_TS,
    }
    score = float(item * item)
    return acc + item, [fact], score


def _det_argmax(branches: list[FoldBranchScore]) -> int:
    """Deterministic argmax; ties broken by lower index."""
    best_idx = 0
    best_score = branches[0].score
    for i, b in enumerate(branches[1:], start=1):
        if b.score > best_score:
            best_idx = i
            best_score = b.score
    return best_idx


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------


@st.composite
def fold_input(draw):
    """Draw a (seed, items) tuple with bounded size."""
    seed = draw(st.integers(min_value=-1000, max_value=1000))
    n = draw(st.integers(min_value=1, max_value=8))
    items = draw(
        st.lists(
            st.integers(min_value=-100, max_value=100),
            min_size=n,
            max_size=n,
        )
    )
    return seed, items


# ---------------------------------------------------------------------------
# Property 1 — s.txn.fold byte-identity across replays
# ---------------------------------------------------------------------------


def _run_s_txn_fold(seed: int, items: list[int]) -> tuple[Any, int, list[Any]]:
    with Substrate.open("memory") as s:
        acc, n = s.txn.fold(seed=seed, items=items, fn=_det_fold_fn)
        rows = list(s.escape.fact.store.all_datoms())
        projected = sorted(
            (r.e, r.a, r.v, r.valid_from.isoformat() if r.valid_from else None)
            for r in rows
            if r.a == "fold/value"
        )
    return acc, n, projected


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(payload=fold_input())
def test_s_txn_fold_byte_identity_across_replays(payload):
    """§ 4.3 acceptance gate for s.txn.fold."""
    seed, items = payload
    acc1, n1, rows1 = _run_s_txn_fold(seed, items)
    acc2, n2, rows2 = _run_s_txn_fold(seed, items)
    assert acc1 == acc2
    assert n1 == n2
    assert rows1 == rows2


# ---------------------------------------------------------------------------
# Property 2 — s.txn.fold_into :fork/chosen audit datom byte-identity
# ---------------------------------------------------------------------------


def _capture_op_handler(
    captured: list[tuple[str, dict]], ops: tuple[str, ...] = _FORK_OPS
) -> Handler:
    """Capture (op, kwargs) pairs for the listed ops."""
    clauses = {
        op: (
            lambda args, _k, _ctx, _op=op: captured.append((_op, args)) or None
        )
        for op in ops
    }
    return Handler(name="capture", wraps=set(ops), clauses=clauses)


def _run_s_txn_fold_into_capture_chosen(
    seed: int, items: list[int]
) -> dict:
    """Capture only the :fork/chosen intent payload (last in the
    4-datom sequence). Used by Property 2 for backward-regression
    parity with the v0.8.0a1 Path-A property.
    """
    captured: list[tuple[str, dict]] = []
    rt = Runtime(handlers=[_capture_op_handler(captured)])

    with Substrate.open("memory") as s:
        with with_runtime(rt):
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=seed,
                    items=items,
                    fn=_det_fold_into_fn,
                    choose=_det_argmax,
                    tx=tx,
                )

    chosen_intents = [args for op, args in captured if op == ":fork/chosen"]
    assert len(chosen_intents) == 1
    intent = dict(chosen_intents[0])
    intent.pop("_txn_commit", None)
    return intent


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(payload=fold_input())
def test_s_txn_fold_into_chosen_datom_byte_identity(payload):
    """§ 4.3 acceptance gate for s.txn.fold_into — :fork/chosen
    payload is byte-identical across replays for fixed inputs.
    """
    seed, items = payload
    intent1 = _run_s_txn_fold_into_capture_chosen(seed, items)
    intent2 = _run_s_txn_fold_into_capture_chosen(seed, items)

    # Structural equality first.
    assert intent1 == intent2

    # Byte-identity through canonical-JSON encoding.
    j1 = json.dumps(intent1, sort_keys=True, default=str)
    j2 = json.dumps(intent2, sort_keys=True, default=str)
    assert j1 == j2


# ---------------------------------------------------------------------------
# Property 3 — Phase 2.0c-extended FULL 4-datom-shape byte-identity
# ---------------------------------------------------------------------------


def _run_s_txn_fold_into_capture_all(
    seed: int, items: list[int]
) -> list[tuple[str, dict]]:
    """Capture the COMPLETE 4-datom intent sequence in emission order.

    Returns a list of (op, kwargs) pairs with _txn_commit stripped so
    the comparison is byte-identical across replays (commit_ids are
    per-run UUIDs by design).
    """
    captured: list[tuple[str, dict]] = []
    rt = Runtime(handlers=[_capture_op_handler(captured)])

    with Substrate.open("memory") as s:
        with with_runtime(rt):
            with s.txn.dosync() as tx:
                s.txn.fold_into(
                    seed=seed,
                    items=items,
                    fn=_det_fold_into_fn,
                    choose=_det_argmax,
                    tx=tx,
                )

    # Strip _txn_commit (per-run UUID).
    cleaned: list[tuple[str, dict]] = []
    for op, args in captured:
        a = dict(args)
        a.pop("_txn_commit", None)
        cleaned.append((op, a))
    return cleaned


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(payload=fold_input())
def test_s_txn_fold_into_4_datom_shape_byte_identity(payload):
    """Phase 2.0c-extended falsifiable acceptance gate.

    For any (seed, items) input + the deterministic
    (_det_fold_into_fn, _det_argmax) pair, two independent runs
    produce byte-identical 4-datom intent sequences (probe +
    branch x N + score x N + chosen).
    """
    seed, items = payload
    n = len(items)
    seq1 = _run_s_txn_fold_into_capture_all(seed, items)
    seq2 = _run_s_txn_fold_into_capture_all(seed, items)

    # Same length: 1 + N + N + 1 = 2 + 2*N.
    assert len(seq1) == 2 + 2 * n
    assert len(seq1) == len(seq2)

    # Same op order.
    expected_ops = (
        [":fork/probe"]
        + [":fork/branch"] * n
        + [":fork/score"] * n
        + [":fork/chosen"]
    )
    assert [op for op, _ in seq1] == expected_ops
    assert [op for op, _ in seq2] == expected_ops

    # Byte-identity at the kwargs level.
    for (op1, a1), (op2, a2) in zip(seq1, seq2):
        assert op1 == op2
        assert a1 == a2

    # Byte-identity through canonical-JSON encoding.
    j1 = json.dumps(seq1, sort_keys=True, default=str)
    j2 = json.dumps(seq2, sort_keys=True, default=str)
    assert j1 == j2


# ---------------------------------------------------------------------------
# Property 4 — Phase 2.0c-extended rollback verification
# ---------------------------------------------------------------------------


def _run_s_txn_fold_into_collect_committed_branch_eids(
    seed: int, items: list[int]
) -> tuple[int, set[str]]:
    """Run s.txn.fold_into; return (chosen_index, set of branch eids
    actually committed to the substrate).
    """
    with Substrate.open("memory") as s:
        with s.txn.dosync() as tx:
            result = s.txn.fold_into(
                seed=seed,
                items=items,
                fn=_det_fold_into_fn,
                choose=_det_argmax,
                tx=tx,
            )
        committed_eids = {
            d.e for d in s.escape.fact.store.all_datoms()
            if d.a == "fold/value"
        }
    return result.chosen_index, committed_eids


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(payload=fold_input())
def test_s_txn_fold_into_rolls_back_non_chosen_branches(payload):
    """Phase 2.0c-extended rollback property.

    For ANY (seed, items) input + deterministic fn + argmax choose,
    only the chosen branch's facts persist in the substrate. Non-chosen
    branches' fact eids MUST NOT appear in db.history().

    This is the substrate-true rollback contract — the wedge story for
    persistence-coder. Path-A's all-branches-commit semantic would
    fail this property.
    """
    seed, items = payload
    chosen_idx, committed_eids = (
        _run_s_txn_fold_into_collect_committed_branch_eids(seed, items)
    )
    # The chosen branch's eid IS committed.
    chosen_item = items[chosen_idx]
    chosen_eid = f"branch-{chosen_item}"
    assert chosen_eid in committed_eids, (
        f"chosen branch {chosen_eid} must be committed; got {committed_eids}"
    )

    # Items can collide on value (e.g. items=[5, 5] both map to
    # branch-5). The rollback property is structurally about
    # "non-chosen-eids are absent" — but if two items share an eid and
    # only one is chosen, the eid IS still committed (legitimately). So
    # we check the contrapositive on UNIQUE non-chosen eids that don't
    # collide with the chosen one.
    non_chosen_unique_eids = {
        f"branch-{item}"
        for i, item in enumerate(items)
        if i != chosen_idx and f"branch-{item}" != chosen_eid
    }
    leaked = non_chosen_unique_eids & committed_eids
    assert not leaked, (
        f"non-chosen branches {leaked} must NOT appear in committed "
        f"facts; got {committed_eids}"
    )
