# ARIS Round 5 — Consolidation

**Date:** 2026-04-21
**HEAD:** `60b3c85` (main, 575 tests green)
**Round-5 gate:** min ≥ 9.2 (clean freeze)
**Verdict:** ⚠️ **CLEAN FREEZE MISSED by 0.2** — min 9.0 (R1). Convergent R1+R3 finding (R5 N1) prompts one more 30-min W-polish3 pass.

## Scorecard (full arc)

| Reviewer | R1 | R2 | R3 | R4 | R5 | R5-target |
|---|---:|---:|---:|---:|---:|---:|
| R1 Correctness | 7.4 | 8.3 | 8.6 | 9.0 | **9.0** | 9.3 (missed) |
| R2 Rigor | 6.4 | 8.3 | 8.9 | 9.1 | **9.3** | 9.2 |
| R3 Composability | **4.5** | 8.6 | 8.9 | 9.3 | **9.2** | 9.5 (missed) |
| R4 Research | 6.5 | 8.2 | 8.6 | 9.0 | **9.3** | 9.3 |
| **min** | 4.5 | 8.2 | 8.6 | 9.0 | **9.0** | **9.2 MISS** |

R1 and R3 both held the grade down for the same reason: R5 N1.

## Convergent new finding — R5 N1

Both R1 (MAJOR) and R3 (MEDIUM) independently caught a regression that **W-polish2 itself introduced**:

**Location:** `src/persistence/effect/handlers/audit.py:364-381` (`make_audit_handler`).

**Defect:** the factory computes `_content_hash(content)` *before* constructing the `AuditEntry`. Post-W-polish2, `AuditEntry.__post_init__` canonicalises `policy_id`/`handler_chain`/`principal`. So the hash is over the pre-canonical content, but `entry.to_dict()` reflects the canonical form. `verify_chain` rehashes `entry.to_dict()` and mismatches.

**Reproduction (both reviewers):** `make_audit_handler(policy_id="bankability-v3")` + 2 `perform(":llm/call", ...)` → `verify_chain(entries) is False`.

**Why it's serious:**
- Paper Proposition 4 (just added in W-wire) claims `verify_chain = True` for any chain produced by `make_audit_handler`'s Merkle-append clause.
- `policy_eval.py:161` emits bare-string `policy_id` ("unknown", "bankability-v3") — this is the exact production path.
- The abstract claims the property is checked on the shipped artifact.
- Post-W-polish2, **it's false-by-construction** on the factory subdomain.

**Why the 575-test suite missed it:** W-polish2's 14 `test_audit_canonicalize.py` tests exercise direct `AuditEntry(...)` construction only. `test_audit.py` factory tests never pass `policy_id=` or non-default `principal=`. The tests + the fix + the assertion were all consistent in isolation; only the composition was broken.

**Fix (~10 lines, ~30 min):** pre-canonicalise `content` inside `make_audit_handler` before `_content_hash(content)`. Extract a shared `_canonicalise_content()` helper, or construct-and-serialise the AuditEntry first to get the canonical dict. Add 3 factory-path `verify_chain` regression tests.

## Other R5 findings

### Closed cleanly

- R1 N7 (MAJOR policy_id), R1 N8 (paper L365 date), R1 N9/N11 (minors) — all closed via `d12946d` / `1e291b5` / related.
- R3 R4-N1 (handler_chain verify_chain break), R4-N2 (principal keys), R4-N3 (Datom double-colon idempotency), R4-N4 (_provenance_to_wire asymmetry) — all closed.
- R4 paper residuals (L317+L365 dates, Prop 4 phantom, §4.5 intervention drift) — all closed.
- R2 R4-G1 bonus (`AuditEntry` dataclass `==` round-trip) — closed via `9299e40`.

### New minor findings (not freeze-blocking)

- **R5 N2 [MINOR]** (R1) — sibling canonicalisation asymmetry: `policy_id` uses prepend-if-missing (not idempotent on `"::x"`); `handler_chain`/`principal` use `lstrip(":")` (idempotent). Harmonise to `":" + s.lstrip(":")` in W-polish3 alongside R5 N1 (same class).
- **R5-G1/G2/G3 (R2)** — G1 bonus narrow verdict coverage (covers 1 of 5), `test_datom_idempotent.py` missing empty/whitespace/None edge cases, R2-G2/G3/G4 now two rounds old. Reviewer recommends closing all 5 in pre-Phase-2 cleanup (~1h20).
- **R4 two citation-hygiene flags** — paper revision-history still reads "v0.2" (should be v0.3); "356 tests green" cited in abstract/§6/§6.6/§8 vs HEAD 575. Either cut `v0.1.0a1` tag at 356-commit, or update paper to 575 and tag at current HEAD. ~5 min author choice.

## W-polish3 scope (recommended, ~30 min)

Single-worker, tightest possible scope:

1. **W6-factory-canonicalize** — pre-canonicalise `content` in `make_audit_handler` before `_content_hash`. Extract shared helper. 3 factory-path `verify_chain` regression tests in `tests/effect/test_audit_factory_verify_chain.py`. Closes R5 N1.
2. **W6-canonicalize-harmonize** — `AuditEntry.__post_init__` `policy_id` harmonisation to `":" + s.lstrip(":")` to match sibling fields. Idempotency test on `"::x"`. Closes R5 N2.

**Optional bundle (if time permits, +20 min):**
3. **W6-paper-meta** — paper revision-history v0.2 → v0.3; cut `v0.1.0a1` tag at HEAD `60b3c85` and update test-count citations to 575.

**Deferred to pre-Phase-2 cleanup:**
- R2-G2/G3/G4 (~1h20): wire_identity algebraic `==`, empty/out-of-order/duplicate intervention tests, Prop 4 combined-violation + consecutive-middle-deletion tests.

## Round 6 target grades

- R1: ≥ 9.3 (closing R5 N1 removes the MAJOR weight)
- R2: ≥ 9.3 (same as R5; G1 full parametrise if bundled)
- R3: ≥ 9.6 (R5 N1 was the cap per reviewer's explicit note)
- R4: ≥ 9.3 (same as R5; paper-meta if bundled lifts to 9.4)
- **min ≥ 9.3 → Phase 1 FROZEN unambiguously clean → `v0.1.0a1` tag → NeSy-submittable**

## Post-freeze (Phase 2 kickoff)

Four parallel workstreams, same conductor pattern as Phase 1:
1. Memory Palace bitemporal retrofit
2. Adaptive Trader v2 post-trade counterfactual cron
3. Module 3 `persistence.plan` (consumes spec-first `:persistence.plan/node` vector form already registered)
4. Module 5 `persistence.txn` + co-design `DB.transact` input self-conform for F8 residual

## Reports

- `R1-correctness.md` · `R2-rigor.md` · `R3-composability.md` · `R4-research.md` · `W-polish2-summary.md`
