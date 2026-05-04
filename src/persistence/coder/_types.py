"""Phase 2.1a/2.1b — value-shape dataclasses for Coder ReAct primitives.

Observation is empty in 2.1a/b; fields land 2.2a (substrate read shape).
LLMDecision filled in 2.1b per base design § 3.4 + 2.1b LD2.

Per design § 3.4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping


DecisionKind = Literal["act", "plan", "branch"]


@dataclass(frozen=True)
class Observation:
    """Substrate read result returned by `_observe`. Empty in 2.1a/2.1b;
    fields land in 2.2a as the read shape stabilizes."""


@dataclass(frozen=True)
class LLMDecision:
    """LLM provider output returned by `_decide`. Phase 2.1b filled.

    Fields per base design § 3.4:
        kind: "act" | "plan" | "branch"
        confidence: float in [0.0, 1.0]
        payload: kind-dispatched shape; loose in 2.1b. Tightens
            progressively in 2.2a (act payload), 2.3a (plan payload),
            2.3b (branch payload).
    """

    kind: DecisionKind
    confidence: float
    payload: Mapping[str, Any]
