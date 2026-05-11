"""Phase 2.1a/b — `Coder` ReAct skeleton + first-LLM-call body.

Phase 2.1a: skeleton — every behavioral method raises
`CoderStubNotImplemented` tagged with the downstream sub-phase.
Phase 2.1b: `_decide` body filled — calls :llm/call, transacts
:llm/messages (before) + :llm/decision (after), returns a structured
LLMDecision. Other stubs unchanged.
Phase 2.2a: `_observe` reads (iter_count, recent_decisions ≤5,
recent_actions ≤5) via s.fact.since(self._session_start_dt). `_act`
dispatches structured payload via s.effect.perform(op, args) and
records :act/result provenance in BOTH success and failure paths
(R0 B1 — bare raise INSIDE except). `run()` widens to a max-iters
loop with done/plan/branch exit paths. `_should_escalate_{plan,branch}`
filled as one-liners. `_escalate_*` and `_check_pause` keep 2.1a stubs.

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
from typing import TYPE_CHECKING, Any

from persistence.effect.canonical import canonical_dumps, canonical_hash
from persistence.sdk import Substrate

from ._prompt import EMIT_DECISION_TOOL_SCHEMA, build_messages, parse_text_decision
from ._recursion import DispatcherContext, dispatcher_context
from ._types import LLMDecision, Observation

if TYPE_CHECKING:  # pragma: no cover
    from persistence.plan import SkillLibrary
    from persistence.coder._steering import _CoderSteeringSession


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
    skill_library: "SkillLibrary | None" = None  # Phase 2.3c.2 LD3+LD6 — threaded into _escalate_branch_body
    _session_start_dt: dt.datetime | None = field(init=False, default=None)  # set by run() — T6
    _iter_count: int = field(init=False, default=0)                          # incremented each loop iter
    _steering_session: "_CoderSteeringSession | None" = field(init=False, default=None)  # Phase 2.3d — set by _CoderSteeringSession.__post_init__

    # main entry — Phase 2.2a T6: max-iters loop
    def run(self) -> None:
        """Phase 2.2a: max-iters loop. Three exit paths:
        1. payload.done=true → return BEFORE _act (LD2; locked § 5.7).
        2. _should_escalate_{plan,branch} → escalation body raises CoderStubNotImplemented
           (2.3a / 2.3b territory; gate logic routes correctly).
        3. max_iters exhausted → silent return (no :loop/exhausted sentinel; revisit 2.4a).
        _check_pause is NOT called — re-added in 2.3d (REPL pause hook).
        """
        self._session_start_dt = dt.datetime.now(dt.timezone.utc)  # noqa: wall-clock
        for i in range(self.max_iters):
            self._iter_count = i
            # Phase 2.3d — REPL pause hook. _check_pause() blocks if a
            # _CoderSteeringSession has called pause(); no-op otherwise.
            self._check_pause()
            # Phase 2.3c.2 LD1 R0-fold I2: DispatcherContext lifetime is
            # ONE full _decide ↔ _act ↔ _observe cycle. Fresh context per
            # iteration; counters reset between iterations. The
            # ContextVar binding lets `_audit_stack._make_dispatcher_handler`
            # read the live context for budget enforcement + cycle API.
            with dispatcher_context(DispatcherContext()):
                obs = self._observe()
                decision = self._decide(obs)
                if self._should_escalate_branch(decision):
                    self._escalate_branch(decision)  # raises CoderStubNotImplemented
                    return
                if self._should_escalate_plan(decision):
                    self._escalate_plan(decision)  # raises CoderStubNotImplemented
                    return
                if decision.payload.get("done"):
                    return
                self._act(decision)

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
        now = self.substrate.effect.perform(":sys/now", {})

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
                "e": uuid.uuid4().hex,                             # noqa: wall-clock — entity-id (txn precedent)
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

    # --- Escalation gates — Phase 2.2a T6 filled these ---------------

    def _should_escalate_plan(self, decision: LLMDecision) -> bool:
        return decision.kind == "plan"

    def _escalate_plan(self, decision: LLMDecision) -> None:
        """Phase 2.3a — terminal mode-switch to s.plan.execute.

        Delegates to `persistence.coder._planner._escalate_plan_body`,
        which builds the plan from `decision.payload["plan_edn"]`,
        validates, registers handlers on a fresh Dispatcher, and runs
        s.plan.execute. On success: emits :act/result per leaf +
        :plan/done; returns. On failure: emits partial-trace + failing-
        leaf + summary :act/result; raises PlanExecutionFailed.
        Caller-visible failure mirrors _act's contract.
        """
        from persistence.coder._planner import _escalate_plan_body
        _escalate_plan_body(self, decision)

    def _should_escalate_branch(self, decision: LLMDecision) -> bool:
        """Phase 2.3b LD1: ``kind=="branch"`` only.

        Confidence-based escalation half is removed; deferred to Phase
        2.4a once dogfood calibration data exists. Per LD1 R0 codex
        finding: a confidence-based trigger could route non-`branch`
        payloads (e.g. ``kind=="act"`` with ``{op,args}``) into the
        branch escalator, which expects branch-specific payload
        contract — that creates a type/shape mismatch.
        """
        return decision.kind == "branch"

    def _escalate_branch(self, decision: LLMDecision) -> None:
        """Phase 2.3b — terminal mode-switch to ``s.plan.mcts_search``.

        Delegates to ``persistence.coder._searcher._escalate_branch_body``
        which builds a seed plan from ``decision.payload['seed_plan_edn']``,
        validates, runs MCTS, and executes the WINNER once via 2.3a's
        ``_escalate_plan_body``.

        On success: emits ``:act/result`` per winner leaf + ``:plan/done``
        provenance datom; returns. On search-layer failure (LD4): emits
        ``:act/result`` with ``op=':mcts/search'`` and raises
        ``BranchSearchFailed``. On winner-execution failure: 2.3a's
        ``PlanExecutionFailed`` propagates unchanged.

        Caller-visible failure mirrors ``_escalate_plan`` / ``_act``
        contract.
        """
        from persistence.coder._searcher import _escalate_branch_body
        _escalate_branch_body(self, decision)

    # --- REPL pause hook — Phase 2.3d fills this ---------------------

    def _check_pause(self) -> None:
        """Phase 2.3d — REPL pause hook. Delegates to _CoderSteeringSession
        if one is attached (set by _CoderSteeringSession.__post_init__);
        no-op otherwise so all 2.2a/2.3a/2.3b/2.3c tests continue to pass
        without a session attached.

        LD-2 / R0-fold B1: threading.Event semantics — the session's
        _pause_event.wait() blocks when cleared (paused) and returns
        immediately when set (default / resumed state).
        """
        if self._steering_session is not None:
            self._steering_session._check_pause()


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
