# ARIS Bitemporal Design Round 1 — R4 — Research Alignment

*Design `docs/plans/2026-04-22-memory-palace-bitemporal-design.md` vs. paper `paper/persistence-nesy-2026-draft.md` v0.3 (460 lines) vs. vision memory `project_juba_os_vision.md`. R4 Phase-1 axis: 6.5 → 8.2 → 8.6 → 9.0 → 9.3 → 9.4. Round 1 on DESIGN; bar is "submit with honest claims."*

---

## Summary grade: 7.8 / 10 — green-light `writing-plans` after six closable corrections

Strategy is correct. Taxonomy-v2 × bitemporal composition (§4.4) and "datom log canonical, Qdrant/Kuzu are projections" (§4.1) are the right moves. Three language overclaims and one missing artifact are the closable residuals. **7.8 clears the 7.5 green-light bar.** Phase 4 should wait on one restatement (see §4 below).

---

## 1. Paper coherence (4 propositions)

- **Prop 1 (branch isolation)** — *strengthened.* Branch Qdrant collections are namespaced; stricter than the paper's in-memory-store claim.
- **Prop 2 (well-formedness)** — *unchanged.* Retrofit adds no new ops; no new coverage gaps.
- **Prop 3 (NO-OP byte-identity)** — *not engaged, correctly.* Vault queries are deterministic-given-`(tx, filter)`.
- **Prop 4 (`verify_chain` iff)** — **OVERREACH.** Design §4.5(c)/§4.6: "Prop 4 extended to vault layer." Prop 4 (paper L163) is iff over *audit-entry* Merkle-chain mutation/deletion/reorder. The design describes a distinct property — **Prop 4' (vault-replay fidelity)**: for audit entry E with `vault_snapshot_tx = k`, re-querying `D_k` with caller's *current* capabilities recomputes `result_hash`. The capability-at-query-time vs. -at-write-time asymmetry (§4.4) makes the iff direction non-trivial — different caller capabilities yield different hashes, which is correct but breaks naive "re-run matches."

**Fix:** §4.5(c)/§4.6 downgrade to "new Prop 4' to be stated and proven in Phase 3." Same honesty pattern the paper used for per-step-rng Phase 2 gap.

---

## 2. Case B (Adaptive Trader v2) coupling

Separation is **correct.** Paper §6.5 L342–351 says Case B persists trajectories through "the Fact module" — persistence-os, not the vault. No paper drift today. Drift risk: if §6.3 numbers start pulling from the vault datom log, an implicit "deployed on production vault" claim opens. **Fix:** §2 driver-3 add one sentence clarifying Case B's Fact-store is the trader's, not the vault's.

---

## 3. Claim honesty — R4-signature hunt

**§4.6 "regulator-grade"** — unqualified. Paper L163 hedges: detects mutation/deletion/reorder; tail-truncation needs external length recording; authenticity is Phase-2 ed25519. Design inherits none. **Fix:** one-sentence qualifier inheriting paper hedges.

**§4.7 "deterministic, hash-verifiable"** — Gemini is not deterministic across model-versions; Qdrant vector search is deterministic given vectors not given text. Determinism holds **only within a single embedding-model epoch** (paper's Phase 5B re-embed event is where this breaks). **Fix:** scope to a fixed-embedding-model epoch.

**§9 "no open questions"** — four glossed: (1) capability-asymmetry iff direction (§1 above); (2) branch TTL × audit chain — if TTL'd branch is referenced by audit entry, does `verify_chain` pass?; (3) Qdrant schema — §4.3 "omit when currently-valid" vs §4.5(b) filter `valid_to IS NULL OR valid_to > t` — different predicates, canonical?; (4) Kuzu backfill determinism under concurrent writes during Phase 5. **Fix:** §9 rename to "four deferred to implementation phases."

---

## 4. Novelty claim vs. prior art

Individual axes are prior art: bitemporal (Datomic, Zep, XTDB), effect-audit (Pangolin), capability-gated (Conjur, Cedar, RLS), counterfactual (Datomic `asOf+with`, Dolt, CAMO). **Composition is novel**; web-search for the 4-axis conjunction on 2023–2025 papers returns no direct match. Closest prior: **SSGM** (Hannecke 2026) — dual-track immutable-episodic + mutable-active — maps cleanly onto "datom log + Qdrant projection." Paper §2 "Related Work" doesn't mention capability-gating composition. **Fix (paper):** §2.1 add: "Persistence composes bitemporal immutability (Zep/Datomic) with capability-gated access (Conjur/Cedar) and counterfactual branching (CAMO) — the composition is the contribution."

---

## 5. Paper §6.3 walkthrough readiness — **CRITICAL**

**`bench/regulator_replay/` does not exist.** `ls persistence-os/bench/` → `NO bench/ DIRECTORY`. `verticals/` and `prototypes/` empty. Paper §6.3 commits to releasing a 50-trajectory generator + reconstruction harness under CC-BY-4.0 before camera-ready. Design (§2 driver-3, §8 risk row) *names* this directory as if it existed. Same class of paper-vs-code drift R4 caught on ed25519, HAMT, "7 capabilities → 4." Pre-submission cheap; post-submission retraction-class.

**Required parallel micro-track `persistence-os-bench-scaffold_20260422` (1–2 engineer-days):** `README.md` + `placeholder_generate.py` (N=2 end-to-end through `persistence.fact`) + `placeholder_reconstruct.py` (replay + hash) + root README pointer. Without this, R4-paper-review downgrades a full grade next round.

---

## 6. Investor thesis alignment

YC-partner pitch: *"Every framework treats memory as a vector-store sidecar you regret. We make it a first-class time-travelling substrate. 'What did it know on April 10?' — one query. 'What if it hadn't known X?' — fork the log."* That lands. Design delivers three distinct wow-moments (Phase 2 time-travel, Phase 3 regulator-query, Phase 4 counterfactual) in 90 seconds.

**Prioritization correct.** §4.4 resists the "snapshot the capability map" complexity bomb cleanly. **Unflagged risk:** Phase 2's iOS/dashboard time-picker touches three workers; if it slips the demo has no user surface. Phase-2 plan should include curl-the-API fallback demo.

---

## 7. NeSy deadline pressure — minimum honest submission

Abstract +48d, paper +55d, design 3-4w. **Paper v0.3 makes zero vault claims** — `grep "vault\|memory-palace\|juba" paper/` → 0 hits. Vault retrofit is not paper-load-bearing. **Strongest design property.**

Worst-case: Phase 1 ships; Phases 2/3/4/5 slip. Paper submits unchanged. R4 grade unaffected. Vault becomes 2026-Q3 product ship decoupled from NeSy. Grade-preserving decoupling. **Paper critical path is the bench scaffold (§5), not the retrofit.**

---

## 8. Required corrections before `writing-plans`

(1) §3 driver-3 flag bench as parallel micro-track; (2) §4.5(c)+§4.6 downgrade to "new Prop 4' proven in Phase 3"; (3) §4.6 add "regulator-grade" qualifier inheriting L163 hedges; (4) §4.7 scope determinism to embedding-model epoch; (5) §9 rename to "four deferred"; (6) create `bench/regulator_replay/` scaffold (1-2 days parallel).

No architectural changes. Green-light `writing-plans` after these. **Phase 4 waits on (2).**

---

## 9. Re-grade rubric (R2 target)

Bench scaffold +0.5, Prop 4' language +0.3, §3 bench flag +0.2, §4.6/§4.7 hedges +0.2, §9 list +0.1, paper §2.1 novelty sentence +0.1. **R2 realistic target: 8.8–9.0.** Design-doc R2 of 8.8 is clean-enough to implement with zero paper-retraction risk.

---

*End R4 round 1. Strategic direction sound, Juba OS thesis alignment clean, 4-axis novelty real. Three overclaims + missing bench scaffold = closable. Phase 1 safe to plan; Phase 4 waits on Prop 4' restatement.*
