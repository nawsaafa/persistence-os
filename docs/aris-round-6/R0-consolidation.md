# ARIS Round 6 — Consolidation (Phase 1 FREEZE)

**Date:** 2026-04-21
**HEAD:** `e8347c6` (main, 579 tests green)
**Round-6 gate:** min ≥ 9.3 (clean freeze)
**Verdict:** ✅ **PHASE 1 FREEZES CLEAN** at min 9.4. Cut `v0.1.0a1` at `e8347c6`. Phase 2 kickoff.

## Scorecard (complete ARIS arc)

| Reviewer | R1 | R2 | R3 | R4 | R5 | R6 | R6-target |
|---|---:|---:|---:|---:|---:|---:|---:|
| R1 Correctness | 7.4 | 8.3 | 8.6 | 9.0 | 9.0 | **9.4** | 9.3 ✓ |
| R2 Rigor | 6.4 | 8.3 | 8.9 | 9.1 | 9.3 | **9.4** | 9.3 ✓ |
| R3 Composability | **4.5** | 8.6 | 8.9 | 9.3 | 9.2 | **9.7** | 9.6 ✓ |
| R4 Research | 6.5 | 8.2 | 8.6 | 9.0 | 9.3 | **9.4** | 9.3 ✓ |
| **min** | 4.5 | 8.2 | 8.6 | 9.0 | 9.0 | **9.4** | **9.3 PASS** |

R3 composability arc: **4.5 → 9.7** across six rounds. Biggest ladder climb in the loop.

Diminishing-returns curve on min: +3.7, +0.4, +0.4, +0.0 (gate missed), +0.4 → reviewer-friction asymptote hit. Round 7 explicitly not recommended per R4.

## What Round 6 verified

### R5 N1 MAJOR (closed by W-polish3) — verified 3 ways

1. **Committed regression tests** at `tests/effect/test_audit_factory_verify_chain.py` — 3 tests cover `policy_id` / `handler_chain` / `principal` arms with real `Runtime([raw, clock, audit])` + 2× `perform(":llm/call")` for `prev_hash` linkage. All pass on HEAD, confirmed to fail on `60b3c85`.
2. **R1 built a 3-entry production-shape chain** with all three canonicalised fields simultaneously bare (`policy_id="bankability-v3"`, `principal={"user":..., "tenant":...}`, `handler_chain=("audit","llm","tool")`) — `verify_chain` returns `True`.
3. **Hash-invariant probe** (R1 + R3): `_content_hash(_canonicalise_content(content)) == _content_hash(AuditEntry(**content).to_dict() - {id})` byte-identical in every cell of R3's 150-point permutation matrix. R3's 80-point end-to-end factory × verify_chain grid: 80/80 True.

### R5 N2 MINOR (closed) — verified

`policy_id` canonicalisation harmonised to `":" + s.lstrip(":")` matching sibling fields. Live-probed: `":::x"` / `"::x"` / `":x"` / `"x"` → all canonicalise to `":x"` (idempotent).

### R4 paper-meta (closed) — verified

- `grep -c "356 tests"` → 0 hits of stale citation
- `grep -c "579"` → 6 hits at L12 (revision history), L20 (abstract), L38 (§1), L297 (§6), L363 (§6.6), L420 (§8) — R4 expected 4+, exceeded
- `grep -c "ed25519"` → 7 hits (unchanged; all Phase-2 disclosures + revision history)
- Revision-history block separates v0.3 (R5 close-out) from v0.2 (R4 corrections) — better than single conflated entry
- **Prop 4 warrant upgraded** A (stronger) because universal-quantified claim now strictly true on factory subdomain

### Paper↔code fidelity post-W-polish3

Zero new drift introduced by W-polish3. Two upside opportunities for paper deadline (not blocking):
- §4.3 bullet-1 addition naming `_canonicalise_content` pre-hash step (~5 min, upgrades Prop 4 warrant to A+)
- §4.7 two-layer self-conform naming (R5 carry, strengthened by concrete second-layer symbol) (~10 min, A → A+)
- L162 "hardening target for Round 2" prose is stale since W-wire — strike or rewrite for paper deadline

## Convergent new R6 finding (LOW-MINOR, Phase-2 must-fix)

**R1 R6 N1 + R3 R6-N1** — `_canonicalise_content` and `AuditEntry.__post_init__` are two parallel canonicalisers that must stay in sync under future field additions. Currently bit-equivalent but `grep -r _canonicalise_content tests/` returns zero hits pinning the invariant. ~15-line pinning test recommended in Phase 2. Does not block freeze.

## Freeze actions

1. **Cut `v0.1.0a1` tag at `e8347c6`** — Phase 1 NeSy-reference snapshot
2. **No more Phase 1 code changes** unless critical-path blocker
3. **Phase 2 kickoff** — 4 parallel workstreams (same conductor pattern as Phase 1):
   - **Memory Palace bitemporal retrofit** — `/vault-as-of`, counterfactual branches, full audit
   - **Adaptive Trader v2 post-trade counterfactual cron** — Case B reference deployment
   - **`persistence.plan`** — EDN AST + skill library + optimizer (consumes already-registered spec-first `:persistence.plan/node` vector form)
   - **`persistence.txn`** — STM over refs + co-design `DB.transact` input self-conform for F8 residual
4. **Phase-2 pre-cleanup backlog** (~1h30 before forking verticals):
   - R2 R4-G2/G3/G4 carried rigor items (wire_identity algebraic `==`, empty/out-of-order intervention tests, Prop 4 combined-violation)
   - R6 N1 parallel-canonicaliser drift-pin test (~15 lines)
   - R6-G1 `":::x"` policy_id edge case pin (~2 min)
   - R6-G2 direct hash invariant test (~15 min)
   - R6-G3 inline canonicalisation rule → helper call in `test_verify_chain_survives_to_edn_from_edn` (~5 min)

## NeSy 2026 path

- **2026-06-09 (49 days) — Abstract submission: GO AS-IS.** Zero preconditions per R4.
- **2026-06-16 (56 days) — Paper submission: GO after ~3h 15m.**
  - `bench/regulator_replay/` 10-trajectory walkthrough (~3h, flagship §6.3 objection mitigation)
  - §6.5 A/C/D → §7.3 structural demotion (Case B stays as named in §6.5)
  - Optional §4.7 A+ upgrade (two-layer self-conform naming)
  - Optional §4.3 Prop 4 warrant A+ upgrade
  - L162 stale prose strike
- **2026-07-20 (90 days) — Camera-ready: path clear, timeline tight.** Any two of:
  - Per-step rng-state recording
  - 50-trajectory synthetic regulator-replay generator + CC-BY-4.0 dataset
  - Case B post-migration dry-run with real numbers

## Phase 1 deliverables snapshot (at `e8347c6`, cut as `v0.1.0a1`)

- **Code:** 4 modules shipped — `fact` · `effect` · `spec` · `replay`. 3 modules as spec-registered stubs — `plan` · `txn` · `repl`.
- **Tests:** 579 passed in 2.52s. Zero skipped, zero xfailed.
- **Paper:** v0.3 at 8789 words, 460 lines. Abstract + §1–§8. 4 named propositions (Prop 1, 2, 3, 4). Case B named, A/C/D anonymized.
- **Research contributions:** unified bitemporal substrate + machine-checked well-formedness (Prop 2) + byte-identical NO-OP trajectory hash (Prop 3) + regulator-grade Merkle audit with iff integrity (Prop 4) + spec-first parse-don't-validate methodology + homoiconic plan AST.

## Reports

- `R1-correctness.md` · `R2-rigor.md` · `R3-composability.md` · `R4-research.md` · `W-polish3-summary.md`
- All Rounds 1-6 archived under `docs/aris-round-{1..6}/`

## The ARIS self-review loop in retrospect

Six rounds, 24 reviewer reports, 5 worker passes (W-boundary, W-integration, W-rigor, W-paper + W-wire + W-polish + W-polish2 + W-polish3), 223 new tests (356 → 579), 6 convergent cross-reviewer findings (boundary incoherence, replay↔effect unpluggable, HAMT+ed25519 overclaims, e2e wire-up untested, paper drift post-polish, factory canonicalisation gap).

**The loop caught what the happy-path tests missed.** Every round, at least one reviewer reproduced a real defect with a live reproducer. Every fix pass introduced at least one sibling defect the next round caught. The pattern is the value.
