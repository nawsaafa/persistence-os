"""ARIS Round 4 nice-to-have — ``AuditEntry.from_edn`` inverse (closes R3 N6).

Round 3 surfaced R3 N6 MINOR: ``AuditEntry.to_edn`` is the single producer
of ``:persistence.effect/audit-entry`` wire form but there's no symmetric
inverse. The available inverse goes datom→entry (``datom_to_audit_entry``),
not audit-entry-wire→entry. Phase-2 regulator-replay needs to reconstruct
chains from archived JSON via the audit-entry wire form directly.

This test locks in the inverse: ``from_edn(to_edn(e)) == e`` for the
fields that survive the wire encoding (principal keys, handler chain,
verdict — all of which are keywordified on the wire and restored on the
Python side).
"""
from __future__ import annotations

import uuid

import pytest

from persistence.effect.handlers.audit import AuditEntry


def _entry(**overrides) -> AuditEntry:
    base = dict(
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
        handler_chain=("audit", "policy", "raw"),
        principal={"agent": "bankability"},
        run_id=str(uuid.uuid4()),
        parent=None,
    )
    base.update(overrides)
    return AuditEntry(**base)


class TestAuditEntryFromEdnRoundTrip:
    """``from_edn(to_edn(e))`` must reconstruct an equivalent ``AuditEntry``
    — bare-string handler chain, bare-string principal keys, Python-side
    verdict strings (not EDN keywords).
    """

    def test_round_trip_preserves_all_fields(self):
        original = _entry()
        restored = AuditEntry.from_edn(original.to_edn())

        assert restored.id == original.id
        assert restored.prev_hash == original.prev_hash
        assert restored.op == original.op
        assert restored.args_hash == original.args_hash
        assert restored.verdict == original.verdict
        assert restored.latency_ms == original.latency_ms
        # recorded_at round-trips via datetime → float conversion.
        assert abs(restored.recorded_at - original.recorded_at) < 1e-6
        assert restored.result_hash == original.result_hash
        assert restored.policy_id == original.policy_id
        assert restored.handler_chain == original.handler_chain
        assert restored.principal == original.principal
        assert restored.run_id == original.run_id

    def test_handler_chain_restored_to_bare_strings(self):
        """The wire form has keyworded chain entries; from_edn strips
        them back to Python-native bare strings, so round-trip equality
        holds."""
        original = _entry(handler_chain=("audit", "llm"))
        wire = original.to_edn()
        # wire has keyworded form
        assert wire[":audit/handler-chain"] == [":audit", ":llm"]
        # from_edn restores bare form
        restored = AuditEntry.from_edn(wire)
        assert restored.handler_chain == ("audit", "llm")

    def test_principal_keys_restored_to_bare_strings(self):
        """Principal keys are keywordified on the wire; restored bare
        on the Python side."""
        original = _entry(principal={"agent": "bankability", "team": "dfi"})
        restored = AuditEntry.from_edn(original.to_edn())
        # Keys are strings without leading colons on the Python side.
        for k in restored.principal.keys():
            assert not k.startswith(":")
        assert restored.principal == {"agent": "bankability", "team": "dfi"}

    def test_verdict_restored_to_python_form(self):
        """Verdict is ``"ok"`` on Python, ``":ok"`` on the wire."""
        original = _entry(verdict="ok")
        wire = original.to_edn()
        assert wire[":audit/verdict"] == ":ok"
        restored = AuditEntry.from_edn(wire)
        assert restored.verdict == "ok"

    def test_round_trip_preserves_error_entry(self):
        original = _entry(verdict="error", error="boom", result_hash=None)
        restored = AuditEntry.from_edn(original.to_edn())
        assert restored.verdict == "error"
        assert restored.error == "boom"
        assert restored.result_hash is None
