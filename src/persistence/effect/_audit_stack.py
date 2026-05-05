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

  - The runtime is returned **unactivated**. ``Substrate.open`` activates
    it (via ``persistence.effect.runtime._active.set(rt)``) at construction
    time and releases it in ``close()`` — this gives audit coverage for
    the substrate's lifetime without requiring callers to wrap usage in a
    ``with with_runtime(...):`` block.

Cross-references:
  - design doc § 3.7 per-effect replay table (the ops covered here are the
    same ones that have audit-replay actions defined); ADR-5 default-on
    posture; ADR-6 plan-edit audit invariant.
  - :func:`persistence.txn.transaction._replay_effect_intents` enforces a
    fail-fast guard when intent log is non-empty but no runtime is active
    (raises :class:`persistence.txn.AuditStackMissing`); this stack is the
    intended satisfier of that guard for substrate-driven flows.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

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


def canonical_audit_stack(entries: list[AuditEntry]) -> Runtime:
    """Return a :class:`Runtime` with the canonical audit handler stack.

    The stack covers every audit-emitting op shipped through Phase 2.0a /
    2.0b / 2.0c / 2.0c-ext; see :data:`CANONICAL_AUDIT_OPS`. The audit
    middleware's :class:`AuditEntry` outputs are appended to ``entries``
    (caller-owned), forming the substrate's session-local Merkle chain.

    Args:
        entries: caller-owned list to receive :class:`AuditEntry` records.
            ``Substrate.open`` passes ``Substrate._audit_entries`` here so
            ``s.audit.entries()`` and ``s.audit.verify_chain()`` see the
            same chain.

    Returns:
        :class:`Runtime` ready to be activated via
        ``persistence.effect.with_runtime(rt)`` or by direct
        ``persistence.effect.runtime._active.set(rt)`` call (the latter
        is the substrate's path — its lifetime matches the substrate's,
        not a single ``with`` block).

    See module docstring for the design rationale; the handler order
    (raw terminator → clock → audit middleware) is innermost-first per
    the :class:`Runtime` constructor's documented stack convention.
    """
    raw = _make_canonical_raw_terminator()
    clock = make_system_clock_handler()
    audit = make_audit_handler(entries, wraps=set(CANONICAL_AUDIT_WRAPPED_OPS))
    return Runtime(handlers=[raw, clock, audit])


__all__ = [
    "CANONICAL_AUDIT_OPS",          # backwards-compat alias
    "CANONICAL_AUDIT_RAW_OPS",
    "CANONICAL_AUDIT_WRAPPED_OPS",
    "canonical_audit_stack",
]
