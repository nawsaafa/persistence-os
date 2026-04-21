"""Op-name format invariants (ARIS Round 3 P-op-invariants).

Every op name in the effect catalog and every op stored on an
``AuditEntry`` must be a well-formed EDN keyword of the shape
``:namespace/op`` with:

- a leading colon,
- exactly one forward slash,
- no literal ``.`` before the slash (the audit encoding uses
  ``/ → .`` to make the inner slash fit a single-slash EDN keyword, and
  a literal dot in the input would collide with that encoding).

These invariants eliminate two latent defects:

1. The ``/`` → ``.`` encoding in ``audit_entry_to_datom`` was
   acknowledged in the code as "co-inverse only because no op in the
   catalog contains a literal ``.``" (R1 N2 / R3 N4).
2. ``AuditEntry.op`` previously had no ``__post_init__`` format
   invariant, so bare-string ops like ``"llm/call"`` could sneak in.
"""
from __future__ import annotations

import re

import pytest

from persistence.effect.catalog import CATALOG, OP_NAMES
from persistence.effect.handlers.audit import AuditEntry


# ---------------------------------------------------------------------------
# Catalog-wide invariants
# ---------------------------------------------------------------------------


class TestCatalogLint:
    def test_every_op_has_leading_colon(self):
        bad = [op for op in OP_NAMES if not op.startswith(":")]
        assert not bad, f"ops missing leading colon: {bad}"

    def test_every_op_has_at_most_one_slash(self):
        """Ops may be a bare keyword (``:decide``) or a namespaced keyword
        (``:llm/call``). More than one slash breaks the audit datom's
        ``/ → .`` single-slash encoding.
        """
        bad = [op for op in OP_NAMES if op.count("/") > 1]
        assert not bad, (
            f"ops with > 1 forward slash break the audit encoding: {bad}"
        )

    def test_no_op_contains_literal_dot(self):
        """A literal ``.`` in an op name collides with the audit datom's
        ``/ → .`` encoding, which would make
        ``datom_to_audit_entry ∘ audit_entry_to_datom`` non-identity.
        """
        bad = [op for op in OP_NAMES if "." in op]
        assert not bad, (
            f"ops with literal dot — collides with audit encoding: {bad}"
        )

    def test_catalog_keys_match_opspec_name(self):
        """Catalog keys and OpSpec.name must agree."""
        for k, v in CATALOG.items():
            assert k == v.name, f"catalog key {k!r} != OpSpec.name {v.name!r}"


# ---------------------------------------------------------------------------
# AuditEntry.__post_init__ invariant
# ---------------------------------------------------------------------------


class TestAuditEntryOpInvariant:
    def _kwargs(self, **overrides):
        d = dict(
            id="sha256:aa",
            prev_hash=None,
            op=":llm/call",
            args_hash="sha256:bb",
            verdict="ok",
            latency_ms=1,
            recorded_at=1_700_000_000.0,
        )
        d.update(overrides)
        return d

    def test_well_formed_op_accepted(self):
        AuditEntry(**self._kwargs(op=":llm/call"))  # no raise

    def test_missing_leading_colon_rejected(self):
        with pytest.raises(ValueError, match="leading colon"):
            AuditEntry(**self._kwargs(op="llm/call"))

    def test_bare_keyword_op_accepted(self):
        """Ops may be bare keywords (``:decide``) or namespaced
        (``:llm/call``). The ``/ → .`` audit encoding is trivial for
        bare keywords because there's no slash to encode."""
        AuditEntry(**self._kwargs(op=":decide"))  # no raise

    def test_multiple_slashes_rejected(self):
        with pytest.raises(ValueError, match="at most one"):
            AuditEntry(**self._kwargs(op=":llm/call/extra"))

    def test_literal_dot_rejected(self):
        with pytest.raises(ValueError, match="literal dot|literal `\\.`"):
            AuditEntry(**self._kwargs(op=":llm.foo/call"))

    def test_empty_op_rejected(self):
        with pytest.raises(ValueError):
            AuditEntry(**self._kwargs(op=""))
