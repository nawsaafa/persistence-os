"""Canonical audit-handler stack tests — Phase 2.0d W1 + Phase 2.1c.6 + Phase 2.2a extensions.

Tests pin the membership of CANONICAL_AUDIT_WRAPPED_OPS and
CANONICAL_AUDIT_RAW_OPS, verifying that new audit-emitting ops are
registered when their phase ships.
"""
from __future__ import annotations


def test_canonical_audit_wrapped_ops_includes_2_1c_6_ops():
    """Phase 2.1c.6: :claim/emit and :blob/put are audit-anchor ops."""
    from persistence.effect._audit_stack import CANONICAL_AUDIT_WRAPPED_OPS

    wrapped = frozenset(CANONICAL_AUDIT_WRAPPED_OPS)
    assert ":claim/emit" in wrapped, (
        "2.1c.6 contract: :claim/emit must be in CANONICAL_AUDIT_WRAPPED_OPS "
        "to anchor the canonical audit chain on each claim emit."
    )
    assert ":blob/put" in wrapped, (
        "2.1c.6 contract: :blob/put must be in CANONICAL_AUDIT_WRAPPED_OPS "
        "to anchor the canonical audit chain on each blob put."
    )


def test_canonical_audit_raw_ops_includes_2_1c_6_ops():
    """Phase 2.1c.6: both new ops are audit-only (raw terminator covers them)."""
    from persistence.effect._audit_stack import CANONICAL_AUDIT_RAW_OPS

    raw = frozenset(CANONICAL_AUDIT_RAW_OPS)
    assert ":claim/emit" in raw, (
        "2.1c.6 contract: :claim/emit is audit-only — raw terminator must "
        "cover it (returns None; the request datom IS the audit signal)."
    )
    assert ":blob/put" in raw, (
        "2.1c.6 contract: :blob/put is audit-only — raw terminator must "
        "cover it (the request datom IS the audit signal)."
    )


def test_canonical_audit_stack_covers_phase_2_ops():
    """Drift-pin: every wrapped audit op shipped through Phase 2.0a-2.2b is here.

    Phase 2.2a additions: :fs/read, :fs/write, :fs/glob, :fs/grep, :shell/exec.
    Phase 2.2b additions: :code/run, :git/diff, :git/status, :git/log, :git/commit.
    These are substantive-return ops — wrapped by audit middleware but NOT in
    CANONICAL_AUDIT_RAW_OPS (their bottom-of-stack handler IS the substantive
    handler installed via s.effect.install_handler at position="bottom").
    """
    from persistence.effect._audit_stack import (
        CANONICAL_AUDIT_RAW_OPS,
        CANONICAL_AUDIT_WRAPPED_OPS,
    )

    expected_wrapped = {
        # Phase 2.0a / 2.0b / 2.0c / 2.0c-ext / 2.1b / 2.1c.6
        ":plan/edit", ":code/exec",
        ":fork/probe", ":fork/branch", ":fork/score", ":fork/chosen",
        ":fold/chosen", ":llm/call",
        ":claim/emit", ":blob/put",
        # Phase 2.2a additions:
        ":fs/read", ":fs/write", ":fs/glob", ":fs/grep", ":shell/exec",
        # Phase 2.2b additions:
        ":code/run", ":git/diff", ":git/status", ":git/log", ":git/commit",
        # Phase 2.3c.1 additions:
        ":skill/define", ":skill/lookup",
    }
    assert set(CANONICAL_AUDIT_WRAPPED_OPS) == expected_wrapped

    # The 2.2a fs/shell ops are NOT in RAW_OPS — they have substantive returns.
    new_ops = {":fs/read", ":fs/write", ":fs/glob", ":fs/grep", ":shell/exec"}
    assert not (new_ops & set(CANONICAL_AUDIT_RAW_OPS)), \
        "fs/shell ops MUST NOT be in CANONICAL_AUDIT_RAW_OPS"

    # The 2.2b :code/run + :git/* ops are NOT in RAW_OPS either — :code/run
    # returns a CodeRunResult, and :git/* clauses internally dispatch through
    # :shell/exec (substantive return). Bottom-of-stack handlers are installed
    # separately via s.effect.install_handler(..., position="bottom").
    new_ops_2_2b = {":code/run", ":git/diff", ":git/status", ":git/log", ":git/commit"}
    assert not (new_ops_2_2b & set(CANONICAL_AUDIT_RAW_OPS)), \
        ":code/run and :git/* ops MUST NOT be in CANONICAL_AUDIT_RAW_OPS"

    # Phase 2.3c.1: :skill/define + :skill/lookup are substantive-return
    # (define returns {:skill-id, :plan-id}; lookup returns
    # {:plan-edn, :promotion-id, :plan-id}). Bottom-of-stack handler
    # installed separately via s.effect.install_handler(..., position="bottom").
    new_ops_2_3c_1 = {":skill/define", ":skill/lookup"}
    assert not (new_ops_2_3c_1 & set(CANONICAL_AUDIT_RAW_OPS)), \
        ":skill/* ops MUST NOT be in CANONICAL_AUDIT_RAW_OPS (substantive-return per LD5)"
