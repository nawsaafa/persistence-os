# ARIS Bitemporal Design Round 1 — Consolidation

**Date:** 2026-04-22
**Target:** Memory Palace bitemporal retrofit design doc (`docs/plans/2026-04-22-memory-palace-bitemporal-design.md`) + Phase 1 implementation plan (`ai-box/docs/plans/2026-04-22-memory-palace-bitemporal-phase1-impl.md`)
**Round 1 gate:** min ≥ 9.0 (Phase 0 exit criterion)
**Verdict:** ❌ **GATE MISSED by 2.4** — min 6.6. Single-pass fix round required; architecture sound, language/citation discipline weak. Round 2 expected to clear 9.0 cleanly.

## Scorecard

| Reviewer | Score | vs 9.0 target | Headline |
|---|---:|---:|---|
| R1 Correctness | **6.8** | -2.2 | API surface cited in design doesn't match real `persistence.fact` API |
| R2 Rigor | **6.6** | -2.4 | Same API mismatch blocks most test code; drift-pin matrix missing |
| R3 Composability | **8.2** | -0.8 | Architecture composes cleanly; three specific gaps |
| R4 Research | **7.8** | -1.2 | `bench/regulator_replay/` doesn't exist but is referenced as if it did |
| **min** | **6.6** | **-2.4** | **MISS** |

R3 grade (8.2) is the strongest signal: the architecture is genuinely right. R1/R2 pulled the min down on the same issue (API naming); R4 caught one paper-vs-code drift. These are corrections, not redesigns.

## Convergent findings — the important ones

### Class-A: API contract mismatch (R1 N1/N2 + R2 M1) — CRITICAL

**Severity:** MAJOR + blocking. Workers cannot execute Phase 1 as written.

Design doc + Phase 1 plan cite API that doesn't exist on `persistence.fact.DB`:

| Cited (wrong) | Actual (verified by reviewers reading `src/persistence/fact/`) |
|---|---|
| `DB.transact([datoms]) -> tx` (returns int) | `DB.transact(list[dict]) -> DB` (returns new DB) |
| `DB.q_latest(e, a)` | `DBView.entity(e)` (filtered by caller) |
| `DB.q_entity(e)` | `DB.history(e)` |
| `DB.q_as_of(e, a, tx)` | `DB.as_of(t) -> DBView` then `.entity(e)` |
| `DB.q_all()` / `DB.q_by_tx(tx)` / `DB.latest_tx()` | `DB.log()` / `DB.since(t)` — different shape entirely |

Design §4.5 also conflates **transaction-time** (`tx_time`) with **valid-time** for `/vault-as-of`. `persistence.fact` distinguishes them via `DB.as_of(t)` (system-time snapshot) vs `DB.as_of_valid(vt)` (user-time view). The `/vault-as-of?t=X` endpoint needs to choose one semantically — the answer depends on whether "what did the vault believe on date X" means "system-time state as of X" or "facts valid at world-time X."

**Fix:** Single-session edit pass on design doc §4.1, §4.2, §4.5 + Phase 1 plan Task 2 rewriting the `VaultFactStore` adapter against the real API. This is mechanical — the architecture survives intact. `DB.transact` returning a new `DB` rather than a tx is slightly different ergonomics (we track `tx` via `DB.log()[-1].tx` or similar), not different semantics.

### Class-B: paper-vs-code drift (R4 critical) — MAJOR

**Severity:** MAJOR.

`persistence-os/bench/regulator_replay/` doesn't exist. Verified: `ls persistence-os/bench/` → no such directory. Paper §6.3 commits to releasing it as flagship novelty; design doc references it as if it were a live artifact. **Exact same class as the ed25519 / HAMT / 7-capabilities overclaims that R4 caught in prior rounds** (and which drove R4 up from 6.5 → 9.4 as they were closed).

**Fix:** Scaffold `bench/regulator_replay/` as a real directory with README (what it will contain), placeholder generator (plan for 10-trajectory synthetic dataset), reconstruction harness stub. Do NOT over-claim what's in it yet. Paper §6.3 then has a real path to cite.

### Class-C: self-conform discipline gap (R3 N4 + R2 M3) — MEDIUM

Phase 1 Task 1 registers four `:persistence.vault/memory-*` specs for datom values, but the 10-field Qdrant payload schema has no registered `:persistence.vault/qdrant-payload` spec. This violates the persistence-os discipline "every wire boundary has a registered spec and self-conforms" pinned at R3 F8 across multiple prior rounds.

**Fix:** Add a Task 5.0 to the Phase 1 plan: register `:persistence.vault/qdrant-payload` with field specs for `{valid_from, valid_to, tx, tx_time, op, entity_id, embedding_hash, tier, bucket, audit_id}`. `QdrantProjector.project` self-conforms before upsert.

## Non-convergent MAJOR findings

### R2 M2: Property-test coverage gap

Design §7.3 gestures at the "Qdrant projection is deterministic function of log" invariant with one sentence. No Hypothesis strategy, no out-of-order projection replay test, no partial-replay idempotency test. Persistence-os's Prop 4 drift-pin is a 29-case parametrised matrix (`tests/effect/test_audit_canonicalize_drift_pin.py`) — the vault retrofit needs an equivalent for projection determinism.

**Fix:** Expand Phase 1 plan Tasks 10 + 11 with explicit Hypothesis strategies (random write sequences including retractions), named invariants, and state-machine testing for concurrent write + repair.

### R2 M5: Retraction edge cases unpinned

Design §4.2 shows retract-and-reassert. Reviewers flagged: double retraction, non-existent-memory retraction, content-not-hash retraction, tier/bucket change via retract-and-reassert. None pinned in Phase 1 plan.

**Fix:** Add retraction test class to Phase 1 plan Task 2 with the four edge cases.

### R2 M6: Concurrency — same-memory-id race

Two simultaneous `/vault/remember` on same `memory_id` → lost update in Qdrant; repair job racing with live projection → can resurrect retracted content. `persistence.fact.DB.transact` handles tx ordering via `BEGIN IMMEDIATE`, but the Qdrant projection layer has no equivalent guard.

**Fix:** Pin a per-memory serialisation guard in Phase 1 plan Task 3 (either via `memory_id` hash → lock, or single-writer queue). Document in §4.1 of design doc.

### R3 N2: Runtime binding unspecified

Design §4.6 says `/vault/recall` runs inside `persistence.effect.Runtime` wrapped in audit handler, but doesn't say *where* `with_runtime(rt)` is bound. Existing FastAPI middleware stack (ai-box `apps/backend/src/app/main.py:439–444`) runs outside any Runtime scope. Need an explicit `VaultRuntimeMiddleware` positioned below `TraceContext` and above the API router.

**Fix:** Add §4.6.1 to design doc documenting middleware placement + per-request Runtime instantiation.

### R3 N3: Read-your-writes contract missing

During the commit→projection window, `/vault/recall` might miss a memory that `/vault/remember` just committed (if projection is async). Design doesn't specify the consistency contract.

**Fix:** Pin the synchronous-projection-in-same-UoW semantics in §4.1 — `ProjectionError` bubbles up to the client if Qdrant upsert fails AFTER datom commit (caller sees "committed but unindexed, retrieved via `/vault/as-of?tx_min=N` until repair"). Repair job is failure-recovery only, not steady-state.

### R1 N3: `vault_snapshot_tx` injection has no clean hook

`make_audit_handler` in `persistence.effect` freezes `ctx` at construction. Adding per-call `vault_snapshot_tx` needs either an `AuditEntry` schema change (triggers own ARIS) or a `ctx_provider` extension.

**Fix:** ADR written alongside the fix pass. Recommend `ctx_provider` extension — callable evaluated per invocation, returns additional ctx. Smaller surface than schema change. Spec'd against `:persistence.effect/audit-entry` compatibility.

## R4-signature overclaims to strike

Matches prior R4 pattern (ed25519 / HAMT / 7-capabilities):

1. §4.6 "**regulator-grade** audit" — unqualified marketing language. Strike or qualify with specific properties (Merkle-chained, `verify_chain` iff integrity, tier-preserved).
2. §4.7 "**deterministic, hash-verifiable**" counterfactual replay — ignores Gemini embedding non-determinism and the Phase 5B re-embed event. Qualify: deterministic *modulo the embedding model*, hash-verifiable *at the datom layer* (not the vector layer).
3. §9 "**no open questions at design level**" — four are glossed (tx-time vs valid-time axis for /as-of; audit_id cardinality; Kuzu bitemporal schema; per-branch TTL cleanup race). Rename to "Open questions — none freeze-blocking" with the four listed.

## Non-blocking MINORs (accept-or-defer)

- R1 N6: genesis `tx=0` wording — pin in §6.
- R1 N7: Phase 1 depends on Phase 5B; encode in §5 phase table.
- R1 N8: §4.2 example is retroactive; note `force_retroactive=True` or reframe.
- R2 MINORs: "modulo ordering" vague; tamper-test needs 3 cases; branch TTL needs freezegun; embedding_hash→vector proof unnamed; latency regression spec will flake; no wall-clock-ban lint analogue for vault layer.
- R3 N1 nit: drift-pin test between `_TIER_VALUES` and taxonomy-v2 CSV.
- R3 N5 nit: `_build_capability_filter(caller)` helper in Phase 1 to avoid duplication in Phase 4.
- R3 N6: branch TTL two-phase cleanup (mark expired + grace, then drop).
- R3 N8: pre-commit `VaultFactStore` Protocol/ABC stub in Task 0 so parallel workers import the stub.

## What round 1 got right (high-confidence pins)

- **Datom schema** — 10-field `Datom` shape including provenance + invalidated_by verified against source
- **Atomic `BEGIN IMMEDIATE`** — concurrency claim holds, stress test from ARIS R2 P-concurrency applies directly
- **`lstrip(":")` canonicalisation** — verified at `datom.py:89` and `audit.py:128–157`
- **Spec registry namespace freedom** — `:persistence.vault/*` works per R1 N5 and R3 read
- **Phase 5B pins 1/3/6/8** — R1 confirmed consistency with design body
- **Strategic alignment** — R4 confirms vault layer is genuinely paper-decoupled; worst-case slip of Phases 2/3/4/5 still lets the paper submit unchanged (`grep "vault" paper/` → 0 hits, verified)
- **4-axis novelty claim** — R4 confirms (bitemporal + effect-audit + taxonomy-gated + counterfactual) has no 2023-2025 peer combining all four; closest is SSGM dual-track
- **Zero cross-repo coupling** — R3 verified `persistence-os/bench/` unreferenced in `ai-box/apps/backend/`; coupling is one-way via pyproject pin
- **Worker-team file paths in Phase 1 plan do NOT overlap** (R3 N8)

## Round-2 target grades + fix-pass scope

| Reviewer | R1 | R2 target | Delta needed |
|---|---:|---:|---:|
| R1 Correctness | 6.8 | ≥ 9.0 | +2.2 |
| R2 Rigor | 6.6 | ≥ 8.8 | +2.2 |
| R3 Composability | 8.2 | ≥ 9.3 | +1.1 |
| R4 Research | 7.8 | ≥ 9.0 | +1.2 |

**All deltas are single-pass fix territory** — mechanical citation corrections (R1/R2), scaffold-then-cite (R4), documented pins (R3). No architectural rework. Est. 1.5–2 hours for the fix pass.

## Fix-pass scope (single-session worker, next step)

1. **API contract rewrite** — design §4.1/§4.2/§4.5 cite real API names, `VaultFactStore` in Phase 1 Task 2 reshaped against `DB.transact` → `DB` semantics. Pick tx-time OR valid-time for `/vault-as-of` semantics with rationale. (30 min)
2. **Scaffold `bench/regulator_replay/`** in persistence-os: `README.md` + `generator.py` placeholder + `harness.py` stub. Real directory, not phantom. (15 min)
3. **Register `:persistence.vault/qdrant-payload` spec** in Phase 1 Task 5.0. Full field list. (10 min)
4. **Pin retraction edge cases + concurrency guards** in Phase 1 plan (Task 2 retraction tests; Task 3 per-memory serialisation). (15 min)
5. **ADR on `vault_snapshot_tx` via `ctx_provider` extension** — small new doc, may need tiny persistence.effect PR. (15 min)
6. **Middleware placement + read-your-writes** — design §4.6.1 + §4.1 pins. (10 min)
7. **Strike/qualify overclaims** — §4.6 "regulator-grade", §4.7 "deterministic", §9 "no open questions". (10 min)
8. **Minor fold-ins** — genesis tx wording, Phase 5B dependency in phase table, retroactive example reframing, all the R3/R2 MINORs. (15 min)

After fix pass: dispatch Round 2 (4 parallel reviewers, same reviewer identities). Target min ≥ 9.0.

## Reports

- `R1-correctness.md` · `R2-rigor.md` · `R3-composability.md` · `R4-research.md` · this file

## Meta-observation

This design-doc review arc mirrors Phase 1's own round-1 (min 4.5 on the code). Same pattern: **R3 caught architectural issues Phase 1 code review missed; R1/R2 caught mechanical discipline; R4 caught paper drift**. The loop works. Fix pass is well-scoped.
