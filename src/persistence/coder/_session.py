"""Phase 2.1a — `Coder` ReAct skeleton + CoderStubNotImplemented sentinel.

Every behavioral method raises `CoderStubNotImplemented` tagged with
the downstream sub-phase that fills it. Substrate ownership is
dependency-injected (LD2 / refresh Q2): callers (CLI, tests) open the
substrate; Coder never does.

The loop shape mirrors base design § 3.4 (agent-loop diagram)
top-to-bottom; reordering is an ARIS-reviewable architectural change,
not a refactor.
"""

from __future__ import annotations

from dataclasses import dataclass

from persistence.sdk import Substrate

from ._types import LLMDecision, Observation


class CoderStubNotImplemented(NotImplementedError):
    """Phase 2.1a skeleton sentinel: a method body has not been
    filled yet by its target sub-phase. The CLI banner catches THIS
    subtype only; bare NotImplementedError raised by real code in
    2.1b+ propagates as a real failure rather than being banner-masked.
    R1 fix-1 (codex hard-mode review of design doc 2026-05-03)."""


@dataclass
class Coder:
    """ReAct skeleton with two escalations (plan, branch) and a REPL pause hook."""

    task: str
    substrate: Substrate
    confidence_threshold: float = 0.65          # base § 3.4; tuned at 2.4a
    missing_confidence_default: float = 0.5     # base § 3.4 fail-safe

    # main entry — Phase 2.1b fills the loop body (one iter → while-loop)
    def run(self) -> None:
        obs = self._observe()
        decision = self._decide(obs)
        if self._should_escalate_branch(decision):
            self._escalate_branch(decision)
        elif self._should_escalate_plan(decision):
            self._escalate_plan(decision)
        else:
            self._act(decision)
        self._check_pause()

    # --- ReAct primitives — Phase 2.1b/2.2a fill these ---------------

    def _observe(self) -> Observation:
        raise CoderStubNotImplemented(
            "Phase 2.2a — substrate read via s.fact.q"
        )

    def _decide(self, obs: Observation) -> LLMDecision:
        raise CoderStubNotImplemented(
            "Phase 2.1b — LLM provider call → :llm/decision datom"
        )

    def _act(self, decision: LLMDecision) -> None:
        raise CoderStubNotImplemented(
            "Phase 2.2a — s.effect.perform on :fs/:shell/:code/:git"
        )

    # --- Escalation gates — Phase 2.3a/b fill these ------------------

    def _should_escalate_plan(self, decision: LLMDecision) -> bool:
        raise CoderStubNotImplemented(
            "Phase 2.3a — checks decision.kind == 'plan'"
        )

    def _escalate_plan(self, decision: LLMDecision) -> None:
        raise CoderStubNotImplemented(
            "Phase 2.3a — Plan AST builder + s.plan.execute"
        )

    def _should_escalate_branch(self, decision: LLMDecision) -> bool:
        raise CoderStubNotImplemented(
            "Phase 2.3b — decision.kind == 'branch' "
            "or confidence < threshold"
        )

    def _escalate_branch(self, decision: LLMDecision) -> None:
        raise CoderStubNotImplemented(
            "Phase 2.3b — s.plan.mcts_search + s.txn.fork + s.plan.judge"
        )

    # --- REPL pause hook — Phase 2.3d fills this ---------------------

    def _check_pause(self) -> None:
        raise CoderStubNotImplemented(
            "Phase 2.3d — :repl/request datom check + pause/resume"
        )
