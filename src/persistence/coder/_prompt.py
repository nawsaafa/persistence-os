"""Phase 2.1b — prompt construction + tool schema + text fallback parser.

`EMIT_DECISION_TOOL_SCHEMA` is the ONLY tool exposed to the LLM in
2.1b per LD4 (decision/action split). Real effect tools (:fs/, :shell/,
:code/, :git/) land in 2.2a — they are NEVER reachable by the LLM
directly; the substrate routes intents instead.

The text-fenced fallback parser handles backends that do not natively
support tool-use (Mode 3 callable wired to a text-only LLM, or any
provider whose tool_calls field is empty).
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from ._types import Observation

_PLAN_EDN_GUIDANCE = """\
When emitting kind="plan", the payload["plan_edn"] field carries an \
EDN-formatted plan (s.plan.parse-compatible canonical EDN). Constraints:
- Root MUST be :seq. No other root kinds at this phase.
- Leaves MUST be one of: :fs/read, :fs/write, :fs/glob, :fs/grep, \
:shell/exec, :code/run, :git/diff, :git/status, :git/log, :git/commit.
- NO :branch or :code leaves (queued to later phases).
- Max 64 nodes, max depth 4, max 8192 bytes.

Examples:

(1) Read a file, run code, then diff:
[:seq {} [:fs/read {:path "src/foo.py"}] [:code/run {:source "print(42)"}] [:git/diff {}]]

(2) Grep then commit:
[:seq {} [:fs/grep {:pattern "TODO" :path "src/"}] [:git/commit {:message "wip"}]]"""


EMIT_DECISION_TOOL_SCHEMA: dict[str, Any] = {
    "name": "emit_decision",
    "description": (
        "Emit your structured next-step decision for the persistence-coder agent. "
        "kind='act' for a single tool invocation; kind='plan' for a multi-step "
        "composition; kind='branch' if you are uncertain and want the agent to "
        "fork-and-explore. confidence is in [0.0, 1.0]; values below 0.65 trigger "
        "branch escalation. " + _PLAN_EDN_GUIDANCE
    ),
    "input_schema": {
        "type": "object",
        "required": ["kind", "confidence", "payload"],
        "additionalProperties": False,
        "properties": {
            "kind": {"type": "string", "enum": ["act", "plan", "branch"]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "payload": {"type": "object"},  # 2.1b: loose; tightens 2.2a+
        },
    },
}


def _render_latest_action(action: Mapping[str, Any]) -> list[str]:
    """LD3 — render the most-recent action with explicit field formatting.

    Bypasses build_messages' [:200] truncation for the LATEST action only;
    older history entries (in 'Recent loop history' block) keep the cap.

    Fields:
      op: dispatched op name
      error: error string (or 'none' when None)
      result_summary: dict — render each (key, value) explicitly:
        - str values: render with `|\\n    <value>` (block-string indented).
          The upstream _summarize_result already truncates strings >512 chars.
        - list values: collapse to `count=N, first_3=<repr of first 3>`.
        - other: repr() truncated at 200 chars.
      result_summary=None: render placeholder '(no result_summary — exception path)'.
    """
    parts = ["Latest action output:"]
    parts.append(f"  op: {action.get('op', '?')}")
    parts.append(f"  error: {action.get('error') or 'none'}")
    rs = action.get("result_summary")
    if rs is None:
        parts.append("  (no result_summary — exception path)")
    elif isinstance(rs, dict):
        for k, v in rs.items():
            if isinstance(v, str):
                # _summarize_result already capped at 512; render verbatim.
                parts.append(f"  {k}: |\n    {v}")
            elif isinstance(v, list):
                parts.append(f"  {k}: count={len(v)}, first_3={v[:3]!r}")
            else:
                parts.append(f"  {k}: {v!r}"[:202])
    else:
        parts.append(f"  result_summary: {rs!r}"[:202])
    return parts


def build_messages(task: str, obs: Observation) -> list[dict[str, Any]]:
    """Construct the LLM message list.

    Appends a 'Recent loop history' section when obs has non-empty
    decisions or actions; section is omitted entirely on the first iter
    (zero-prompt-overhead when both tuples are empty).
    """
    parts = [f"Task: {task}", "", "Use emit_decision to respond."]
    # Phase 2.2b LD3 — render latest action verbatim BEFORE the
    # truncated history block, so stdout/stderr/traceback content
    # reaches the LLM without the [:200] cap eating sentinels.
    if obs.recent_actions:
        parts.append("")
        parts.extend(_render_latest_action(obs.recent_actions[-1]))
    if obs.recent_decisions or obs.recent_actions:
        parts.extend(["", f"Recent loop history (iter {obs.iter_count}):"])
        if obs.recent_decisions:
            parts.append("Decisions:")
            for d in obs.recent_decisions[-3:]:  # truncate for prompt cost
                parts.append(f"  - {json.dumps(d, sort_keys=True)[:200]}")
        if obs.recent_actions:
            parts.append("Actions:")
            for a in obs.recent_actions[-3:]:
                parts.append(f"  - {json.dumps(a, sort_keys=True)[:200]}")
    return [{"role": "user", "content": "\n".join(parts)}]


_DECISION_ENVELOPE_RE = re.compile(r"<decision>\s*(\{.*?\})\s*</decision>", re.DOTALL)


def parse_text_decision(text: str) -> dict[str, Any] | None:
    """Tier-2 fallback parser for ``<decision>{json}</decision>`` envelope.

    Returns ``None`` if envelope absent OR JSON invalid OR shape doesn't
    match the required ``{kind, confidence, payload}`` fields. Caller
    falls to tier 3 (missing-confidence default).
    """
    if not (m := _DECISION_ENVELOPE_RE.search(text)):
        return None
    try:
        parsed = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("kind") not in {"act", "plan", "branch"}:
        return None
    try:
        confidence = float(parsed["confidence"])
    except (KeyError, ValueError, TypeError):
        return None
    if not (0.0 <= confidence <= 1.0):
        return None
    payload = parsed.get("payload", {})
    if not isinstance(payload, dict):
        return None
    return {
        "kind": parsed["kind"],
        "confidence": confidence,
        "payload": payload,
    }
