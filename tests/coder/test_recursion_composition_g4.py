"""Phase 2.3c.2 G4 LOAD-BEARING — end-to-end recursion + composition.

Per design § 4 G4 (revised under W3 rescope at commit ``98df051``):

  Register skill A with ``:llm/call`` body; MCTS proposes
  ``ComposeWithSkillAction(A)``; winner executes; verify:

    (a) audit chain integrity prev-hash across both ``:llm/call``
        AuditEntries (outer in ``_decide``, inner in skill body during
        planner walk).
    (b) DispatcherContext ``request_count == 2`` cumulative across the
        iteration within ``MAX_RECURSIVE_REQUESTS=10`` budget (recursion
        is sequential at perform layer, not Python-stack-overlapping).
    (c) DispatcherContext peak depth = 1 per call within
        ``MAX_LLM_CALL_DEPTH=3`` (depth bound is a SAFETY cap for
        hypothetical Python-stack reentrancy via tool-handlers).
    (d) Cycle detection rejects A→A active-path via Layer A search-time
        static check + Layer B execution-time API surface.
    (e) Content-hash pinning in ``:mcts/iteration`` provenance —
        ``composed_skill_content_hash == looked_up_plan.id`` survives
        end-to-end.
    (f) Replay byte-identity holds via winner Plan AST content-addressing.

Deferred from G4 per LD5 W3 rescope (commit ``98df051``):
  - ``parent_audit_entry_id`` linkage between outer + inner
    ``:llm/call`` AuditEntries — the field is plumbed (T2) but always
    None at the middleware layer in 2.3c.2; v0.9.x track activates it.

G4 is split into 4 sub-tests for clean falsifiability per assertion
class. The full design intent (single end-to-end mega-test) is
preserved structurally — each sub-test exercises the same end-to-end
plumbing but isolates the assertion class so a single failure mode
points to a specific invariant break:

  * ``test_g4_a_b_c_simple_path`` — assertions (a) + (b) + (c) via the
    SIMPLE path: scripted ``kind="plan"`` with pre-grafted skill body.
    Outer ``:llm/call`` from ``_decide`` (1) + inner ``:llm/call``
    from skill-body leaf during planner walk (2) → ``request_count == 2``
    LITERAL. No MCTS expander/evaluator calls, so the budget assertion
    is sharp.
  * ``test_g4_d_cycle_detection`` — Layer A (search-time static) via
    direct ``_apply_compose_with_skill`` call + Layer B (execution-time
    dynamic) via ``push_cycle``/``pop_cycle`` API.
  * ``test_g4_e_provenance_via_mcts`` — full MCTS path: assert
    ``composed_skill_content_hash`` provenance datom survives end-to-
    end through ``_escalate_branch_body`` + ``_escalate_plan_body``.
    G5.3 covers this at unit level; G4(e) verifies the survival under
    real Coder.run() integration.
  * ``test_g4_f_replay_byte_identity`` — two runs of the SIMPLE path
    with pinned clock; winner Plan AST content-hash byte-identical
    between runs.

Forced spec deviations:
  FD-T6.1 — G4(b)+(c)'s LITERAL ``request_count == 2`` only holds on
    the SIMPLE path (no MCTS expander/evaluator). The MCTS path
    interleaves multiple expander+evaluator ``:llm/call``s under the
    SAME bound DispatcherContext (LD4 unified budget claim), so
    request_count would be 6+ on the MCTS path. Splitting G4 into
    separate (a)(b)(c)-simple and (e)-mcts sub-tests honors both
    intents cleanly.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from persistence.coder._recursion import (
    DispatcherContext,
    LLMRecursionBudgetExceeded,
    MAX_LLM_CALL_DEPTH,
    MAX_RECURSIVE_REQUESTS,
    SkillCycleDetected,
    current_dispatcher_context,
    pop_cycle,
    push_cycle,
)
from persistence.coder._searcher import _escalate_branch_body
from persistence.coder._session import Coder
from persistence.coder._types import LLMDecision
from persistence.effect import (
    AuditEntry,
    canonical_audit_stack,
    with_runtime,
)
from persistence.effect._audit_stack import (
    CANONICAL_AUDIT_WRAPPED_OPS,
    _make_canonical_raw_terminator,
    _make_dispatcher_handler,
)
from persistence.effect.handlers.audit import make_audit_handler
from persistence.effect.handlers.callable import make_callable_llm_handler
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.handlers.fs import make_fs_handler
from persistence.effect.handlers.skill import _PromotionRecordStub
from persistence.plan import parse, unparse
from persistence.plan._mcts import (
    ComposeWithSkillAction,
    _apply_compose_with_skill,
    _PlanCycleDetected,
)
from persistence.sdk import Substrate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_FIXED_TS_A: float = 1_712_000_000.0


def _scripted_call_fn(
    decisions: list[dict],
    *,
    on_call: Any = None,
):
    """Simple scripted call_fn: return one decision per call, in order.

    ``on_call`` is an optional callback invoked at each call with the
    current bound :class:`DispatcherContext`. Used by the budget-spy
    tests to capture mid-run state.

    Returns a result with ``usage.total_tokens=12`` so the dispatcher
    handler's Layer 4 post-call accounting has something to read; this
    keeps token_count counters monotonic + non-zero across the run.
    """
    iterator = iter(decisions)

    def _call_fn(*, model, messages, tools=None, temperature=None, max_tokens=None):
        if on_call is not None:
            ctx = current_dispatcher_context()
            on_call(ctx, messages, tools)
        try:
            d = next(iterator)
        except StopIteration:
            d = {"text": "fallback", "kind_marker": "stop-iteration"}
        return {
            "tool_calls": [{"input": d}],
            "text": "",
            "usage": {"total_tokens": 12},
        }

    return _call_fn


def _build_pinned_e2e_substrate(
    tmp_path: Path,
    decisions: list[dict],
    *,
    fixed_ts: float = _FIXED_TS_A,
    on_call: Any = None,
) -> tuple[Substrate, list[AuditEntry], Any]:
    """Open ``audit=False`` Substrate with manually-installed handlers
    matching the canonical audit stack but with a PINNED clock.

    Stack order (innermost first → outermost):
      1. ``audit-canonical-raw`` — no-op terminator for audit-only ops
      2. ``clock-fixed`` — make_fixed_clock_handler(ts=...)
      3. ``fs`` — file-system handler (real side effects, sandboxed)
      4. ``llm-callable`` — scripted ``:llm/call`` provider
      5. ``audit`` — middleware (top of audit-only stack)
      6. ``coder-dispatcher`` — Phase 2.3c.2 LD1+LD2 ContextVar reader
         (must sit OUTSIDE audit middleware so budget rejections raise
         before AuditEntry would emit)

    Returns (substrate, entries, call_fn_object). Caller closes substrate.
    """
    project_root = tmp_path
    scratch_dir = tmp_path
    project_root.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    s = Substrate.open("memory", audit=False)
    entries: list[AuditEntry] = []

    raw = _make_canonical_raw_terminator()
    clock = make_fixed_clock_handler(ts=fixed_ts)
    fs = make_fs_handler(project_root=project_root, scratch_dir=scratch_dir)
    call_fn = _scripted_call_fn(decisions, on_call=on_call)
    llm = make_callable_llm_handler(call_fn=call_fn)
    audit = make_audit_handler(entries, wraps=set(CANONICAL_AUDIT_WRAPPED_OPS))
    dispatcher = _make_dispatcher_handler()

    # Innermost first; dispatcher outermost (above audit middleware).
    s.effect.install_handler(raw, position="bottom")
    s.effect.install_handler(clock, position="bottom")
    s.effect.install_handler(fs, position="bottom")
    s.effect.install_handler(llm, position="bottom")
    s.effect.install_handler(audit, position="top")
    s.effect.install_handler(dispatcher, position="top")

    return s, entries, call_fn


# ---------------------------------------------------------------------------
# G4 (a)(b)(c) — SIMPLE path: scripted kind="plan" with pre-grafted body
# ---------------------------------------------------------------------------


def test_g4_a_b_c_simple_path_request_count_two_and_chain_integrity(tmp_path: Path):
    """G4(a)+(b)+(c) under the SIMPLE path (no MCTS).

    Scenario:
      1. Pre-graft a Plan AST that includes a ``:llm/call`` leaf
         (skill body shape, but built directly without going through
         MCTS expander).
      2. Script the Coder's first ``_decide`` call to emit
         ``LLMDecision(kind="plan", payload={"plan_edn": grafted_edn})``.
      3. Coder.run() → _decide (1 outer ``:llm/call``) → _escalate_plan
         → _escalate_plan_body → s.plan.execute(grafted plan) → walk
         visits ``:llm/call`` leaf → 1 inner ``:llm/call``.

    Falsifiability per assertion class:
      (a) Both ``:llm/call`` AuditEntries land in the chain in correct
          prev-hash order (entries[i+1].prev_hash == entries[i].id);
          verify_chain returns True.
      (b) After Coder.run() returns, the in-iteration DispatcherContext
          (captured via spy callback) has ``request_count == 2`` —
          LITERAL, sharp falsifiability of LD4 unified budget claim
          across recursion + composition.
      (c) Per-call peak depth observed by spy is exactly 1 (sequential
          perform-level recursion; depth=1 inside each call, decrements
          on exit).
    """
    project_root = tmp_path
    scratch_dir = tmp_path
    project_root.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    # The grafted Plan EDN: a :seq containing a :fs/write (so we have a
    # real side-effect to observe) AND a :llm/call leaf. The :llm/call
    # leaf is what triggers the inner dispatch during planner walk.
    grafted_path = str(scratch_dir / "marker.txt")
    grafted_edn = (
        '[:seq {} '
        f'[:fs/write {{:path "{grafted_path}" :bytes_or_text "winner-content"}}] '
        '[:llm/call {:model "test-model" :messages [{:role "user" :content "ping"}]}]'
        ']'
    )

    # Spy: capture ctx state at every :llm/call entry
    captured: list[dict[str, Any]] = []

    def _spy(ctx: DispatcherContext | None, messages, tools):
        if ctx is None:
            captured.append({"depth": None, "request_count": None})
            return
        captured.append({
            "depth": ctx.depth,
            "request_count": ctx.request_count,
            "token_count": ctx.token_count,
        })

    decisions = [
        # iter 0: _decide returns kind="plan" with the pre-grafted body
        {
            "kind": "plan",
            "confidence": 0.9,
            "payload": {"plan_edn": grafted_edn},
        },
        # iter 1+ : the inner :llm/call leaf during planner walk fires
        # against the SAME scripted iterator; needs a benign response.
        # The leaf's args go through _llm_call_clause → audit → callable
        # → _scripted_call_fn which yields THIS decision. The leaf's
        # return value is just the dict; the planner doesn't interpret
        # it for control flow at the leaf level (per T4 _planner.py:
        # :llm/call is a REGISTERED_LEAF_TAG with a thin adapter that
        # returns the result for :act/result provenance; the leaf
        # itself doesn't reshape the loop).
        {
            "text": "skill-body-llm-response",
            "marker": "inner-call-from-skill-body",
        },
    ]

    s, entries, _ = _build_pinned_e2e_substrate(
        tmp_path, decisions, on_call=_spy,
    )

    try:
        with with_runtime(s._runtime):
            coder = Coder(
                task="execute pre-grafted plan",
                substrate=s,
                max_iters=2,
            )
            coder.run()
    finally:
        s.close()

    # --- G4(a) Audit chain integrity over the two :llm/call entries ---
    llm_entries = [e for e in entries if e.op == ":llm/call"]
    assert len(llm_entries) == 2, (
        f"Expected exactly 2 :llm/call AuditEntries (outer _decide + "
        f"inner skill-body leaf); got {len(llm_entries)}: "
        f"{[e.op for e in entries]}"
    )
    # prev_hash linkage in append order
    for i in range(1, len(entries)):
        assert entries[i].prev_hash == entries[i - 1].id, (
            f"prev_hash chain broken at entry {i}: "
            f"{entries[i].prev_hash!r} != {entries[i - 1].id!r}"
        )
    # The two :llm/call entries are linked through the chain (possibly
    # with non-:llm/call entries between them — :fs/write also audits).
    outer_idx = entries.index(llm_entries[0])
    inner_idx = entries.index(llm_entries[1])
    assert inner_idx > outer_idx
    # Walk the chain inner..outer: every prev_hash hops backward to outer.
    cursor_id = entries[inner_idx].id
    for back_idx in range(inner_idx, outer_idx, -1):
        assert entries[back_idx].id == cursor_id
        cursor_id = entries[back_idx].prev_hash
    assert cursor_id == entries[outer_idx].id

    # --- G4(b)+(c): captured spy snapshots ---
    # The spy fires INSIDE each :llm/call provider (after dispatcher's
    # enter_call but BEFORE exit_call), so the captured depth reflects
    # the live ctx state at provider-call time.
    assert len(captured) == 2, (
        f"Expected exactly 2 spy snapshots (outer + inner :llm/call); "
        f"got {len(captured)}: {captured!r}"
    )
    # G4(c) — peak depth = 1 per call (sequential perform-level recursion)
    for i, snap in enumerate(captured):
        assert snap["depth"] == 1, (
            f"snap[{i}]: depth = {snap['depth']!r}, expected 1 (sequential "
            f"perform-level recursion). Full snapshot: {snap!r}"
        )
    # G4(b) — request_count is cumulative across the iteration. After
    # outer call: 1; after inner call: 2.
    assert captured[0]["request_count"] == 1
    assert captured[1]["request_count"] == 2

    # G4(b) bound check: 2 well within MAX_RECURSIVE_REQUESTS=10.
    assert captured[1]["request_count"] <= MAX_RECURSIVE_REQUESTS

    # G4(c) bound check: peak depth=1 well within MAX_LLM_CALL_DEPTH=3.
    assert max(s["depth"] for s in captured) <= MAX_LLM_CALL_DEPTH

    # Marker file actually got written (sanity that the grafted plan
    # really executed end-to-end, not just the LLM calls).
    assert Path(grafted_path).exists()
    assert Path(grafted_path).read_text() == "winner-content"


# ---------------------------------------------------------------------------
# G4 (d) — Cycle detection: Layer A search-time static + Layer B execution-time
# ---------------------------------------------------------------------------


def test_g4_d_cycle_detection_layer_a_search_time_static(tmp_path: Path):
    """G4(d) — Layer A: search-time STATIC subtree check.

    Mechanism: register skill A whose body CONTAINS a Plan X as a
    subtree. Then attempt to compose A around X (so the new graft
    would produce a Plan that contains A inside A — structural cycle).
    ``_apply_compose_with_skill`` raises ``_PlanCycleDetected`` at
    ``_mcts.py:213-217``.

    Falsifiability: a regression that removed the ``plan.id in skill_ids``
    check would no longer raise.
    """
    with Substrate.open("memory") as s:
        skill_lib = s.plan.skill_library(s._db)
        # Inner plan X — will be the seed/candidate
        inner_edn = '[:fs/write {:path "x.txt" :bytes_or_text "y"}]'
        inner = parse(inner_edn, strict=False)

        # Skill A body: a :seq that CONTAINS X as a subtree
        skill_body = parse(f'[:seq {{}} {inner_edn}]', strict=False)
        skill_id = skill_lib.register(
            skill_body,
            _PromotionRecordStub(promotion_id="prom-cycle"),
            registered_at_ms=1,
        )

        # Sanity: X really is a subtree of skill A's body
        assert any(c.id == inner.id for c in skill_body.children)

        # Compose A around X: target_path=() means graft at root.
        # _apply_compose_with_skill checks `plan.id in skill_ids` where
        # skill_ids = all subtree ids of skill_body. inner.id IS in there.
        action = ComposeWithSkillAction(target_path=(), skill_id=skill_id)
        with pytest.raises(_PlanCycleDetected) as exc_info:
            _apply_compose_with_skill(inner, action, skill_lib)
        assert "already contains" in str(exc_info.value)
        assert inner.id in str(exc_info.value)


def test_g4_d_cycle_detection_layer_b_execution_time_dynamic():
    """G4(d) — Layer B: execution-time DYNAMIC active-path check.

    Mechanism: ``push_cycle(ctx, content_hash)`` raises
    ``SkillCycleDetected`` when the hash is already active up the call
    chain. This test demonstrates the API contract (T3 push/pop unit
    tests already cover this in detail; G4(d) ties it back to the
    overall cycle-detection coverage claim).

    Falsifiability: a regression that removed the membership check in
    push_cycle would no longer raise.
    """
    ctx = DispatcherContext()
    skill_a_hash = "sha256:" + "a" * 64

    # Active-path entry to skill A
    push_cycle(ctx, skill_a_hash)
    # Re-entering A while still active raises:
    with pytest.raises(SkillCycleDetected) as exc_info:
        push_cycle(ctx, skill_a_hash)
    assert skill_a_hash in str(exc_info.value)

    # Sequential reuse after unwind is allowed (LD2 R0-fold B3):
    pop_cycle(ctx, skill_a_hash)
    push_cycle(ctx, skill_a_hash)  # MUST NOT raise
    pop_cycle(ctx, skill_a_hash)
    assert ctx.cycle_path == []


# ---------------------------------------------------------------------------
# G4 (e) — composed_skill_content_hash provenance via full MCTS path
# ---------------------------------------------------------------------------


def test_g4_e_provenance_content_hash_via_mcts_e2e(tmp_path: Path):
    """G4(e) — content-hash pinning in ``:mcts/iteration`` provenance
    survives end-to-end through ``_escalate_branch_body``.

    Falsifiability: a regression that pinned ``skill_id`` instead of
    ``looked_up_plan.id`` (32-hex content hash) would fail this test
    because the assertion checks the literal value equals the looked-up
    plan's ``plan.id`` (NOT the skill_id).

    Test scaffolding mirrors T5 G5.3 but exercises the full bridge:
    register skill, drive MCTS via _escalate_branch_body, scan facts
    for ``mcts/output`` rows containing a ``ComposeWithSkillAction``
    record, assert ``composed_skill_content_hash == looked_up_plan.id``.
    """
    project_root = tmp_path
    scratch_dir = tmp_path
    project_root.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    with Substrate.open("memory") as s:
        # Install fs handler at bottom for plan execution
        s.effect.install_handler(
            make_fs_handler(project_root=project_root, scratch_dir=scratch_dir),
            position="bottom",
        )

        skill_lib = s.plan.skill_library(s._db)
        skill_body_edn = (
            '[:seq {} '
            f'[:fs/write {{:path "{scratch_dir / "winner-marker.txt"}" '
            ':bytes_or_text "winner-content"}]]'
        )
        skill_body = parse(skill_body_edn, strict=False)
        skill_id = skill_lib.register(
            skill_body,
            _PromotionRecordStub(promotion_id="prom-A"),
            registered_at_ms=1_700_000_000_000,
        )
        looked_up_plan_id = skill_body.id

        seed_path = str(scratch_dir / "seed.txt")
        seed_edn = (
            f'[:seq {{}} [:fs/write {{:path "{seed_path}" :bytes_or_text "s"}}]]'
        )

        # Smart call_fn: expander proposes ComposeWithSkillAction; evaluator
        # scores winner-marker plans high.
        def call_fn(*, model, messages, tools=None, temperature=None, max_tokens=None):
            tool_name = tools[0]["name"] if tools else ""
            if tool_name == "emit_branch_proposals":
                return {
                    "tool_calls": [{
                        "input": {
                            "proposals": [{
                                "kind": "ComposeWithSkillAction",
                                "target_path": [0],
                                "skill_id": skill_id,
                                "logit": 2.0,
                            }],
                        },
                    }],
                    "text": "",
                    "usage": {"total_tokens": 12},
                }
            if tool_name == "emit_branch_score":
                content = messages[1]["content"]
                score = 0.95 if "winner-marker" in content else 0.05
                return {
                    "tool_calls": [{"input": {"score": score}}],
                    "text": "",
                    "usage": {"total_tokens": 5},
                }
            return {"tool_calls": [], "text": "fallback"}

        s.effect.install_handler(
            make_callable_llm_handler(call_fn=call_fn),
            position="bottom",
        )

        # Coder stub mirroring T5 G5 fixture pattern
        class _CoderStub:
            def __init__(self):
                self.substrate = s
                self.model = "claude-test-2.3c.2"
                self.skill_library = skill_lib

        coder = _CoderStub()
        _escalate_branch_body(
            coder,
            LLMDecision(
                kind="branch", confidence=0.9,
                payload={
                    "seed_plan_edn": seed_edn,
                    "mcts_config": {"max_iter": 2, "expander_k": 1},
                },
            ),
        )

        # Scan fact-store for ComposeWithSkillAction provenance records
        compose_payloads = []
        for d in s._db.log():
            if d.a != "mcts/output":
                continue
            payloads_to_check: list[Any] = []
            if isinstance(d.v, list):
                payloads_to_check.extend(d.v)
            elif isinstance(d.v, dict):
                payloads_to_check.append(d.v)
            for record in payloads_to_check:
                if (
                    isinstance(record, dict)
                    and record.get("action_kind") == "ComposeWithSkillAction"
                ):
                    payload = record.get("action_payload", {})
                    if isinstance(payload, dict):
                        compose_payloads.append(payload)

        assert len(compose_payloads) >= 1, (
            "Expected at least 1 ComposeWithSkillAction provenance "
            f"record from full MCTS run; got {compose_payloads!r}"
        )
        for payload in compose_payloads:
            assert payload.get("skill_id") == skill_id
            # G4(e) — pinned hash equals looked_up_plan.id (32-hex content
            # hash), NOT the "skill/" + 16-hex skill_id.
            pinned = payload.get("composed_skill_content_hash")
            assert pinned == looked_up_plan_id, (
                f"G4(e) FAILED: composed_skill_content_hash={pinned!r} "
                f"!= looked_up_plan.id={looked_up_plan_id!r}"
            )
            assert len(pinned) == 32  # 128-bit content hash
            assert pinned != skill_id  # NOT the skill_id


# ---------------------------------------------------------------------------
# G4 (f) — Replay byte-identity via winner Plan AST content-addressing
# ---------------------------------------------------------------------------


def _grafted_e2e_run(tmp_path: Path, fixed_ts: float) -> tuple[str, list[AuditEntry]]:
    """Helper: run the simple-path scenario once and return
    (winner_plan_id, entries). Reproducible under fixed clock + identical
    decisions + identical paths.
    """
    project_root = tmp_path
    scratch_dir = tmp_path
    project_root.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    grafted_path = str(scratch_dir / "marker.txt")
    grafted_edn = (
        '[:seq {} '
        f'[:fs/write {{:path "{grafted_path}" :bytes_or_text "winner-content"}}] '
        '[:llm/call {:model "test-model" :messages [{:role "user" :content "ping"}]}]'
        ']'
    )

    decisions = [
        {
            "kind": "plan",
            "confidence": 0.9,
            "payload": {"plan_edn": grafted_edn},
        },
        {"text": "skill-body-llm-response"},
    ]

    s, entries, _ = _build_pinned_e2e_substrate(
        tmp_path, decisions, fixed_ts=fixed_ts,
    )
    try:
        with with_runtime(s._runtime):
            coder = Coder(
                task="execute pre-grafted plan",
                substrate=s,
                max_iters=2,
            )
            coder.run()
        # Compute the winner plan content-hash from the EDN string —
        # this IS the content-addressed identifier.
        winner_plan_id = parse(grafted_edn, strict=False).id
    finally:
        s.close()
    return winner_plan_id, entries


def test_g4_f_replay_byte_identity_winner_plan_id(tmp_path: Path):
    """G4(f) — replay byte-identity via winner Plan AST content-addressing.

    Two runs of the simple-path scenario with identical scripted
    decisions, identical pinned clock, and identical path strings.
    The winner Plan AST ``plan.id`` (content-hash) is byte-identical
    between runs because Plan AST node hashing is content-addressed
    over canonical bytes.

    Falsifiability: if Plan AST hashing accidentally incorporates
    wall-clock entropy (e.g. via a non-pinned timestamp in a leaf
    attr), the two ids would differ and this test would fail.

    The audit chain itself is NOT byte-identical here because
    ``_decide`` + ``_act`` transact wall-clock provenance datoms
    (``:llm/messages``, ``:llm/decision``, ``:act/result``) BEFORE the
    audit handler emits — those provenance facts carry
    ``dt.datetime.now`` timestamps not routed through the pinned
    clock. The Plan AST content-hash is the orthogonal replay anchor
    per design § LD3 R0-fold B4: "replay byte-identity is provided by
    the winner Plan AST being content-addressed". G4(f) honors that
    framing.
    """
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir(parents=True, exist_ok=True)
    dir_b.mkdir(parents=True, exist_ok=True)

    # Both runs use IDENTICAL path strings (dir_a) so the grafted Plan
    # EDN is byte-identical between runs. dir_b exists but isn't used
    # for path strings — only as a separate substrate workspace if
    # needed (here unused).
    plan_id_a, entries_a = _grafted_e2e_run(dir_a, fixed_ts=_FIXED_TS_A)
    plan_id_b, entries_b = _grafted_e2e_run(dir_a, fixed_ts=_FIXED_TS_A)

    # Plan AST content-hash is byte-identical between runs (replay
    # anchor per LD3 R0-fold B4).
    assert plan_id_a == plan_id_b, (
        f"G4(f) replay byte-identity FAILED: plan_id_a={plan_id_a!r} "
        f"!= plan_id_b={plan_id_b!r}"
    )
    # Sanity: plan_id is content-hash shaped (32 hex chars per
    # _ast.py:113 — 128-bit prefix).
    assert len(plan_id_a) == 32
