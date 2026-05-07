"""Phase 2.3c.1 G3 — :skill/define + :skill/lookup audit-chain integration.

Verifies that both new ops emit canonical AuditEntry records on the
substrate's append-only audit log under the prev-hash chain, when the
bottom-of-stack handler is installed via
``s.effect.install_handler(handler, position="bottom")``.

Five sub-cases (per design § 4 G3):

  G3.1 — handler.wraps == {":skill/define", ":skill/lookup"}.
  G3.2 — :skill/define emits exactly ONE :skill/define AuditEntry whose
         prev_hash links to the prior chain head.
  G3.3 — :skill/lookup emits exactly ONE :skill/lookup AuditEntry.
  G3.4 — sequential calls form a CHAIN: B.prev_hash == A.id (Merkle
         linkage on the AuditEntry content-hash field, ``id``).
  G3.5 — idempotent re-define emits a FRESH AuditEntry but ZERO new
         fact-store datoms (LD5 idempotent-re-define ordering invariant).

Fixture pattern: ``Substrate.open("memory")`` (audit=True default) +
``make_skill_handler(skill_lib)`` + ``s.effect.install_handler(...,
position="bottom")``. The 2.3b T6 broken pattern
(``s.effect.perform = scripted_fn`` direct attribute assignment) is
NOT used — that bypasses ``Substrate._runtime`` and the audit middleware
never fires.

Forced spec deviations:
  FD-T5.1 (CONFIRMED): AuditEntry.op KEEPS the leading colon
    (``":skill/define"`` not ``"skill/define"``); see
    persistence.effect.handlers.audit:AuditEntry.__post_init__ which
    rejects non-leading-colon ops. Filter ``e.op == ":skill/define"``.
  FD-T5.2 (CONFIRMED): ``s.effect.perform(op, args)`` MUST be called
    with ``args`` as a positional dict, NOT as ``**kwargs``. The
    skill handler's bare arg keys (``"plan-edn"``, ``"promotion-id"``,
    ``"registered-at-ms"``, ``"skill-id"``) contain hyphens and are
    NOT valid Python identifiers, so kwarg expansion fails with
    ``TypeError: Runtime.perform() got an unexpected keyword argument
    'plan-edn'``. The Plan AST dispatcher path
    (``substrate.effect.perform(tag, dict(node.attrs))`` at
    _planner.py:303) already uses positional dicts, so this is
    consistent with production routing.
"""
from __future__ import annotations

import pytest

from persistence.effect.handlers.audit import AuditEntry
from persistence.effect.handlers.skill import (
    SkillNotFound,
    make_skill_handler,
)
from persistence.sdk import Substrate


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


_PLAN_EDN = (
    '[:seq {} [:fs/write {:path "marker.txt" :bytes_or_text "hi"}]]'
)
_PROMOTION_ID = "p-skill-audit"
_REGISTERED_AT_MS = 1700000000000


@pytest.fixture
def s_with_skill_handler():
    """Open in-memory substrate (audit=True default), construct the skill
    library, install make_skill_handler at bottom of stack.
    """
    s_cm = Substrate.open("memory")
    s = s_cm.__enter__()
    try:
        skill_lib = s.plan.skill_library(s._db)
        handler = make_skill_handler(skill_lib)
        s.effect.install_handler(handler, position="bottom")
        yield s
    finally:
        s_cm.__exit__(None, None, None)


def _audit_entries_for(s, op: str) -> list[AuditEntry]:
    """Return AuditEntry records (filtered out the dict-shaped escape-hatch
    telemetry envelopes per _facade.py:1055-1063) whose ``op`` equals the
    given keyword-form op string.
    """
    return [
        e for e in s.audit.entries()
        if isinstance(e, AuditEntry) and e.op == op
    ]


def _all_audit_entries(s) -> list[AuditEntry]:
    return [e for e in s.audit.entries() if isinstance(e, AuditEntry)]


def _skill_fact_datoms(s) -> list:
    """Return fact-store datoms whose ``a`` is one of the three skill/*
    attrs (Datom.a is BARE — no leading colon — per the canonical
    convention)."""
    skill_attrs = {"skill/plan", "skill/promotion-record", "skill/registered-at"}
    return [d for d in s._db.log() if d.a in skill_attrs and d.op == "assert"]


# ---------------------------------------------------------------------------
# G3.1 — handler.wraps shape
# ---------------------------------------------------------------------------


def test_g3_1_handler_wraps_set_is_skill_define_and_lookup():
    """make_skill_handler returns a Handler wrapping exactly the two
    :skill/* ops (no fewer, no extra)."""
    s_cm = Substrate.open("memory")
    s = s_cm.__enter__()
    try:
        skill_lib = s.plan.skill_library(s._db)
        handler = make_skill_handler(skill_lib)
        assert handler.wraps == {":skill/define", ":skill/lookup"}
        assert set(handler.clauses.keys()) == {":skill/define", ":skill/lookup"}
    finally:
        s_cm.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# G3.2 — :skill/define emits ONE AuditEntry; prev_hash links to prior head
# ---------------------------------------------------------------------------


def test_g3_2_skill_define_emits_one_audit_entry_with_prev_hash_linkage(
    s_with_skill_handler,
):
    s = s_with_skill_handler
    # Capture chain head BEFORE the call so we can verify prev_hash linkage.
    prior_entries = _all_audit_entries(s)
    prior_head_id = prior_entries[-1].id if prior_entries else None

    out = s.effect.perform(
        ":skill/define",
        {
            "plan-edn": _PLAN_EDN,
            "promotion-id": _PROMOTION_ID,
            "registered-at-ms": _REGISTERED_AT_MS,
        },
    )
    assert ":skill-id" in out
    assert ":plan-id" in out

    define_entries = _audit_entries_for(s, ":skill/define")
    assert len(define_entries) == 1, (
        f"expected exactly 1 :skill/define AuditEntry, got {len(define_entries)}: "
        f"{define_entries}"
    )
    entry = define_entries[0]
    assert entry.op == ":skill/define"  # FD-T5.1: leading colon retained
    assert entry.args_hash and isinstance(entry.args_hash, str)
    assert entry.result_hash and isinstance(entry.result_hash, str)
    assert entry.verdict == "ok"
    # prev_hash linkage: the entry must point to the prior chain head
    # (or be a chain root if no prior entries existed).
    assert entry.prev_hash == prior_head_id


# ---------------------------------------------------------------------------
# G3.3 — :skill/lookup emits ONE AuditEntry
# ---------------------------------------------------------------------------


def test_g3_3_skill_lookup_emits_one_audit_entry(s_with_skill_handler):
    s = s_with_skill_handler
    define_out = s.effect.perform(
        ":skill/define",
        {
            "plan-edn": _PLAN_EDN,
            "promotion-id": _PROMOTION_ID,
            "registered-at-ms": _REGISTERED_AT_MS,
        },
    )
    skill_id = define_out[":skill-id"]

    lookup_out = s.effect.perform(":skill/lookup", {"skill-id": skill_id})
    assert lookup_out[":plan-id"] == define_out[":plan-id"]
    assert lookup_out[":promotion-id"] == _PROMOTION_ID

    lookup_entries = _audit_entries_for(s, ":skill/lookup")
    assert len(lookup_entries) == 1, (
        f"expected exactly 1 :skill/lookup AuditEntry, got {len(lookup_entries)}"
    )
    entry = lookup_entries[0]
    assert entry.op == ":skill/lookup"
    assert entry.args_hash and isinstance(entry.args_hash, str)
    assert entry.result_hash and isinstance(entry.result_hash, str)
    assert entry.verdict == "ok"


# ---------------------------------------------------------------------------
# G3.4 — sequential calls form a CHAIN
# ---------------------------------------------------------------------------


def test_g3_4_define_then_lookup_chain_links_via_prev_hash(s_with_skill_handler):
    """Define entry's id == lookup entry's prev_hash — the Merkle chain
    spans both new ops without a gap.

    Mirrors the existing test_audit_stack_llm_call.py linkage assertion
    pattern: ``entries[i+1].prev_hash == entries[i].id``.
    """
    s = s_with_skill_handler
    define_out = s.effect.perform(
        ":skill/define",
        {
            "plan-edn": _PLAN_EDN,
            "promotion-id": _PROMOTION_ID,
            "registered-at-ms": _REGISTERED_AT_MS,
        },
    )
    skill_id = define_out[":skill-id"]
    s.effect.perform(":skill/lookup", {"skill-id": skill_id})

    all_entries = _all_audit_entries(s)
    # Locate the contiguous (define, lookup) pair — they should be adjacent
    # because no other audit-emitting ops fire between them.
    define_idx = next(
        i for i, e in enumerate(all_entries) if e.op == ":skill/define"
    )
    # Next AuditEntry must be the :skill/lookup; assert via op identity AND
    # prev_hash linkage so a misplaced/missing entry fails the test.
    assert define_idx + 1 < len(all_entries)
    next_entry = all_entries[define_idx + 1]
    assert next_entry.op == ":skill/lookup"
    assert next_entry.prev_hash == all_entries[define_idx].id


# ---------------------------------------------------------------------------
# G3.5 — idempotent re-define emits FRESH AuditEntry, ZERO new fact datoms
# ---------------------------------------------------------------------------


def test_g3_5_idempotent_redefine_emits_fresh_audit_entry_zero_new_fact_datoms(
    s_with_skill_handler,
):
    """LD5 idempotent-re-define ordering invariant:
    - SkillLibrary.register fast-path or slow-path returns same skill_id —
      ZERO additional fact datoms.
    - Audit middleware emits ONE fresh AuditEntry regardless (the call
      EVENT is the audit signal, not the fact-state delta). Mirrors 2.2a
      :fs/read semantics (re-read of same path emits fresh entries).
    """
    s = s_with_skill_handler
    args = {
        "plan-edn": _PLAN_EDN,
        "promotion-id": _PROMOTION_ID,
        "registered-at-ms": _REGISTERED_AT_MS,
    }
    out1 = s.effect.perform(":skill/define", args)
    fact_count_after_1 = len(_skill_fact_datoms(s))
    audit_count_after_1 = len(_audit_entries_for(s, ":skill/define"))
    assert fact_count_after_1 == 3, (
        f"expected 3 skill/* fact datoms after first define, got {fact_count_after_1}"
    )
    assert audit_count_after_1 == 1

    # Second define with byte-identical args — content-addressing means
    # SkillLibrary.register returns the same skill_id and writes ZERO new
    # datoms (cache fast-path or log-scan slow-path). Audit middleware
    # still emits ONE fresh AuditEntry for the call event.
    out2 = s.effect.perform(":skill/define", args)
    assert out2[":skill-id"] == out1[":skill-id"]
    assert out2[":plan-id"] == out1[":plan-id"]

    fact_count_after_2 = len(_skill_fact_datoms(s))
    audit_count_after_2 = len(_audit_entries_for(s, ":skill/define"))
    assert fact_count_after_2 == 3, (
        f"idempotent re-define wrote {fact_count_after_2 - 3} extra "
        f"skill/* fact datom(s); LD5 invariant requires ZERO"
    )
    assert audit_count_after_2 == 2, (
        f"idempotent re-define expected to emit FRESH AuditEntry "
        f"(now 2 total), got {audit_count_after_2}"
    )


# ---------------------------------------------------------------------------
# G3 sanity — :skill/lookup on unregistered id raises SkillNotFound and
# does NOT corrupt the chain (failure entry still emits with verdict).
# ---------------------------------------------------------------------------


def test_g3_lookup_unregistered_id_raises_skill_not_found(s_with_skill_handler):
    """Negative path sanity: SkillNotFound propagates through
    s.effect.perform for downstream PlanExecutionFailed handling. This
    is G6 territory primarily but cross-references G3 to confirm the
    audit middleware does not eat the exception."""
    s = s_with_skill_handler
    with pytest.raises(SkillNotFound):
        s.effect.perform(":skill/lookup", {"skill-id": "skill/does-not-exist"})
