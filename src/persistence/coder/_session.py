"""Phase 2.1a/b — `Coder` ReAct skeleton + first-LLM-call body.

Phase 2.1a: skeleton — every behavioral method raises
`CoderStubNotImplemented` tagged with the downstream sub-phase.
Phase 2.1b: `_decide` body filled — calls :llm/call, transacts
:llm/messages (before) + :llm/decision (after), returns a structured
LLMDecision. Other stubs unchanged.

Substrate ownership is dependency-injected (LD2 / refresh Q2):
callers (CLI, tests) own the substrate; Coder never opens or closes it.

Loop shape mirrors base design § 3.4 (agent-loop diagram); reordering
is an ARIS-reviewable architectural change, not a refactor.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from persistence.effect.canonical import canonical_dumps, canonical_hash
from persistence.sdk import Substrate

from ._prompt import EMIT_DECISION_TOOL_SCHEMA, build_messages, parse_text_decision
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
    model: str = "claude-opus-4-7"             # Phase 2.1b; overridable from CLI --model
    confidence_threshold: float = 0.65          # base § 3.4; tuned at 2.4a
    missing_confidence_default: float = 0.5     # base § 3.4 fail-safe (must be < confidence_threshold)
    max_iters: int = 20                          # Phase 2.2a LD2; loop ceiling for run()
    observe_depth: int = 5                       # Phase 2.2a LD3; trailing datom window
    _session_start_dt: dt.datetime | None = field(init=False, default=None)  # set by run() — T6
    _iter_count: int = field(init=False, default=0)                          # incremented each loop iter

    # main entry — Phase 2.1b loop body deferred to 2.2a (T6 widens to while-loop)
    def run(self) -> None:
        if self._session_start_dt is None:
            self._session_start_dt = dt.datetime.now(dt.timezone.utc)  # noqa: wall-clock
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
        view = self.substrate.fact.since(self._session_start_dt)
        in_tx_order = sorted(view.datoms, key=lambda d: d.tx)
        decisions = [
            d for d in in_tx_order
            if d.a == "llm/decision" and d.op == "assert"  # Datom.__post_init__ strips leading colon
        ][-self.observe_depth:]
        actions = [
            d for d in in_tx_order
            if d.a == "act/result" and d.op == "assert"    # Datom.__post_init__ strips leading colon
        ][-self.observe_depth:]
        # d.v is canonical_dumps()-encoded by every writer of these attrs
        # (T5 _act, 2.1b _decide, test helpers). Local-only substrate,
        # closed schema — no defensive JSONDecodeError guard. If a future
        # phase opens this attribute to external writers (2.4a HTTP
        # concurrent path), revisit at that boundary, not here.
        return Observation(
            iter_count=self._iter_count,
            recent_decisions=tuple(json.loads(d.v) for d in decisions),
            recent_actions=tuple(json.loads(d.v) for d in actions),
        )

    def _decide(self, obs: Observation) -> LLMDecision:
        """Phase 2.1b: call the LLM provider via :llm/call, transact
        :llm/messages provenance BEFORE the call (so it persists even
        if the call raises), parse the response in two tiers (tool-use
        primary, text-envelope fallback, missing-confidence default
        last), transact :llm/decision AFTER parsing.
        """
        messages = build_messages(self.task, obs)
        tools = [EMIT_DECISION_TOOL_SCHEMA]
        call_id = uuid.uuid4().hex  # noqa: wall-clock — entity-id (txn precedent)
        now = dt.datetime.now(dt.timezone.utc)  # noqa: wall-clock — provenance ts; routes through :sys/now in 2.4a

        # Provenance BEFORE the call — captured even if perform() raises.
        self.substrate.fact.transact([
            {
                "e": call_id,
                "a": ":llm/messages",
                "v": canonical_dumps({
                    "messages": messages,
                    "tools": tools,
                    "model": self.model,
                }),
                "valid_from": now,
            },
        ])

        result = self.substrate.effect.perform(
            ":llm/call",
            {
                "model": self.model,
                "messages": messages,
                "tools": tools,
            },
        )

        decision, parsed_via = self._parse_decision(result)

        decision_id = uuid.uuid4().hex  # noqa: wall-clock — entity-id (txn precedent)
        self.substrate.fact.transact([
            {
                "e": decision_id,
                "a": ":llm/decision",
                "v": canonical_dumps({
                    "kind": decision.kind,
                    "confidence": decision.confidence,
                    "payload": dict(decision.payload),
                    "parsed_via": parsed_via,
                    "source_call": call_id,
                }),
                "valid_from": now,
            },
        ])
        return decision

    def _parse_decision(self, result: dict) -> tuple[LLMDecision, str]:
        """Two-tier parsing per LD3 + tier-3 missing-confidence default."""
        # Tier 1: tool-use
        if result.get("tool_calls"):
            try:
                payload = result["tool_calls"][0]["input"]
                kind = payload["kind"]
                if kind not in {"act", "plan", "branch"}:
                    raise ValueError("invalid kind")
                confidence = float(payload["confidence"])
                if not (0.0 <= confidence <= 1.0):
                    raise ValueError("confidence out of range")
                return (
                    LLMDecision(
                        kind=kind,
                        confidence=confidence,
                        payload=payload.get("payload", {}),
                    ),
                    "tool_use",
                )
            except (KeyError, ValueError, TypeError):
                pass  # malformed tool_call — fall through to tier 2

        # Tier 2: text envelope fallback
        if (parsed := parse_text_decision(result.get("text", ""))) is not None:
            return (LLMDecision(**parsed), "text_fallback")

        # Tier 3: missing-confidence default — branch-escalates per base § 3.4
        return (
            LLMDecision(
                kind="act",
                confidence=self.missing_confidence_default,
                payload={"raw_text": result.get("text", "")},
            ),
            "missing_default",
        )

    def _act(self, decision: LLMDecision) -> None:
        if decision.kind != "act":
            raise ValueError(f"_act called with kind={decision.kind!r}")
        op = decision.payload.get("op")
        args = decision.payload.get("args", {})
        if not isinstance(op, str) or not op.startswith(":"):
            raise ValueError(f"malformed act payload: missing/invalid op {op!r}")

        t0 = dt.datetime.now(dt.timezone.utc)  # noqa: wall-clock

        def _record(result: Any | None, error: str | None) -> None:
            latency_ms = int((dt.datetime.now(dt.timezone.utc) - t0).total_seconds() * 1000)  # noqa: wall-clock
            self.substrate.fact.transact([{
                "e": uuid.uuid4().hex,  # noqa: wall-clock
                "a": ":act/result",
                "v": canonical_dumps({
                    "op": op,
                    "args_hash": canonical_hash(args),
                    "result_summary": _summarize_result(result) if result is not None else None,
                    "error": error,
                    "latency_ms": latency_ms,
                }),
                "valid_from": t0,
            }])

        try:
            result = self.substrate.effect.perform(op, args)
        except Exception as e:
            _record(None, f"{type(e).__name__}: {e}")
            raise

        _record(result, None)

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


def _summarize_result(r: Any) -> Any:
    """Truncate large blobs; keep small results intact for prompt re-read.

    Only truncates string values >512 chars inside dict-shaped results.
    Non-dict results are returned unchanged. If a future op returns a
    bare string >512 chars it will not be truncated — accepted limitation
    per Phase 2.2a spec (revisit at 2.4a if prompt sizes become a problem).
    """
    if isinstance(r, dict):
        out = {}
        for k, v in r.items():
            if isinstance(v, str) and len(v) > 512:
                out[k] = v[:256] + "...[truncated]..." + v[-256:]
            else:
                out[k] = v
        return out
    return r
