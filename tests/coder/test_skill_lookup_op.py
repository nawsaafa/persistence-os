"""Phase 2.3c.1 G2 — :skill/lookup op semantics.

Per design § 4 G2: happy path (round-trip integrity) + not-found +
arg-validation + canonicalization-determinism. Same fixture pattern
as G1 / G3 — `make_skill_handler(skill_lib)` installed at the bottom
of the substrate effect stack.

5 sub-cases:

  G2.1 — Happy path: register skill via ``:skill/define``, then
         ``:skill/lookup`` returns ``{":plan-edn", ":promotion-id",
         ":plan-id"}``; ``parse(returned[":plan-edn"]).id ==
         returned[":plan-id"]`` (round-trip integrity proves the
         serialized EDN is canonical-form for the registered AST).
  G2.2 — Not-found: lookup of an unregistered skill_id raises
         :class:`SkillNotFound` carrying the queried ``skill_id``.
  G2.3 — Missing ``"skill-id"`` raises :class:`SkillLookupValidation`
         with ``field="skill-id"``.
  G2.4 — Non-string ``"skill-id"`` raises :class:`SkillLookupValidation`.
  G2.5 — Returned ``:plan-edn`` is byte-for-byte stable across multiple
         lookups of the same ``skill_id`` (canonicalization is
         deterministic — load-bearing for the G4 splice-byte-determinism
         contract since the coder MUST splice the result verbatim).

Forced spec deviations (CONFIRMED from prior tasks):
  FD-T5.2 / FD1: positional dict args; bare arg keys; keyword-form
    return-map keys.
"""
from __future__ import annotations

import pytest

from persistence.effect.handlers.skill import (
    SkillLookupValidation,
    SkillNotFound,
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
_PROMOTION_ID = "p-skill-lookup"
_REGISTERED_AT_MS = 1700000000000


@pytest.fixture
def s_with_skill_handler():
    s_cm = Substrate.open("memory")
    s = s_cm.__enter__()
    try:
        skill_lib = s.plan.skill_library(s._db)
        handler = make_skill_handler(skill_lib)
        s.effect.install_handler(handler, position="bottom")
        yield s
    finally:
        s_cm.__exit__(None, None, None)


def _register(s) -> dict:
    """Helper: register the canonical test skill, return the define-out
    dict so callers can capture the ``:skill-id``."""
    return s.effect.perform(
        ":skill/define",
        {
            "plan-edn": _PLAN_EDN,
            "promotion-id": _PROMOTION_ID,
            "registered-at-ms": _REGISTERED_AT_MS,
        },
    )


# ---------------------------------------------------------------------------
# G2.1 — Happy path: round-trip integrity
# ---------------------------------------------------------------------------


def test_g2_1_skill_lookup_happy_path_round_trip(s_with_skill_handler):
    """Register, then lookup; returned ``:plan-edn`` parses back to the
    same plan_id (proves canonical-form round-trip).
    """
    s = s_with_skill_handler
    define_out = _register(s)
    skill_id = define_out[":skill-id"]
    expected_plan_id = define_out[":plan-id"]

    lookup_out = s.effect.perform(":skill/lookup", {"skill-id": skill_id})
    assert ":plan-edn" in lookup_out
    assert ":promotion-id" in lookup_out
    assert ":plan-id" in lookup_out

    assert lookup_out[":promotion-id"] == _PROMOTION_ID
    assert lookup_out[":plan-id"] == expected_plan_id

    # Round-trip integrity: re-parsing the returned EDN yields the SAME
    # content-addressed plan_id. This is the load-bearing property the
    # G4 inline-execute test depends on.
    plan_edn = lookup_out[":plan-edn"]
    assert isinstance(plan_edn, str)
    assert parse(plan_edn, strict=False).id == expected_plan_id


# ---------------------------------------------------------------------------
# G2.2 — Not-found: SkillNotFound with skill_id attribute populated
# ---------------------------------------------------------------------------


def test_g2_2_skill_lookup_unregistered_id_raises_skill_not_found(
    s_with_skill_handler,
):
    s = s_with_skill_handler
    bogus_id = "skill/" + ("0" * 16)
    with pytest.raises(SkillNotFound) as exc_info:
        s.effect.perform(":skill/lookup", {"skill-id": bogus_id})
    assert exc_info.value.skill_id == bogus_id


# ---------------------------------------------------------------------------
# G2.3 — Missing "skill-id" raises SkillLookupValidation
# ---------------------------------------------------------------------------


def test_g2_3_skill_lookup_missing_skill_id_raises_validation(s_with_skill_handler):
    s = s_with_skill_handler
    with pytest.raises(SkillLookupValidation) as exc_info:
        s.effect.perform(":skill/lookup", {})
    assert exc_info.value.field == "skill-id"
    assert "missing" in exc_info.value.reason.lower()


# ---------------------------------------------------------------------------
# G2.4 — Non-string "skill-id" raises SkillLookupValidation
# ---------------------------------------------------------------------------


def test_g2_4_skill_lookup_non_string_skill_id_raises_validation(
    s_with_skill_handler,
):
    s = s_with_skill_handler
    with pytest.raises(SkillLookupValidation) as exc_info:
        s.effect.perform(":skill/lookup", {"skill-id": 12345})
    assert exc_info.value.field == "skill-id"
    assert "expected str" in exc_info.value.reason


# ---------------------------------------------------------------------------
# G2.5 — :plan-edn byte-stable across repeat lookups (canonicalization)
# ---------------------------------------------------------------------------


def test_g2_5_skill_lookup_plan_edn_byte_stable_across_repeat_lookups(
    s_with_skill_handler,
):
    """Multiple ``:skill/lookup`` calls for the same ``skill_id`` return a
    byte-identical ``:plan-edn`` string. Load-bearing for the G4 splice
    contract — the coder splices verbatim, so any non-determinism in
    :func:`unparse` would break content-addressing of the spliced plan.
    """
    s = s_with_skill_handler
    define_out = _register(s)
    skill_id = define_out[":skill-id"]

    out_a = s.effect.perform(":skill/lookup", {"skill-id": skill_id})
    out_b = s.effect.perform(":skill/lookup", {"skill-id": skill_id})
    out_c = s.effect.perform(":skill/lookup", {"skill-id": skill_id})

    assert out_a[":plan-edn"] == out_b[":plan-edn"] == out_c[":plan-edn"], (
        "unparse() must be byte-deterministic across repeat lookups"
    )
    # And each round-trips back to the original plan_id.
    expected_plan_id = define_out[":plan-id"]
    for out in (out_a, out_b, out_c):
        assert parse(out[":plan-edn"], strict=False).id == expected_plan_id
