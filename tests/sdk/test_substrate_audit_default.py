"""Substrate.open default audit-stack install tests — Phase 2.0d W1 / M2.

Phase 2.0d W1 (R2 MAJOR M2 fix): :meth:`persistence.sdk.Substrate.open`
installs the canonical audit handler stack
(:func:`persistence.effect.canonical_audit_stack`) by default. Opt-out
via ``Substrate.open(uri, audit=False)``. This test module pins that
contract per ADR-5 capability-denial-default + ADR-6 plan-edit audit
invariant + design § 3.7 per-effect replay table.

The substrate-default install closes the M2 footgun where
``:plan/edit`` / ``:fork/*`` / ``:code/exec`` / ``:fold/chosen`` audit
intents could be silently dropped at commit time when no runtime was
active. With the W1 wiring + the
:class:`persistence.txn.AuditStackMissing` fail-fast guard in
:func:`persistence.txn.transaction._replay_effect_intents`, that path
is no longer reachable through ``Substrate.open()``.
"""
from __future__ import annotations

import pytest

from persistence.effect import AuditEntry
from persistence.sdk import Substrate
from persistence.txn import AuditStackMissing


# ---------------------------------------------------------------------------
# Default audit stack is installed
# ---------------------------------------------------------------------------


def test_substrate_open_installs_audit_by_default() -> None:
    """``Substrate.open(uri)`` (default ``audit=True``) installs the
    canonical audit stack at construction time, so ``:plan/edit``
    intents queued in a dosync are replayed against the audit
    middleware and produce a verifiable Merkle chain.
    """
    from persistence.plan import Node, edit_step

    s = Substrate.open("memory")
    try:
        inner = Node(tag=":llm-call", attrs={"prompt": "v0"}, children=())
        plan = Node(tag=":seq", attrs={}, children=(inner,))
        new_op = Node(tag=":llm-call", attrs={"prompt": "v1"}, children=())

        with s.txn.dosync() as tx:
            edit_step(plan, inner.id, new_op, tx=tx)

        # Audit chain has the :plan/edit entry.
        chain = [e for e in s.audit.entries() if isinstance(e, AuditEntry)]
        assert len(chain) == 1
        assert chain[0].op == ":plan/edit"
        # verify_chain accepts the chain.
        assert s.audit.verify_chain() is True
    finally:
        s.close()


def test_substrate_open_audit_false_opts_out() -> None:
    """``Substrate.open(uri, audit=False)`` does NOT install the audit
    stack. Queueing a ``:plan/edit`` intent inside a dosync will trip
    :class:`AuditStackMissing` at commit time per the W1 fail-fast
    guard.
    """
    from persistence.plan import Node, edit_step

    s = Substrate.open("memory", audit=False)
    try:
        inner = Node(tag=":llm-call", attrs={"prompt": "v0"}, children=())
        plan = Node(tag=":seq", attrs={}, children=(inner,))
        new_op = Node(tag=":llm-call", attrs={"prompt": "v1"}, children=())

        with pytest.raises(AuditStackMissing):
            with s.txn.dosync() as tx:
                edit_step(plan, inner.id, new_op, tx=tx)
    finally:
        s.close()


def test_substrate_open_audit_false_no_intents_is_fine() -> None:
    """``Substrate.open(uri, audit=False)`` only fails when the dosync
    actually queues an audit-emitting intent. A "raw" dosync that only
    writes via ``tx.assoc`` does not need a runtime.
    """
    s = Substrate.open("memory", audit=False)
    try:
        r = s._db.new_ref(0)
        with s.txn.dosync() as tx:
            tx.assoc(r, 7)
        # Commit succeeded; no AuditStackMissing.
        view = s._db.as_of(s._db._clock())
        assert view.entity(r.eid) == {"value": 7}
    finally:
        s.close()


# ---------------------------------------------------------------------------
# All four canonical audit ops emit under the default stack
# ---------------------------------------------------------------------------


def test_plan_edit_emits_audit_under_default_stack() -> None:
    """Round-trip: ``s.plan.edit_step(...)`` → commit →
    ``s.audit.verify_chain()`` accepts the recovered ``:plan/edit``
    entry.
    """
    from persistence.plan import Node, edit_step

    s = Substrate.open("memory")
    try:
        inner = Node(tag=":llm-call", attrs={"prompt": "v0"}, children=())
        plan = Node(tag=":seq", attrs={}, children=(inner,))
        new_op = Node(tag=":llm-call", attrs={"prompt": "v1"}, children=())

        with s.txn.dosync() as tx:
            edit_step(plan, inner.id, new_op, tx=tx)

        plan_edit_entries = [
            e for e in s.audit.entries()
            if isinstance(e, AuditEntry) and e.op == ":plan/edit"
        ]
        assert len(plan_edit_entries) == 1
        assert s.audit.verify_chain() is True
    finally:
        s.close()


def test_fork_emits_audit_under_default_stack() -> None:
    """``s.txn.fork(items, fn, choose, ...)`` emits the canonical
    4-datom audit shape (probe + branch×N + score×N + chosen) under
    the substrate-default stack; ``verify_chain`` accepts the chain.
    """
    s = Substrate.open("memory")
    try:
        with s.txn.dosync() as tx:
            s.txn.fork(
                items=[1, 5, 3],
                fn=lambda state, item: state + item,
                choose=lambda branches: max(
                    range(len(branches)),
                    key=lambda i: branches[i].branch_state,
                ),
                seed=0,
                tx=tx,
            )

        chain = [e for e in s.audit.entries() if isinstance(e, AuditEntry)]
        # 1 probe + 3 branch + 3 score + 1 chosen = 8.
        ops = [e.op for e in chain]
        assert ops == [
            ":fork/probe",
            ":fork/branch", ":fork/branch", ":fork/branch",
            ":fork/score", ":fork/score", ":fork/score",
            ":fork/chosen",
        ]
        assert s.audit.verify_chain() is True
    finally:
        s.close()


def test_code_exec_emits_audit_under_default_stack() -> None:
    """``persistence.effect.exec_code(...)`` inside a dosync emits a
    ``:code/exec`` audit entry under the substrate-default stack;
    ``verify_chain`` accepts the chain.
    """
    import sys

    from persistence.effect import exec_code

    if sys.platform.startswith("win"):  # pragma: no cover
        pytest.skip("subprocess sandbox uses POSIX setrlimit")

    s = Substrate.open("memory")
    try:
        with s.txn.dosync() as tx:
            result = exec_code("print(2 + 2)", tx=tx)
        assert result.exit_code == 0
        assert "4" in result.stdout

        chain = [
            e for e in s.audit.entries()
            if isinstance(e, AuditEntry) and e.op == ":code/exec"
        ]
        assert len(chain) == 1
        assert s.audit.verify_chain() is True
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Lifecycle — close releases the runtime token
# ---------------------------------------------------------------------------


def test_substrate_close_releases_audit_runtime() -> None:
    """After ``s.close()``, the substrate's runtime is released from
    the active ContextVar so a subsequent ``Substrate.open()`` does
    not see the prior substrate's runtime leaking through.
    """
    from persistence.effect.runtime import _active as _effect_active

    s1 = Substrate.open("memory")
    rt1 = _effect_active.get()
    assert rt1 is not None
    s1.close()
    # After close, the active runtime should be back to None (or
    # whatever was active before s1 opened).
    assert _effect_active.get() is None

    # A fresh substrate gets its own fresh runtime.
    s2 = Substrate.open("memory")
    try:
        rt2 = _effect_active.get()
        assert rt2 is not None
        assert rt2 is not rt1
    finally:
        s2.close()


def test_substrate_context_manager_audit_default() -> None:
    """``with Substrate.open("memory") as s:`` works — the audit
    stack is installed for the duration of the ``with`` block, then
    released on ``__exit__``.
    """
    from persistence.effect.runtime import _active as _effect_active

    with Substrate.open("memory") as s:
        from persistence.plan import Node, edit_step

        inner = Node(tag=":llm-call", attrs={"prompt": "v0"}, children=())
        plan = Node(tag=":seq", attrs={}, children=(inner,))
        new_op = Node(tag=":llm-call", attrs={"prompt": "v1"}, children=())

        with s.txn.dosync() as tx:
            edit_step(plan, inner.id, new_op, tx=tx)

        chain = [e for e in s.audit.entries() if isinstance(e, AuditEntry)]
        assert len(chain) == 1
        assert s.audit.verify_chain() is True

    # After context exit, runtime is released.
    assert _effect_active.get() is None
