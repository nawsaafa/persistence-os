"""Canonical audit-handler stack for substrate-default install — Phase 2.0d W1.

Phase 2.0d W1 (R2 MAJOR M2 fix): :class:`persistence.sdk.Substrate` installs
this handler stack by default at ``Substrate.open(...)`` time so the
"`:plan/edit` / `:fork/*` / `:code/exec` / `:fold/chosen` audit datoms ride
the existing Merkle chain at :mod:`persistence.effect.handlers.audit`"
contract from design § 3.7 + ADR-6 is *enforced*, not merely *available*.

Per ADR-5 capability-denial-default posture: the audit stack is on by
default; opt-out is explicit via ``Substrate.open(uri, audit=False)``.

Why a single canonical stack:
  Phase 2.0a (`:plan/edit`), 2.0b (`:code/exec`), and 2.0c-ext (`:fork/*` +
  legacy `:fold/chosen`) each shipped their own raw-terminator handlers
  (`make_code_exec_handler`, the `_noop_plan_edit_raw_handler` test helper,
  the per-test `fork-raw` handler). Production callers needed to compose
  these themselves — and the SDK never did, hence M2. This module makes
  composition the responsibility of one factory.

Semantics:
  - ``canonical_audit_stack(entries)`` returns a ``Runtime`` whose handler
    chain (innermost-first) is:

      1. ``audit-canonical-raw`` — no-op terminator handler covering every
         canonical-audit op (`:plan/edit`, `:fork/probe`, `:fork/branch`,
         `:fork/score`, `:fork/chosen`, `:code/exec`, `:fold/chosen`). Sits
         under the audit middleware so ``k(args)`` has somewhere to land;
         each clause returns ``None`` (the request datom IS the audit
         signal — there is no further side effect to perform here).
      2. ``clock`` — :func:`make_system_clock_handler` so the audit
         middleware can read ``:clock/now`` for ``recorded_at`` /
         ``latency_ms``.
      3. ``audit`` — :func:`make_audit_handler` middleware wrapping every
         canonical-audit op; appends :class:`AuditEntry` records into
         ``entries`` (the same list bound to ``Substrate._audit_entries``
         under the M2 wiring, so ``s.audit.entries()`` and
         ``s.audit.verify_chain()`` see the post-commit Merkle chain).
      4. ``coder-dispatcher`` — Phase 2.3c.2 LD1+LD2 dispatcher middleware
         wrapping ``:llm/call`` only. Reads the
         :class:`persistence.coder._recursion.DispatcherContext` ContextVar
         and, when bound: enforces 4-layer token budget, depth + request
         bounds via :func:`enter_call` / :func:`exit_call`, and provides
         the ContextVar plumbing for cycle detection (LD2 Layer B; the
         actual ``cycle_path`` push/pop is owned by the coder-side, not
         the middleware). Transparent (pass-through) when no
         DispatcherContext is bound — preserves backward compatibility
         for pre-2.3c.2 callers and substrate-only flows that don't go
         through the coder loop.

  - The runtime is returned **unactivated**. ``Substrate.open`` activates
    it (via ``persistence.effect.runtime._active.set(rt)``) at construction
    time and releases it in ``close()`` — this gives audit coverage for
    the substrate's lifetime without requiring callers to wrap usage in a
    ``with with_runtime(...):`` block.

Phase 2.3c.2 LD5 W3 rescope (commit ``98df051``): ``parent_audit_entry_id``
active wiring (non-None values for nested dispatch) is rescoped to v0.9.x.
The dispatcher handler here threads DispatcherContext for budget + cycle
purposes ONLY; ``parent_audit_entry_id`` stays ``None`` at the audit
middleware layer in 2.3c.2. T2 already shipped the field plumbing through
the dataclass + chain-hash + wire form + datom round-trip + spec layers
(commit ``4dc3fb3``); v0.9.x activates the population path.

Cross-references:
  - design doc § 3.7 per-effect replay table (the ops covered here are the
    same ones that have audit-replay actions defined); ADR-5 default-on
    posture; ADR-6 plan-edit audit invariant.
  - :func:`persistence.txn.transaction._replay_effect_intents` enforces a
    fail-fast guard when intent log is non-empty but no runtime is active
    (raises :class:`persistence.txn.AuditStackMissing`); this stack is the
    intended satisfier of that guard for substrate-driven flows.
  - :mod:`persistence.coder._recursion` for the DispatcherContext + budget
    types + ContextVar binding + push/pop API consumed by the dispatcher
    handler factory below.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from persistence.effect.handlers.audit import AuditEntry, make_audit_handler
from persistence.effect.handlers.clock import make_system_clock_handler
from persistence.effect.runtime import Handler, Runtime

if TYPE_CHECKING:  # pragma: no cover
    pass


# Wrapped by the audit middleware — every op here gets a :audit/<op>
# entry emitted on perform with prev-hash linkage and (per Mimir
# Phase B) Ed25519 signing. The W1 test
# ``test_canonical_audit_stack_covers_phase_2_ops`` pins the
# wrapped-set membership.
CANONICAL_AUDIT_WRAPPED_OPS: tuple[str, ...] = (
    # Phase 2.0a — Plan Edit API
    ":plan/edit",
    # Phase 2.0b — :code sandbox effect
    ":code/exec",
    # Phase 2.0c-extended — DB.fork 4-datom shape
    ":fork/probe",
    ":fork/branch",
    ":fork/score",
    ":fork/chosen",
    # Phase 2.0c (Path-A, kept for backward-compat) — DB.fold chosen-marker
    ":fold/chosen",
    # Phase 2.1b — :llm/call wrapping (provider handler is installed
    # separately at position="bottom" by coder/__main__.py or by
    # library callers via s.effect.install_handler).
    ":llm/call",
    # Phase 2.1c.6 — claim/blob audit anchors (audit-only; the request
    # datom IS the audit signal — facts are committed separately via
    # s.fact.transact for provenance-survives-audit-failure).
    ":claim/emit",
    ":blob/put",
    # Phase 2.2a — fs/shell substantive-return ops; bottom-of-stack handler
    # installed separately via s.effect.install_handler(..., position="bottom").
    ":fs/read",
    ":fs/write",
    ":fs/glob",
    ":fs/grep",
    ":shell/exec",
    # Phase 2.2b — :code/run substantive-return + :git/* thin-wrapper-over-shell.
    # All five WRAPPED only (substantive return; bottom-of-stack handlers
    # installed separately via s.effect.install_handler at position="bottom").
    # :git/* clauses internally call runtime.perform(":shell/exec", ...) wrapped
    # in with mask(audit_handler_name) so ONE outer :git/<sub> AuditEntry emits
    # per op call. See docs/plans/2026-05-06-phase-2.2b-git-code-exec-design.md § 2 LD2.
    ":code/run",
    ":git/diff",
    ":git/status",
    ":git/log",
    ":git/commit",
    # Phase 2.3c.1 — skill library coder integration; substantive-return ops
    # with bottom-of-stack handlers installed separately via
    # s.effect.install_handler(handler, position="bottom"). Audit middleware
    # emits :audit/<op> AuditEntry; for :skill/define the 3 fact datoms
    # (skill/plan, skill/promotion-record, skill/registered-at) are written by
    # SkillLibrary.register via db.transact BEFORE the AuditEntry — matches
    # 2.1c.6 fact-write-first / audit-second pattern. NOT added to
    # CANONICAL_AUDIT_RAW_OPS (substantive return; LD5 design doc).
    ":skill/define",
    ":skill/lookup",
    # Phase 2.3d — REPL steering integration; audit-only ops (request +
    # response per public op on _CoderSteeringSession). Each public op
    # (pause, resume, snapshot, context_at, branch, fold, commit) emits
    # one :repl/request before action + one :repl/response after,
    # producing a replayable stream filterable to :repl/* + :coder/branch.
    # ADDED to CANONICAL_AUDIT_RAW_OPS below — no further side effect; the
    # request datom IS the audit signal (mirrors 2.1c.6 :claim/emit /
    # :blob/put pattern). LD-4.
    ":repl/request",
    ":repl/response",
    # Phase 2.3d — agent-side commit datom emitted by branch() on the
    # parent's audit chain. Lets a downstream replayer reconstruct the
    # branch graph from the parent's log alone (the branch DBs themselves
    # are session-scoped). Audit-only; raw terminator returns None.
    ":coder/branch",
)


# Raw terminator covers ONLY audit-only ops where the request datom IS
# the audit signal and there is no further side effect to perform. Ops
# with real return values (currently :llm/call — provider-supplied
# response shape) are NOT in this set; their bottom-of-stack handler is
# installed separately via ``s.effect.install_handler(handler, position="bottom")``.
CANONICAL_AUDIT_RAW_OPS: tuple[str, ...] = (
    ":plan/edit",
    ":code/exec",
    ":fork/probe",
    ":fork/branch",
    ":fork/score",
    ":fork/chosen",
    ":fold/chosen",
    # Phase 2.1c.6 — both audit-only; raw terminator returns None.
    ":claim/emit",
    ":blob/put",
    # NOT ":llm/call" — see CANONICAL_AUDIT_WRAPPED_OPS above.
    # Phase 2.3d — REPL steering audit-only ops; raw terminator returns
    # None. The :repl/request and :repl/response datoms ARE the audit
    # signal (no further side effect to perform). LD-4. NOT to be added
    # for any op that has a substantive return; the steering session
    # uses these solely to produce the replayable :repl/* stream.
    ":repl/request",
    ":repl/response",
    # Phase 2.3d — :coder/branch agent-side commit datom; audit-only. The
    # branch DB itself is registered in session.branches; the audit
    # entry on the parent's chain is the durable record of the fork.
    ":coder/branch",
)


# Backwards-compatibility alias for any external code that imported the
# old name. Equal to the wrapped set (the audit-coverage perspective is
# the more frequent caller-of-truth). Deprecated in v0.9.0a1; consumers
# should pick CANONICAL_AUDIT_WRAPPED_OPS or CANONICAL_AUDIT_RAW_OPS
# explicitly per their use case.
CANONICAL_AUDIT_OPS: tuple[str, ...] = CANONICAL_AUDIT_WRAPPED_OPS


def _make_canonical_raw_terminator() -> Handler:
    """Raw no-op terminator handler covering every audit-ONLY op.

    Per Phase 2.1b R2 fix-pass: uses ``CANONICAL_AUDIT_RAW_OPS``
    (audit-only subset), NOT ``CANONICAL_AUDIT_WRAPPED_OPS``. Ops with
    a real return value (here ``:llm/call``) are wrapped by the audit
    middleware but their bottom-of-stack handler is the LLM provider —
    not this no-op.

    Each clause returns ``None`` because the request datom IS the audit
    signal for these ops — there is no further side effect. Mirrors the
    per-effect terminators (``make_code_exec_handler``, the
    ``_noop_plan_edit_raw_handler`` test pattern) consolidated here.
    """
    clauses = {op: (lambda _args, _k, _ctx: None) for op in CANONICAL_AUDIT_RAW_OPS}
    return Handler(
        name="audit-canonical-raw",
        wraps=set(CANONICAL_AUDIT_RAW_OPS),
        clauses=clauses,
    )


# ---------------------------------------------------------------------------
# Phase 2.3c.2 — coder-dispatcher middleware (LD1 budget + LD2 cycle plumbing)
# ---------------------------------------------------------------------------


# Layer 2 input-token estimation rate per design § LD1: rough
# ``len(json.dumps(messages)) // 4``. Tokenizers under-count for some
# encodings/role/tool blocks (R1-fold I1 caveat); load-bearing safety is
# Layer 4 post-call accounting which catches per-call overshoot before
# the next nested call.
_LAYER_2_BYTES_PER_TOKEN: int = 4


def _estimate_input_tokens(args: dict[str, Any]) -> int:
    """Rough Layer 2 input-token estimate.

    Per design § LD1 R1.1-fold: ``len(json.dumps(messages)) // 4``. Used
    by Layers 2 (early reject if estimated_input would push over budget)
    and 4 (fallback when provider response lacks ``usage.total_tokens``).
    """
    messages = args.get("messages", [])
    try:
        serialised = json.dumps(messages, default=str)
    except (TypeError, ValueError):  # pragma: no cover — defensive
        serialised = str(messages)
    return len(serialised) // _LAYER_2_BYTES_PER_TOKEN


def _make_dispatcher_handler() -> Handler:
    """Phase 2.3c.2 LD1 + LD2 dispatcher middleware for ``:llm/call``.

    Reads the :class:`DispatcherContext` ContextVar from
    ``persistence.coder._recursion``. When bound, enforces the 4-layer
    token budget + depth + request bounds; transparent pass-through
    otherwise.

    Layer enforcement (per design § LD1 R1-fold + R1.1-fold):

      * **Pre-call (Layer 1):** ``ctx.token_count >= ctx.budget.max_tokens``
        → raise :class:`LLMRecursionBudgetExceeded(field="tokens")` BEFORE
        ``k(args)`` is called.
      * **Pre-call (Layer 2):** estimated input tokens
        (``_estimate_input_tokens``) plus current ``token_count`` over
        budget → raise BEFORE ``k(args)``.
      * **Pre-call (enter_call):** :func:`enter_call` increments ``depth``
        + ``request_count`` and raises on bound violation.
      * **Pre-call (Layer 3):** inject ``args["max_tokens"] = min(existing,
        remaining)`` so providers honoring the field cap output tokens.
      * **Post-call (Layer 4):** read ``result["usage"]["total_tokens"]``
        if present; otherwise fall back to
        ``estimated_input + injected_max_tokens`` (conservative
        overcount; R1.1-fold NICE).
      * **Post-call (exit_call):** :func:`exit_call` decrements depth.
        ``request_count`` + ``token_count`` are cumulative across the
        recursion tree (LD1) — NOT decremented.

    LD5 W3 rescope: this handler does NOT populate
    ``parent_audit_entry_id`` on the wrapped audit handler's ctx. The
    audit middleware below (innermost direction) reads
    ``ctx.get("parent_audit_entry_id")`` and gets ``None`` per
    backward-compat default; v0.9.x will activate the population path.

    Returns:
        :class:`Handler` named ``"coder-dispatcher"`` wrapping ``:llm/call``.
        Add to a runtime's handler list at the OUTER end (highest index)
        so it sees ops before the audit middleware.
    """
    name = "coder-dispatcher"

    def llm_call_clause(args: dict[str, Any], k, ctx_local) -> Any:
        # Lazy import — avoids circular dependency at module-load time:
        # persistence.coder._recursion imports persistence.plan._mcts which
        # eventually pulls in persistence.effect (this module's parent
        # package). Lazy at call-time keeps the import graph clean.
        from persistence.coder._recursion import (
            LLMRecursionBudgetExceeded,
            current_dispatcher_context,
            enter_call,
            exit_call,
        )

        dctx = current_dispatcher_context()
        if dctx is None:
            # Transparent pass-through — preserve pre-2.3c.2 behavior
            # for substrate-only flows that don't go through the coder
            # loop (no DispatcherContext bound).
            return k(args)

        budget = dctx.budget
        # --- Layer 1: best-effort early reject on cumulative token budget
        remaining = budget.max_tokens - dctx.token_count
        if remaining <= 0:
            raise LLMRecursionBudgetExceeded(
                field="tokens",
                limit=budget.max_tokens,
                observed=dctx.token_count,
            )
        # --- Layer 2: best-effort input estimation
        estimated_input = _estimate_input_tokens(args)
        if dctx.token_count + estimated_input > budget.max_tokens:
            raise LLMRecursionBudgetExceeded(
                field="tokens",
                limit=budget.max_tokens,
                observed=dctx.token_count + estimated_input,
            )

        # --- enter_call: depth + request bounds (raises on overflow)
        enter_call(dctx)
        # NOTE: enter_call mutates dctx.depth + dctx.request_count
        # in-place; on raise, the counters are NOT rolled back per LD1.
        # Sequential demo uses don't observe this because the raised
        # error propagates out of the dispatcher_context block and the
        # ctx is discarded; Python-stack-nested cases (v0.9.x) need
        # caller-side cleanup discipline.

        # --- Layer 3: output cap injection
        # ``args`` is mutated in place — the audit middleware below
        # already pops ``_txn_commit`` similarly. Both producers see the
        # capped value; ``args_hash`` covers it.
        existing_max = args.get("max_tokens")
        if existing_max is None:
            injected_max_tokens = remaining
        else:
            injected_max_tokens = min(int(existing_max), remaining)
        args["max_tokens"] = injected_max_tokens

        result: Any = None
        try:
            result = k(args)
            return result
        finally:
            # --- Layer 4: post-call hard accounting
            usage_total: int | None = None
            if isinstance(result, dict):
                usage = result.get("usage")
                if isinstance(usage, dict):
                    raw_total = usage.get("total_tokens")
                    # bool is a subclass of int — exclude bool explicitly.
                    if isinstance(raw_total, int) and not isinstance(raw_total, bool):
                        usage_total = raw_total
            if usage_total is not None:
                dctx.token_count += usage_total
            else:
                # Conservative-overcount substitute (R1.1-fold NICE):
                # provider didn't report usage.total_tokens, so we charge
                # the budget as if input + injected_max_tokens were spent.
                dctx.token_count += estimated_input + injected_max_tokens
            # --- exit_call: decrement depth (cumulative counters stay)
            exit_call(dctx)

    return Handler(
        name=name,
        wraps={":llm/call"},
        clauses={":llm/call": llm_call_clause},
    )


def canonical_audit_stack(
    entries: list[AuditEntry],
    *,
    signer: tuple[str, bytes] | None = None,
) -> Runtime:
    """Return a :class:`Runtime` with the canonical audit handler stack.

    The stack covers every audit-emitting op shipped through Phase 2.0a /
    2.0b / 2.0c / 2.0c-ext; see :data:`CANONICAL_AUDIT_OPS`. The audit
    middleware's :class:`AuditEntry` outputs are appended to ``entries``
    (caller-owned), forming the substrate's session-local Merkle chain.

    Phase 2.3c.2 LD1+LD2: a NEW ``coder-dispatcher`` handler is added at
    the outer end of the stack wrapping ``:llm/call``. It reads the
    :class:`persistence.coder._recursion.DispatcherContext` ContextVar
    and enforces the 4-layer token budget + depth + request bounds when
    a context is bound; pass-through otherwise. See
    :func:`_make_dispatcher_handler` for layer-by-layer semantics.

    Phase 2.4a LD-4: the optional ``signer`` kwarg is forwarded verbatim
    to :func:`make_audit_handler`. When set, every emitted AuditEntry
    carries an Ed25519 ``signature`` + ``signer_id`` over its content
    hash; ``None`` (default) preserves pre-2.4a unsigned behaviour. The
    Mimir Phase B contract at ``handlers/audit.py:488-518`` is the
    source of truth for the tuple shape ``(signer_id, raw_32_byte_priv)``.

    Args:
        entries: caller-owned list to receive :class:`AuditEntry` records.
            ``Substrate.open`` passes ``Substrate._audit_entries`` here so
            ``s.audit.entries()`` and ``s.audit.verify_chain()`` see the
            same chain.
        signer: optional Ed25519 signing tuple ``(signer_id,
            private_key_bytes)`` — see :func:`make_audit_handler`. None
            (default) emits unsigned entries.

    Returns:
        :class:`Runtime` ready to be activated via
        ``persistence.effect.with_runtime(rt)`` or by direct
        ``persistence.effect.runtime._active.set(rt)`` call (the latter
        is the substrate's path — its lifetime matches the substrate's,
        not a single ``with`` block).

    See module docstring for the design rationale; the handler order
    (raw terminator → clock → audit middleware → coder-dispatcher) is
    innermost-first per the :class:`Runtime` constructor's documented
    stack convention. The dispatcher sits OUTSIDE the audit middleware
    so budget rejections raise BEFORE an AuditEntry would be emitted —
    an over-budget call produces zero entries.
    """
    raw = _make_canonical_raw_terminator()
    clock = make_system_clock_handler()
    audit = make_audit_handler(
        entries,
        wraps=set(CANONICAL_AUDIT_WRAPPED_OPS),
        signer=signer,
    )
    dispatcher = _make_dispatcher_handler()
    return Runtime(handlers=[raw, clock, audit, dispatcher])


__all__ = [
    "CANONICAL_AUDIT_OPS",          # backwards-compat alias
    "CANONICAL_AUDIT_RAW_OPS",
    "CANONICAL_AUDIT_WRAPPED_OPS",
    "canonical_audit_stack",
]
