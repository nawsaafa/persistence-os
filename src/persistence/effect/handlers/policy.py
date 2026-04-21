"""Policy handler — runs the policy evaluator on every wrapped op.

Verdicts:
- ``allow``            → delegate to k.
- ``deny``             → raise :class:`PolicyDenied`.
- ``deny-silently``    → return ``{"denied": True, "silently": True, ...}`` — no
                         exception crosses the boundary, matching the spec
                         semantics where the agent cannot tell it was blocked.
- ``require-approval`` → call ``approval_fn(info)``; if it returns truthy, allow.
                         Else raise :class:`ApprovalRequired`.

By design, the policy handler does NOT call the LLM itself. If a policy needs
an LLM second opinion, the approval_fn hook is the single escape — and
callers must use ``mask("audit")`` around that body to prevent audit re-entry.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from persistence.effect.policy_eval import evaluate as _evaluate
from persistence.effect.runtime import Handler


class PolicyDenied(Exception):
    """Raised when policy verdict is ``deny``."""

    def __init__(self, info: dict[str, Any]):
        super().__init__(
            f"policy denied op {info.get('op')!r} "
            f"(policy={info.get('policy_id')!r}, reasons={info.get('reasons')})"
        )
        self.info = info


class ApprovalRequired(Exception):
    """Raised when policy verdict is ``require-approval`` and no approval was granted."""

    def __init__(self, info: dict[str, Any]):
        super().__init__(
            f"policy requires approval for op {info.get('op')!r} "
            f"(policy={info.get('policy_id')!r}, reasons={info.get('reasons')})"
        )
        self.info = info


def make_policy_handler(
    policy: dict,
    *,
    wraps: Iterable[str] = ("llm/call", "tool/call", "decide", "emit-artifact", "mem/write"),
    mode: str = "live",
    principal: dict[str, Any] | None = None,
    approval_fn: Callable[[dict[str, Any]], bool] | None = None,
) -> Handler:
    """Return a policy handler."""

    def make_op_clause(op_name: str):
        def clause(args, k, ctx):
            verdict_info = _evaluate(
                ctx["policy"],
                ctx["principal"],
                op_name,
                args,
                mode=ctx["mode"],
            )
            verdict_info = {**verdict_info, "op": op_name}
            v = verdict_info["verdict"]
            if v == "allow":
                return k(args)
            if v == "deny":
                raise PolicyDenied(verdict_info)
            if v == "deny-silently":
                return {
                    "denied": True,
                    "silently": True,
                    "reasons": list(verdict_info["reasons"]),
                    "policy_id": verdict_info.get("policy_id"),
                }
            if v == "require-approval":
                af = ctx.get("approval_fn")
                if af is not None and af(verdict_info):
                    return k(args)
                raise ApprovalRequired(verdict_info)
            raise RuntimeError(f"unknown verdict {v!r}")

        return clause

    clauses = {op: make_op_clause(op) for op in wraps}
    return Handler(
        name="policy",
        wraps=set(wraps),
        clauses=clauses,
        ctx={
            "policy": policy,
            "mode": mode,
            "principal": principal or {},
            "approval_fn": approval_fn,
        },
    )
