"""Phase 2.1a — value-shape dataclasses for Coder ReAct primitives.

Empty bodies in 2.1a so type hints in `_session.py` stubs resolve
without dragging in the wire shape. 2.1b/2.2a fill the fields when
the LLM-provider/observation contracts stabilize.

Per design § 3.4.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    """Substrate read result returned by `_observe`. Empty in 2.1a;
    fields land in 2.2a as the read shape stabilizes."""


@dataclass(frozen=True)
class LLMDecision:
    """LLM provider output returned by `_decide`. Empty in 2.1a;
    fields land in 2.1b as the provider abstraction stabilizes.

    Expected eventual fields per base § 3.4:
        kind: Literal["act", "plan", "branch"]
        confidence: float
        payload: Mapping[str, Any]
    """
