"""Policy handler tests — blocks denied ops, surfaces require-approval.

Policy handler runs the evaluator over every wrapped op. Verdicts:
- ``allow``            → proceed (call k)
- ``deny``             → raise PolicyDenied
- ``deny-silently``    → return a benign "denied" result (no exception)
- ``require-approval`` → raise ApprovalRequired

Spec §9: policy must NEVER call the LLM directly. If it wants an LLM
second opinion, it must use mask(":audit") and a *different* stack — or
via the ``approval_fn`` hook which is the single exit to external logic.
"""
import pytest

from persistence.effect.handlers.policy import (
    ApprovalRequired,
    PolicyDenied,
    make_policy_handler,
)
from persistence.effect.handlers.raw import make_echo_llm_handler
from persistence.effect.runtime import Handler, Runtime, perform, with_runtime


DECIDE_POLICY = {
    "policy/id": "decide-needs-rationale",
    "rules": [
        {
            "id": "r4",
            "when": [":op=", ":decide"],
            "require": [":non-empty?", [":args", "rationale"]],
            "on-fail": "require-approval",
        }
    ],
}


BAN_BINANCE_POLICY = {
    "policy/id": "ban-binance",
    "rules": [
        {
            "id": "ban",
            "when": [":matches?", [":args", "name"], r"binance"],
            "on-fail": "deny",
        }
    ],
}


DRY_RUN_PROD_POLICY = {
    "policy/id": "dry-run-guard",
    "rules": [
        {
            "id": "r3",
            "when": [
                ":and",
                [":mode=", "dry-run"],
                [":matches?", [":args", "name"], r"^(stripe|supabase-prod)"],
            ],
            "on-fail": "deny-silently",
        }
    ],
}


def _decide_raw():
    def clause(args, k, ctx):
        return {"choice": args["options"][0] if args.get("options") else None, "confidence": 0.9}

    return Handler(name="raw", wraps={":decide"}, clauses={":decide": clause})


def _tool_raw():
    def clause(args, k, ctx):
        return {"result": f"called-{args['name']}", "error": None}

    return Handler(name="raw", wraps={":tool/call"}, clauses={":tool/call": clause})


# ---------- allow ----------


def test_policy_allows_when_rule_does_not_fire():
    policy = make_policy_handler(DECIDE_POLICY, wraps={":decide"})
    rt = Runtime([_decide_raw(), policy])
    with with_runtime(rt):
        out = perform(":decide", question="q", options=["a", "b"], rationale="good")
    assert out["choice"] == "a"


# ---------- deny ----------


def test_policy_denies_raises_policy_denied():
    policy = make_policy_handler(BAN_BINANCE_POLICY, wraps={":tool/call"})
    rt = Runtime([_tool_raw(), policy])
    with with_runtime(rt), pytest.raises(PolicyDenied) as info:
        perform(":tool/call", name="binance.trade", input={})
    assert "binance" in str(info.value) or "ban" in str(info.value)


# ---------- deny-silently ----------


def test_policy_deny_silently_returns_placeholder_no_exception():
    policy = make_policy_handler(
        DRY_RUN_PROD_POLICY, wraps={":tool/call"}, mode="dry-run"
    )
    rt = Runtime([_tool_raw(), policy])
    with with_runtime(rt):
        out = perform(":tool/call", name="stripe.charge", input={})
    # Must not have called downstream; must return a denied sentinel.
    assert out.get("denied") is True
    assert out.get("silently") is True


# ---------- require-approval ----------


def test_require_approval_calls_approval_fn_and_returns_if_true():
    """approval_fn(verdict_info) -> bool. True means override to allow."""
    calls: list = []

    def approval(info):
        calls.append(info)
        return True

    policy = make_policy_handler(DECIDE_POLICY, wraps={":decide"}, approval_fn=approval)
    rt = Runtime([_decide_raw(), policy])
    with with_runtime(rt):
        out = perform(":decide", question="q", options=["x"])
    assert out["choice"] == "x"
    assert len(calls) == 1


def test_require_approval_raises_when_no_approval_fn():
    policy = make_policy_handler(DECIDE_POLICY, wraps={":decide"})
    rt = Runtime([_decide_raw(), policy])
    with with_runtime(rt), pytest.raises(ApprovalRequired):
        perform(":decide", question="q", options=["a"])


def test_require_approval_raises_when_approval_fn_returns_false():
    policy = make_policy_handler(
        DECIDE_POLICY, wraps={":decide"}, approval_fn=lambda info: False
    )
    rt = Runtime([_decide_raw(), policy])
    with with_runtime(rt), pytest.raises(ApprovalRequired):
        perform(":decide", question="q", options=["a"])


# ---------- policy is immutable ----------


def test_hot_reload_by_value_swap_not_mutation():
    """Replacing ctx['policy'] with a new value must take effect without mutating the old."""
    original = dict(BAN_BINANCE_POLICY)
    policy_handler = make_policy_handler(original, wraps={":tool/call"})
    # Swap to a permissive policy.
    new_policy = {"policy/id": "permissive", "rules": []}
    policy_handler.ctx["policy"] = new_policy
    rt = Runtime([_tool_raw(), policy_handler])
    with with_runtime(rt):
        out = perform(":tool/call", name="binance.trade", input={})
    assert out["result"] == "called-binance.trade"
    # Original dict untouched.
    assert original["policy/id"] == "ban-binance"
    assert len(original["rules"]) == 1
