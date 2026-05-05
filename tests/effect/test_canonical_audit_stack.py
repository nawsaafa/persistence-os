"""Canonical audit-handler stack tests — Phase 2.0d W1 + Phase 2.1c.6 extensions.

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
