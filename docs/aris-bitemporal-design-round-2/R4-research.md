# ARIS R2 Research Review
**Grade:** 9.1 / 10 (prior: 7.8)

## Verdict

Clears 9.0. Class-B CRITICAL (phantom `bench/regulator_replay/`) resolved exemplarily: scaffold present, README reads **"SCAFFOLD. Not yet operational,"** `generator.py`/`harness.py` raise `NotImplementedError` citing Phase-4. Opposite of the ed25519/HAMT/7-capabilities pattern — same problem class, solved correctly.

Three overclaims hedged substantively. §9 renamed and populated. One residual nit in §4.5(c) line 141 ("Prop 4 now covers the vault layer") contradicts §4.6's hedge three lines later — single-line edit. Paper has **zero `vault` hits** (grep-verified), exceeding the R1 ask.

Ship to `writing-plans`.

## Round-1 findings — resolution status

| R1 finding | Class | Status | Evidence |
|---|---|---|---|
| `bench/regulator_replay/` missing (paper §6.3 phantom) | Class-B CRITICAL | **RESOLVED** | Directory + README + generator.py + harness.py + trajectories/ + reports/ all present; README line 3 "SCAFFOLD. Not yet operational"; stubs raise `NotImplementedError` citing Phase-4. |
| §4.6 "regulator-grade" unqualified | Class-A overclaim | **RESOLVED with caveat** | §4.6 lines 157–163 explicitly frame "regulator-grade" as "marketing framing" with three named technical properties (Merkle-chained, `verify_chain` iff, tier-preserved). The phrase "reserved for external-facing copy" is a clean discipline statement. Residual: §1 line 13 "regulator-grade provenance" still appears unqualified in the thesis sentence. Trivial fix. |
| §4.7 "deterministic, hash-verifiable" unqualified | Class-A overclaim | **RESOLVED** | §4.7 lines 183–188: split into "deterministic at the datom layer" / "hash-verifiable at the datom layer" / "NOT deterministic at the vector layer" with the Phase-5B re-embed rotation invalidating branches. This is the precise embedding-epoch qualifier R1 asked for. |
| §9 "no open questions" | Class-A honesty | **RESOLVED** | Renamed "Open questions — none freeze-blocking." Four items listed (`audit_id` cardinality, Kuzu schema, branch TTL grace, Phase-5B concurrency). Matches the "deferred to implementation" pattern. |
| Prop 4' restatement (capability-asymmetry iff) | Class-A paper-load-bearing | **PARTIALLY RESOLVED** | §4.6 line 160 now states iff precisely as "(a) no tamper (b) no reorder-or-delete, extended: re-running at `db.as_of(tx_time_of(vault_snapshot_tx))` reproduces `result_hash`." But §4.5(c) line 141 still says "Prop 4 now covers the vault layer" — asymmetry (caller-capabilities-at-query-time, §4.4) is not acknowledged in the iff statement. One-line fix. |
| §2 driver-3 Case B coupling clarification | Class-B honesty | **RESOLVED** | §2 driver-3 line 19: "The paper's `bench/regulator_replay/` demo dataset is Dockerised and NOT plumbed into production vault." Clean. |

**Score delta from R1 rubric:** bench scaffold +0.5, Prop 4' language +0.2 (partial), driver-3 flag +0.2, §4.6/§4.7 hedges +0.2, §9 list +0.1, paper §2.1 not independently verified this round. Total ≈ +1.2 → 9.0; one-line residuals round down to 9.1.

## `bench/regulator_replay/` scaffold — READ and graded

**Grade: scaffold-framed, not overclaim.**

README specifics (line-by-line):

- Line 3: **"Status: SCAFFOLD. Not yet operational. Paper §6.3 cites this directory as the walkthrough artifact; the scaffold exists so the citation path is real, not phantom."** Exact acknowledgement of the R4 prior-art concern. Dated: "Phase 4 (~2026-05) and paper v1.0 pass."
- Line 5: "Deliberately decoupled from the production `ai-box` vault." R3's one-way-dependency pin re-pinned.
- Line 18: "Layout (**planned, not yet populated**)." "Planned" is load-bearing and present.
- Line 35: "Importing from `ai-box` from this directory is a lint failure." Operational, not aspirational.
- Lines 37–39 "Current overclaim-hygiene notes": quotes the exact paper language to use until Phase 4 — "the walkthrough artifact is the synthetic-replay dataset scaffolded at `bench/regulator_replay/`; generator-harness implementation lands with the memory-palace retrofit."

`generator.py` / `harness.py`: both raise `NotImplementedError` with the Phase-4 schedule string; `__main__` exits with the same message. Docstrings describe planned shape (seeds, op-mix, retraction quotas). No functional code → reader gets a clear "not yet implemented," not a silent misleading stub.

`trajectories/` and `reports/` exist but empty (no `.gitkeep` yet; not R4-load-bearing).

**Verdict:** exemplar of how to resolve a paper-vs-code drift finding. No ed25519-class risk. Paper can cite in v1.0; until Phase 4 lands, language is honest.

## New research hygiene issues

### 1. ADR `ctx_provider` "small PR" + "<1% overhead" — grounded or hand-waved?

The **"<1% overhead"** claim (line 84) is qualified — "Measured in Phase 3 Task 3 benchmark; **expected** <1% overhead." Word "expected" is correct for an unmeasured estimate. Acceptable.

The **"small PR"** + **"~30 minutes engineering + 15 minutes TDD"** (lines 111, 119) is more concerning. The PR touches `_canonicalise_content` drift-pin matrix. R5/R6 arcs on `_canonicalise_content` cost 5 rounds of polish in Phase-1 ARIS history; treating any adjacent change as "30 minutes" invites the same failure class. Impact: low (internal planning, not paper claim).

**Fix (nit):** ADR "Implementation plan" add one line: "Drift-pin matrix extension historically costs multiple ARIS rounds; budget accordingly."

### 2. Phase 5B bundling ordering — hard-blocking or soft-wished?

§6 pin 5: "Order: Phase 5B ships first (~2026-04-26), Phase 1 layers on top." §9 deferred item 4 acknowledges `tx` counter contention if Phase 1 starts before Phase 5B finishes.

This is **soft-wished**, not hard-blocking. No CI gate, no `AIOPS_VAULT_BITEMPORAL_ENABLED` interlock against "Phase 5B complete." A scheduling mistake produces `tx` contention Task 12 tests may not catch in production.

**Fix (non-blocking):** Phase-1 plan add a startup gate querying Qdrant for winner genesis payload coverage; refuse to enable bitemporal writes otherwise. Paper-risk: none; operational-safety: yes.

### 3. "No open questions" freeze-blocking language

§9 title: "Open questions — none freeze-blocking." R1's exact framing; four items listed. Branch TTL grace duration *could* become freeze-blocking for Phase 4 if not measured against prod load. Honest for Phase 1 freeze; re-check at Phase 4 kickoff. Note for next round rather than a fix.

## New marketing-language audit

Grep pattern: `regulator-grade|enterprise-grade|battle-tested|production-ready|universal|deterministic|no open questions`.

Design doc hits:

- Line 13 "regulator-grade provenance" — §1 thesis sentence, **unqualified** (single residual). Fix: add "— the three-property form defined in §4.6."
- Line 64 "deterministic function of the log" — **qualified in-line** ("modulo the Gemini embedding model itself — see §4.7"). Good.
- Line 157 "Regulator-grade is a marketing framing" — explicit de-marketing. Good.
- Line 163 "makes the system legible to a regulator" — softened. Good.
- Line 184 "Deterministic at the datom layer" — scoped correctly.

Paper draft hits for `vault` (case-insensitive): **zero**. Exceeds R1 ask. Paper remains decoupled — strongest design property from R1.

Paper draft hits for R4-risk phrases: "Regulator-replay fidelity" (line 92) and §6.3 title — these are the *benchmark name*, not a system claim. Scaffold README now backs the citation. Acceptable.

No new "enterprise-grade," "battle-tested," "production-ready," or "universal" introduced.

## 4-axis novelty claim — still intact?

R1 established: bitemporal + effect-audit + capability-gated + counterfactual composition is novel; individual axes prior art.

R2 check: fix pass did not dilute. §1 claims "first-class time-travelling substrate with regulator-grade provenance" (axes 1+2). §4.4 "Taxonomy-v2 × bitemporal composition" names axis 3. §4.7 names axis 4. Composition language intact.

Paper §2.1 novelty sentence (R1 fix request): not re-verified this round; R1 scoped paper edits as non-blocking for design `writing-plans` green-light. Parked for R3-paper round.

**Verdict:** 4-axis novelty intact. No dilution.

## Residual one-line fixes (non-blocking, rollup for R3)

1. §1 line 13 "regulator-grade provenance" → "regulator-grade provenance (three-property form defined in §4.6)."
2. §4.5(c) line 141 "Prop 4 now covers the vault layer" → "a narrower Prop 4' covers the vault layer, with the caller-capability asymmetry (§4.4) handled by re-evaluating against *current* capabilities at reconstruction time — stated and proven in Phase 3."
3. ADR §109 "Implementation plan" add: "Drift-pin matrix extension historically costs multiple ARIS rounds; budget accordingly."
4. §6 pin 5 "Order: Phase 5B ships first" → "**HARD-BLOCKING**: Phase 1 of this track MUST NOT enable `AIOPS_VAULT_BITEMPORAL_ENABLED` until Phase 5B has landed all 14,914 winner genesis payloads. Enforcement: startup gate queries Qdrant for winner bitemporal coverage, refuses boot otherwise."

None of these block `writing-plans`. All are R3-cleanup-pass scope.

## Sign-off

**9.1 / 10.** Clears the 9.0 target. Green-light `writing-plans` for Phase 1.

R1 CRITICAL (bench scaffold) resolved exemplarily. Three R1 overclaims substantively hedged (one partial on Prop 4', one residual thesis-line nit). Paper remains zero-vault-hits. 4-axis novelty intact. No new marketing language introduced.

R4-signature hunt (ed25519 / HAMT / 7-capabilities drift pattern) returns clean this round. The scaffold-with-honest-stubs approach is the model pattern.

Phase 4 still waits on the Prop 4' restatement (residual §4.5(c) line 141). Phase 1 is safe to plan immediately.

*End R4 round 2.*
