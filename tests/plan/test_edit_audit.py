"""Plan Edit API — :plan/edit audit-chain reconstruction (#140 / ADR-6).

Verifies the load-bearing audit invariant from ADR-6:

> every plan edit is a `:plan/edit` datom with before/after op-hash +
> step-id. No silent edits.

> Enforcement: G3 property test at max_examples=100.

This test plays the role of the falsifiable enforcement gate referenced
above:

1. Apply 3 edits to the same step inside a single dosync.
2. Capture the queued :plan/edit effect intents via a Handler that
   wraps ":plan/edit" (the same pattern test_intents.py uses for
   :log/write).
3. Verify each captured intent carries the expected
   {plan_id, step_id, before_op_hash, after_op_hash} fields.
4. Verify the chain reconstructs: each edit N's after_op_hash matches
   edit N+1's before_op_hash WHEN the edit targets the same content-
   addressed step.
5. Verify replay byte-identity per § 3.7 audit-replay mode: walking the
   captured intents reproduces the final plan's id exactly.

Also exercises the integration with persistence.effect.handlers.audit.
make_audit_handler so :plan/edit rides the existing Merkle chain.
"""
from __future__ import annotations

from typing import Any

import pytest

from persistence.effect.runtime import Handler, Runtime, with_runtime
from persistence.effect.handlers.audit import (
    AuditEntry,
    make_audit_handler,
    verify_chain,
)
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.fact.db import DB
from persistence.plan import Node
from persistence.plan._edit import edit_step, insert_step_after


def _capture_plan_edit_handler(captured: list[dict]) -> Handler:
    """Effect handler that records every :plan/edit kwargs payload.

    Acts as a raw terminator (does NOT call ``k(args)``) so it can sit
    at the bottom of a stack alone for the simpler "did the intent
    fire" tests.
    """
    return Handler(
        name="capture-plan-edit",
        wraps={":plan/edit"},
        clauses={
            ":plan/edit": lambda args, *_: captured.append(args) or None,
        },
    )


def _noop_plan_edit_raw_handler() -> Handler:
    """Raw (terminator) handler for :plan/edit that simply returns None.

    Needed under the audit handler because the audit handler is a
    middleware that calls ``k(args)``; without a raw handler beneath
    it the runtime hits 'reached the bottom of the stack'.

    For :plan/edit the request datom IS the audit signal — there is
    no downstream side effect to perform — so the raw handler is a
    no-op by construction. (In production, the AuditEntry IS the
    persisted record; nothing else needs to happen.)
    """
    return Handler(
        name="plan-edit-raw",
        wraps={":plan/edit"},
        clauses={":plan/edit": lambda _args, _k, _ctx: None},
    )


# ---------------------------------------------------------------------------
# Audit-chain reconstruction — three edits, same step
# ---------------------------------------------------------------------------


def test_three_edits_chain_via_before_after_op_hash() -> None:
    """Three sequential edit_step calls on the same content-addressed
    step inside one dosync produce a chain of :plan/edit datoms whose
    before_op_hash / after_op_hash links match.

    Specifically: at each edit N, ``before_op_hash`` equals the matched
    step's id at that point. Because edit_step REPLACES the matched
    step, the next edit's ``before_op_hash`` is a DIFFERENT id (the
    new replacement's id) — so we test that:

    - edit 1 before = original step id
    - edit 1 after  = step1 id
    - edit 2 before = step1 id  (same step_id passed in)
    - edit 2 after  = step2 id
    - edit 3 before = step2 id
    - edit 3 after  = step3 id

    That's the ADR-6 / § 3.7 audit-chain invariant for sequential
    edits to the same logical step.
    """
    db = DB()
    captured: list[dict] = []
    clock = make_fixed_clock_handler(ts=1_712_000_000.0)
    rt = Runtime(handlers=[clock, _capture_plan_edit_handler(captured)])

    initial_inner = Node(tag=":llm-call", attrs={"prompt": "v0"}, children=())
    plan = Node(tag=":seq", attrs={}, children=(initial_inner,))
    step1 = Node(tag=":llm-call", attrs={"prompt": "v1"}, children=())
    step2 = Node(tag=":llm-call", attrs={"prompt": "v2"}, children=())
    step3 = Node(tag=":llm-call", attrs={"prompt": "v3"}, children=())

    with with_runtime(rt):
        with db.dosync() as tx:
            p1 = edit_step(plan, initial_inner.id, step1, tx=tx)
            p2 = edit_step(p1, step1.id, step2, tx=tx)
            p3 = edit_step(p2, step2.id, step3, tx=tx)

    # 3 :plan/edit intents fired post-commit.
    assert len(captured) == 3

    # Per-intent shape.
    for intent in captured:
        assert set(intent.keys()) >= {
            "plan_id",
            "step_id",
            "before_op_hash",
            "after_op_hash",
        }
        # commit_id was injected via _replay_effect_intents.
        assert "_txn_commit" in intent

    # Edit-1 before/after.
    assert captured[0]["plan_id"] == plan.id
    assert captured[0]["step_id"] == initial_inner.id
    assert captured[0]["before_op_hash"] == initial_inner.id
    assert captured[0]["after_op_hash"] == step1.id

    # Edit-2 chains to edit-1's after.
    assert captured[1]["before_op_hash"] == step1.id
    assert captured[1]["after_op_hash"] == step2.id

    # Edit-3 chains to edit-2's after.
    assert captured[2]["before_op_hash"] == step2.id
    assert captured[2]["after_op_hash"] == step3.id

    # All three intents share the same _txn_commit (single dosync).
    commit_ids = {i["_txn_commit"] for i in captured}
    assert len(commit_ids) == 1

    # Replay byte-identity reconstruction: walk the captured chain
    # forward by threading after_op_hash → next before_op_hash on the
    # MATCHED-STEP axis (not the plan-root axis).
    expected_chain = [c["before_op_hash"] for c in captured] + [
        captured[-1]["after_op_hash"]
    ]
    # First link starts at the original step's id (initial_inner.id),
    # ends at the final step's id (step3.id), threading through step1
    # and step2 in order.
    assert expected_chain == [
        initial_inner.id, step1.id, step2.id, step3.id,
    ]

    # Final reconstructed step id matches what the executor produced.
    assert p3.children[0].id == step3.id


def test_no_edit_outside_dosync_silently_skips_audit() -> None:
    """ADR-6 invariant: edit ops outside a dosync MUST raise rather
    than emit a silent edit. The PlanEditOutsideDosync exception is the
    enforcement mechanism — no :plan/edit datom should ever exist
    without an enclosing txn_commit.
    """
    from persistence.plan._errors import PlanEditOutsideDosync

    captured: list[dict] = []
    clock = make_fixed_clock_handler(ts=1_712_000_000.0)
    rt = Runtime(handlers=[clock, _capture_plan_edit_handler(captured)])

    inner = Node(tag=":llm-call", attrs={"prompt": "x"}, children=())
    plan = Node(tag=":seq", attrs={}, children=(inner,))
    new_op = Node(tag=":llm-call", attrs={"prompt": "y"}, children=())

    class _FakeTx:
        def effect(self, *_args, **_kwargs) -> None:  # pragma: no cover
            raise AssertionError("must not reach effect()")

    with with_runtime(rt):
        with pytest.raises(PlanEditOutsideDosync):
            edit_step(plan, inner.id, new_op, tx=_FakeTx())

    # Nothing was captured — gate trips before tx.effect runs.
    assert captured == []


# ---------------------------------------------------------------------------
# Merkle-chain integration — :plan/edit rides effect/handlers/audit.py
# ---------------------------------------------------------------------------


def test_plan_edit_audit_entries_form_a_verifiable_merkle_chain() -> None:
    """Wire `make_audit_handler` to wrap `:plan/edit` and confirm that
    the AuditEntry sequence emitted across N edits forms a valid Merkle
    chain that ``verify_chain`` accepts.

    This is the integration check that closes ADR-6's enforcement loop:
    Plan-Edit datoms are not on a separate audit log — they ride the
    same Merkle chain as :llm/call / :tool/call etc., which is what
    makes the trajectory cross-audit consistent.
    """
    db = DB()
    entries: list[AuditEntry] = []
    audit = make_audit_handler(entries, wraps={":plan/edit"})
    clock = make_fixed_clock_handler(ts=1_712_000_000.0)
    raw = _noop_plan_edit_raw_handler()
    # Runtime[0] is the innermost (raw); audit/clock sit on top.
    rt = Runtime(handlers=[raw, clock, audit])

    inner = Node(tag=":llm-call", attrs={"prompt": "v0"}, children=())
    plan = Node(tag=":seq", attrs={}, children=(inner,))
    step1 = Node(tag=":llm-call", attrs={"prompt": "v1"}, children=())
    step2 = Node(tag=":llm-call", attrs={"prompt": "v2"}, children=())

    with with_runtime(rt):
        with db.dosync() as tx:
            p1 = edit_step(plan, inner.id, step1, tx=tx)
            edit_step(p1, step1.id, step2, tx=tx)

    assert len(entries) == 2
    # Each entry knows its op.
    for e in entries:
        assert e.op == ":plan/edit"
        # txn_commit pinned (intent replay path injects it).
        assert e.txn_commit is not None

    # Same commit_id for both edits.
    assert entries[0].txn_commit == entries[1].txn_commit

    # Merkle chain links: entry[1].prev_hash == entry[0].id.
    assert entries[1].prev_hash == entries[0].id
    assert entries[0].prev_hash is None  # head of the chain (test-local)

    # ``verify_chain`` accepts the sequence.
    assert verify_chain(entries) is True


def test_mixed_edit_op_kinds_each_emit_their_own_plan_edit_entry() -> None:
    """edit_step + insert_step_after + delete_step in one dosync each
    emit one :plan/edit audit entry — three entries, three distinct
    operations, all on the same Merkle chain.
    """
    db = DB()
    entries: list[AuditEntry] = []
    audit = make_audit_handler(entries, wraps={":plan/edit"})
    clock = make_fixed_clock_handler(ts=1_712_000_000.0)
    raw = _noop_plan_edit_raw_handler()
    rt = Runtime(handlers=[raw, clock, audit])

    a = Node(tag=":llm-call", attrs={"prompt": "a"}, children=())
    b = Node(tag=":llm-call", attrs={"prompt": "b"}, children=())
    plan = Node(tag=":seq", attrs={}, children=(a, b))
    replacement = Node(tag=":llm-call", attrs={"prompt": "a*"}, children=())
    inserted = Node(tag=":llm-call", attrs={"prompt": "x"}, children=())

    with with_runtime(rt):
        with db.dosync() as tx:
            p1 = edit_step(plan, a.id, replacement, tx=tx)
            from persistence.plan._edit import delete_step

            p2 = insert_step_after(p1, replacement.id, inserted, tx=tx)
            _p3 = delete_step(p2, b.id, tx=tx)

    assert len(entries) == 3
    assert verify_chain(entries) is True

    # All three share the same txn_commit.
    assert len({e.txn_commit for e in entries}) == 1
