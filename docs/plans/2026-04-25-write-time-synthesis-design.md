# Write-Time Synthesis — Design Map

> **⚠️ PARKED 2026-04-25** — Two parallel reviews (substrate-fit + paper-claim) returned converging "do not ship as written." Awaiting Memory Palace burn-in #3 close (~16:30 UTC+1), then full redesign. **Do not implement R1.** See vault memory `4799908f-151a-42e0-a13d-93bc5360a6d8` for review summary. Known fatal claims: `persistence.plan._interpret.py` is a DFS visitor (no evaluator); Qdrant has no atomic rename API; `DB.branch` has zero projector hooks; `provenance` is unindexed dict; Prop 6 is a corollary of Props 2+5, not a novel theorem.

**Date:** 2026-04-25
**Status:** PARKED — redesign pending post-burn-in #3 close
**Author:** Nawfal Saadi (with Claude Opus 4.7)
**Audience:** persistence-os engineering agent (next session)
**Predecessor design:** `docs/plans/2026-04-22-memory-palace-bitemporal-design.md`
       + `ai-box/docs/plans/2026-04-23-memory-palace-bitemporal-phase2-design.md`
**Phase 1 dependency:** v0.1.0a1 (frozen clean, ARIS min 9.4)
**Phase 2.B dependency:** `persistence.plan` v0.3.0a1 (R3-M4 coercion registry shipped)
**Phase 2.A dependency:** Memory Palace burn-in #3 close ~2026-04-25 16:30 UTC+1 — **THIS DESIGN STARTS AFTER BURN-IN PASSES**
**NeSy 2026 relevance:** candidate **Proposition 6** for paper v0.7+ — must land before 2026-06-09 abstract deadline

---

## 1. One-paragraph summary

The Memory Palace retrofit makes `/vault/remember` write content datoms to the canonical log. **Synthesis** — tier classification, similarity-driven supersession, ripple confidence updates, backlinks wiring — still happens in nightly batches outside the transaction. This design promotes synthesis to a first-class datom emitter: every synthesis effect produces datoms in the **same `DB.transact` call** as the content datoms, sharing one `tx` and `tx_time`. Synthesis becomes (a) atomic, (b) reversible via `DB.branch` + `persistence.replay`, (c) provenance-traceable via a new `/vault/why` API, and (d) **counterfactually swappable** — upgrade the tier classifier and re-project the entire history without migration.

This is the move that turns the bitemporal retrofit from "infrastructure" into a **paper-grade research claim**.

---

## 2. Invariant alignment (track plan.edn)

| # | Invariant | How write-time synthesis honours it |
|---|-----------|--------------------------------------|
| I1 | Every fact is immutable, temporal, content-addressed | Synthesis-derived facts (`:memory/tier`, `:memory/cluster_id`, `:memory/confidence`) are datoms with the same shape — content-addressed via canonical hash, retracted via companion retract on update |
| I2 | Every action is an effect | Synthesis runs through `persistence.effect.Runtime` — `:llm-call` for classification, `:audit` for trace, all under the existing handler stack |
| I3 | Every plan is an EDN AST | Synthesis pipeline is itself a `[:seq [:llm-call ...] [:tool-call ...] [:transact ...]]` plan node, executed through `persistence.plan` |
| I4 | Every shared state change is a transaction | All synthesis datoms commit in **one** `DB.transact` call with the content datoms — atomic or full rollback |
| I5 | Every LLM boundary has a spec | Tier classifier output, ripple-target list, supersession candidates each have Malli specs in `persistence.spec` |
| I6 | Everything is REPL-live | `/vault/why` exposes provenance walk live; classifier branches steerable through `persistence.repl` (Phase 3) |

**Conclusion:** the design is a *consequence* of the 6 invariants, not an extension. The current vault retrofit only honours I1+I4 partially because synthesis is outside the transaction. This closes that gap.

---

## 3. Architecture

### 3.1 Current state (post-burn-in #3)

```
POST /vault/remember
    │
    ▼
VaultService.remember()
    │
    ├──► persistence.fact.DB.transact([content_datoms])    ← canonical, atomic
    │       │
    │       ├──► QdrantProjector.upsert (vector + payload)
    │       └──► KuzuProjector.upsert (entity + edges)
    │
    └──► return  (synthesis happens later, in nightly cron)

(nightly 23:00..02:00)
    │
    ├──► tier classifier batch       (writes to PG side-tables, NOT log)
    ├──► ripple propagation          (mutates confidence in-place)
    ├──► backlinks scan              (writes Kuzu edges directly)
    └──► cluster reassignment        (mutates :memory/cluster_id in-place)
```

Synthesis state is *derived but not deterministic-from-log*. Classifier upgrades require migration. Ripple effects are not auditable. This violates the spirit of I1.

### 3.2 Proposed state

```
POST /vault/remember
    │
    ▼
VaultService.remember()
    │
    ▼
SynthesisPlan (plan AST, executed through persistence.effect)
    │
    ├──► [:llm-call {tier-classifier}]              → tier_value
    ├──► [:tool-call {similarity-search}]           → supersession_candidates
    ├──► [:tool-call {neighbour-scan}]              → ripple_targets (N=1)
    │
    ▼
persistence.fact.DB.transact([
    # content datoms (existing)
    (mem-X, :memory/content, "...", op=assert, valid_from=now),
    (mem-X, :memory/embedding_hash, sha256, op=assert),

    # synthesis datoms (new — same tx)
    (mem-X, :memory/tier, :L1, op=assert, provenance={classifier_version: v3}),
    (mem-X, :memory/cluster_id, cluster-7, op=assert),
    (mem-X, :memory/confidence, 0.84, op=assert),

    # auto-supersession datoms (new — companion retracts emitted by DB.transact)
    (mem-Y, :memory/content, "older claim", op=retract, valid_to=now,
     provenance={supersedes_caused_by: mem-X}),

    # ripple effect datoms (new — same tx)
    (mem-Z, :memory/confidence, 0.71, op=assert,
     provenance={ripple_caused_by: mem-X}),
])
    │
    ▼
Single tx commit. Projections fan out. Audit entry hashed over the whole tx.
```

**The defining property:** every synthesis effect has a `tx` and a `provenance` pointing to its trigger. The log is sufficient to reconstruct any synthesis state. Replay is therefore well-defined for synthesis the same way it is for plain writes.

---

## 4. Module deliverables

Four deliverables, sequenced **R1 → R2 → R3 → R4**. Sized for the NeSy 2026 abstract window (2026-04-25 → 2026-06-09).

### R1 — Explicit supersession on write

**Persistence OS side:** none. `DB.transact` already auto-retracts on same-`(e, a)` overwrite (db.py:174–180) and `RetroactiveCorrectionError` already guards retroactive writes. No persistence.fact change needed.

**ai-box side:**
- `apps/backend/src/app/api/v1/vault.py` — `RememberRequest` gains `supersedes: list[str] | None`
- `apps/backend/src/app/services/vault/bitemporal/fact_store.py` — `VaultFactStore.remember_fact()` accepts `supersedes` and emits explicit `op=retract` datoms for each prior memory_id with `valid_to=now`, `provenance.supersedes_caused_by=new_memory_id`
- `apps/backend/src/app/api/v1/vault.py` — `/vault/recall` default filter becomes `valid_to IS NULL OR valid_to > now()`
- New endpoint: `GET /vault/as-of?t=<iso>&q=<text>` (already designed in Phase 2 Stream B as B1; this confirms its semantics for synthesis use)

**Ships:** 1–2 weeks. Closes the "telemetry pollution / winners-losers cull" failure mode permanently — retraction replaces deletion.

**ARIS gate:** R1 (correctness) + R3 (composability with VaultService entry path); skip R2/R4 — no new claims.

### R2 — Auto-supersession via similarity + LLM contradiction detector

**ai-box side:**
- New service: `apps/backend/src/app/services/vault/bitemporal/synthesis/supersession_detector.py`
  - On write, run vector similarity top-K against same `(agent_id, bucket, attribute)`
  - If `cos_sim > τ` AND opposing-claim LLM call returns `True`, propose supersession
  - Surface to caller via response field `proposed_supersessions: list[memory_id]` (NOT auto-applied)
  - Caller (UI / agent) confirms; second `/vault/remember` call with `supersedes=[...]` finalises

**Persistence OS side:**
- `src/persistence/spec/registry.py` — register `:vault/supersession-candidate` spec
- `src/persistence/effect/handlers/llm_call.py` — already exists; add `:contradiction-check` op to catalog (`catalog.py`) for ARIS-reviewable boundary

**Ships:** 2–3 weeks after R1. Threshold tuning is the long pole; default τ=0.92 conservative.

**ARIS gate:** R1 + R2 (rigor of false-positive/negative rate) + R4 (paper relevance — this is the data point for "AI as maintainer" claim).

### R3 — `/vault/why` provenance trace API

**ai-box side:**
- New endpoint: `GET /vault/why?memory_id=<id>&depth=<n>`
- Implementation: pure log walker over `DB.history(memory_id)` + transitive walk via `provenance.ripple_caused_by` and `provenance.supersedes_caused_by` edges
- Returns DAG: `{nodes: [{memory_id, tx, tx_time, content_excerpt}], edges: [{from, to, kind: "ripple"|"supersedes"|"backlink"}]}`

**Persistence OS side:**
- `src/persistence/fact/db.py` — extend `DB.history(e)` (line 258) to optionally return *transitive* history via provenance edges. Currently follows only same-`e`; new `DB.causal_history(e, depth)` follows provenance pointers. **Composability concern:** must not loop on cyclic provenance; depth cap + visited set required.
- New spec: `:vault/why-result` Malli definition in `persistence.spec.registry`

**Ships:** 1 week (read-only, layers on existing primitives).

**ARIS gate:** R3 (composability — extension of existing `DB.history`) + R4 (paper relevance — this is the demo for Prop 6).

### R4 — Counterfactual classifier swap (Phase 2b dependency)

**Hard dependency:** `persistence.replay` Phase 2b counterfactual branches. The retrofit design memo defers this to "Post" (paternity leave). For NeSy submission, it must be promoted to "Pre" or executed Phase 2b in parallel.

**Persistence OS side:**
- Already designed via `DB.branch(t, [interventions])` (db.py existing, returns fresh in-memory store seeded from `as_of(t)`)
- New: classifier-swap intervention shape — list of `(:memory/tier, mem_X, new_value, classifier_version=v4)` interventions
- New: `bench/classifier_swap/` — benchmark that re-projects 10k memories with classifier v3→v4, measures: (a) wall-time, (b) tier-distribution diff, (c) downstream recall-quality delta

**ai-box side:**
- `apps/backend/scripts/classifier_swap_dryrun.py` — admin CLI: fork branch, re-project, show diff, accept-or-discard
- Atomic swap: rename Qdrant collections (current → archive, branch → current); Kuzu node attribute update via `DB.transact` retract+assert

**Ships:** 3–4 weeks. **This is the headline demo** for the NeSy paper figure.

**ARIS gate:** all 4 reviewers. **min ≥ 9.0** (research-claim quality bar).

---

## 5. Paper claim — candidate Proposition 6

**Proposition 6 (Synthesis as Projection).**
*Let `D` be a datom log and `Π` be a synthesis projector — a deterministic function from `D` to a synthesis state `S`. For any `D'` produced by `D.branch(t, interventions)`, `Π(D')` is computable in time linear in `|interventions| + |D' \ D|`, and `Π(D)` ⊕ `Π(D')` differ exactly at the points downstream of `interventions` in the provenance DAG.*

**Why this matters for NeSy:**
- LongMemEval and AgentBench treat synthesis as opaque. We make it *replayable*.
- Counterfactual fidelity benchmark (already in Phase 4 plan.edn) gets a new axis: classifier counterfactuals, not just intervention counterfactuals.
- Editorial-trap critique (Karpathy wiki failure mode) becomes a **strength**: our editorial decisions are reversible.

**Evidence required for paper:**
1. Formal statement + proof sketch (4–6 pages, §4.6 of paper)
2. R4 benchmark numbers: classifier v3→v4 swap on a 10k-memory production-realistic corpus
3. Property-test: random `(D, interventions)` → `Π(D.branch) == replay(Π, D, interventions)` on `max_examples=200`

---

## 6. Sequencing

```
2026-04-25 16:30 UTC+1   ← Burn-in #3 close (gate)
2026-04-25 → 2026-04-29  R1 explicit supersession              (1 week, simple)
2026-04-29 → 2026-05-06  R2 auto-supersession detector         (1 week, threshold-tuning)
2026-05-06 → 2026-05-13  R3 /vault/why API                     (1 week, layer on history)
2026-05-13 → 2026-06-03  R4 counterfactual classifier swap     (3 weeks, paper figure)
2026-06-03 → 2026-06-09  ARIS Round 1 on full deliverable + paper §4.6 + Prop 6 + abstract
2026-06-09 23:59 AoE     NeSy abstract submission
```

Buffer: zero on R4. If R4 slips, ship R1+R2+R3 only and demote Prop 6 to "future work" in the paper. **Decision point at 2026-05-20:** R4 progress check — if classifier-swap dry-run isn't producing diff on 10k corpus by then, cut and submit without it.

---

## 7. Out of scope (be explicit)

- **Persistence.txn (Module 5)** — no STM dependency for write-time synthesis. Defer.
- **Persistence.repl (Module 7)** — `/vault/why` is HTTP-only for now; live REPL navigation deferred.
- **Cross-tenant supersession** — same-tenant only. Cross-tenant raises an explicit error.
- **Backfill of existing 10k pre-synthesis memories** — treat as `tx=0 genesis`, no synthesis datoms attached. New writes only carry synthesis. Decision is consistent with Memory Palace retrofit memo §"Backfill or fresh start".
- **Synthesis under flag-OFF path** — if `AIOPS_VAULT_BITEMPORAL_ENABLED=false`, synthesis runs in legacy nightly mode unchanged. No new code-path on the legacy hot path.

---

## 8. Risk register

| # | Risk | Mitigation |
|---|------|-----------|
| 1 | Write latency budget blown by synchronous LLM tier classification | Rules-based classifier first-pass on `(agent_id, bucket, source)`; LLM only on ambiguous (<10% of writes); budget p95 +200ms over Phase 1 baseline |
| 2 | Auto-supersession false positives corrupt graph | Default = propose only, never auto-apply; explicit `supersedes` field carries human-confirmed ids only; threshold τ tunable per agent_id |
| 3 | Provenance cycles in `/vault/why` walk | Depth cap + visited set; reject cycles at write-time via spec validator |
| 4 | Classifier-swap rollback during atomic projection swap | Two-phase: fork branch + re-project to shadow Qdrant collection, validate counts + checksums, atomic rename, keep archive for 7 days |
| 5 | NeSy deadline pressure forces shipping R4 unproven | 2026-05-20 cutoff: ship R1+R2+R3 only, demote Prop 6 to "future work" |
| 6 | Synthesis datoms blow up `tx` count, hurting `as_of` query latency | All synthesis datoms share one `tx` per `/vault/remember`; query-side already filters by `valid_to`; cardinality stays at 1 tx per remember call |

---

## 9. Open questions for the persistence-os agent

These need the persistence-os agent's call before R1 starts:

1. **Should `:memory/tier` and `:memory/cluster_id` live in `:memory/*` namespace, or get a new `:synthesis/*` namespace?** Argument for split: cleaner ARIS-reviewable boundary; argument against: tier is provenance metadata in the existing retrofit design, splitting now would be a breaking change.

2. **Does Prop 6 deserve a fresh proof in the paper, or does it follow from Prop 2 (replay determinism) + Prop 5 (plan content-addressing)?** If derivative, §4.6 is one page; if novel, §4.6 is 4–6 pages and needs a new lemma chain. Reviewer R4 should weigh in.

3. **Synthesis-as-effect vs synthesis-as-plan.** R1 architecture sketch shows a `SynthesisPlan` (EDN AST executed via `persistence.plan`). Alternative: synthesis-as-handler-stack, no plan AST. Plan-AST is more ARIS-reviewable and matches I3, but adds a `persistence.plan` evaluator dependency we don't have yet (interpret.py is partial). Decision blocks R2 architecture.

4. **Backfill stance.** Memory Palace memo leaned "backfill". For synthesis, backfill means re-running tier classifier on 10k legacy memories — that's the R4 demo run done as one-off ETL. Promote backfill to part of R4? Or stay genesis-only?

5. **Should R4 land before NeSy abstract (2026-06-09) or before camera-ready (2026-07-20)?** Abstract gives more time but makes Prop 6 "future work" in submission. Camera-ready gives less time but lets Prop 6 ship as a headline claim. Default plan above assumes abstract.

---

## 10. File:symbol references (for the persistence-os agent's grep convenience)

**Persistence OS:**
- `src/persistence/fact/db.py:74` — `class DB`
- `src/persistence/fact/db.py:99` — `DB.transact` (auto-retraction lines 174–180)
- `src/persistence/fact/db.py:239` — `DB.as_of`
- `src/persistence/fact/db.py:258` — `DB.history` (extend for R3 `causal_history`)
- `src/persistence/fact/datom.py` — `Datom` 8-tuple frozen dataclass
- `src/persistence/effect/catalog.py` — 15-op effect catalog (R2 adds `:contradiction-check`)
- `src/persistence/spec/_registry.py` — register `:vault/why-result`, `:vault/supersession-candidate`
- `src/persistence/plan/_interpret.py` — partial; full eval needed for synthesis-as-plan (Q3)

**ai-box:**
- `apps/backend/src/app/api/v1/vault.py` — `/vault/remember` route (R1: add `supersedes` field)
- `apps/backend/src/app/services/vault/bitemporal/fact_store.py:130` — `class VaultFactStore`
- `apps/backend/src/app/services/vault/bitemporal/fact_store.py:184` — `VaultFactStore.remember_fact` (R1: accept `supersedes`)
- `apps/backend/src/app/services/vault/bitemporal/fact_store.py:376` — `VaultFactStore.retract` (R1: reuse for explicit retraction path)
- `apps/backend/src/app/services/vault/bitemporal/fact_store.py:454` — `VaultFactStore.current` (R1: confirm `valid_to IS NULL` filter semantics)
- `apps/backend/src/app/services/vault/bitemporal/synthesis/` — NEW directory for R2 supersession_detector + tier classifier wrappers

---

## 11. ARIS reviewer assignment guidance

For the agent running ARIS rounds:

- **R1 Correctness** — focus: does `supersedes` correctly produce companion retracts? does `/vault/recall` filter correctly? does `/vault/why` walk terminate?
- **R2 Rigor** — focus: false-positive/negative rates on supersession detector; property tests for synthesis projection equivalence; provenance-cycle Hypothesis test
- **R3 Composability** — focus: `causal_history` extension to `DB.history` doesn't break existing 65 fact tests; SynthesisPlan composes with existing handler stack; classifier-swap doesn't violate `as_of` invariants
- **R4 Research** — focus: Prop 6 statement and proof; benchmark methodology; comparison to LongMemEval / AgentBench / Voyager

Min gate: 8.5 for ARIS Round 1 on R1+R2+R3; **9.0 for R4** (paper-claim bar).

---

## 12. Memory & Serena handoff

- This file: `/Users/nawfalsaadi/Projects/persistence-os/docs/plans/2026-04-25-write-time-synthesis-design.md`
- Serena memory: `persistence-os/write-time-synthesis-design-handoff` (matches `bitemporal-retrofit-design` precedent)
- ai-box Serena memory: `write-time-synthesis-handoff` (for the ai-box agent picking up R1)
- Auto-memory pointer: project_write_time_synthesis_design.md → MEMORY.md

---

## End

Pick up at §9 — the persistence-os agent's first action is to answer the 5 open questions, then R1 implementation begins.
