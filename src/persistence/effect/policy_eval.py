"""Pure evaluator for policy-as-data (spec §4).

Policies are plain Python dicts (JSON-compatible subset of EDN). No
side effects — the evaluator returns a verdict dict and never calls the
LLM / network / disk.

Grammar summary (as Python lists, keyword operators keep the leading ``:``
to preserve the EDN flavor):

    predicate ::=
        [":op=", op]                        — operation name equality
      | [":op-in", [op ...]]                — membership
      | [":mode=", mode]                    — evaluation-mode equality
      | [":contains?", PATH, value]         — collection contains value
      | [":matches?", PATH, regex]          — regex match on a string
      | [":non-empty?", PATH]               — truthy + non-zero-length
      | [":=", PATH, value]                 — generic equality
      | [":and", predicate, ...]
      | [":or",  predicate, ...]
      | [":not", predicate]

    PATH ::= [":args", key]         — reach into the op's args
           | [":principal", key]    — reach into the principal dict

Rule schema:

    {
      "id":       str,
      "when":     predicate,      # required
      "require":  predicate,      # optional; if given, must be True
      "on-fail":  "deny" | "deny-silently" | "require-approval"
    }

Semantics:
- Rules evaluated in order.
- A rule *matches* when ``when`` evaluates True.
- If ``require`` is present, the rule ``fires`` iff ``require`` is False.
- If ``require`` is absent, the rule ``fires`` as soon as ``when`` matches.
- First fired rule's ``on-fail`` is the verdict. If no rule fires → ``allow``.
"""
from __future__ import annotations

import re
from typing import Any


class PolicyError(Exception):
    """Raised when a policy contains an unknown operator or malformed node."""


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _resolve_path(path: Any, principal: dict, op: str, args: dict) -> Any:
    """Resolve a PATH form like ``[":args", "tags"]`` → args.get("tags")."""
    # Literal value — return as-is.
    if not isinstance(path, list):
        return path
    if not path:
        return None
    root = path[0]
    if root == ":args":
        # [":args", key, ...subkey] — support nested lookups
        cur: Any = args
        for key in path[1:]:
            if isinstance(cur, dict):
                cur = cur.get(key)
            else:
                return None
        return cur
    if root == ":principal":
        cur = principal
        for key in path[1:]:
            if isinstance(cur, dict):
                cur = cur.get(key)
            else:
                return None
        return cur
    if root == ":op":
        return op
    # Not a PATH — treat as literal list value.
    return path


# ---------------------------------------------------------------------------
# Predicate evaluation
# ---------------------------------------------------------------------------


def _match_when(predicate: Any, principal: dict, op: str, args: dict, mode: str) -> bool:
    """Evaluate a predicate node. Raises :class:`PolicyError` on unknown ops."""
    if not isinstance(predicate, list) or not predicate:
        raise PolicyError(f"malformed predicate: {predicate!r}")
    head = predicate[0]
    if head == ":op=":
        return op == predicate[1]
    if head == ":op-in":
        return op in predicate[1]
    if head == ":mode=":
        return mode == predicate[1]
    if head == ":and":
        return all(
            _match_when(p, principal, op, args, mode) for p in predicate[1:]
        )
    if head == ":or":
        return any(
            _match_when(p, principal, op, args, mode) for p in predicate[1:]
        )
    if head == ":not":
        return not _match_when(predicate[1], principal, op, args, mode)
    if head == ":contains?":
        value = _resolve_path(predicate[1], principal, op, args)
        needle = predicate[2]
        if value is None:
            return False
        try:
            return needle in value
        except TypeError:
            return False
    if head == ":matches?":
        value = _resolve_path(predicate[1], principal, op, args)
        pattern = predicate[2]
        if not isinstance(value, str):
            return False
        return re.search(pattern, value) is not None
    if head == ":non-empty?":
        value = _resolve_path(predicate[1], principal, op, args)
        if value is None:
            return False
        try:
            return len(value) > 0
        except TypeError:
            return bool(value)
    if head == ":=":
        lhs = _resolve_path(predicate[1], principal, op, args)
        rhs = predicate[2]
        return lhs == rhs
    raise PolicyError(f"unknown operator {head!r} in predicate {predicate!r}")


# ---------------------------------------------------------------------------
# Top-level evaluate
# ---------------------------------------------------------------------------


def evaluate(
    policy: dict,
    principal: dict,
    op: str,
    args: dict,
    *,
    mode: str = "live",
) -> dict:
    """Return ``{"verdict": <str>, "reasons": [<str>...], "policy_id": <str>}``.

    Verdicts: ``allow | deny | deny-silently | require-approval``.
    """
    rules = policy.get("rules", [])
    policy_id = policy.get("policy/id", "unknown")
    reasons: list[str] = []

    for rule in rules:
        rid = rule.get("id", "anon")
        when = rule.get("when")
        if when is None:
            raise PolicyError(f"rule {rid!r} missing :when")
        # Does :when match?
        try:
            when_match = _match_when(when, principal, op, args, mode)
        except PolicyError:
            raise
        if not when_match:
            continue
        # :when matched. If :require is present, rule fires when require is False.
        require = rule.get("require")
        fires: bool
        if require is None:
            fires = True
        else:
            require_ok = _match_when(require, principal, op, args, mode)
            fires = not require_ok
        if fires:
            verdict = rule.get("on-fail", "deny")
            reasons.append(f"{rid}: on-fail={verdict}")
            return {"verdict": verdict, "reasons": reasons, "policy_id": policy_id}

    return {"verdict": "allow", "reasons": reasons, "policy_id": policy_id}


# Expose _match_when for tests.
evaluate._match_when = _match_when  # type: ignore[attr-defined]
