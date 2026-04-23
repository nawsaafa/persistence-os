# ARIS Round 4 — Consolidation

**Date:** 2026-04-21
**HEAD:** `61644f6` (main, 551 tests green)
**Round-4 gate:** min ≥ 9.0 (NeSy-submittable floor)
**Verdict:** ✅ **PASS** at min 9.0 — but 6 convergent new findings prompt a Round 5 surgical polish (~60 min) for a clean ≥ 9.2 freeze.

## Scorecard (full arc)

| Reviewer | R1 | R2 | R3 | R4 | R4-target |
|---|---:|---:|---:|---:|---:|
| R1 Correctness | 7.4 | 8.3 | 8.6 | **9.0** | 9.0 (met) |
| R2 Rigor | 6.4 | 8.3 | 8.9 | **9.1** | 9.0 |
| R3 Composability | **4.5** | 8.6 | 8.9 | **9.3** | 9.2 |
| R4 Research | 6.5 | 8.2 | 8.6 | **9.0** | 9.0 (met) |
| **min** | 4.5 | 8.2 | 8.6 | **9.0** | 9.0 PASS |

R3 arc: 4.5 → 8.6 → 8.9 → 9.3. Biggest ladder jump across the loop.

## What W-wire closed cleanly

- **R1 N5** — `Trajectory.to_edn()` now conforms on real 2-step + 3-step `engine.replay(...)` output (reproduced interactively by R1 reviewer).
- **R1 N6** — `AuditEntry.to_edn()` conforms on bare-string `handler_chain=("audit","llm")` (production shape).
- **R3 N2 ("single biggest drag")** — `Datom(a=":x/y") == Datom(a="x/y")`; `wire.datom_to_wire ∘ wire_to_datom = id` on extended domain (verified 9.5/10).
- **B1** — `Trajectory.intervention: list[dict]`, multi-step lineage preserved at `engine.py:170`.
- **R2 G4** — shape pin rewritten to `isinstance(cf_a.intervention, list)` + length + per-entry field equality + cross-replay determinism. No xfail dance.
- **R3 N6** — `AuditEntry.from_edn` inverse landed.
- **Rigor polish** — `BANNED_CALLS` extended, multi-line noqa scan, Prop 4 formally stated with 4-case iff backing.
- **Paper** — §5.1 SQL portability struck, §5.3 name sync, §6 opener date fix, 3 new-contribution-angle sentences landed cleanly (vector homoiconicity §4.4 A+, self-conforming producers §4.7 A, atomic allocation §5.1 A+).

## Six convergent new findings prompting Round 5

### Class-A: canonicalisation sibling defect (R1 + R3 convergent)

W-wire mirrored the `Datom.__post_init__` pattern for content-addressing but left three `AuditEntry` wire-boundary siblings un-canonicalised:

- **R1 N7 [MAJOR]** — `AuditEntry.to_edn()` rejects bare-string `policy_id`. `policy_eval.py:161` emits `"unknown"`/`"bankability-v3"`; `audit.py:147-148` passes through verbatim; spec requires keyword (`_canonical.py:340`). Reproduced: `AuditEntry(..., policy_id="bankability-v3").to_edn()` raises `ValueError`.
- **R3 R4-N1 [MEDIUM]** — `AuditEntry.from_edn ∘ to_edn` breaks `verify_chain` on pre-keyworded `handler_chain`. Asymmetry in the just-shipped inverse.
- **R3 R4-N2 [LOW]** — same class on `principal` keys. Predates W-wire but newly reachable via the `from_edn` round-trip.

**Root fix:** canonicalise `policy_id`, `handler_chain`, `principal` at `AuditEntry.__post_init__`. Mirrors the N2 `Datom.__post_init__` treatment. One commit.

### Class-B: paper residuals (R1 + R4 convergent)

- **R1 N8 + R4** — paper L365 still says "2026-06-16 abstract deadline". W-wire fixed §6 opener L296 but missed bullet-4 L365. **One-word fix.**
- **R4 Prop 4 phantom name** — proposition references `append_audit_entry`; `grep` returns 0 hits. Chain construction is inline inside `make_audit_handler`'s clause closure. Math is correct; name wrong.
- **R4 §4.5 L184** — `I = ⟨step, field, new-value⟩` still defined as single triple; code now supports `list[dict]` multi-step. Not abstract-blocking (NO-OP uses single-step) but paper-deadline fix.

### Class-C: minor composability follow-ups (R3)

- **R3 R4-N3 [LOW]** — `Datom.__post_init__` uses `a[1:]` instead of `lstrip(":")`. `Datom(a="::x")` is non-idempotent (strips one colon, leaves `":x"` bare).
- **R3 R4-N4 [LOW]** — `_provenance_to_wire` key/value asymmetry: `{":source": "bare-v"}` leaks bare `v` through. Self-conform catches it loudly — no silent hole — but the inverse isn't symmetric.

### Class-D: tracked but not fixable in 60 min

- **R2 new gaps R4-G1..G4** — AuditEntry round-trip uses field-list instead of `==`; wire_identity tests aren't full algebraic identity; no empty/out-of-order/duplicate-step intervention tests; Prop 4 missing combined-violation case. All Phase-2 candidates, not Round-5-surgical.

## Round 5 surgical scope (~60 min, 4-5 commits)

Single-worker, zero-scope-creep:

1. **W5-audit-canonicalize** — `AuditEntry.__post_init__` normalises `policy_id`, `handler_chain`, `principal`. Closes R1 N7 + R3 R4-N1 + R3 R4-N2. TDD-first; target 4 new tests.
2. **W5-datom-idempotent** — `Datom.__post_init__` uses `lstrip(":")`. Closes R3 R4-N3. One test.
3. **W5-provenance-symmetry** — `_provenance_to_wire` value-side keywordify. Closes R3 R4-N4. One test.
4. **W5-paper-patch2** — L365 date, Prop 4 `append_audit_entry` → correct reference, §4.5 L184 `I` definition accepts list. Three surgical edits.
5. Optional: extend `AuditEntry.from_edn` round-trip test to use `==` not field-list (closes R2 R4-G1 for free since `__post_init__` now normalises).

## Round 5 target grades

- R1: ≥ 9.3 (closes N7; three minors N9/N10/N11 remain as docs)
- R2: ≥ 9.2 (G1-G4 all deferred but `==` roundtrip is a bonus close)
- R3: ≥ 9.5 (R4-N1/N2 canonicalisation + R4-N3 idempotent + R4-N4 symmetry)
- R4: ≥ 9.3 (paper residuals closed)
- **min ≥ 9.2** → Phase 1 FROZEN clean → NeSy-submittable without patch debt

## After Round 5 (Phase 1 freeze)

- No more code changes to Phase 1 modules unless blocker.
- Abstract submission 2026-06-09 AoE (49 days).
- Paper submission 2026-06-16 AoE (56 days) — needs §6.3 bench walkthrough (~3 hr, deferred to this window).
- Phase 2 workstreams start:
  - Memory Palace bitemporal retrofit
  - Adaptive Trader v2 counterfactual cron
  - `persistence.plan` (EDN AST + skill library + optimizer)
  - `persistence.txn` (STM + co-design `DB.transact` input self-conform for F8 residual)

## Reports

- `R1-correctness.md` · `R2-rigor.md` · `R3-composability.md` · `R4-research.md` · `W-wire-summary.md`
