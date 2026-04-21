"""Module 6 Spec demo.

Run as:

    python -m persistence.spec.demo

Prints ten valid and ten invalid examples across the canonical specs, with
the LLM-friendly explanations for the invalid ones. Use this as a smoke test
and as a reference for downstream modules (Fact, Effect, Plan, Replay) when
building their own values.
"""
from __future__ import annotations

import datetime as dt
import uuid

from . import (
    conform,
    explain_for_llm,
    generate_example,
    registered_keys,
)
from ._canonical import CANONICAL_SPECS


# ---------------------------------------------------------------------------
# Demo corpus — hand-crafted invalid values per canonical spec. Pairs with the
# generate_example() round-trip for the valid side.
# ---------------------------------------------------------------------------


def _datom_bad():
    return {
        ":datom/e": "not-a-uuid",  # bad: should be UUID
        ":datom/a": ":project/wacc",
        ":datom/v": 0.087,
        ":datom/tx": 1,
        ":datom/tx-time": dt.datetime.now(dt.timezone.utc),
        ":datom/valid-from": dt.datetime.now(dt.timezone.utc),
        ":datom/valid-to": None,
        ":datom/op": ":delete",  # bad: not in {:assert, :retract}
        ":datom/provenance": {},
    }


def _effect_op_bad():
    return ":drop-table"


def _audit_entry_bad():
    return {
        ":audit/id": uuid.uuid4(),
        ":audit/run-id": uuid.uuid4(),
        ":audit/parent": None,
        ":audit/op": ":llm/call",
        ":audit/args": {},
        ":audit/args-hash": "sha256:aa",
        ":audit/verdict": ":maybe",  # bad
        ":audit/policy-id": ":p",
        ":audit/result": None,
        ":audit/latency-ms": 10,
        ":audit/cost": {":units": 0.01, ":currency": ":usd"},
        ":audit/valid-from": dt.datetime.now(dt.timezone.utc),
        ":audit/recorded-at": dt.datetime.now(dt.timezone.utc),
        ":audit/handler-chain": [":audit"],
        ":audit/principal": {},
        ":audit/prev-hash": None,
    }


def _plan_node_bad():
    # Vector form — bad tag (":mutate" isn't a PLAN_NODE_KIND) and bad :id
    # (not a sha256 hex). Both fail the spec (ARIS Round 3 P-plan-node).
    return [":mutate", {":id": "not-a-hash"}]


def _plan_skill_bad():
    return {
        ":skill/name": ":regime",
        ":skill/version": "3",  # bad: must be 'v3'
        ":skill/ast": [":seq", {":id": "sha256:aa"}],
        ":skill/stats": {":uses": 1, ":success": 0.5, ":cost": 0.01},
    }


def _trajectory_bad():
    return {
        ":trajectory/id": uuid.uuid4(),
        ":trajectory/parent-id": None,
        ":trajectory/branch-point": 0,
        ":trajectory/agent": "trader",
        ":trajectory/goal": {":type": ":profit"},
        ":trajectory/seeds": {":llm": 1, ":tool": 2},  # missing :env
        ":trajectory/started-at": dt.datetime.now(dt.timezone.utc),
        ":trajectory/wall-clock-basis": ":recorded",
        ":trajectory/status": ":running",
        ":trajectory/outcome": {},
        ":trajectory/facts": [],
        ":trajectory/hash": "sha",
    }


def _replay_fact_bad():
    return {":step": "0", ":t": "not-a-datetime",
            ":state": {}, ":obs": {}, ":action": {}}


def _replay_intervention_bad():
    return {":step": 10, ":field": "action", ":new-value": 1}  # :field must be kw


def _decision_bad():
    return {":question": "Buy?", ":options": ["y", "n"],
            ":rationale": "",  # empty → LLM fix-hint trigger
            ":choice": "y", ":confidence": 1.5}


def _wacc_bad():
    return {":project-id": "p-042", ":percent": 15.0,  # > 1
            ":source": "not-a-keyword", ":confidence": 0.9}


INVALID_SAMPLES = {
    ":persistence.fact/datom": _datom_bad,
    ":persistence.effect/op": _effect_op_bad,
    ":persistence.effect/audit-entry": _audit_entry_bad,
    ":persistence.plan/node": _plan_node_bad,
    ":persistence.plan/skill": _plan_skill_bad,
    ":persistence.replay/trajectory": _trajectory_bad,
    ":persistence.replay/fact": _replay_fact_bad,
    ":persistence.replay/intervention": _replay_intervention_bad,
    ":persistence.domain/decision": _decision_bad,
    ":persistence.domain/wacc-assumption": _wacc_bad,
}


def _truncate(s, n=120):
    s = str(s)
    return s if len(s) <= n else s[: n - 3] + "..."


def main() -> None:
    print("=" * 70)
    print("Persistence OS — Module 6 Spec demo")
    print(f"{len(registered_keys())} specs registered")
    print("=" * 70)

    print("\n--- 10 VALID examples (generated + conformed) ---\n")
    for i, key in enumerate(CANONICAL_SPECS, start=1):
        val = generate_example(key)
        result = conform(key, val)
        mark = "OK " if result.is_ok else "!! "
        print(f"{mark}{i:>2}. {key}")
        print(f"     generated: {_truncate(val)}")
        print(f"     conformed: {result.is_ok}")

    print("\n--- 10 INVALID examples (with LLM-ready fix hints) ---\n")
    for i, key in enumerate(CANONICAL_SPECS, start=1):
        bad = INVALID_SAMPLES[key]()
        result = conform(key, bad)
        mark = "!! " if not result.is_ok else "?? "
        print(f"{mark}{i:>2}. {key}")
        print(f"     bad value: {_truncate(bad)}")
        if not result.is_ok:
            explanation = explain_for_llm(key, bad)
            for line in explanation.splitlines():
                print(f"     {line}")
        print()

    print("=" * 70)
    print("demo complete")


if __name__ == "__main__":
    main()
