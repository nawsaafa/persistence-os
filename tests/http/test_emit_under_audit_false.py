"""Phase 2.1c.6 G7 — emit under audit=False raises Unhandled.

When a substrate is opened with audit=False, the canonical audit stack
is absent and :claim/emit perform has no handler — Unhandled fires.
Documents the expectation rather than silently passing.
"""
from __future__ import annotations


def test_emit_under_audit_false_raises_unhandled():
    """Substrate with audit=False + :claim/emit perform → Unhandled."""
    from persistence.effect.runtime import Unhandled
    from persistence.sdk import Substrate
    import pytest

    s = Substrate.open("memory", audit=False)
    try:
        with pytest.raises(Unhandled, match=":claim/emit"):
            s.effect.perform(":claim/emit", {
                "claim_ids": ["test-claim-1"],
                "tx": 0,
                "kind_counts": {":claim/tool-exec": 1},
            })
    finally:
        s.close()
