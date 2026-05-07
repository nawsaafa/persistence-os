"""Phase 2.3c.1 G1 — :skill/define op semantics.

Per design § 4 G1: happy path + idempotent re-define + arg-validation
failure modes. The handler factory is `make_skill_handler(skill_lib)`;
install at the bottom of the substrate effect stack with
``s.effect.install_handler(handler, position="bottom")``.

7 sub-cases:

  G1.1 — Happy path: returns ``{":skill-id", ":plan-id"}``; the
         ``:skill-id`` equals ``"skill/" + plan.id[:16]`` per
         :func:`persistence.plan._skill_library._derive_skill_id`.
  G1.2 — Idempotent re-define: same args twice → same ``:skill-id``;
         SkillLibrary ``_plans`` cache populated; only ONE set of 3
         ``skill/*`` fact datoms in ``db.log()``.
  G1.3 — Missing ``"plan-edn"`` raises :class:`SkillDefineValidation`
         with ``field="plan-edn"``.
  G1.4 — Non-string ``"plan-edn"`` raises :class:`SkillDefineValidation`.
  G1.5 — Missing ``"promotion-id"`` raises :class:`SkillDefineValidation`
         with ``field="promotion-id"``.
  G1.6 — Non-int ``"registered-at-ms"`` (string) AND bool variant raise
         :class:`SkillDefineValidation` (FD2 from T1: bools rejected).
  G1.7 — Malformed ``"plan-edn"`` (parse error) raises
         :class:`SkillDefineValidation` with the parse error wrapped.

Forced spec deviations (CONFIRMED from prior tasks):
  FD-T5.2 / FD1: ``s.effect.perform(":skill/define", {...})`` takes a
    POSITIONAL dict, NOT kwargs. Arg keys are BARE strings (no leading
    colon). Return-map keys preserve keyword-form (``":skill-id"``,
    ``":plan-id"``).
"""
from __future__ import annotations

import pytest

from persistence.effect.handlers.skill import (
    SkillDefineValidation,
    make_skill_handler,
)
from persistence.plan import parse
from persistence.sdk import Substrate


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


_PLAN_EDN = (
    '[:seq {} [:fs/write {:path "marker.txt" :bytes_or_text "hi"}]]'
)
_PROMOTION_ID = "p-skill-define"
_REGISTERED_AT_MS = 1700000000000


@pytest.fixture
def s_with_skill_handler():
    """Open in-memory substrate (audit=True default), construct skill
    library, install make_skill_handler at bottom of stack. Mirrors T5
    fixture pattern verbatim.
    """
    s_cm = Substrate.open("memory")
    s = s_cm.__enter__()
    try:
        skill_lib = s.plan.skill_library(s._db)
        handler = make_skill_handler(skill_lib)
        s.effect.install_handler(handler, position="bottom")
        yield s, skill_lib
    finally:
        s_cm.__exit__(None, None, None)


def _skill_fact_datoms(s) -> list:
    """All ``skill/*`` fact-store datoms (no leading colon per
    Datom.__post_init__ stripping convention)."""
    skill_attrs = {"skill/plan", "skill/promotion-record", "skill/registered-at"}
    return [d for d in s._db.log() if d.a in skill_attrs and d.op == "assert"]


# ---------------------------------------------------------------------------
# G1.1 — Happy path
# ---------------------------------------------------------------------------


def test_g1_1_skill_define_happy_path_returns_skill_id_and_plan_id(
    s_with_skill_handler,
):
    """Define returns ``{":skill-id", ":plan-id"}``; ``:skill-id`` is
    ``"skill/" + plan.id[:16]`` per :func:`_derive_skill_id`.
    """
    s, _ = s_with_skill_handler
    out = s.effect.perform(
        ":skill/define",
        {
            "plan-edn": _PLAN_EDN,
            "promotion-id": _PROMOTION_ID,
            "registered-at-ms": _REGISTERED_AT_MS,
        },
    )
    assert isinstance(out, dict) or hasattr(out, "__getitem__")
    assert ":skill-id" in out
    assert ":plan-id" in out

    skill_id = out[":skill-id"]
    plan_id = out[":plan-id"]
    assert isinstance(skill_id, str)
    assert isinstance(plan_id, str)
    assert skill_id.startswith("skill/")

    # _derive_skill_id contract: skill_id == "skill/" + plan.id[:16]
    expected_plan = parse(_PLAN_EDN, strict=False)
    assert plan_id == expected_plan.id
    assert skill_id == "skill/" + expected_plan.id[:16]
    # plan_id is 32-hex (canonical content-address)
    assert len(plan_id) == 32
    int(plan_id, 16)  # round-trip — fails if not pure hex


# ---------------------------------------------------------------------------
# G1.2 — Idempotent re-define: same skill_id, ZERO new fact datoms
# ---------------------------------------------------------------------------


def test_g1_2_skill_define_idempotent_redefine_zero_new_fact_datoms(
    s_with_skill_handler,
):
    """Calling ``:skill/define`` twice with byte-identical args returns
    the same ``:skill-id``; ``SkillLibrary._plans`` cache is populated;
    the fact-store contains EXACTLY ONE set of 3 ``skill/*`` datoms.

    Mirrors the LD5 idempotent-re-define ordering invariant — fact
    state is content-addressed, repeat calls hit the fast-path cache.
    """
    s, skill_lib = s_with_skill_handler
    args = {
        "plan-edn": _PLAN_EDN,
        "promotion-id": _PROMOTION_ID,
        "registered-at-ms": _REGISTERED_AT_MS,
    }
    out1 = s.effect.perform(":skill/define", args)
    out2 = s.effect.perform(":skill/define", args)

    assert out1[":skill-id"] == out2[":skill-id"]
    assert out1[":plan-id"] == out2[":plan-id"]

    # SkillLibrary _plans cache populated (closure pattern LD1 R0-fold B1)
    assert out1[":skill-id"] in skill_lib._plans

    # ONE set of 3 fact datoms — re-define is idempotent at the fact level.
    skill_datoms = _skill_fact_datoms(s)
    assert len(skill_datoms) == 3, (
        f"idempotent re-define wrote {len(skill_datoms) - 3} extra "
        f"skill/* datom(s); LD5 invariant requires ZERO"
    )


# ---------------------------------------------------------------------------
# G1.3 — Missing "plan-edn" raises SkillDefineValidation
# ---------------------------------------------------------------------------


def test_g1_3_skill_define_missing_plan_edn_raises_validation(s_with_skill_handler):
    s, _ = s_with_skill_handler
    with pytest.raises(SkillDefineValidation) as exc_info:
        s.effect.perform(
            ":skill/define",
            {
                "promotion-id": _PROMOTION_ID,
                "registered-at-ms": _REGISTERED_AT_MS,
            },
        )
    assert exc_info.value.field == "plan-edn"
    assert "missing" in exc_info.value.reason.lower()


# ---------------------------------------------------------------------------
# G1.4 — Non-string "plan-edn" raises SkillDefineValidation
# ---------------------------------------------------------------------------


def test_g1_4_skill_define_non_string_plan_edn_raises_validation(s_with_skill_handler):
    s, _ = s_with_skill_handler
    with pytest.raises(SkillDefineValidation) as exc_info:
        s.effect.perform(
            ":skill/define",
            {
                "plan-edn": 12345,  # int, not str
                "promotion-id": _PROMOTION_ID,
                "registered-at-ms": _REGISTERED_AT_MS,
            },
        )
    assert exc_info.value.field == "plan-edn"
    assert "expected str" in exc_info.value.reason


# ---------------------------------------------------------------------------
# G1.5 — Missing "promotion-id" raises SkillDefineValidation
# ---------------------------------------------------------------------------


def test_g1_5_skill_define_missing_promotion_id_raises_validation(
    s_with_skill_handler,
):
    s, _ = s_with_skill_handler
    with pytest.raises(SkillDefineValidation) as exc_info:
        s.effect.perform(
            ":skill/define",
            {
                "plan-edn": _PLAN_EDN,
                "registered-at-ms": _REGISTERED_AT_MS,
            },
        )
    assert exc_info.value.field == "promotion-id"
    assert "missing" in exc_info.value.reason.lower()


# ---------------------------------------------------------------------------
# G1.6 — Non-int "registered-at-ms" (string + bool) raises SkillDefineValidation
# ---------------------------------------------------------------------------


def test_g1_6a_skill_define_string_registered_at_ms_raises_validation(
    s_with_skill_handler,
):
    """A non-numeric string for ``registered-at-ms`` must reject."""
    s, _ = s_with_skill_handler
    with pytest.raises(SkillDefineValidation) as exc_info:
        s.effect.perform(
            ":skill/define",
            {
                "plan-edn": _PLAN_EDN,
                "promotion-id": _PROMOTION_ID,
                "registered-at-ms": "abc",
            },
        )
    assert exc_info.value.field == "registered-at-ms"
    assert "expected int" in exc_info.value.reason


def test_g1_6b_skill_define_bool_registered_at_ms_raises_validation(
    s_with_skill_handler,
):
    """FD2 from T1: ``bool`` is an ``int`` subclass in Python; the handler
    explicitly rejects bool to prevent ``True``/``False`` from sneaking
    into the ``skill/registered-at`` fact datom."""
    s, _ = s_with_skill_handler
    with pytest.raises(SkillDefineValidation) as exc_info:
        s.effect.perform(
            ":skill/define",
            {
                "plan-edn": _PLAN_EDN,
                "promotion-id": _PROMOTION_ID,
                "registered-at-ms": True,
            },
        )
    assert exc_info.value.field == "registered-at-ms"
    assert "expected int" in exc_info.value.reason
    assert "bool" in exc_info.value.reason


# ---------------------------------------------------------------------------
# G1.7 — Malformed plan-edn raises SkillDefineValidation (parse error wrapped)
# ---------------------------------------------------------------------------


def test_g1_7_skill_define_malformed_plan_edn_raises_validation(s_with_skill_handler):
    """Unparseable ``:plan-edn`` raises :class:`SkillDefineValidation`
    with the underlying :class:`ParseError` chained via ``__cause__``.
    """
    s, _ = s_with_skill_handler
    with pytest.raises(SkillDefineValidation) as exc_info:
        s.effect.perform(
            ":skill/define",
            {
                "plan-edn": "not valid edn [",
                "promotion-id": _PROMOTION_ID,
                "registered-at-ms": _REGISTERED_AT_MS,
            },
        )
    assert exc_info.value.field == "plan-edn"
    assert "parse" in exc_info.value.reason.lower()
    # The handler raises `from exc`, so __cause__ points to the ParseError.
    assert exc_info.value.__cause__ is not None
