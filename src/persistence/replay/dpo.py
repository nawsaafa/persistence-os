"""DPO (Direct Preference Optimization) pair extraction.

From agent4-replay-spec §5:

  extract-dpo-pair [factual counterfactual]
    when (prefix-matches AND |outcome-delta| > threshold)
      prompt  <- llm_in at branch_point
      chosen  <- llm_out from better-of(factual, counterfactual) at branch_point
      rejected<- llm_out from worse-of(factual, counterfactual) at branch_point
      margin  <- |outcome-delta|
"""
from __future__ import annotations

from typing import Optional

from persistence.replay.trajectory import Trajectory


def _prefix_matches(a: Trajectory, b: Trajectory, up_to: int) -> bool:
    for k in range(up_to):
        if k >= len(a.facts) or k >= len(b.facts):
            return False
        fa, fb = a.facts[k], b.facts[k]
        if fa.state != fb.state:
            return False
        if fa.obs != fb.obs:
            return False
        if fa.llm_out != fb.llm_out:
            return False
        if fa.action != fb.action:
            return False
    return True


def extract_dpo_pair(
    factual: Trajectory,
    counterfactual: Trajectory,
    threshold: float = 0.0,
) -> Optional[dict]:
    """Return a DPO pair iff prefix matches and |outcome delta| > threshold."""
    branch = counterfactual.branch_point
    if branch is None:
        return None
    if branch >= len(factual.facts) or branch >= len(counterfactual.facts):
        return None

    if not _prefix_matches(factual, counterfactual, branch):
        return None

    f_pnl = float(factual.outcome.get("pnl", 0.0))
    c_pnl = float(counterfactual.outcome.get("pnl", 0.0))
    delta = c_pnl - f_pnl
    margin = abs(delta)

    if margin <= threshold:
        return None

    winner, loser = (counterfactual, factual) if c_pnl > f_pnl else (factual, counterfactual)
    return {
        "prompt": factual.facts[branch].llm_in,
        "chosen": winner.facts[branch].llm_out,
        "rejected": loser.facts[branch].llm_out,
        "margin": margin,
    }
