"""ARIS Round 4 W4-handler-chain-wire — keywordify ``handler_chain``
entries at the AuditEntry wire boundary.

Round 3 surfaced R1 N6: production code registers handlers with bare-string
names (``"audit"``, ``"llm"``, ``"tool"``, ``"retry"``, ...), so any
``AuditEntry`` produced under a real ``Runtime`` has a bare-string
``handler_chain``. The self-conform gate on ``AuditEntry.to_edn`` then
rejected every such entry because ``:audit/handler-chain`` in the spec
requires a sequence of EDN keywords.

The happy-path test in ``test_audit_self_conform.py`` pre-keywordifies the
chain (``handler_chain=(":audit", ":policy", ":raw")``), hiding the bug.
This test exercises the production-realistic shape — bare strings in,
conform passes — and locks in the W4 fix.
"""
from __future__ import annotations

import uuid

from persistence import spec as S
from persistence.effect.handlers.audit import AuditEntry


def _bare_chain_entry(handler_chain=("audit", "policy", "raw")) -> AuditEntry:
    """Representative AuditEntry with production-shape (bare-string)
    handler_chain. Everything else matches the spec's required shape.
    """
    return AuditEntry(
        id="sha256:" + "a" * 64,
        prev_hash=None,
        op=":llm/call",
        args_hash="sha256:" + "b" * 64,
        verdict="ok",
        latency_ms=42,
        recorded_at=1_700_000_000.0,
        result_hash="sha256:" + "c" * 64,
        error=None,
        policy_id=None,
        handler_chain=tuple(handler_chain),
        principal={"agent": ":bankability"},
        run_id=str(uuid.uuid4()),
        parent=None,
    )


class TestAuditEntryToEdnBareStringChain:
    """R1 N6 closure — bare-string handler_chain must survive the wire
    boundary. Keywordification happens in ``to_edn`` (serialisation-time,
    symmetric with ``_principal_to_keyword_map``).
    """

    def test_to_edn_on_real_bare_string_chain_conforms(self):
        """This test WILL FAIL on main before W4-handler-chain-wire —
        ``to_edn`` emitted ``list(self.handler_chain)`` verbatim and
        the self-conform gate rejected ``"audit"`` as a non-keyword.
        """
        entry = _bare_chain_entry()
        edn = entry.to_edn()
        result = S.conform(":persistence.effect/audit-entry", edn)
        assert result.is_ok, (
            f"AuditEntry.to_edn on bare-string handler_chain failed "
            f"spec conform: {result}"
        )

    def test_wire_chain_entries_are_keywordified(self):
        """Explicit shape check — every entry in the wire-form
        ``:audit/handler-chain`` starts with ':' after keywordification."""
        entry = _bare_chain_entry()
        edn = entry.to_edn()
        wire_chain = edn[":audit/handler-chain"]
        assert isinstance(wire_chain, list)
        assert len(wire_chain) == 3
        for h in wire_chain:
            assert isinstance(h, str), f"chain entry is not str: {h!r}"
            assert h.startswith(":"), f"chain entry not keywordified: {h!r}"

    def test_to_edn_idempotent_on_prekeyworded_chain(self):
        """A chain that was already keywordified (e.g. from an explicit
        test fixture) must round-trip without double-colons."""
        entry = _bare_chain_entry(handler_chain=(":audit", ":policy"))
        edn = entry.to_edn()
        wire_chain = edn[":audit/handler-chain"]
        assert wire_chain == [":audit", ":policy"], (
            f"double-keywordification: {wire_chain}"
        )
        assert S.conform(":persistence.effect/audit-entry", edn).is_ok

    def test_to_edn_handles_mixed_chain(self):
        """A chain with both bare and pre-keyworded entries (possible in
        polyglot environments) must produce a uniformly-keyworded wire."""
        entry = _bare_chain_entry(handler_chain=("audit", ":policy", "raw"))
        edn = entry.to_edn()
        wire_chain = edn[":audit/handler-chain"]
        assert wire_chain == [":audit", ":policy", ":raw"]
        assert S.conform(":persistence.effect/audit-entry", edn).is_ok

    def test_to_edn_preserves_empty_chain(self):
        """Edge case: a top-of-stack audit entry has an empty chain."""
        entry = _bare_chain_entry(handler_chain=())
        edn = entry.to_edn()
        assert edn[":audit/handler-chain"] == []
        assert S.conform(":persistence.effect/audit-entry", edn).is_ok
