"""Phase 2.3c.1 — :skill/* effect handlers (define, lookup).

Two new audit-wrapped substantive-return ops bridging the substrate effect
runtime to :class:`persistence.plan.SkillLibrary`:

  * ``:skill/define`` — registers a Plan AST as a skill; returns the
    content-addressed ``skill_id`` plus the input plan's ``plan_id``.
    Three fact datoms (``skill/plan``, ``skill/promotion-record``,
    ``skill/registered-at``) are written by ``SkillLibrary.register``
    BEFORE the audit middleware emits the outer ``:audit/<:skill/define>``
    AuditEntry — fact-write-first / audit-second pattern (matches the
    2.1c.6 ``:claim/emit`` / ``:blob/put`` precedent for
    provenance-survives-audit-failure).

  * ``:skill/lookup`` — resolves a previously-registered ``skill_id``;
    returns the canonical-EDN form of the registered Plan AST plus the
    ``promotion_id`` and ``plan_id``. Audit-only side effect (no fact
    write).

LD1 / LD2 closure pattern (R0-fold B1): the factory takes an INJECTED
:class:`SkillLibrary` instance and the two clauses close over it. Per-call
construction is FORBIDDEN — :class:`SkillLibrary` keeps its skill-id →
``Node`` and promotion-id → record-like lookup graph in process-local
in-memory caches (``_plans`` / ``_records``). A fresh SkillLibrary per
call would have empty caches, so ``lookup`` would always return ``None``.

LD3 :class:`_PromotionRecordStub` — minimal in-coder fabrication
satisfying ``_PromotionRecordLike`` (only ``promotion_id: str`` is
read by SkillLibrary code). A7's full ``PromotionRecord`` integration
is queued for v0.9.x; the stub is OPAQUE PROVENANCE only and makes no
correctness claim about promotion validity.

LD4 — three :class:`ValueError` subclasses for the failure-mode taxonomy:

  * :class:`SkillNotFound` — ``:skill/lookup`` on an unregistered
    ``skill_id``.
  * :class:`SkillDefineValidation` — ``:skill/define`` arg-shape failures.
  * :class:`SkillLookupValidation` — ``:skill/lookup`` arg-shape failures.

Forced spec deviations vs T1 spec:
  FD1: arg keys are BARE strings (no leading colon). The EDN parser
       converts ``{:plan-edn "..."}`` map-keys to plain strings BEFORE
       the dispatcher adapter at ``_planner.py:303`` calls
       ``substrate.effect.perform(tag, dict(node.attrs))``. Confirmed via
       :file:`fs.py:33` (``args["path"]``) and :file:`_parse.py:67-73`
       (the EDN ``ImmutableDict`` keyword-key conversion). The handler's
       PUBLIC RETURN map uses keyword-form keys (``":skill-id"`` etc.)
       per LD1 / LD2 spec — the substrate encodes returns into
       ``:act/result.result_summary`` and downstream code reads them
       symmetrically.

References:
  docs/plans/2026-05-07-phase-2.3c.1-skill-library-design.md §§ LD1-LD5
  src/persistence/effect/handlers/fs.py — make_fs_handler precedent
  src/persistence/plan/_skill_library.py — SkillLibrary contract
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from persistence.effect.runtime import Handler
from persistence.plan import SkillLibrary, parse, unparse
from persistence.plan._errors import ParseError


# ---------------------------------------------------------------------------
# Error classes (LD4)
# ---------------------------------------------------------------------------


class SkillNotFound(ValueError):
    """:skill/lookup on an unregistered ``skill_id``.

    Carries ``skill_id`` for ergonomic introspection in
    :class:`persistence.coder._planner_errors.PlanExecutionFailed`'s
    ``error_repr``.
    """

    def __init__(self, *, skill_id: str) -> None:
        self.skill_id = skill_id
        super().__init__(f"SkillNotFound: skill_id={skill_id!r}")


class SkillDefineValidation(ValueError):
    """:skill/define arg-shape failure — missing field, wrong-type field,
    or unparseable :plan-edn.

    Mirrors the
    :class:`persistence.coder._planner_errors.PlanPayloadValidation`
    shape (``field=`` / ``reason=``) so downstream catches read
    consistently.
    """

    def __init__(self, *, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"SkillDefineValidation[{field}]: {reason}")


class SkillLookupValidation(ValueError):
    """:skill/lookup arg-shape failure — missing or wrong-type
    ``skill-id``.
    """

    def __init__(self, *, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"SkillLookupValidation[{field}]: {reason}")


# ---------------------------------------------------------------------------
# Promotion-record stub (LD3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _PromotionRecordStub:
    """Minimal stub satisfying ``_PromotionRecordLike`` structurally.

    LD3 invariant boundary: ``promotion_id`` is OPAQUE PROVENANCE only.
    2.3c.1 makes NO correctness claim about promotion validity. A7's
    real ``PromotionRecord`` (when shipped) MAY reject or ignore skills
    registered through this stub. Same minimal-stub pattern that
    ``tests/plan/test_skill_library.py`` uses today.
    """

    promotion_id: str


# ---------------------------------------------------------------------------
# Arg-validation helpers
# ---------------------------------------------------------------------------


def _required_str_define(args: Mapping[str, Any], key: str) -> str:
    """Return ``args[key]`` if present and ``isinstance(str)``; else raise
    :class:`SkillDefineValidation`."""
    if key not in args:
        raise SkillDefineValidation(
            field=key,
            reason=f"missing required field {key!r}",
        )
    v = args[key]
    if not isinstance(v, str):
        raise SkillDefineValidation(
            field=key,
            reason=f"expected str, got {type(v).__name__}",
        )
    return v


def _required_int_define(args: Mapping[str, Any], key: str) -> int:
    """Return ``args[key]`` if present and ``isinstance(int)``; else raise
    :class:`SkillDefineValidation`. Booleans are rejected (Python's
    ``bool`` is an ``int`` subclass; allowing them would let
    ``True``/``False`` slip into the ``skill/registered-at`` datom)."""
    if key not in args:
        raise SkillDefineValidation(
            field=key,
            reason=f"missing required field {key!r}",
        )
    v = args[key]
    if isinstance(v, bool) or not isinstance(v, int):
        raise SkillDefineValidation(
            field=key,
            reason=f"expected int, got {type(v).__name__}",
        )
    return v


def _required_str_lookup(args: Mapping[str, Any], key: str) -> str:
    """Return ``args[key]`` if present and ``isinstance(str)``; else raise
    :class:`SkillLookupValidation`."""
    if key not in args:
        raise SkillLookupValidation(
            field=key,
            reason=f"missing required field {key!r}",
        )
    v = args[key]
    if not isinstance(v, str):
        raise SkillLookupValidation(
            field=key,
            reason=f"expected str, got {type(v).__name__}",
        )
    return v


# ---------------------------------------------------------------------------
# Factory (LD1 + LD2)
# ---------------------------------------------------------------------------


def make_skill_handler(
    skill_library: SkillLibrary,
    *,
    name: str = "skill",
) -> Handler:
    """Factory for the :skill/* handler pair.

    Closes over a SINGLE long-lived :class:`SkillLibrary` instance per
    LD1 / LD2 R0-fold B1. The caller (test fixture or production CLI)
    constructs ``SkillLibrary`` ONCE per :class:`Substrate` via
    ``s.plan.skill_library(s._db)`` and passes it here. The instance's
    ``_plans`` / ``_records`` caches MUST persist across :skill/define
    and :skill/lookup calls so :meth:`SkillLibrary.lookup` can round-trip
    the Plan AST :class:`Node`.

    Install via
    ``substrate.effect.install_handler(handler, position="bottom")``.
    The audit middleware (registered separately via
    ``CANONICAL_AUDIT_WRAPPED_OPS`` extended in T2) handles the outer
    ``:audit/<:skill/<sub>>`` AuditEntry emission.

    Parameters
    ----------
    skill_library
        The injected long-lived :class:`SkillLibrary` instance.
    name
        Handler ``name`` for the runtime; default ``"skill"``. Visible
        to ``mask(name)`` callers.
    """

    def _skill_define_clause(
        args: dict[str, Any],
        _k: Any,
        _ctx: dict[str, Any],
    ) -> Mapping[str, Any]:
        # FD1: arg keys are BARE — leading EDN colons are stripped by
        # the parser before reaching the dispatcher adapter.
        plan_edn = _required_str_define(args, "plan-edn")
        promotion_id = _required_str_define(args, "promotion-id")
        registered_at_ms = _required_int_define(args, "registered-at-ms")
        try:
            plan = parse(plan_edn, strict=False)  # FD3: strict=False per LD1
        except ParseError as exc:
            raise SkillDefineValidation(
                field="plan-edn",
                reason=f"parse failed: {exc}",
            ) from exc
        skill_id = skill_library.register(  # CLOSURE — not per-call construction
            plan,
            _PromotionRecordStub(promotion_id=promotion_id),
            registered_at_ms=registered_at_ms,
        )
        return {":skill-id": skill_id, ":plan-id": plan.id}

    def _skill_lookup_clause(
        args: dict[str, Any],
        _k: Any,
        _ctx: dict[str, Any],
    ) -> Mapping[str, Any]:
        # FD1: arg keys are BARE.
        skill_id = _required_str_lookup(args, "skill-id")
        result = skill_library.lookup(skill_id)  # CLOSURE — same instance as define
        if result is None:
            raise SkillNotFound(skill_id=skill_id)
        plan, record = result
        return {
            ":plan-edn": unparse(plan),  # FD4: canonical-EDN bare-keyword form
            ":promotion-id": record.promotion_id,
            ":plan-id": plan.id,
        }

    return Handler(
        name=name,
        wraps={":skill/define", ":skill/lookup"},
        clauses={
            ":skill/define": _skill_define_clause,
            ":skill/lookup": _skill_lookup_clause,
        },
    )


__all__ = [
    "SkillDefineValidation",
    "SkillLookupValidation",
    "SkillNotFound",
    "_PromotionRecordStub",
    "make_skill_handler",
]
