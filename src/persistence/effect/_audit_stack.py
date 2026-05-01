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


# Canonical audit ops covered by the substrate-default stack. The set is
# the union of every audit-emitting op shipped through Phase 2.0a / 2.0b /
# 2.0c / 2.0c-ext. Adding a new audit op (Phase 2.x) means adding it here
# AND wiring its raw terminator below — both sides MUST stay in sync, and
# the W1 test ``test_canonical_audit_stack_covers_phase_2_ops`` pins the
# membership.
CANONICAL_AUDIT_OPS: tuple[str, ...] = (
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
)


def _make_canonical_raw_terminator() -> Handler:
    """Raw no-op terminator handler covering every canonical-audit op.

    The audit middleware (built by :func:`make_audit_handler`) calls
    ``k(args)`` to delegate downstream; without a terminator below, the
    runtime raises :class:`persistence.effect.Unhandled`. For audit-only
    ops the request datom IS the audit signal, so each clause returns
    ``None``. Mirrors the per-effect terminators
    :func:`persistence.effect.make_code_exec_handler` and the
    ``_noop_plan_edit_raw_handler`` pattern in
    ``tests/plan/test_edit_audit.py`` — consolidated here so the SDK
    does not have to compose them ad-hoc.
    """
    clauses = {op: (lambda _args, _k, _ctx: None) for op in CANONICAL_AUDIT_OPS}
    return Handler(
        name="audit-canonical-raw",
        wraps=set(CANONICAL_AUDIT_OPS),
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
    audit = make_audit_handler(entries, wraps=set(CANONICAL_AUDIT_OPS))
    return Runtime(handlers=[raw, clock, audit])


__all__ = [
    "CANONICAL_AUDIT_OPS",
    "canonical_audit_stack",
]
