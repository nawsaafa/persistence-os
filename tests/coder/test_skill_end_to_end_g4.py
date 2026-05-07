"""Phase 2.3c.1 G4 — LOAD-BEARING end-to-end test.

Per design § 4 (codex consensus): G4 is the SOLE acceptance signal that
proves wrong invariants (identity model, audit anchoring granularity,
expansion determinism) cannot ship. The 4 falsifiability assertion
classes (R0-fold B2 + R1-fold I1 + R1.1-fold IMPORTANT) plus the 5
original G4 assertions span the full define-lookup-inline-execute pipe
under the canonical audit chain.

3-iter scripted scenario:
  iter 1: kind="plan" body = [:seq {} [:skill/define {...}]]
          → :skill/define audit datom + 3 skill/* fact datoms
  iter 2: kind="plan" body = [:seq {} [:skill/lookup {:skill-id ...}]]
          → :skill/lookup audit datom; result_summary carries :plan-edn
  iter 3: kind="plan" body = the looked-up :plan-edn inlined VERBATIM
          → :fs/write executes; marker.txt on disk

Structural note (LD0 terminal mode-switch): Coder.run() returns
IMMEDIATELY after _escalate_plan(decision) per _session.py:77-79.
The "3-iter scenario" is realized via THREE sequential coder.run()
calls on the SAME Substrate — fact-store + skill_library state
persists across calls. Each run() consumes ONE scripted LLM decision
before the plan-escalation exit. This faithfully exercises the
loop's _observe → _decide → gate → _escalate_plan → _escalate_plan_body
→ s.plan.execute → :act/result/leaf → :plan/done pipe per call.

Falsifiability assertion classes (R0/R1/R1.1 folds):
  (f) Full-payload datom match: 3 skill/* datoms with correct (a, v, op)
      triples — blocks the "writes 3 junk datoms while serving correct
      lookups from a separate cache" sophisticated-bug class.
  (g) Idempotent re-define mutation test: ZERO new fact datoms, +1 fresh
      AuditEntry, same skill_id returned (LD5 invariant).
  (h) Splice byte-determinism: parametrized verbatim vs perturbed cases
      — parse(verbatim).id == iter_1_plan_id; parse(perturbed).id !=.
  (i) Store-identity invariant: id(substrate._db.store) ==
      id(skill_library._db.store) after each iter (R1-fold I1 — catches
      accidental fork/branch leak).

Plus G4(a)-(e):
  (a) :skill/define + :skill/lookup audit entries chain via prev_hash
  (b) iter-3's plan body matches looked-up :plan-edn byte-identically
  (c) iter-3 produces marker.txt on disk in scratch_dir
  (d) ZERO :skill/compose audit datoms (proves procedural-recall LD0)
  (e) SkillLibrary.list_skills() consistent (1 entry across iters)

Forced spec deviations encountered:
  FD-T6.1 (CONFIRMED): LD0 terminal mode-switch means a single
    coder.run() processes ONE plan decision then returns. The 3-iter
    scenario uses THREE sequential run() calls on the same Substrate
    rather than one run() consuming three decisions. Substrate state
    is append-only and persists across calls; observation across
    iterations works via the fact-store, not via _observe's
    session-window (which resets per run()).
  FD-T6.2: scratch_dir uses pytest tmp_path fixture per 2.2a precedent.
  FD-T6.3: skill_id is computable up-front via "skill/" + parse(edn).id[:16]
    per _skill_library.py:_derive_skill_id; tests can splice it into
    iter-2's plan_edn before running iter-1, simplifying the scripted
    decision sequence.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from persistence.coder._session import Coder
from persistence.effect.handlers.audit import AuditEntry
from persistence.effect.handlers.callable import make_callable_llm_handler
from persistence.effect.handlers.fs import make_fs_handler
from persistence.effect.handlers.skill import make_skill_handler
from persistence.plan import parse, unparse
from persistence.sdk import Substrate


# ---------------------------------------------------------------------------
# Scripted-decisions helper (matches test_loop_e2e.py:14 pattern; calls
# call_fn with kwargs only)
# ---------------------------------------------------------------------------


def _scripted_decisions(decisions: list[dict]):
    iterator = iter(decisions)

    def _call_fn(*, model, messages, tools=None, temperature=None, max_tokens=None):
        return {"tool_calls": [{"input": next(iterator)}], "text": ""}

    return _call_fn


# ---------------------------------------------------------------------------
# Iter-1 inner plan EDN (the skill body) — drives skill_id derivation.
# Iter-3 verbatim case re-uses this string byte-identically.
# Iter-3 perturbed case flips one character in the path so parse().id
# diverges from iter_1_plan_id — proves the splice-discipline test
# rejects byte-perturbation.
# ---------------------------------------------------------------------------


_PROMOTION_ID = "p-skill-g4"
_REGISTERED_AT_MS = 1700000000000


def _inner_plan_edn(marker_path: str) -> str:
    """The skill's body — a single :fs/write leaf creating the marker file."""
    return f'[:seq {{}} [:fs/write {{:path "{marker_path}" :bytes_or_text "g4-marker"}}]]'


def _outer_define_edn(inner_edn: str, *, promotion_id: str, registered_at_ms: int) -> str:
    """Iter-1's outer plan: a :seq containing one :skill/define leaf.

    Note the EDN escaping discipline — the inner_edn (which contains its
    own quotes) is embedded as a string literal, so all internal " must
    be backslash-escaped at the EDN level.
    """
    escaped_inner = inner_edn.replace("\\", "\\\\").replace('"', '\\"')
    return (
        '[:seq {} [:skill/define {'
        f':plan-edn "{escaped_inner}" '
        f':promotion-id "{promotion_id}" '
        f':registered-at-ms {registered_at_ms}'
        '}]]'
    )


def _outer_lookup_edn(skill_id: str) -> str:
    """Iter-2's outer plan: a :seq containing one :skill/lookup leaf."""
    return f'[:seq {{}} [:skill/lookup {{:skill-id "{skill_id}"}}]]'


def _plan_decision(plan_edn: str) -> dict:
    return {
        "kind": "plan",
        "confidence": 0.95,
        "payload": {"plan_edn": plan_edn},
    }


# ---------------------------------------------------------------------------
# Datom-introspection helpers
# ---------------------------------------------------------------------------


def _audit_entries_filter(s, op: str) -> list[AuditEntry]:
    return [
        e for e in s.audit.entries()
        if isinstance(e, AuditEntry) and e.op == op
    ]


def _all_audit_entries(s) -> list[AuditEntry]:
    return [e for e in s.audit.entries() if isinstance(e, AuditEntry)]


def _skill_fact_datoms(s, skill_id: str) -> list:
    """Datoms whose entity matches the skill_id and whose attr is one of
    the three skill/* attrs. Datom.a is BARE (no leading colon) per
    Datom.__post_init__ stripping convention.
    """
    skill_attrs = {"skill/plan", "skill/promotion-record", "skill/registered-at"}
    return [
        d for d in s._db.log()
        if d.e == skill_id and d.a in skill_attrs and d.op == "assert"
    ]


def _act_result_payloads(s) -> list[dict]:
    """Decoded :act/result payloads in tx order across the entire log."""
    raw = sorted(
        [d for d in s._db.log() if d.a == "act/result" and d.op == "assert"],
        key=lambda d: d.tx,
    )
    return [json.loads(d.v) for d in raw]


def _build_g4_substrate(tmp_path: Path):
    """Open Substrate, install fs + skill + scripted-LLM handlers.
    Returns (s_cm, s, skill_lib, project_root, scratch_dir, marker_path)."""
    project_root = tmp_path / "p"
    scratch_dir = tmp_path / "s"
    project_root.mkdir()
    scratch_dir.mkdir()
    marker_path = str(scratch_dir / "marker.txt")

    s_cm = Substrate.open("memory")
    s = s_cm.__enter__()
    skill_lib = s.plan.skill_library(s._db)

    s.effect.install_handler(
        make_fs_handler(project_root=project_root, scratch_dir=scratch_dir),
        position="bottom",
    )
    s.effect.install_handler(make_skill_handler(skill_lib), position="bottom")

    return s_cm, s, skill_lib, project_root, scratch_dir, marker_path


# ---------------------------------------------------------------------------
# G4 — parametrized verbatim/perturbed end-to-end test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("perturb_iter3", [False, True], ids=["verbatim", "perturbed"])
def test_g4_end_to_end_define_lookup_inline_execute(tmp_path: Path, perturb_iter3: bool):
    """LOAD-BEARING G4 test — define -> lookup -> inline-execute under audit chain.

    Both parametrize cases run iters 1+2 identically; case differs at iter-3:
      verbatim: iter-3 plan_edn equals the lookup result byte-for-byte.
                parse(iter_3).id == iter_1_plan_id (content-addressing match).
                :fs/write produces marker.txt at scratch_dir/marker.txt.
      perturbed: iter-3 plan_edn has a single character flipped in the
                 path. parse(iter_3).id != iter_1_plan_id (content-address
                 diverges). :fs/write still executes but writes to a
                 DIFFERENT path; marker.txt at the original path is NOT
                 created (proving the perturbed plan is structurally a
                 different plan).
    """
    s_cm, s, skill_lib, project_root, scratch_dir, marker_path = _build_g4_substrate(tmp_path)
    perturbed_marker_path = str(scratch_dir / "marker.txX")  # 1-byte flip

    try:
        # --- compute up-front ---
        iter_1_inner_edn = _inner_plan_edn(marker_path)
        iter_1_plan_id = parse(iter_1_inner_edn, strict=False).id
        expected_skill_id = "skill/" + iter_1_plan_id[:16]

        # T9.1 R1-fold I2: canonicalize the verbatim case via parse->unparse
        # round-trip so the precomputed string matches what :skill/lookup
        # returns byte-identically. SkillLibrary stores the parsed Plan AST
        # Node; lookup() returns unparse(node) which canonicalizes dict-key
        # ordering (alphabetical). Without this round-trip, the upfront
        # iter_1_inner_edn string has surface dict-key ordering that differs
        # from the canonical form, defeating the byte-equality check codex
        # Impl R1 I1 asks for. The perturbed case is left un-canonicalized
        # to make the divergence cleanly observable (its plan_id will not
        # match either way).
        iter_3_inner_edn_verbatim = unparse(parse(iter_1_inner_edn, strict=False))
        iter_3_inner_edn_perturbed = _inner_plan_edn(perturbed_marker_path)
        iter_3_inner_edn = (
            iter_3_inner_edn_perturbed if perturb_iter3 else iter_3_inner_edn_verbatim
        )

        # --- script three plan decisions ---
        # Iter-3's plan_edn IS the inner skill body verbatim (or the
        # perturbed variant). The skill body is itself a :seq-rooted
        # plan ([:seq {} [:fs/write ...]]) — exactly the shape required
        # for the coder's plan_edn payload, so no re-wrapping needed.
        # In the verbatim case, iter_3_plan_edn_str is byte-identical to
        # the :plan-edn returned by :skill/lookup at iter-2.
        decisions = [
            _plan_decision(_outer_define_edn(
                iter_1_inner_edn,
                promotion_id=_PROMOTION_ID,
                registered_at_ms=_REGISTERED_AT_MS,
            )),
            _plan_decision(_outer_lookup_edn(expected_skill_id)),
            _plan_decision(iter_3_inner_edn),
        ]
        s.effect.install_handler(
            make_callable_llm_handler(call_fn=_scripted_decisions(decisions)),
            position="bottom",
        )

        coder = Coder(task="g4", substrate=s, max_iters=2)

        # ============================================================
        # ITER 1 — :skill/define
        # ============================================================
        coder.run()

        # G4(a) — exactly 1 :skill/define audit entry so far
        define_entries = _audit_entries_filter(s, ":skill/define")
        assert len(define_entries) == 1, (
            f"iter-1: expected 1 :skill/define audit entry, got {len(define_entries)}"
        )

        # G4(f) FULL-PAYLOAD datom match (R1.1-fold IMPORTANT closure):
        # exactly 3 skill/* fact datoms with correct (a, v, op) triples,
        # all keyed on expected_skill_id.
        skill_datoms = _skill_fact_datoms(s, expected_skill_id)
        assert len(skill_datoms) == 3, (
            f"expected 3 skill/* datoms, got {len(skill_datoms)}: {skill_datoms}"
        )
        triples = {(d.a, d.v, d.op) for d in skill_datoms}
        # expected_plan_id = the :plan-id field returned by :skill/define
        # (equals parse(input_plan_edn).id per LD1).
        # input_promotion_id = the "promotion-id" ARG passed into :skill/define.
        # input_registered_at_ms = the "registered-at-ms" ARG passed into :skill/define.
        assert ("skill/plan", iter_1_plan_id, "assert") in triples
        assert ("skill/promotion-record", _PROMOTION_ID, "assert") in triples
        assert ("skill/registered-at", _REGISTERED_AT_MS, "assert") in triples

        # G4(i) store-identity invariant after iter 1
        assert id(s._db.store) == id(skill_lib._db.store), (
            "iter-1: substrate._db.store and skill_lib._db.store diverged "
            "(possible fork/branch leak — would silently bind SkillLibrary "
            "to a different store)"
        )

        # G4(e) SkillLibrary state — exactly 1 skill registered
        listed = list(skill_lib.list_skills())
        assert len(listed) == 1
        assert listed[0] == expected_skill_id

        # Extract the skill_id actually returned by :skill/define from the
        # :act/result.result_summary so we can prove the runtime path
        # matches up-front computation.
        iter_1_acts = _act_result_payloads(s)
        # iter-1 emits exactly 1 :act/result for the single :skill/define leaf.
        assert len(iter_1_acts) == 1
        rs = iter_1_acts[0]["result_summary"]
        assert rs[":skill-id"] == expected_skill_id, (
            f"runtime-returned :skill-id {rs[':skill-id']!r} != "
            f"up-front computed {expected_skill_id!r}"
        )
        assert rs[":plan-id"] == iter_1_plan_id

        # ============================================================
        # ITER 2 — :skill/lookup
        # ============================================================
        coder.run()

        lookup_entries = _audit_entries_filter(s, ":skill/lookup")
        assert len(lookup_entries) == 1, (
            f"iter-2: expected 1 :skill/lookup audit entry, got {len(lookup_entries)}"
        )

        # G4(a) prev-hash chain spans both ops (Merkle linkage)
        all_entries = _all_audit_entries(s)
        define_idx = next(
            i for i, e in enumerate(all_entries) if e.op == ":skill/define"
        )
        lookup_idx = next(
            i for i, e in enumerate(all_entries) if e.op == ":skill/lookup"
        )
        # The lookup entry must come AFTER the define entry and chain via
        # prev_hash up to it (intermediate :llm/call entries from iter-2's
        # _decide are also in the chain — assert chain integrity rather
        # than direct adjacency).
        assert lookup_idx > define_idx
        for i in range(1, len(all_entries)):
            assert all_entries[i].prev_hash == all_entries[i - 1].id, (
                f"audit chain broken between entry {i - 1} ({all_entries[i - 1].op}) "
                f"and entry {i} ({all_entries[i].op})"
            )

        # iter-2's :act/result for :skill/lookup carries :plan-edn in result_summary
        iter_12_acts = _act_result_payloads(s)
        assert len(iter_12_acts) == 2
        lookup_rs = iter_12_acts[1]["result_summary"]
        assert lookup_rs[":plan-id"] == iter_1_plan_id
        assert lookup_rs[":promotion-id"] == _PROMOTION_ID
        # The lookup-returned :plan-edn round-trips back to the SAME plan id
        # that iter-1 registered (proves SkillLibrary.lookup serves the
        # registered Plan AST, not stale or empty cache content — directly
        # blocks the R0-fold B1 "fresh SkillLibrary per call → empty cache"
        # bug class).
        looked_up_plan_edn = lookup_rs[":plan-edn"]
        assert parse(looked_up_plan_edn, strict=False).id == iter_1_plan_id

        # G4(i) store-identity invariant after iter 2
        assert id(s._db.store) == id(skill_lib._db.store)

        # ============================================================
        # T9.1 R1-fold I1 + I2: literal byte-equality of looked-up :plan-edn
        # vs the iter-3 scripted plan_edn (verbatim case only). Codex Impl R1
        # I1: AST-equality (parse().id match) is INDIRECT — a plan_edn that
        # parses to the same canonical id but with surface-text variations
        # would still pass. Direct string equality is the literal procedural-
        # recall claim: the coder splices the observed :plan-edn VERBATIM into
        # iter-3 (precomputation here matches what the runtime lookup returns,
        # demonstrating dataflow alignment between iter-2's observable and
        # iter-3's payload — minimal close per codex Impl R1 I2 NICE-strength).
        # ============================================================
        if not perturb_iter3:
            iter_3_plan_edn_check = decisions[2]["payload"]["plan_edn"]
            assert looked_up_plan_edn == iter_3_plan_edn_check, (
                "verbatim iter-3 plan_edn must EQUAL the looked-up :plan-edn "
                "string byte-for-byte (literal byte-determinism, not just "
                "AST-equality via parse().id match)"
            )

        # ============================================================
        # ITER 3 — inline-execute
        # ============================================================
        coder.run()

        # G4(c) — verbatim case: marker.txt on disk; perturbed case: NOT.
        if perturb_iter3:
            # Perturbed iter-3 wrote to a DIFFERENT path (marker.txX) — the
            # ORIGINAL marker.txt was never written by THIS test.
            assert not Path(marker_path).exists(), (
                "perturbed iter-3 should have written to a different path, "
                "but marker.txt exists at the original path"
            )
            assert Path(perturbed_marker_path).exists(), (
                "perturbed iter-3's :fs/write should have written to the "
                "perturbed path"
            )
        else:
            assert Path(marker_path).exists(), (
                "verbatim iter-3 should have written marker.txt at scratch_dir"
            )
            assert Path(marker_path).read_text() == "g4-marker"

        # G4(b) iter-3 plan body matches lookup result byte-identically
        # (verbatim case). The decision-3 plan_edn is the [:seq {} ...] form
        # of the looked-up skill body (no re-wrapping needed — :skill/lookup
        # returns the canonical [:seq {} ...] EDN). Content-address match
        # via parse().id is the structural check; the literal byte-equality
        # check above (T9.1 R1-fold I1) is the dataflow claim.
        iter_3_decision = decisions[2]  # the dict we scripted
        iter_3_plan_edn_str = iter_3_decision["payload"]["plan_edn"]
        iter_3_plan_id = parse(iter_3_plan_edn_str, strict=False).id

        # G4(h) splice byte-determinism — parametrized falsifiability
        if perturb_iter3:
            assert iter_3_plan_id != iter_1_plan_id, (
                "perturbed iter-3 must NOT match iter-1's plan_id "
                "(falsifiability of byte-determinism splice discipline)"
            )
        else:
            assert iter_3_plan_id == iter_1_plan_id, (
                "verbatim iter-3 plan_id should match iter-1's "
                "(content-addressed splice discipline)"
            )

        # G4(d) — ZERO :skill/compose audit entries (proves no
        # dispatcher-level magic substitution; LD0 procedural-recall scope).
        compose_entries = _audit_entries_filter(s, ":skill/compose")
        assert len(compose_entries) == 0

        # G4(i) store-identity invariant after iter 3
        assert id(s._db.store) == id(skill_lib._db.store)

        # G4(e) SkillLibrary still exactly 1 entry (no extra registration
        # from iter-2/iter-3).
        listed_after = list(skill_lib.list_skills())
        assert len(listed_after) == 1
        assert listed_after[0] == expected_skill_id

    finally:
        s_cm.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# G4(g) — Idempotent re-define mutation test (separate test for clean
# isolation — the parametrized test above verifies the happy path, this
# one targets specifically the LD5 idempotent-re-define ordering invariant
# in the full coder-loop context, not just the single-handler context
# already covered by T5 G3.5).
# ---------------------------------------------------------------------------


def test_g4_g_idempotent_redefine_in_coder_loop_zero_new_fact_datoms(tmp_path: Path):
    """G4(g) — re-emitting :skill/define for the same plan_edn under the
    coder loop emits a FRESH AuditEntry but ZERO new fact-store datoms.

    Same-instance SkillLibrary across both calls (LD1 R0-fold B1 closure
    pattern) is what makes this work: the fast-path cache hit on the
    second call returns the existing skill_id without invoking
    db.transact for the 3 skill/* datoms.
    """
    s_cm, s, skill_lib, project_root, scratch_dir, marker_path = _build_g4_substrate(tmp_path)
    try:
        iter_1_inner_edn = _inner_plan_edn(marker_path)
        iter_1_plan_id = parse(iter_1_inner_edn, strict=False).id
        expected_skill_id = "skill/" + iter_1_plan_id[:16]
        outer_edn = _outer_define_edn(
            iter_1_inner_edn,
            promotion_id=_PROMOTION_ID,
            registered_at_ms=_REGISTERED_AT_MS,
        )

        decisions = [_plan_decision(outer_edn), _plan_decision(outer_edn)]
        s.effect.install_handler(
            make_callable_llm_handler(call_fn=_scripted_decisions(decisions)),
            position="bottom",
        )

        coder = Coder(task="g4-g", substrate=s, max_iters=2)
        coder.run()  # first :skill/define

        skill_datoms_after_1 = _skill_fact_datoms(s, expected_skill_id)
        define_entries_after_1 = _audit_entries_filter(s, ":skill/define")
        assert len(skill_datoms_after_1) == 3
        assert len(define_entries_after_1) == 1

        coder.run()  # second :skill/define (idempotent)

        skill_datoms_after_2 = _skill_fact_datoms(s, expected_skill_id)
        define_entries_after_2 = _audit_entries_filter(s, ":skill/define")
        assert len(skill_datoms_after_2) == 3, (
            f"idempotent re-define wrote {len(skill_datoms_after_2) - 3} extra "
            f"skill/* datom(s) — LD5 invariant requires ZERO"
        )
        assert len(define_entries_after_2) == 2, (
            f"idempotent re-define expected to emit FRESH AuditEntry; got "
            f"{len(define_entries_after_2)} total"
        )

        # And the runtime returned the SAME skill_id (idempotent return).
        acts = _act_result_payloads(s)
        # Two iters, each with one :skill/define leaf → 2 :act/result entries.
        assert len(acts) == 2
        assert acts[0]["result_summary"][":skill-id"] == expected_skill_id
        assert acts[1]["result_summary"][":skill-id"] == expected_skill_id

        # Store-identity invariant after both iters
        assert id(s._db.store) == id(skill_lib._db.store)
    finally:
        s_cm.__exit__(None, None, None)
