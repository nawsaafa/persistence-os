"""Phase 2.3c.1 G6 — negative tests + PlanExecutionFailed propagation.

Per design § 4 G6 + LD4: error-class hierarchy + frozen+slots stub +
runtime_checkable Protocol satisfaction + plan-execution failure
propagation.

6 sub-cases:

  G6.1 — ``:skill/define`` failure inside a plan execution propagates
         as :class:`PlanExecutionFailed` from ``_escalate_plan_body``;
         ``failure.error_repr`` mentions ``SkillDefineValidation``.
  G6.2 — ``:skill/lookup`` of an unregistered id inside a plan execution
         propagates as :class:`PlanExecutionFailed`;
         ``failure.error_repr`` mentions ``SkillNotFound``.
  G6.3 — :class:`SkillNotFound`, :class:`SkillDefineValidation`,
         :class:`SkillLookupValidation` are all subclasses of
         :class:`ValueError` (matches design LD4).
  G6.4 — :class:`_PromotionRecordStub` is ``frozen=True`` + ``slots=True``;
         ``dataclasses.fields()`` shows only ``promotion_id``;
         ``__slots__`` is set; assignment to a fresh attr raises
         (frozen contract).
  G6.5 — :class:`_PromotionRecordStub` satisfies the
         ``@runtime_checkable`` :class:`_PromotionRecordLike` Protocol —
         ``isinstance(stub, _PromotionRecordLike)`` is ``True``.
  G6.6 — Empty-string ``"plan-edn"`` for ``:skill/define`` raises
         :class:`SkillDefineValidation` (not silently passes through to
         a parser that would also reject — this guards against handler
         changes that drop the parse-error wrapper but keep an empty-
         string short-circuit somewhere upstream).

Forced spec deviations (CONFIRMED from prior tasks):
  FD-T6.1: ``coder.run()`` is terminal mode-switch — for plan-execution
    failure tests we exercise ``_escalate_plan_body`` directly with a
    ``Coder``-shaped stub (mirrors the ``test_planner_failure.py``
    pattern), avoiding the need for scripted LLM decisions.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from dataclasses import dataclass

import pytest

from persistence.coder._planner import _escalate_plan_body
from persistence.coder._planner_errors import PlanExecutionFailed
from persistence.coder._types import LLMDecision
from persistence.effect.handlers.skill import (
    SkillDefineValidation,
    SkillLookupValidation,
    SkillNotFound,
    _PromotionRecordStub,
    make_skill_handler,
)
from persistence.plan._skill_library import _PromotionRecordLike
from persistence.sdk import Substrate


# ---------------------------------------------------------------------------
# Coder stub (mirrors test_planner_failure.py pattern)
# ---------------------------------------------------------------------------


@dataclass
class _CoderStub:
    """Minimal Coder-shaped stub. ``_escalate_plan_body`` only reads
    ``.substrate`` (and optionally ``._session_start_dt``)."""
    substrate: Substrate
    _session_start_dt: dt.datetime | None = None


def _make_coder_stub(s: Substrate) -> _CoderStub:
    return _CoderStub(
        substrate=s,
        _session_start_dt=dt.datetime.now(dt.timezone.utc),
    )


def _make_plan_decision(plan_edn: str) -> LLMDecision:
    return LLMDecision(
        kind="plan",
        confidence=0.9,
        payload={"plan_edn": plan_edn},
    )


# ---------------------------------------------------------------------------
# G6.1 — :skill/define failure → PlanExecutionFailed mentions SkillDefineValidation
# ---------------------------------------------------------------------------


def test_g6_1_skill_define_failure_propagates_as_plan_execution_failed():
    """Plan with a single ``:skill/define`` leaf carrying malformed
    ``:plan-edn`` triggers ``SkillDefineValidation`` at handler runtime;
    ``s.plan.execute`` captures it; ``_escalate_plan_body`` re-raises
    it as :class:`PlanExecutionFailed` with ``failure.error_repr``
    mentioning ``SkillDefineValidation``.
    """
    with Substrate.open("memory") as s:
        skill_lib = s.plan.skill_library(s._db)
        s.effect.install_handler(
            make_skill_handler(skill_lib),
            position="bottom",
        )

        plan_edn = (
            '[:seq {} '
            '[:skill/define {'
            ':plan-edn "not valid edn [" '
            ':promotion-id "p-fail" '
            ':registered-at-ms 1700000000000'
            '}]]'
        )
        coder = _make_coder_stub(s)
        decision = _make_plan_decision(plan_edn)

        with pytest.raises(PlanExecutionFailed) as exc_info:
            _escalate_plan_body(coder, decision)

        failure = exc_info.value.failure
        assert failure.failed_tag == ":skill/define"
        assert "SkillDefineValidation" in failure.error_repr, (
            f"expected SkillDefineValidation in error_repr, got "
            f"{failure.error_repr!r}"
        )
        assert failure.error_class == "SkillDefineValidation"


# ---------------------------------------------------------------------------
# G6.2 — :skill/lookup unregistered → PlanExecutionFailed mentions SkillNotFound
# ---------------------------------------------------------------------------


def test_g6_2_skill_lookup_not_found_propagates_as_plan_execution_failed():
    """Plan with a single ``:skill/lookup`` leaf carrying an unregistered
    skill_id triggers :class:`SkillNotFound` at handler runtime;
    ``_escalate_plan_body`` re-raises as :class:`PlanExecutionFailed`
    with ``failure.error_repr`` mentioning ``SkillNotFound``.
    """
    with Substrate.open("memory") as s:
        skill_lib = s.plan.skill_library(s._db)
        s.effect.install_handler(
            make_skill_handler(skill_lib),
            position="bottom",
        )

        bogus_id = "skill/" + ("0" * 16)
        plan_edn = f'[:seq {{}} [:skill/lookup {{:skill-id "{bogus_id}"}}]]'
        coder = _make_coder_stub(s)
        decision = _make_plan_decision(plan_edn)

        with pytest.raises(PlanExecutionFailed) as exc_info:
            _escalate_plan_body(coder, decision)

        failure = exc_info.value.failure
        assert failure.failed_tag == ":skill/lookup"
        assert "SkillNotFound" in failure.error_repr, (
            f"expected SkillNotFound in error_repr, got {failure.error_repr!r}"
        )
        assert failure.error_class == "SkillNotFound"


# ---------------------------------------------------------------------------
# G6.3 — error classes are ValueError subclasses (LD4)
# ---------------------------------------------------------------------------


def test_g6_3_error_classes_are_value_error_subclasses():
    """Per design LD4: all three new error classes inherit from
    :class:`ValueError` so callers can catch them with the same shape
    used by 2.3a's :class:`PlanPayloadValidation`."""
    assert issubclass(SkillNotFound, ValueError)
    assert issubclass(SkillDefineValidation, ValueError)
    assert issubclass(SkillLookupValidation, ValueError)


# ---------------------------------------------------------------------------
# G6.4 — _PromotionRecordStub is frozen + slots, single field
# ---------------------------------------------------------------------------


def test_g6_4_promotion_record_stub_is_frozen_and_slots():
    """LD3 stub contract: dataclass(frozen=True, slots=True) with exactly
    one field (``promotion_id: str``).
    """
    fields = dataclasses.fields(_PromotionRecordStub)
    field_names = [f.name for f in fields]
    assert field_names == ["promotion_id"], (
        f"expected only promotion_id field, got {field_names}"
    )

    # ``slots=True`` should produce __slots__ on the class.
    assert hasattr(_PromotionRecordStub, "__slots__")
    assert "promotion_id" in _PromotionRecordStub.__slots__

    # ``frozen=True`` rejects assignment.
    stub = _PromotionRecordStub(promotion_id="p-frozen-test")
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        stub.promotion_id = "new-value"  # type: ignore[misc]
    # And a fresh attr also rejects. Acceptable failure classes:
    # - FrozenInstanceError: frozen=True path
    # - AttributeError: slots=True path (no __dict__)
    # - TypeError: CPython 3.11+ frozen+slots interaction can route
    #   through dataclass-generated __setattr__ which raises TypeError
    #   from super().__setattr__ when the attr isn't in __slots__.
    with pytest.raises(
        (dataclasses.FrozenInstanceError, AttributeError, TypeError)
    ):
        stub.foo = "bar"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# G6.5 — _PromotionRecordStub satisfies _PromotionRecordLike (Protocol)
# ---------------------------------------------------------------------------


def test_g6_5_promotion_record_stub_satisfies_protocol():
    """The stub is structurally compatible with the ``@runtime_checkable``
    :class:`_PromotionRecordLike` Protocol — same minimal stub pattern
    ``tests/plan/test_skill_library.py`` uses today.
    """
    stub = _PromotionRecordStub(promotion_id="p-protocol-test")
    assert isinstance(stub, _PromotionRecordLike)
    # And the read-only ``promotion_id`` attribute is exposed.
    assert stub.promotion_id == "p-protocol-test"


# ---------------------------------------------------------------------------
# G6.6 — Empty string :plan-edn rejected
# ---------------------------------------------------------------------------


def test_g6_6_skill_define_empty_string_plan_edn_raises_validation():
    """Empty-string ``:plan-edn`` is structurally a string but cannot
    parse — must raise :class:`SkillDefineValidation` with the parse-
    error wrapper. Defends against a refactor that drops the parse-
    error catch but keeps an empty-string short-circuit elsewhere.
    """
    with Substrate.open("memory") as s:
        skill_lib = s.plan.skill_library(s._db)
        s.effect.install_handler(
            make_skill_handler(skill_lib),
            position="bottom",
        )

        with pytest.raises(SkillDefineValidation) as exc_info:
            s.effect.perform(
                ":skill/define",
                {
                    "plan-edn": "",
                    "promotion-id": "p-empty",
                    "registered-at-ms": 1700000000000,
                },
            )
        assert exc_info.value.field == "plan-edn"
