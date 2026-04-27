# persistence-os v1.0 — Ferrari-first Roadmap

**Date:** 2026-04-27
**Status:** Locked. Strategic pivot from "paper-first" to "substrate-first." Two prior defer-decisions reversed. 8-stream roadmap. Hard deadline 2026-06-16.
**Author:** Nawfal Saadi (with Claude Opus 4.7)
**Audience:** persistence-os engineering agent (next session and downstream)
**Predecessors:**
- [`2026-04-25-v0.4-substrate-primitives.md`](2026-04-25-v0.4-substrate-primitives.md) — supersedes Decision 3 (`:branch MCTS → Phase 3 NeSy 2027`). See ADR-2.
- [`2026-04-23-persistence-plan-v0.1-design.md`](2026-04-23-persistence-plan-v0.1-design.md) — context for Plan execution scope.
- Paper `paper/v0.9-aris-refresh` merged at `0343321` (L2R3 8.3/10 READY) — stays on `main` as fallback.

**Target tag:** `v1.0.0` (Stream G, 2026-05-28)
**Target paper:** v1.0 rewrite for NeSy 2026 abstract submission 2026-06-09 / late paper 2026-06-16
**NeSy 2026 relevance:** load-bearing — substrate-complete is the entire paper claim.

---

## 1. One-paragraph summary

Persistence OS v1.0 ships all 7 modules + MCTS + 3 numeric evaluations into a tagged substrate before the NeSy 2026 paper deadline. Module 5 Txn ships first via merge train (already built, tagged `v0.5.0a1`+`v0.5.1` on `feat/v0.5-txn`+`feat/v0.5.1-rev-o-narrowings`). Module 3 Plan execution ships next as `v0.6.0a1` with skill-library + 4-gate promotion + MIPROv2 wrapper, then Module 3.X MCTS ships as `v0.6.5` extension. Module 7 REPL ships as `v0.7.0a1` with capability-gated WebSocket + browser console UI + 4 ops. Three numeric evals (LongMemEval, CAMO distributional, regulator-replay 50-trajectory) land in parallel streams. v1.0.0 tag closes cumulative ARIS R4 across all 7 modules. Paper v1.0 rewrites against the substrate-complete state. **38 days critical path / 12 days buffer.** Velocity baseline (vault emerge 2026-04-27): Claude estimates 3-5x too long; v0.3+v0.4+v0.5-A+v0.5-B all shipped April 25-27 (3 days). 5 weeks is comfortable.

---

## 2. Strategic frame

User direct override 2026-04-27: *"My goal is to build the ferrari so that I can sell it. Time is of the essence. Research paper is an addon."*

### What changed at the pivot

Prior plan: paper v0.9 ships first → camera-ready window 2026-07-08 → 2026-07-20 → substrate `:code` execution layer + MCTS in v1.0 post-camera-ready → Phase 3 NeSy 2027 features later.

New plan: substrate v1.0.0 ships first → paper v1.0 rewrites against substrate-complete state → camera-ready window becomes paper-only polish, not substrate completion.

### What does **not** change

- Bitemporal datom store, append-only log, content-addressed plan ASTs, replay byte-identity, effect handler stack, spec parse-don't-validate boundaries — all preserved bit-for-bit. v1.0.0 is additive over v0.5.1.
- `PLAN_CANONICAL_VERSION = 1` stays at 1 across all v1.0 work.
- Audit-chain hash continuity preserved across v0.5.1 → v0.6.0a1 → v0.6.5 → v0.7.0a1 → v1.0.0.
- TDD discipline, no live LLM in tests, canonical JSON for cache + audit keys, mask `:audit` inside policy body.
- Paper v0.9 (`0343321` L2R3 8.3/10 READY) stays merged on `main` as fallback. v1.0 paper is a separate Stream H artifact.

### Velocity baseline

Per `vault emerge` 2026-04-27:

- v0.3 ship: Claude estimated 3-5h, actual ~1h.
- April 25-27 window shipped v0.3, v0.4, v0.5-A, v0.5-B — four versions in three days.
- v0.5-txn Phase B is the execution proof: 12 commits / 8 tasks / 2-stage review per task / single sprint, all subagent-driven, caught two real production defects (TOCTOU race in `_run` commit gate + `@db.dosync(max_retries=0)` decorator-factory broken). **This is the v1.0 execution model.**
- 5 weeks (2026-04-27 → 2026-06-16) is comfortable, not tight.

---

## 3. ADRs — two strategic decisions LOCKED today

### ADR-1: REPL scope = FULL (was Phase 2.E deferred)

**Status:** ACCEPTED 2026-04-27. Overrides prior "REPL is headless-only / UI deferred to Phase 2.E."

**Context.** Module 7 REPL was scoped headless-only because "live steering" was treated as a paper claim that did not require a UI to defend formally. Foundation primitives needed for live steering — `asOf`, `branch`, `fork`, handler-stack push/pop — all already shipped in v0.1.0a1 → v0.5.1.

**Decision.** Module 7 REPL ships as a capability-gated WebSocket + browser console UI + 4 operations `{inspect, edit, rewind, branch}` over the existing primitives. UI ships with the substrate, not in a separate phase.

**Why.** The "live steering" claim is load-bearing for the substrate's positioning against MemGPT/Mem0/Zep — those systems are read-only stores; persistence-os is the only one with a write-time + steering-time + replay-time triad. Without a UI the proposition reads aspirational. With a UI the demo is the proof.

**Consequences.**
- Stream D adds ~7 days to critical path (was 0 in old plan).
- Module 7 paper section in v1.0 rewrite has a runnable artifact, not a forward-looking promise.
- Capability gating is mandatory: WebSocket must require an operator-issued capability token, no anonymous access. (Threat model: no production-data leakage via REPL.)
- `inspect`, `edit`, `rewind`, `branch` map 1:1 to existing primitives (`asOf`, `db.transact`, `db.branch(t)`, `db.fork()`) — no new substrate API.

### ADR-2: MCTS scope = IN (was Phase 3 NeSy 2027)

**Status:** ACCEPTED 2026-04-27. **Overrides** [`2026-04-25-v0.4-substrate-primitives.md`](2026-04-25-v0.4-substrate-primitives.md) §3 Decision 3 (`:branch MCTS → Phase 3 NeSy 2027`).

**Context.** v0.4.0a1 design deferred MCTS to Phase 3 because the timeline assumed paper-first → substrate-completion-after-camera-ready. The deferral was timing-driven, not technical.

**Decision.** Module 3.X MCTS plan-search extension ships as `v0.6.5` on top of `v0.6.0a1` execution layer:
- PUCT-style tree search over the homoiconic Plan AST (already content-addressed via `_dispatch.py`).
- LLM evaluator pluggable via the same `:llm/handler-id` registry (no `:provider` field — model-agnostic).
- Skill-library 4-gate promotion gates which leaf actions enter the search prior.
- Plan AST nodes stay content-addressed; MCTS state is provenance-recorded (every rollout's `parent_provenance_hash` chain is reconstructible).

**Why.** Ferrari-first reframing makes the original Phase 3 timing irrelevant. Tree search over content-addressed plan ASTs is the substrate's strongest compositional-planning claim — without it, the paper is "we have a plan AST" without "we can search over it." Module 3 execution-without-MCTS leaves the paper one proposition short.

**Consequences.**
- Stream B adds 5 days to critical path.
- New paper Proposition 6 — "MCTS over the homoiconic Plan AST inherits content-addressed identity (every rollout's parent provenance hash is reconstructible from the audit log)." Defended in §5.X of v1.0 paper.
- Skill-library 4-gate is now a precondition, not a Phase 4 polish item.
- `:branch` semantics in Plan AST gain a third executor (the existing `:branch eval` for control flow + the new `:branch mcts` for plan-search). Dispatcher key is the only delta in `_dispatch.py`.

### What both ADRs preserve

- Zero canonical-form change to existing modules. `PLAN_CANONICAL_VERSION = 1`.
- Zero proposition-1-through-5 wording change. Props 1-5 stay byte-identical to v0.7 paper.
- Zero `_interpret.py` shim removal — backwards-compat shim from v0.4 stays through v1.0.0.
- Audit-chain hash continuity preserved.

---

## 4. Eight-stream roadmap

| Stream | Window | Days | Output | Tag |
|---|---|---|---|---|
| **0 — merge train** | 04-27 → 04-28 | 2 | merge `feat/v0.5-txn` (`9377b86`, tag `v0.5.0a1`) + `feat/v0.5.1-rev-o-narrowings` (`ffd51cf`, tag `v0.5.1`) into `main` | (existing tags) |
| **A — Module 3 Plan execution** | 04-28 → 05-04 | 7 | execution layer (`_optimize.py`, `_skill_library.py`, `_promotion.py`); MIPROv2 wrapper; 4-gate skill promotion; ARIS R1+R2+R3 | `v0.6.0a1` |
| **B — Module 3.X MCTS** | 05-04 → 05-09 | 5 | PUCT tree search over Plan AST; LLM evaluator over `:llm/handler-id` registry; provenance-recorded rollouts; ARIS R1+R2+R3 | `v0.6.5` |
| **C — LongMemEval (parallel)** | 05-04 → 05-09 | 5 | numeric table Fact vs Mem0 / Zep / Memento / MemGPT (recall@k, latency, audit-cost) | (no tag, lands as `bench/longmem_eval/`) |
| **D — Module 7 REPL** | 05-09 → 05-16 | 7 | capability-gated WebSocket + browser console UI + 4 ops {inspect, edit, rewind, branch}; ARIS R1+R2+R3 | `v0.7.0a1` |
| **E — CAMO distributional (parallel)** | 05-09 → 05-14 | 5 | paired-rollout fidelity numeric table (CAMO replays vs production trajectories) | (lands as `bench/camo/`) |
| **F — regulator-replay 50-trajectory (parallel)** | 05-14 → 05-21 | 7 | 50-trajectory CC-BY-4.0 corpus + byte-identity numeric table | (extends `bench/regulator_replay/`, releases dataset under CC-BY-4.0) |
| **G — substrate v1.0.0 tag** | 05-21 → 05-28 | 7 | cumulative ARIS R4 across all 7 modules + integration sweep + CHANGELOG aggregation | `v1.0.0` |
| **H — paper v1.0 rewrite + ARIS** | 05-28 → 06-09 | 12 | drop all `[TBD]`; numeric tables filled; 7-module shipped story; ~6-7 propositions; abstract submission 2026-06-09 | (paper artifact) |
| **Buffer + late paper** | 06-09 → 06-16 | 7 | final polish; late paper submission 2026-06-16 | — |

**Critical path: 38 days. Buffer: 12 days. Total window: 2026-04-27 → 2026-06-16 = 50 days.**

### Dependency graph

```
Stream 0 ─┬─→ Stream A ─→ Stream B ─┐
          │                          │
          │                          ├─→ Stream G (v1.0.0) ─→ Stream H (paper v1.0)
          │                          │
          ├─→ Stream C (parallel)   │
          │                          │
          └─→ Stream D ──────────────┤
                                     │
              Stream E (parallel) ───┤
                                     │
              Stream F (parallel) ───┘
```

Streams C, E, F run in parallel with their owning critical-path streams (C alongside A/B, E and F alongside D). Stream G is the only stream that requires all prior streams complete.

### Per-stream gate criteria

- **Stream 0:** suite green post-merge (912+7 tests minimum, target 931+7 if v0.5.1 lands together). No new code, just merge + verify.
- **Stream A:** `v0.6.0a1` tagged when ARIS R1+R2+R3 passes mean ≥ 8.5 / min ≥ 7.0; suite ≥ 1000 passed.
- **Stream B:** `v0.6.5` tagged when MCTS rollout produces audit-log-reconstructible plan from a held-out test prompt + ARIS pass.
- **Stream C:** numeric table populated for ≥ 4 baselines × ≥ 3 metrics; document in `paper/tex/persistence-nesy-2026.tex` Stream H slot.
- **Stream D:** `v0.7.0a1` tagged when 4-op browser smoke runs end-to-end against a live `feat/v0.4-substrate-primitives` substrate + capability-gating threat-model review pass.
- **Stream E:** CAMO paired-rollout fidelity ≥ 0.95 byte-identity over a fixed 100-trajectory baseline.
- **Stream F:** 50 trajectories generated + byte-identity verified + CC-BY-4.0 LICENSE + manifest hashes pinned in repo.
- **Stream G:** `v1.0.0` tagged when cumulative ARIS R4 across all 7 modules passes mean ≥ 9.0 / min ≥ 8.0; CHANGELOG-v1.0.0 aggregates per-module CHANGELOGs.
- **Stream H:** paper v1.0 ARIS round passes mean ≥ 8.0; abstract submitted 2026-06-09; full paper submitted 2026-06-16 (or earlier).

---

## 5. Per-stream proposition surface

### Module 3 Plan execution (Stream A) — proposition surface

The execution layer extends Module 3 from "parse, walk, dispatch, coerce" to "parse, walk, dispatch, coerce, **execute, optimize, promote**." Three new internal files:

- **`src/persistence/plan/_optimize.py`** — MIPROv2 wrapper that takes a Plan AST + a training set + a metric, returns an optimized Plan AST. Output AST carries provenance `{:plan/optimizer "miprov2-v1", :plan/optimizer-call <canonical-hash>, :plan/baseline <baseline-hash>}`. Pluggable evaluator key (`:llm/handler-id`) so MIPROv2 is not coupled to a specific model.
- **`src/persistence/plan/_skill_library.py`** — content-addressed skill registry: each skill is a tagged Plan AST with a 4-gate promotion record. Lookup by skill-id is `O(1)` via the existing fact store; provenance walks back through the gate audit.
- **`src/persistence/plan/_promotion.py`** — 4-gate promotion criterion. A skill enters the library when it passes all four gates: (G1) replay byte-identity over ≥ N held-out trajectories; (G2) effect-handler audit-chain unbroken; (G3) MIPROv2 score improvement vs baseline ≥ ε; (G4) operator approval via REPL `inspect` (Stream D dependency for soft-promote; hard-promote in Stream A uses a stub approval that Stream D replaces). Gate-record is a Plan AST node with provenance.

**Paper claim (lifts from Prop 4 wording in v0.7 paper):** "Skill promotion is a 4-gate audit-recorded transition; a promoted skill's provenance walks back to the originating trajectory, the optimizer call, and the gate decisions in deterministic order."

### Module 3.X MCTS (Stream B) — proposition surface

PUCT-style tree search over the homoiconic Plan AST. State is content-addressed (each node is a hash of the partial plan). Action space is "next leaf to expand" (which Plan AST `:branch` to instantiate). Evaluator is an LLM call routed through the `:llm/handler-id` registry — same handler used for production execution.

**New paper Proposition 6 (load-bearing for v1.0 paper §5.X):**

> *Proposition 6 (MCTS provenance reconstructibility).* For any MCTS rollout `R` produced by `Module 3.X` over a content-addressed Plan AST, every node visited carries a `parent_provenance_hash` such that the full search trajectory can be reconstructed from the audit log alone, without re-executing the LLM evaluator.

Defense outline:
- Plan AST nodes are content-addressed (Module 3 invariant).
- Each MCTS expansion is a Plan AST mutation (Module 3 dispatcher).
- Each expansion is recorded as a `:plan/mcts-expand` fact (Module 1 invariant).
- Each evaluator call is recorded as a `:llm/canonical-call` audit entry (Module 2 invariant).
- Therefore the rollout = audit log slice between two timestamps. Q.E.D. by composition of Props 1, 2, 4.

### Module 7 REPL (Stream D) — proposition surface

REPL = capability-gated WebSocket + browser console UI + 4 ops:

- **inspect** — `db.entity(eid).asOf(t)` snapshot view, plus walk `causal_history(eid, depth)`. Reads the audit log. No write surface.
- **edit** — `db.transact([...])` with operator capability token. Writes a `:operator/edit` fact, audit-chained.
- **rewind** — `db.branch(t)` opens a parallel timeline at `t`. Read-only by default; promote-to-main via `db.fork()` requires capability-elevated token.
- **branch** — `db.fork()` from current head with operator-tagged provenance. Used for "what-if" exploration.

UI ships at `src/persistence/repl/ui/` (browser console, no SPA framework — vanilla TS or htmx-style). WebSocket protocol at `src/persistence/repl/_ws.py`. Capability gating at `src/persistence/repl/_caps.py` (token issued by operator, expires by audit policy).

**Paper claim:** "Live steering is a capability-gated audit-chained operation; every operator action is a `:operator/<op>` fact in the audit log, indistinguishable in form from a programmatic write." (Proposition 4 already covers this; Module 7 makes it concrete.)

---

## 6. Numeric eval contracts

### Stream C — LongMemEval

**Goal.** Populate paper §6 Comparison table: persistence-os Fact module vs Mem0 / Zep / Memento / MemGPT.

**Contract.**
- Harness at `bench/longmem_eval/harness.py`.
- 4 baselines × ≥ 3 metrics × ≥ 100 query trajectories.
- Metrics: recall@k (k ∈ {1, 5, 10}), p50/p95 latency, audit-reconstructibility rate (per-query, fraction reconstructible from log alone — persistence-os should hit 1.0 by Prop 1; baselines vary).
- Output: `bench/longmem_eval/reports/2026-05-XX-summary.json` + Markdown table for paper §6.
- Reproducibility: harness deterministic given seed + corpus hash; paper cites `git rev-parse HEAD` of the bench commit.

**Acceptance:** persistence-os audit-reconstructibility = 1.000 (must hold by Prop 1); recall@5 within ±0.05 of best non-persistence baseline (parity, not dominance — the substrate's claim is reconstructibility, not retrieval quality).

### Stream E — CAMO distributional

**Goal.** Defend Module 4 Replay's "stronger than CAMO under NO-OP" claim with paired-rollout numbers (currently rescoped to NO-OP setting in paper abstract + §2.4).

**Contract.**
- Harness at `bench/camo/harness.py`.
- Paired-rollout: same prompt → CAMO trajectory + persistence-os trajectory under identical seed + handler stack.
- 100-trajectory baseline. NO-OP setting: handler outputs are byte-identical, so any divergence is a substrate bug.
- Metric: byte-identity rate (must be 1.000 by Prop 4); fall-through metric: per-step p50/p95 latency overhead vs CAMO.
- Output: `bench/camo/reports/2026-05-XX-paired.json`.

**Acceptance:** byte-identity = 1.000 over all 100 trajectories under NO-OP. Latency overhead within 2x of CAMO baseline (substrate carries audit-chain cost; CAMO does not).

### Stream F — regulator-replay 50-trajectory CC-BY-4.0 corpus

**Goal.** Ship the 50-trajectory corpus referenced in paper §6.5 as a public CC-BY-4.0 release. Currently scaffolded (`bench/regulator_replay/{generator.py, harness.py, README.md}` already on `main`) with zero trajectories and zero reports.

**Contract.**
- 50 trajectories generated by `bench/regulator_replay/generator.py` against synthetic regulator-policy fixtures.
- Each trajectory: `{trajectory_id, prompt, plan_ast_hash, audit_chain_root_hash, replay_byte_identity_proof}`.
- Manifest hashes pinned in `bench/regulator_replay/MANIFEST.json` (one SHA-256 per trajectory + corpus-root hash).
- LICENSE: CC-BY-4.0 in `bench/regulator_replay/LICENSE` (referenced in paper §6.3).
- Replay verifier: `bench/regulator_replay/harness.py` runs each trajectory, checks byte-identity, fails on any divergence. Fail = blocker for Stream G.

**Acceptance:** 50/50 trajectories byte-identity verified. CC-BY-4.0 LICENSE present. MANIFEST hashes match repo state.

---

## 7. Paper v1.0 anchor points (Stream H)

Stream H rewrites `paper/tex/persistence-nesy-2026.tex` against substrate-complete state. **Not** a delta from v0.9 — a fresh draft from the v1.0.0 substrate, then ARIS R3+R4.

**Anchor points:**

| Section | v0.9 state | v1.0 target |
|---|---|---|
| Abstract | 6/7 modules, `[TBD]` evals | 7/7 modules + 3 numeric tables |
| §1 Introduction | "we will ship" framing | "we shipped" framing |
| §2 Related Work | 19 citations | 19 citations + LongMemEval comparison sentence |
| §3 Thesis | 5 propositions named | 6 propositions named (adds Prop 6 MCTS) |
| §4 Formalization | Prop 1-5 unchanged | Prop 1-5 unchanged + Prop 6 added |
| §5 Implementation | 6 module subsections + REPL forward-look | 7 module subsections + MCTS subsection |
| §5.7 REPL | "will expose" forward-look | shipped, screenshots, capability-token threat model |
| §5.X MCTS | absent | NEW: PUCT over Plan AST, LLM evaluator pluggability, Prop 6 defense |
| §6 Comparison | `[TBD camera-ready]` | LongMemEval table + CAMO paired-rollout table + regulator-replay 50/50 |
| §6.5 case studies | 4 anonymized + `[TBD]` numbers | de-anonymize via `review-stage/anon_restore.txt` (12 RESTORE cookies) |
| §7 Discussion | privacy paragraph dropped | substrate-complete posture; future work = Phase 2.E privacy + multi-process Postgres |
| §8 Conclusion | compressed | substrate-complete framing; references `v1.0.0` git tag |

**Process.**
1. Branch from `main` post-Stream-G as `paper/v1.0-substrate-complete`.
2. Rewrite per anchor table (subagent-dispatched, 1 section per task, 2-stage review).
3. ARIS R3 (paper fitness) — codex `gpt-5.2` reviewer, target ≥ 8.0 / ready.
4. ARIS R4 (cumulative across substrate + paper) — only when v1.0.0 + paper land together.
5. Submit abstract 2026-06-09 / late paper 2026-06-16.

**Fallback.** If v1.0 paper rewrite slips past 2026-06-09, paper/v0.9-aris-refresh (`0343321` 8.3/10 READY) submits as-is. Substrate ship is independent of paper success.

---

## 8. Execution model

**Subagent dispatching** per task — pattern proven by v0.5-txn Phase B (12 commits / 8 tasks B1-B8 / fresh implementer + spec-reviewer + code-quality-reviewer per task). For each stream:

1. Task decomposition: each stream broken into ≤ 8 numbered tasks with clear interfaces.
2. Per-task subagent: fresh agent reads design doc + immediate prior-task commit, implements, runs tests.
3. Two-stage review per task: spec-reviewer (does this match the design?) + code-quality-reviewer (is this code reviewable for ARIS R2?).
4. ARIS R1+R2+R3 at stream end: parallel reviewers + W1 fix-pass + tag.
5. Per-stream serena memory + auto-memory MEMORY.md entry on ship.

**ARIS gates** between modules — R1 design fitness, R2 code quality, R3 paper fitness, R4 cumulative when shipping `v1.0.0`. R3 skip warrant matches v0.4.0a1 + v0.5.1 precedent (no proposition change → R3 skipped per-stream; R3 runs once at Stream H).

**Parallel streams** where dependencies allow (C alongside A/B; E alongside D; F alongside D).

**Discipline** (preserved from v0.5.1):
- TDD strict.
- No `time.time()` / `datetime.now()` / `random.random()` in handler code — route through `:clock/now` / `:random` effects.
- Canonical JSON for all cache + audit keys.
- Mask `:audit` inside policy body.
- No live LLM calls in tests except integration suites.
- Local-only tags per repo convention; only push tags when explicitly asked.

---

## 9. Risk + mitigation

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Stream A skill-library 4-gate harder than estimated | Medium | +3 days | Defer G4 operator-approval (REPL dependency) — use stub in Stream A, replace in Stream D. Already factored into ADR-1 dependency graph. |
| Stream B MCTS LLM evaluator hits rate-limit during ARIS | Medium | +2 days | Use mock evaluator in tests; only real LLM in Stream H paper-table generation. |
| Stream D capability-gating threat-model review surfaces hard issue | Low | +5 days | Ship behind `feature_repl=false` flag with default off in Stream D; remove flag at Stream G. |
| Stream F regulator-replay byte-identity divergence | Low | +7 days (substrate bug fix) | Run F generator against current `main` HEAD before Stream G; surface bugs early. |
| Buffer eaten by Stream H paper rewrite | Medium | abstract slip | Paper v0.9 fallback at `0343321` is unconditional submission. v1.0 paper is upgrade, not blocker. |
| Camera-ready window 2026-07-08 → 2026-07-20 not used | Low | n/a | Pure polish window. v1.0 substrate is shipped before camera-ready opens. |

---

## 10. Persistence + recovery

### State locations (canonical at pivot lock 2026-04-27)

- **Serena project memories:**
  - `persistence-os-v1.0-ferrari-first-pivot-locked` — recovery doc (read first on next session).
  - `module_status_v1.0_pivot_20260427` — technical companion (per-module shipped/unshipped state).
- **Conductor track:** `~/Projects/ai-box/conductor/tracks/persistence-os-foundation_20260420/STATUS.md` (pivot section appended).
- **Auto-memory:** `~/.claude-nawfal-2/projects/-Users-nawfalsaadi-Projects/memory/MEMORY.md` index entry + `project_persistence_v1.0_ferrari_first_pivot.md` topic file.
- **Vault:** tx=2107 strategic-pivot, tx=2105 MCTS-reversal ADR, tx=2106 REPL-full-scope ADR (`nawfal-dev/L1`).
- **This roadmap:** `docs/plans/2026-04-27-persistence-os-v1.0-roadmap.md`.

### Per-stream persistence on ship

Each stream's tag commit ships with:
1. CHANGELOG entry per module (`CHANGELOG-<module>.md` aggregated into `CHANGELOG.md` at v1.0.0).
2. Serena memory `<stream>-shipped` (e.g., `v0.6.0a1-shipped-aris-passed`).
3. Auto-memory MEMORY.md entry under "Persistence OS — Cognitive Runtime" topic.
4. Vault memory tx (`nawfal-dev/L1`).
5. Conductor STATUS append at `persistence-os-foundation_20260420`.

---

## 11. Hard cutoffs

- **2026-05-15:** Stream C kickoff cutoff — paper v1.0 rewrite (Stream H) cannot start before substrate is at least at `v0.7.0a1` + numeric tables filled. Slip past 2026-05-15 = pivot to paper v0.9 fallback path.
- **2026-05-28:** `v1.0.0` tag cutoff — Stream G must close.
- **2026-06-09:** NeSy 2026 abstract submission deadline (firm; paper site cutoff).
- **2026-06-16:** NeSy 2026 late full-paper submission deadline (firm; paper site cutoff).
- **2026-07-20:** Camera-ready deadline (only relevant if accepted; substrate-complete by then).

**Buffer math:** 38d critical path + 12d buffer = 50d. Window 04-27 → 06-16 = 50d. Zero slack — but Stream H has paper v0.9 fallback, so Stream H slip is non-fatal.

---

## 12. What ships next (immediately after this doc lands)

1. **Stream 0** — merge train: `feat/v0.5-txn` (`9377b86`, tag `v0.5.0a1`) → main, then `feat/v0.5.1-rev-o-narrowings` (`ffd51cf`, tag `v0.5.1`) → main. Verify suite ≥ 931 passed + 7 xfailed post-merge. ~2 hours.
2. **Stream A design doc** — `docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md` covering `_optimize.py` + `_skill_library.py` + `_promotion.py` + 4-gate criterion + MIPROv2 wrapper + ARIS gate criteria. ~1 day.
3. **Stream A implementation** — subagent-dispatched per the v0.5-txn Phase B model. 6-7 days.

This doc is the load-bearing artifact for the next 50 days. Update in place if streams reorder; otherwise hold the structure.
