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
from typing import Any

from ._types import Observation

EMIT_DECISION_TOOL_SCHEMA: dict[str, Any] = {
    "name": "emit_decision",
    "description": (
        "Emit your structured next-step decision for the persistence-coder agent. "
        "kind='act' for a single tool invocation; kind='plan' for a multi-step "
        "composition; kind='branch' if you are uncertain and want the agent to "
        "fork-and-explore. confidence is in [0.0, 1.0]; values below 0.65 trigger "
        "branch escalation."
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


def build_messages(task: str, obs: Observation) -> list[dict[str, Any]]:
    """Construct the LLM message list. 2.1b: minimal user message.

    Observation fields land in 2.2a; when populated, observation
    context is appended to the message list (e.g. as a separate
    'system' message or formatted into the user message body).
    """
    return [
        {
            "role": "user",
            "content": f"Task: {task}\n\nUse emit_decision to respond.",
        },
    ]


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
