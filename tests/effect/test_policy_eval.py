"""Policy-as-data evaluator tests (pure function; no side effects).

Operators supported per spec §4:
    :op=          equality on op name
    :op-in        op membership test (op-in can also take a list literal)
    :contains?    list/string contains predicate
    :matches?     regex match
    :mode=        equality on the evaluation mode
    :and, :or, :not   boolean combinators

Plus rule-level keys:
    :when         predicate
    :require      predicate (must evaluate to True for rule to pass)
    :on-fail      :deny | :deny-silently | :require-approval

Verdicts returned: ``allow`` (no rule tripped), or the first matching
rule's ``on-fail``.
"""
import re

import pytest

from persistence.effect.policy_eval import evaluate, PolicyError


PRINCIPAL = {"role": "user", "tenant_id": "t1", "clearance": "basic"}


# ---------- primitive operators ----------


def test_op_eq():
    rule_when = [":op=", ":llm/call"]
    assert evaluate._match_when(rule_when, PRINCIPAL, ":llm/call", {}, mode="live") is True
    assert evaluate._match_when(rule_when, PRINCIPAL, ":tool/call", {}, mode="live") is False


def test_op_in():
    rule_when = [":op-in", [":tool/call", ":emit-artifact"]]
    assert evaluate._match_when(rule_when, PRINCIPAL, ":tool/call", {}, mode="live") is True
    assert evaluate._match_when(rule_when, PRINCIPAL, ":llm/call", {}, mode="live") is False


def test_mode_eq():
    rule_when = [":mode=", "dry-run"]
    assert evaluate._match_when(rule_when, PRINCIPAL, ":llm/call", {}, mode="dry-run") is True
    assert evaluate._match_when(rule_when, PRINCIPAL, ":llm/call", {}, mode="live") is False


def test_contains_in_args_list():
    rule_when = [":contains?", [":args", "tags"], "regulator-facing"]
    args = {"tags": ["regulator-facing", "internal"]}
    assert evaluate._match_when(rule_when, PRINCIPAL, ":decide", args, mode="live") is True


def test_contains_miss():
    rule_when = [":contains?", [":args", "tags"], "public"]
    args = {"tags": ["internal"]}
    assert evaluate._match_when(rule_when, PRINCIPAL, ":decide", args, mode="live") is False


def test_matches_regex():
    rule_when = [":matches?", [":args", "name"], r"^(stripe|supabase-prod)"]
    args = {"name": "stripe.charge"}
    assert evaluate._match_when(rule_when, PRINCIPAL, ":tool/call", args, mode="live") is True


def test_and_or_not():
    rule_when = [
        ":and",
        [":op=", ":decide"],
        [":or", [":contains?", [":args", "tags"], "regulator-facing"], [":mode=", "strict"]],
        [":not", [":mode=", "dry-run"]],
    ]
    args = {"tags": ["regulator-facing"]}
    assert evaluate._match_when(rule_when, PRINCIPAL, ":decide", args, mode="live") is True
    # not: :mode=dry-run → false when mode is dry-run
    assert (
        evaluate._match_when(rule_when, PRINCIPAL, ":decide", args, mode="dry-run") is False
    )


def test_non_empty_check_on_args_field():
    rule_when = [":non-empty?", [":args", "rationale"]]
    assert evaluate._match_when(rule_when, PRINCIPAL, ":decide", {"rationale": "yes"}, mode="live")
    assert not evaluate._match_when(rule_when, PRINCIPAL, ":decide", {"rationale": ""}, mode="live")
    assert not evaluate._match_when(rule_when, PRINCIPAL, ":decide", {}, mode="live")


# ---------- full policy decisions ----------


def test_empty_policy_allows_all():
    policy = {"policy/id": "empty", "rules": []}
    v = evaluate(policy, PRINCIPAL, ":llm/call", {}, mode="live")
    assert v["verdict"] == "allow"


def test_rule_denies_on_when_match_and_require_fail():
    """r4-decision-needs-rationale — deny if :decide without rationale."""
    policy = {
        "policy/id": "r4-pol",
        "rules": [
            {
                "id": "r4",
                "when": [":op=", ":decide"],
                "require": [":non-empty?", [":args", "rationale"]],
                "on-fail": "require-approval",
            }
        ],
    }
    v = evaluate(policy, PRINCIPAL, ":decide", {"question": "?", "options": []}, mode="live")
    assert v["verdict"] == "require-approval"
    assert "r4" in v["reasons"][0]


def test_rule_passes_when_require_satisfied():
    policy = {
        "policy/id": "r4-pol",
        "rules": [
            {
                "id": "r4",
                "when": [":op=", ":decide"],
                "require": [":non-empty?", [":args", "rationale"]],
                "on-fail": "require-approval",
            }
        ],
    }
    v = evaluate(policy, PRINCIPAL, ":decide", {"rationale": "because reasons"}, mode="live")
    assert v["verdict"] == "allow"


def test_deny_silently_verdict():
    """r3 — prod tools in dry-run must be denied silently."""
    policy = {
        "policy/id": "r3-pol",
        "rules": [
            {
                "id": "r3",
                "when": [
                    ":and",
                    [":mode=", "dry-run"],
                    [":op-in", [":tool/call", ":emit-artifact"]],
                    [":matches?", [":args", "name"], r"^(stripe|supabase-prod|binance)"],
                ],
                "on-fail": "deny-silently",
            }
        ],
    }
    v = evaluate(
        policy, PRINCIPAL, ":tool/call", {"name": "stripe.charge", "input": {}}, mode="dry-run"
    )
    assert v["verdict"] == "deny-silently"


def test_first_matching_rule_wins():
    policy = {
        "policy/id": "multi",
        "rules": [
            {
                "id": "deny-first",
                "when": [":op=", ":llm/call"],
                "on-fail": "deny",
            },
            {
                "id": "require-approval-never-reached",
                "when": [":op=", ":llm/call"],
                "on-fail": "require-approval",
            },
        ],
    }
    v = evaluate(policy, PRINCIPAL, ":llm/call", {}, mode="live")
    assert v["verdict"] == "deny"
    assert "deny-first" in v["reasons"][0]


def test_rule_without_require_denies_on_when_match():
    """If a rule has :when but no :require, a :when match fires :on-fail."""
    policy = {
        "policy/id": "ban-binance",
        "rules": [
            {
                "id": "ban",
                "when": [":matches?", [":args", "name"], r"binance"],
                "on-fail": "deny",
            }
        ],
    }
    v = evaluate(
        policy, PRINCIPAL, ":tool/call", {"name": "binance.trade", "input": {}}, mode="live"
    )
    assert v["verdict"] == "deny"


def test_principal_attr_check():
    """Policies can gate on principal attributes (e.g. :clearance)."""
    policy = {
        "policy/id": "mia",
        "rules": [
            {
                "id": "require-license",
                "when": [":op=", ":decide"],
                "require": [":=", [":principal", "clearance"], "mia-licensed"],
                "on-fail": "deny",
            }
        ],
    }
    principal_ok = {"clearance": "mia-licensed"}
    principal_bad = {"clearance": "basic"}
    assert (
        evaluate(policy, principal_ok, ":decide", {"rationale": "x"}, mode="live")["verdict"]
        == "allow"
    )
    assert (
        evaluate(policy, principal_bad, ":decide", {"rationale": "x"}, mode="live")["verdict"]
        == "deny"
    )


def test_unknown_operator_raises_policy_error():
    policy = {
        "policy/id": "bad",
        "rules": [
            {
                "id": "x",
                "when": [":totally-unknown-op", 1, 2],
                "on-fail": "deny",
            }
        ],
    }
    with pytest.raises(PolicyError, match="unknown operator"):
        evaluate(policy, PRINCIPAL, ":llm/call", {}, mode="live")


def test_policy_value_is_immutable_from_caller_perspective():
    """Mutating the returned verdict dict must not change the policy itself."""
    policy = {
        "policy/id": "x",
        "rules": [],
    }
    before = repr(policy)
    v = evaluate(policy, PRINCIPAL, ":llm/call", {}, mode="live")
    v["reasons"] = ["mutated"]
    assert repr(policy) == before
