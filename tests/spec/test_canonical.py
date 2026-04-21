"""Test the 10 canonical specs registered at import time.

Each spec:
1. Accepts a hand-crafted valid value
2. Rejects a hand-crafted invalid value with a helpful LLM explanation
3. Round-trips: generate_example(key) -> conform(key, val) is Conformed
"""
from __future__ import annotations

import datetime as dt
import uuid

import pytest

from persistence import spec as S


CANONICAL_KEYS = [
    ":persistence.fact/datom",
    ":persistence.effect/op",
    ":persistence.effect/audit-entry",
    ":persistence.plan/node",
    ":persistence.plan/skill",
    ":persistence.replay/trajectory",
    ":persistence.replay/fact",
    ":persistence.replay/intervention",
    ":persistence.domain/decision",
    ":persistence.domain/wacc-assumption",
]


class TestAllRegistered:
    def test_all_ten_registered_at_import(self):
        missing = [k for k in CANONICAL_KEYS if S.get(k) is None]
        assert missing == [], f"unregistered canonical specs: {missing}"


class TestFactDatom:
    def _good(self):
        return {
            ":datom/e": uuid.uuid4(),
            ":datom/a": ":project/wacc",
            ":datom/v": 0.087,
            ":datom/tx": 17332,
            ":datom/tx-time": dt.datetime.now(dt.timezone.utc),
            ":datom/valid-from": dt.datetime.now(dt.timezone.utc),
            ":datom/valid-to": None,
            ":datom/op": ":assert",
            ":datom/provenance": {":source": ":dfi-agent", ":confidence": 0.82},
            ":datom/invalidated-by": None,
        }

    def test_good_datom_conforms(self):
        assert S.conform(":persistence.fact/datom", self._good()).is_ok

    def test_bad_op_rejected(self):
        d = self._good(); d[":datom/op"] = ":delete"
        assert not S.conform(":persistence.fact/datom", d).is_ok

    def test_missing_required_field(self):
        d = self._good(); del d[":datom/tx"]
        assert not S.conform(":persistence.fact/datom", d).is_ok

    def test_naive_datetime_rejected(self):
        d = self._good(); d[":datom/tx-time"] = dt.datetime(2026, 4, 20, 12)
        assert not S.conform(":persistence.fact/datom", d).is_ok

    # ARIS R1 F2 / R3 F1 — content addressing is load-bearing (paper §4.1);
    # relax the spec to accept the sha256:<hex> content-hash form alongside
    # canonical UUIDs for :datom/e, and alongside ints for :datom/tx. The
    # audit→fact boundary emits sha256 strings for both slots.
    def test_content_hash_e_accepted(self):
        d = self._good()
        d[":datom/e"] = "sha256:" + "a" * 64
        assert S.conform(":persistence.fact/datom", d).is_ok

    def test_content_hash_tx_accepted(self):
        d = self._good()
        d[":datom/tx"] = "sha256:" + "b" * 64
        assert S.conform(":persistence.fact/datom", d).is_ok

    def test_arbitrary_string_e_still_rejected(self):
        # Relaxation is scoped: not-a-UUID-and-not-a-content-hash is still bad.
        d = self._good()
        d[":datom/e"] = "banana"
        assert not S.conform(":persistence.fact/datom", d).is_ok

    def test_arbitrary_string_tx_still_rejected(self):
        d = self._good()
        d[":datom/tx"] = "banana"
        assert not S.conform(":persistence.fact/datom", d).is_ok


class TestEffectOp:
    def test_valid_ops(self):
        for op in [":llm/call", ":tool/call", ":mem/read", ":mem/write",
                   ":decide", ":ask-user", ":emit-artifact", ":sleep",
                   ":random", ":env/read", ":net/fetch", ":secret/use",
                   ":cost/charge", ":clock/now", ":audit/emit"]:
            assert S.conform(":persistence.effect/op", op).is_ok, op

    def test_invalid_op_rejected(self):
        assert not S.conform(":persistence.effect/op", ":nope").is_ok


class TestEffectAuditEntry:
    """Post-ARIS-R3: the spec is aligned with the AuditEntry dataclass
    (P-audit-conform). Keys match the EDN wire form produced by
    :meth:`AuditEntry.to_edn`."""

    def _good(self):
        return {
            ":audit/id": "sha256:cafebabe00010203",
            ":audit/op": ":llm/call",
            ":audit/args-hash": "sha256:abcd",
            ":audit/verdict": ":ok",
            ":audit/latency-ms": 412,
            ":audit/recorded-at": dt.datetime.now(dt.timezone.utc),
            ":audit/handler-chain": [":audit", ":policy", ":raw"],
            ":audit/principal": {":agent": ":bankability"},
            # Optional
            ":audit/policy-id": ":bankability-v3",
            ":audit/run-id": uuid.uuid4(),
            ":audit/prev-hash": None,
        }

    def test_good(self):
        assert S.conform(":persistence.effect/audit-entry", self._good()).is_ok

    def test_verdict_must_be_enum(self):
        e = self._good(); e[":audit/verdict"] = ":maybe"
        assert not S.conform(":persistence.effect/audit-entry", e).is_ok

    def test_op_must_be_in_catalog(self):
        e = self._good(); e[":audit/op"] = ":bogus"
        assert not S.conform(":persistence.effect/audit-entry", e).is_ok

    # ARIS R1 F6 — audit handler factory defaults policy_id to None; making
    # :audit/policy-id required means every entry produced by the effect
    # module's own tests fails conform. Move it to optional.
    def test_policy_id_optional(self):
        e = self._good()
        del e[":audit/policy-id"]
        assert S.conform(":persistence.effect/audit-entry", e).is_ok


class TestPlanNode:
    """Plan node is a vector [:tag {attrs} & children] per agent2 §1
    (ARIS Round 3 P-plan-node migrated from the former map form)."""

    def test_control_node(self):
        node = [":seq", {":id": "sha256:aa"}]
        assert S.conform(":persistence.plan/node", node).is_ok

    def test_leaf_node(self):
        node = [":llm-call", {":id": "sha256:bb",
                              ":model": ":claude-opus"}]
        assert S.conform(":persistence.plan/node", node).is_ok

    def test_unknown_kind_rejected(self):
        node = [":not-a-kind", {":id": "sha256:cc"}]
        assert not S.conform(":persistence.plan/node", node).is_ok


class TestPlanSkill:
    def _good(self):
        return {
            ":skill/name": ":regime-chop-wait",
            ":skill/version": "v3",
            ":skill/parent": "v2",
            ":skill/ast": [":seq", {":id": "sha256:aa"}],
            ":skill/stats": {":uses": 5, ":success": 0.82, ":cost": 0.0031},
            ":skill/embedding": [0.1, 0.2, 0.3],
        }

    def test_good(self):
        assert S.conform(":persistence.plan/skill", self._good()).is_ok

    def test_stats_missing_rejected(self):
        s = self._good(); del s[":skill/stats"]
        assert not S.conform(":persistence.plan/skill", s).is_ok


class TestReplayTrajectory:
    def _good(self):
        return {
            ":trajectory/id": uuid.uuid4(),
            ":trajectory/parent-id": None,
            ":trajectory/branch-point": 0,
            ":trajectory/agent": "adaptive-trader-v2",
            ":trajectory/goal": {":type": ":profit", ":target": 50.0},
            ":trajectory/seeds": {":llm": 8471293, ":tool": 993122, ":env": 552811},
            ":trajectory/started-at": dt.datetime.now(dt.timezone.utc),
            ":trajectory/wall-clock-basis": ":recorded",
            ":trajectory/status": ":completed",
            ":trajectory/outcome": {":pnl": -4.2, ":success?": False},
            ":trajectory/facts": [],
            ":trajectory/hash": "sha256:abc",
            ":trajectory/tags": [":losing"],
        }

    def test_good(self):
        assert S.conform(":persistence.replay/trajectory", self._good()).is_ok

    def test_status_enum(self):
        t = self._good(); t[":trajectory/status"] = ":not-a-status"
        assert not S.conform(":persistence.replay/trajectory", t).is_ok


class TestReplayFact:
    def test_good(self):
        fact = {
            ":step": 0,
            ":t": dt.datetime.now(dt.timezone.utc),
            ":state": {":balance": 400.0},
            ":obs": {":btc-price": 67420},
            ":action": {":type": ":hold"},
        }
        assert S.conform(":persistence.replay/fact", fact).is_ok

    def test_step_must_be_int(self):
        fact = {":step": "0", ":t": dt.datetime.now(dt.timezone.utc),
                ":state": {}, ":obs": {}, ":action": {}}
        assert not S.conform(":persistence.replay/fact", fact).is_ok

    # ARIS R1 F5 — the Python agent reference implementation (spec §7 + the
    # replay module's own conftest) stores state/obs/action with bare string
    # keys (``"balance"``, not ``":balance"``). EDN keywords are a wire-layer
    # convention; Python code uses strings. Relax :state/:obs/:action to
    # ``map_of(str_(), _any_value)``.
    def test_string_keyed_state_accepted(self):
        fact = {
            ":step": 0,
            ":t": dt.datetime.now(dt.timezone.utc),
            ":state": {"step": 0, "balance": 400.0, "position": None, "pnl": 0.0},
            ":obs": {"btc_price": 67420, "btc_atr": 42.0},
            ":action": {"type": "hold"},
        }
        assert S.conform(":persistence.replay/fact", fact).is_ok


class TestReplayIntervention:
    def test_good(self):
        iv = {":step": 42, ":field": ":action", ":new-value": {":type": ":buy"}}
        assert S.conform(":persistence.replay/intervention", iv).is_ok

    def test_field_must_be_kw(self):
        iv = {":step": 42, ":field": "action", ":new-value": 1}
        # :field must be a keyword (leading ":")
        assert not S.conform(":persistence.replay/intervention", iv).is_ok


class TestDomainDecision:
    def _good(self):
        return {":question": "Should we buy?",
                ":options": ["buy", "hold"],
                ":rationale": "Regime is trending and funding is positive",
                ":choice": "buy",
                ":confidence": 0.73}

    def test_good(self):
        assert S.conform(":persistence.domain/decision", self._good()).is_ok

    def test_empty_rationale_rejected(self):
        d = self._good(); d[":rationale"] = ""
        err = S.conform(":persistence.domain/decision", d)
        assert not err.is_ok

    def test_confidence_out_of_bounds(self):
        d = self._good(); d[":confidence"] = 1.5
        assert not S.conform(":persistence.domain/decision", d).is_ok

    def test_llm_explanation_is_self_healing(self):
        """The LLM-friendly explanation must mention the field name, violated
        constraint, and a 'Fix:' clause (spec says so)."""
        d = self._good(); d[":rationale"] = ""
        msg = S.explain_for_llm(":persistence.domain/decision", d)
        assert ":rationale" in msg or "rationale" in msg.lower()
        assert "Fix:" in msg
        # and the constraint is named
        assert "non-empty" in msg.lower() or "empty" in msg.lower()


class TestDomainWaccAssumption:
    def _good(self):
        return {":project-id": "p-042",
                ":percent": 0.087,
                ":source": ":dfi-agent",
                ":confidence": 0.82}

    def test_good(self):
        assert S.conform(":persistence.domain/wacc-assumption", self._good()).is_ok

    def test_percent_above_one_rejected(self):
        a = self._good(); a[":percent"] = 1.5
        assert not S.conform(":persistence.domain/wacc-assumption", a).is_ok

    def test_percent_negative_rejected(self):
        a = self._good(); a[":percent"] = -0.01
        assert not S.conform(":persistence.domain/wacc-assumption", a).is_ok


class TestRoundTrip:
    """For every canonical spec: generate -> conform must succeed."""

    @pytest.mark.parametrize("key", CANONICAL_KEYS)
    def test_round_trip(self, key):
        for _ in range(10):
            val = S.generate_example(key)
            result = S.conform(key, val)
            assert result.is_ok, (
                f"round-trip failed for {key}:\n"
                f"  generated: {val!r}\n"
                f"  error: {S.explain_for_llm(key, val)}"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
