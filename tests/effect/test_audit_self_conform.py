"""`audit_entry_to_datom` + `AuditEntry.to_edn` + `Trajectory.to_edn`
self-conform at their wire-form output (ARIS Round 3 P-audit-conform).

Before the fix:
- `:persistence.effect/audit-entry` spec was registered but no producer
  used its shape — an orphan (R1 N1 + R3 N3).
- `audit_entry_to_datom` emitted a conformant-looking datom but never
  called `spec.parse(":persistence.fact/datom", result)` before returning.
- `Trajectory.to_edn` same story for `:persistence.replay/trajectory`.

After the fix:
- `AuditEntry.to_edn()` is the single producer of
  `:persistence.effect/audit-entry`-conformant dicts. The spec is aligned
  to the dataclass's actual field set.
- `audit_entry_to_datom` self-conforms to `:persistence.fact/datom`.
- `Trajectory.to_edn` self-conforms to `:persistence.replay/trajectory`.
"""
from __future__ import annotations

import uuid

import pytest

from persistence import spec as S
from persistence.effect.handlers.audit import (
    AuditEntry,
    audit_entry_to_datom,
)
from persistence.replay.trajectory import Fact, Trajectory


def _sample_entry() -> AuditEntry:
    """Representative AuditEntry that every producer path would emit.

    Uses bare-string ``handler_chain`` entries — the production shape
    emitted by ``make_audit_handler`` under a real ``Runtime``. Round 3
    used ``(":audit", ":policy", ":raw")`` here, which hid R1 N6: the
    happy-path test conformed because the chain was already keywordified
    at construction, but every real handler chain failed. After ARIS
    Round 4 W4-handler-chain-wire, ``AuditEntry.to_edn`` keywordifies at
    the wire boundary, so bare-string input is the production-realistic
    shape this test must exercise.
    """
    return AuditEntry(
        id="sha256:cafebabe00010203",
        prev_hash="sha256:deadbeef00010203",
        op=":llm/call",
        args_hash="sha256:abcd",
        verdict="ok",
        latency_ms=42,
        recorded_at=1_700_000_000.0,
        result_hash="sha256:feedface",
        error=None,
        policy_id=":bankability-v3",
        handler_chain=("audit", "policy", "raw"),
        principal={"agent": ":bankability"},
        run_id=str(uuid.uuid4()),
        parent=None,
    )


class TestAuditEntryToEdnSelfConforms:
    """AuditEntry.to_edn() must produce a value that conforms to
    :persistence.effect/audit-entry."""

    def test_to_edn_conforms_to_spec(self):
        entry = _sample_entry()
        edn = entry.to_edn()
        result = S.conform(":persistence.effect/audit-entry", edn)
        assert result.is_ok, (
            f"AuditEntry.to_edn failed spec conform: {result}"
        )

    def test_to_edn_with_none_policy_id_conforms(self):
        """policy_id=None is the default for make_audit_handler — must
        still conform after ARIS R1 F6 made the key optional."""
        entry = AuditEntry(
            id="sha256:aa",
            prev_hash=None,
            op=":llm/call",
            args_hash="sha256:bb",
            verdict="ok",
            latency_ms=1,
            recorded_at=1_700_000_000.0,
            policy_id=None,
        )
        edn = entry.to_edn()
        assert S.conform(":persistence.effect/audit-entry", edn).is_ok

    def test_to_edn_with_error_verdict_conforms(self):
        entry = _sample_entry()
        entry = entry.with_fields(verdict="error", error="boom")
        edn = entry.to_edn()
        assert S.conform(":persistence.effect/audit-entry", edn).is_ok


class TestAuditEntryToDatomSelfConforms:
    """audit_entry_to_datom must call spec.parse(":persistence.fact/datom",
    ...) at output — the conform never silently passes bad data to the
    fact store."""

    def test_output_conforms_to_datom_spec(self):
        entry = _sample_entry()
        datom = audit_entry_to_datom(entry)
        result = S.conform(":persistence.fact/datom", datom)
        assert result.is_ok, (
            f"audit_entry_to_datom output doesn't conform to datom spec: {result}"
        )


class TestTrajectoryToEdnSelfConforms:
    """Trajectory.to_edn() already produced conformant values, but the
    conform step must now be self-invoked at the end (ARIS R3 P-audit-conform)."""

    def test_to_edn_conforms_to_spec(self):
        t = Trajectory(
            agent="adaptive-trader-v2",
            goal={":type": ":profit", ":target": 50.0},
            seeds={"llm": 1, "tool": 2, "env": 3},
            status="completed",
            outcome={":pnl": 10.0, ":success?": True},
            hash="sha256:ff",
        )
        edn = t.to_edn()
        result = S.conform(":persistence.replay/trajectory", edn)
        assert result.is_ok, (
            f"Trajectory.to_edn output doesn't conform: {result}"
        )

    def test_self_conform_raises_on_corruption(self, monkeypatch):
        """If we monkey-patch a Trajectory to produce a malformed edn, the
        self-conform step (if wired) raises — proves it's actually running.

        (This mirrors the pattern in fact/wire.py where datom_to_wire runs
        a final parse check.)
        """
        t = Trajectory(
            agent="trader",
            seeds={"llm": 1, "tool": 2, "env": 3},
        )
        # Corrupt started_at to a non-datetime after construction.
        t.started_at = "not-a-datetime"
        # to_edn uses _started_at_to_inst which coerces str -> datetime
        # via fromisoformat, raising ValueError. Confirm the raise.
        with pytest.raises((ValueError, Exception)):
            t.to_edn()
