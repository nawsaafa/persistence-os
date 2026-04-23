# ARIS Round 1 — Reviewer R4 — Research Claims vs. Code Reality

*Repo `/Users/nawfalsaadi/Projects/persistence-os/` @ `2c96fb7`, Phase 1 shipped 2026-04-20. Paper draft `paper/persistence-nesy-2026-draft.md` v0.1 (396 lines). 356 pytest tests green.*

## Summary grade: 6.5 / 10

The paper is a strong vision document anchored by an unusually deep Phase 1 — 4 modules, 356 passing tests, a real handler-stack runtime, a working spec registry, a deterministic replay engine. The *formal skeleton* (§4) is largely honest when read generously, and two of its three propositions are well-supported by code. But the paper systematically conflates **three different artifacts** as if they were one: (a) the Phase 1 code that exists today, (b) the agent specs in `docs/` that describe intended shape, and (c) the Phase 2+ deliverables (Plan, Txn, REPL, Kuzu/mem0 projection, ed25519, Postgres, benchmarks, production case studies). In its current form the paper would fail rigorous NeSy peer review on three grounds: (1) **Proposition 1's O(log n) structural-sharing claim is false for the shipped implementation** — `branch()` does a full deep copy and every query linearly scans the log; (2) **five of the seven "unified capabilities" are not functionally demonstrable in Phase 1** (plan-AST optimization, multi-agent STM, live REPL, live skill promotion, regulator-replay); (3) **all six evaluation tables contain TBD cells** and the benchmark harnesses (LongMemEval, CAMO, regulator-replay, plan-opt) **do not exist in the repo** — not even as skeletons, let alone populated. Sixty days to submission (2026-04-21 → 2026-06-16) is enough to fix this, but only if the paper either softens or ships. I recommend softening aggressively now and using the writing window to ship the three benchmarks that are actually within reach (counterfactual fidelity, Adaptive Trader v2 case study, and a reduced-scope regulator-replay). Underneath the overclaiming there is a real and shippable paper.

---

## Paper claim audit — per section

| § | Claim (quoted short) | Code supports? | Evidence / gap |
|---|---|:---:|---|
| Abstract L12 | "unified substrate … derived properties of one substrate, not five" | PARTIAL | 4 of 7 capabilities demonstrable in Phase 1 code; 3 deferred (see F1). |
| Abstract L12 | "evaluate them on LongMemEval, CAMO-style…, regulator-replay, and four production case studies" | NO | Zero benchmark harnesses exist; `bench/` and `Makefile` referenced in README do not exist. Verticals/ and prototypes/ are empty. |
| Abstract L12 | "Datalog, EDN ASTs, policy-as-data, Malli-style specs, Z3-verifiable proof-of-thought leaves" | PARTIAL | EDN ASTs (as specs) ✓, policy-as-data ✓, Malli-style specs ✓. Datalog: claimed but not present (queries are Python list comprehensions, not Datalog). Z3: zero Z3 code in repo. |
| §3 thesis L90-97 | "accountability, replay, counterfactuals, composable safety, compositional skill learning, coordinated multi-agency, live steering — properties of substrate" | PARTIAL | First four: yes. Skill learning: spec'd but not implemented (no Plan module). Multi-agency: Txn deferred. Live steering: REPL deferred. |
| §4.1 Prop 1 L118 | "branch(D, t, Δ) … O(\|Δ\| log \|D\|) time and space, shares all non-modified entries" | **NO** | See F2. `db.branch()` (src/persistence/fact/db.py:184-223) does a full `list(as_of(t).datoms)` then deep-copies every datom into a fresh `InMemoryStore`. O(n), zero structural sharing. Python dicts/lists are NOT persistent data structures. |
| §4.2 Prop 2 L136 | "stack H … well-formed iff every κ ∈ K has at least one handler" | YES | `Runtime.is_well_formed(catalog)` + `uncovered_ops()` at `runtime.py:113-125`. Runtime `perform()` raises `Unhandled` if no handler covers an op (l.149). Both assertion and enforcement. |
| §4.3 homoiconicity contract | "read / splice / compose / rewrite / fork / promote …" | N/A | Explicitly Phase 2; paper reader will expect it, but contract locks in a plan-node spec (`:persistence.plan/node`) registered in Phase 1 (canonical.py:368) so shape is committed. OK for draft. |
| §4.4 Prop 3 L168 | "replay(T, I) produces a deterministic counterfactual trajectory diverging exactly at I.step" | YES (toy) | `tests/replay/test_determinism.py` enforces byte-identical equality per-field AND trajectory-hash equality for NO-OP intervention. But exercised only with `toy_agent_step` (`tests/replay/conftest.py`). Extension to real LLM/tool trajectories is unproven. See F5. |
| §4.6 L178 | "Parsing yields either a refined value or ⊥" | YES | Core `_conform` returns `Conformed` or `ConformError` — true discriminated union. Caveat: `_UuidSpec` and `_InstSpec` refine (str→UUID, str→datetime), which is semantic refinement per Clojure-spec, not silent coercion (documented in `_primitives.py:122-143`). int→float is strictly rejected. |
| §5.1 L190 | "p95: as-of ≤ 50 ms; branch ≤ 200 ms; history(e) ≤ 100 ms" | **NO** | Zero latency benchmarks in tests or src. See F6. `timeit`/`perf_counter` appear nowhere in the test suite. Numbers are aspirational. |
| §5.1 L188 | "Postgres … with Zstd-compressed segments, content-addressed by SHA-256" | **NO** | CHANGELOG explicitly defers Zstd + content-addressing to Phase 2. Postgres migration SQL exists but no Postgres CI or running adapter (also deferred). Current default is `InMemoryStore`; optional `SQLiteStore`. |
| §5.1 L188 | "Kuzu graph plus mem0 vector index" | **NO** | Only a `ProjectionAdapter` Protocol + `DictProjection` reference (projection.py). No Kuzu code, no mem0 projection (only a mem0 *interceptor* for legacy writes). See F7. |
| §5.2 L200 | "~200-line policy evaluator" | YES | `policy_eval.py` = 193 lines including docstring. Supports `:op=`, `:op-in`, `:mode=`, `:and`, `:or`, `:not`, `:contains?`, `:matches?`, `:non-empty?`, `:=`, principal/args/op path resolution. |
| §5.2 L200 | "every effect emits a datom; every handler is policy-reviewable" | PARTIAL | Audit handler wraps only what its `wraps=` arg declares; default is just `("llm/call",)`. Universal audit is *configurable*, not substrate-enforced. |
| §5.3-5.5 | "Plan … Txn … ~500-line continuation-passing evaluator, MIPROv2, MCTS, evo search, STM with Multiverse hybrid" | **NO** | Zero code for Plan or Txn modules. Not listed in `src/persistence/` at all. |
| §5.4 L210 | "Regression tests are auto-generated from any trajectory tagged as a golden outcome" | PARTIAL | `regression.py:gen_regression_test` emits a pytest source string BUT the generated test does not re-run the agent, only asserts a predicate against the loaded trajectory (see comment regression.py:31-35). Not a replay-regression; a snapshot test. |
| §5.6 L218 | "self-healing via spec-error feedback" | YES | `explain_for_llm` produces Fix-clause-annotated messages with field path, reason, and hint. `tests/spec/test_llm_errors.py` asserts the relevant tokens appear. Actual LLM self-heal loop (LLM reads hint, retries) is not implemented, but spec output is usable. |
| §5.7 L220 | "REPL … capability-gated WebSocket API … rewind / branch / signed operator approval" | **NO** | Not shipped in Phase 1. No `src/persistence/repl/` directory. Paper signals this less clearly than it should. |
| §6.1 L262 | LongMemEval comparison table | **NO** | Benchmark harness not in repo. Published baselines cited but no Fact-module integration. See F8. |
| §6.2 L276 | CAMO-style 1000 synthetic trajectories, 3 metrics | **NO** | No harness; replay engine works for per-trajectory calls but no trajectory generator, no metric computation scripts. |
| §6.3 L286 | Regulator-replay benchmark, 200 trajectories, ≥99% fidelity | **NO** | Dataset not built, reconstruction script not built, target metric ill-defined ("byte-for-byte" — on what serialization?). Biggest risk item for submission. See F9. |
| §6.4 L295 | Plan optimization table | **NO** | Plan module unshipped; tasks listed (HotpotQA, Binance 4h, PF scoring) have no glue. Every cell is TBD. |
| §6.5 L305-317 | Four production case studies | PARTIAL | Case B (Adaptive Trader v2) has pre-existing dry-run data (8 trades, PF 0.43, -$26.87) — cited numbers real. Migration to Persistence not done; DPO pipeline not wired to a live Trader. Cases A/C/D fully anonymized, unverifiable. |
| §7.1 L327 | "write latency rises ~20-40 ms … ed25519 per-tx signing" | **NO** | No ed25519 code in repo (`grep ed25519 src/` finds only comments). "20-40 ms" figure is fabricated until benchmarked. |
| §7.2 L335 | "authoritative datom log … Provenance is ed25519-signed per-transaction" | **NO** | Datom `provenance` dict has a `signature` slot; audit uses sha256 content-hash as a pseudo-signature. Not crypto-signing. CHANGELOG explicitly defers. |
| §7.4 L350 | "substrate is symbolic (Datalog, EDN AST grammars, policy-as-data, Malli-style specs, Z3-verifiable proof-of-thought leaves)" | PARTIAL | 3/5 present (EDN grammar via `:persistence.plan/node`, policy-as-data ✓, Malli-style specs ✓). **Datalog: not present** (queries are plain Python, no Datalog engine, no Datascript). **Z3: not present**. See F10. |

---

## Findings

### F1 — Five of the seven "unified capabilities" are not functionally demonstrable in Phase 1 [severity: CRITICAL] [Abstract, §3 thesis, §2.6 Fig.1]

**Paper claim:** Abstract L12 enumerates "seven capabilities — queryable history, counterfactual branching, composable policy, replayable trajectories, multi-agent coordination, boundary-checked contracts, and live production steering — derive as properties rather than features." §2.6 Fig.1 has Persistence as ● in every row. §3 closes with "These six invariants, implemented together as a single runtime … produce a system where [all seven] are *properties of the substrate*."

**Code reality:** Phase 1 ships fact, effect, spec, replay (CHANGELOG, `docs/phase-1-milestone-for-vault.md`). 
- ✓ Queryable history: `DB.history(e)`, `as_of`, `as_of_valid`
- ✓ Counterfactual branching: `DB.branch()` (but not O(log n) — see F2)
- ✓ Composable policy: `effect.policy_eval` + `policy` handler
- ✓ Replayable trajectories: `replay.engine.replay()` with NO-OP determinism test
- ✓ Boundary-checked contracts: `spec.conform`, `spec.parse`, `explain_for_llm`
- ✗ Multi-agent coordination (STM): no `txn/` module
- ✗ Live production steering (REPL): no `repl/` module
- ✗ Compositional skill learning (Plan/skills): no `plan/` module
- Partial: "audit chain" claimed as ● in §2.6 table — Merkle chain exists in audit handler but ed25519 signatures do not (see F4).

**Gap:** The Abstract presents all seven as Phase 1 deliverables; §2.6 Figure 1 is literally a feature-match table for a paper that will be read as describing a shipped system. A program committee will cross-check this against the artifact.

**Fix proposal (paper-side, feasible):** Rewrite the Abstract contribution sentence to: "We present a formal model of the substrate and a Phase 1 implementation of four modules (fact, effect, spec, replay) that instantiates four of the seven derived capabilities; the remaining three (plan, transactions, REPL) are specified and scheduled for Phase 2." In §2.6 Fig. 1, split the Persistence column into "Persistence (Phase 1, shipped)" with ● for the four, and "Persistence (Phase 2, designed)" with ○ for the three. This is honest and still impressive — four composable capabilities in one substrate is novel.

---

### F2 — Proposition 1 (O(log n) structural sharing) is false for the shipped implementation [severity: CRITICAL] [§4.1]

**Paper claim:** §4.1 L118 — "If the datom set is represented as a persistent hash-array-mapped trie keyed by (e, a, τ), then `branch(D, t, Δ)` shares all non-modified entries with `asOf(D, t)` and is constructed in O(|Δ| log |D|) time and space."

**Code reality:** `src/persistence/fact/db.py:184-223` — `branch()` does:
```
seed = list(self.as_of(t).datoms)         # O(n) scan of entire log
branched_store = InMemoryStore()          # new list-backed log
branched_store.append([Datom(... deepcopy(provenance) ...) for d in seed])  # O(n) copy
```
The `Store` is a plain Python `list` (InMemoryStore) or a SQLite `datom_log` table (SQLiteStore). Both are mutable sequences with no structural-sharing property. `as_of(t)` (db.py:153-156) is a linear scan. No HAMT, no persistent trie, no `pyrsistent` or `immutables` dependency (check `pyproject.toml`).

**Gap:** Proposition 1 describes a structure Phase 1 does not have. A reviewer quoting the paper's big-O and running `time python -m persistence.fact.demo` with a 100k-datom log will see linear-in-n scaling. This is not a rounding error; it is a false claim.

**Fix proposal:** Two options, both acceptable.

1. **Paper-side (recommended for June 16 deadline):** Rephrase Prop 1 as a **structure theorem for an idealized implementation**: "An implementation that represents the datom set as a persistent HAMT … achieves O(|Δ| log |D|). The Phase 1 reference implementation uses a mutable list-backed store and a snapshot-and-copy branching operator; it achieves correct semantics with O(|D|) branch cost. A persistent-trie backend (`pyrsistent.PMap` or an immutables.Map) is drop-in-replaceable at the `Store` Protocol boundary and is Phase 2 work." This preserves the formal contribution while accurately describing the artifact.

2. **Code-side (if time allows):** Swap `InMemoryStore`'s list for a `pyrsistent.pvector` or build a HAMT-indexed `(e, a, τ)` adapter, and rewrite `branch()` to reuse the parent's trie root. This would make Prop 1 true for the artifact, and is achievable in ~2 days of focused work by one engineer. Worth doing if it fits, because "actually O(log n)" is a more compelling contribution than "theoretically O(log n), we chose not to".

---

### F3 — "Every effect emits a datom" is a configuration, not an invariant [severity: MAJOR] [§3 invariant 2, §5.2]

**Paper claim:** §3 invariant 2 — "Every action is an effect. LLM calls, tool calls, memory writes, and side effects pass through a composable handler stack." §5.2 L200 — "Each handler is a pure function … `audit` emits a datom into the Fact module, chaining via `prev-hash` for Merkle integrity."

**Code reality:** `effect/handlers/audit.py:100-108` — `make_audit_handler(..., wraps=("llm/call",), ...)` wraps only the ops passed in `wraps`. Default is just `llm/call`. If a deployer omits `tool/call` or `mem/write` from `wraps`, those effects run without emitting audit entries. The substrate *permits* universal audit; it does not *enforce* it.

**Gap:** Regulator-replay and audit-chain claims depend on the universality of datom emission. A reviewer will ask: what prevents an operator-configured handler stack from silently skipping audit for `tool/call`? Current answer: nothing.

**Fix proposal:**

1. **Paper-side (minimum):** Reword §5.2: "The canonical stack for regulated domains wraps all 15 catalog operations in the audit handler; universality of audit is enforced by a well-formedness check over the (catalog, audit-wraps) pair."
2. **Code-side (recommended):** Add a `Runtime.assert_universal_audit(catalog)` check that fails if any catalog op is not in an audit handler's `wraps`. Runs in ~20 lines. Tie it to the well-formedness check already in place.

---

### F4 — ed25519 signing does not exist in the codebase [severity: MAJOR] [§4.1, §7.1, §7.2, Fig 1 "audit chain"]

**Paper claim:** §4.1 L109 — "A provenance record π (source, model, prompt-hash, confidence, ed25519 signature) accompanies each datom." §7.1 L327 — "write latency rises ~20-40 ms per transaction due to transactor validation, auto-retraction, and ed25519 per-tx signing." §7.2 L335 — "Provenance is ed25519-signed per-transaction."

**Code reality:** `grep ed25519 src/` returns zero hits (verified). The `provenance` dict has a `signature` slot; `audit_entry_to_datom()` (audit.py:236) fills it with `entry.id` — a SHA-256 content hash. Content hashing is integrity (detects tampering-given-the-chain) but NOT authenticity (does not prove *who* signed). The CHANGELOG explicitly defers ed25519: "ed25519 provenance signing — batched at the transaction level per §9."

**Gap:** Paper cites a ~20-40 ms latency overhead for an operation that is not implemented. A reviewer will ask for the measurement; there is no code to measure. Worse, Case A (project finance) and Case C (insurance) case studies lean on cryptographic authenticity for regulator appeal.

**Fix proposal:**

1. **Paper-side (now):** §4.1 — change "ed25519 signature" to "cryptographic signature (ed25519 in the planned deployment; sha-256 content-hash in the reference implementation)". §7.1 — remove the 20-40 ms figure; replace with "write latency rises by a constant overhead dominated by transactor validation and auto-retraction (measured at T ms on SQLite; signing is batched per-transaction at TX ms when enabled)". §7.2 — "Provenance is content-hashed per-datom and will be ed25519-signed per-transaction once key management ships (Phase 2)."
2. **Code-side (8-hour task):** Add `cryptography` as dep, implement `sign_transaction(tx_id, tx_hash, private_key) -> bytes`, call it in `DB.transact`, verify in `verify_chain()`. Now the claim is true.

---

### F5 — Replay determinism is proven only for a toy agent [severity: MAJOR] [§4.4 Prop 3, §6.2]

**Paper claim:** §4.4 L168 — "If all non-determinism in the agent routes through effects in catalog K, then replay(T, I) produces a deterministic counterfactual trajectory." §6.2 — CAMO-style evaluation at 1000 trajectories.

**Code reality:** `tests/replay/test_determinism.py` is the sole determinism evidence, and uses `toy_agent_step` from `conftest.py` — a 68-line deterministic-when-seeded fake that takes exactly one LLM draw and one env draw per step (conftest.py:21-68 and replay/engine.py:65 explicitly encode this assumption via `_advance_rngs_to_match`).

**Gap:** The proposition's antecedent ("all non-determinism routes through effects in catalog K") is not verified for a real LLM. The `_advance_rngs_to_match` helper *assumes* the agent_step_fn takes exactly one llm-rng draw and one env-rng draw per step; any real agent that branches on its own internal state will violate this and drift. This is probably repairable (per-step record the rng state vector instead of assuming a draw count) but the repair is not in the code.

**Fix proposal:**

1. **Paper-side:** §4.4 — add an antecedent: "and the agent step-function's per-step rng consumption is recorded deterministically (via a ProbeRandom wrapper that logs draws)". This makes the formal claim honest.
2. **Code-side (recommended for §6.2 data):** Replace `_advance_rngs_to_match` with a recorded `rng_state_vector` per Fact; in replay, restore the full vector. This is a ~30-line change and removes the toy-specific assumption. Adaptive Trader v2 can then serve as the first non-toy determinism proof.

---

### F6 — Latency targets are aspirational, not measured [severity: MAJOR] [§5.1]

**Paper claim:** §5.1 L190 — "Target p95 latencies: as-of ≤ 50 ms; branch ≤ 200 ms; history(e) ≤ 100 ms for entities with ≤ 1000 datoms."

**Code reality:** Zero latency benchmarks in tests. `grep -E 'p95|time.perf_counter|timeit' src/ tests/` returns no production measurement code. The word "target" is doing all the hedging work, but Phase 1 has neither a "here's where we are" number nor a "here's where we aim" experimental method.

**Gap:** A reviewer will ask — with `InMemoryStore` (a Python list) and `O(n)` `as_of` scans, does the implementation hit these targets at all? Unknown. A 1M-datom `history(e)` on the current `list + linear filter` implementation is almost certainly > 100 ms.

**Fix proposal:**

1. **Code-side (2-hour task, HIGH priority):** Add `tests/perf/test_fact_latency.py` that inserts 1k, 10k, 100k datoms and measures p95 for `as_of`, `history`, `branch`. Report numbers. Let the paper say what's real.
2. **Paper-side:** After benchmarking, rewrite §5.1 as measurements: "On the reference `InMemoryStore`, as-of p95 is T ms at 10k datoms; on a persistent-trie backend … ". Aspirational targets get moved to §9 Future Work.

---

### F7 — Kuzu + mem0 projection is asserted as implemented but only a `DictProjection` exists [severity: MAJOR] [§5.1, §5.8 system diagram]

**Paper claim:** §5.1 L188 — "The materialized projection is a Kuzu graph plus a mem0 vector index; both are disposable caches rebuilt from the log." §5.8 system diagram — "Fact … Postgres log + Kuzu projection + mem0 index".

**Code reality:** `src/persistence/fact/projection.py` ships `ProjectionAdapter` (Protocol) and `DictProjection` (reference in-process dict). CHANGELOG: "Kuzu + mem0 production projection adapters (Phase 2 — agent1-fact-spec §7)." `src/persistence/fact/interceptors/mem0_adapter.py` is a *legacy write interceptor*, not a projection.

**Gap:** The system diagram (§5.8) and the §5.1 narrative describe a stack that exists only as documented intent. A reviewer will expect a working path from "agent calls `mem/read`" through to "Kuzu query hits."

**Fix proposal (paper-side):** §5.1 — "The materialized projection is built against a ProjectionAdapter Protocol (reset, apply) with a reference in-process adapter and pluggable Kuzu/mem0 backends (Phase 2)." System diagram: label the Kuzu + mem0 boxes "(Phase 2)" with hatching or a dashed border.

---

### F8 — LongMemEval harness does not exist [severity: MAJOR] [§6.1]

**Paper claim:** §6.1 — comparison table with MemGPT/Mem0/Zep/Memento baselines, TBD for Persistence, and the hypothesis "Persistence matches Zep's accuracy … with competitive latency."

**Code reality:** No `bench/` directory. No LongMemEval loader. No Persistence-Fact glue that ingests LongMemEval QA pairs, routes them through `mem/write` / `mem/read`, and measures answer accuracy. The README has `make bench` — `Makefile` does not exist either.

**Gap:** Submission deadline is 2026-06-16 (8 weeks from 2026-04-21). Building a LongMemEval harness from scratch, wiring it to the Fact module, running against baselines, and getting a defensible number is ~2-3 weeks of focused engineering if Memento's published pipeline is the template. Tight but feasible.

**Fix proposal:**

1. **If you will build it:** Dedicate Weeks 1-3 of the writing window to (a) cloning LongMemEval v1, (b) wiring a `PersistenceMemoryBackend` class that materializes agent-turn memory via `fact.DB.transact`, (c) implementing read via `as_of_valid` + simple keyword/embedding retrieval (mem0 adapter), (d) running and reporting.
2. **If you will not build it:** Drop the §6.1 table entirely. Replace with a qualitative bullet: "LongMemEval integration is straightforward because the Fact module's `as_of_valid` query is the same temporal filter Zep's bitemporal edges implement; we expect to match Zep's accuracy and report in the camera-ready." This is honest and does not commit to numbers you cannot deliver.

---

### F9 — Regulator-replay benchmark (the novel one) is the biggest submission risk [severity: CRITICAL] [§6.3]

**Paper claim:** §6.3 — "200 trajectories from the project-finance case study (§6.5.A) … submit them to an independent reconstruction script. Target: ≥ 99% fidelity. [TBD.]" "This benchmark has no precedent in the agent literature."

**Code reality:** 
- No reconstruction script exists.
- The 200-trajectory corpus does not exist.
- The project-finance case (§6.5.A) is "anonymized … client identity withheld" — there is no runnable link between the paper's §6.5.A and production BankabilityAI data.
- "Byte-for-byte" fidelity is undefined: on which serialization? Canonical JSON of the Fact datoms? Of the trajectory? Of the final decision object? The paper does not specify.

**Gap:** This is the paper's novelty flagship. If it does not ship with numbers, the contribution collapses from "we introduce a new benchmark" to "we propose a new benchmark." NeSy reviewers will notice.

**Fix proposal:** Pick one of three tracks, commit early:

**Track 1 — Full ship (ambitious, 4-5 weeks):**
- Week 1: define fidelity formally (canonicalize via `effect.canonical.canonical_dumps`). Build a reference BankabilityAI-shaped *synthetic* trajectory corpus (200 trajectories, no client data, purely synthetic DFI-style scoring).
- Week 2-3: build `persistence.bench.regulator_replay` — a script that takes a datom log + a plan AST + a seed vector, re-executes, and compares byte-for-byte against the recorded outcome.
- Week 4: run, iterate, hit ≥99% or understand why not.
- Week 5: write up.

**Track 2 — Ship with explicit synthetic-only scope (safe, 2 weeks):**
- Rescope §6.3 to "Regulator-replay on a 50-trajectory synthetic corpus; full production-scale evaluation left for the camera-ready or a follow-up paper." Hit the smaller target. Still novel.

**Track 3 — Demote to protocol paper (lowest risk, 0-3 days):**
- Rewrite §6.3 as "We propose a regulator-replay benchmark protocol and release the specification; evaluation numbers are deferred to an extended-version companion paper." Risk: reviewers see it as a protocol paper and weight the contribution lower.

I recommend Track 2. The code in Phase 1 is solid enough that a 50-trajectory synthetic run is tractable, and "synthetic but novel" beats "promised but absent."

---

### F10 — Neurosymbolic framing overclaims Datalog + Z3 [severity: MAJOR] [§1, §7.4, Abstract]

**Paper claim:** Abstract and §7.4 list "Datalog queries, EDN AST grammars, policy-as-data, Malli-style specs, Z3-verifiable proof-of-thought leaves" as the symbolic components of the substrate.

**Code reality:**
- **Datalog: absent.** Queries are Python list comprehensions over datoms (`db.py`). No Datascript, no pyDatalog, no relational query engine.
- **EDN AST grammars: present as specs.** `:persistence.plan/node` is registered (canonical.py:368) with `PLAN_NODE_KINDS` enum — OK for structural commitment. Full EDN parser/printer: not needed yet and fine.
- **Policy-as-data: present.** `policy_eval.py`.
- **Malli-style specs: present.** Full module.
- **Z3-verifiable proof-of-thought leaves: absent.** No Z3 integration. No `verify` node handler. Cited (Proof of Thought, arXiv:2409.17270) but not wired.

**Gap:** NeSy reviewers will read "Datalog queries" and expect a Datalog interface; they'll read "Z3-verifiable" and expect at least a proof-of-concept. Three-of-five present is not bad, but two of the most quintessentially *symbolic* primitives are marketing.

**Fix proposal (paper-side):** Abstract and §7.4 — replace with: "the substrate is symbolic (bitemporal datom model with Datalog-shaped query surface, EDN-grammar plan-ASTs, policy-as-data, Malli-style specs) while agency is neural. A future `verify` node type will admit Z3-discharged proof obligations following Proof of Thought (Fan et al. 2024)." This honestly reports the shipped surface and signals Z3 as future work, not current capability.

**Alternative code-side (2-day task):** Ship a minimal Datalog query helper over the datom set (`datalog.query(db, rules)`) — even 100 lines of pyDatalog-style pattern matching on `(e, a, v)` triples would justify the claim. That would genuinely sharpen the neurosymbolic positioning.

---

### F11 — Related-work specific numbers need primary-source verification [severity: MINOR] [§2.1, §2.4, §2.5]

**Paper claims:**
- §2.1 L40 — "Zep … reporting 18.5% accuracy lifts over MemGPT on LongMemEval and 90% latency reductions."
- §2.4 L56 — "Voyager … Memento-Skills … Both promote skills aggressively on single success."
- §2.5 L62 — "CAMO … roughly 10–100× sample efficiency."

**Code reality:** N/A — these are literature claims, not code claims. But: the `agent1-fact-spec.md` explicitly flags "94.8% / 18.5% lift over MemGPT" as "claims SOTA" and notes "not yet benchmarked"; the 18.5% figure is circulated in the internal spec without a table-line citation. The CAMO "10-100×" figure likewise appears in agent4-replay-spec without a direct primary-source verification.

**Gap:** A program committee will check the Zep paper (arXiv:2501.13956) and the CAMO paper. If our numbers don't match verbatim, credibility suffers. For Voyager, the claim is about *aggressive promotion on single success* — Voyager does promote skills on first success in its iterative prompting loop, but phrasing it as "aggressively" is editorial; the paper (Wang et al. 2023 §3.3) frames it as "add to skill library upon successful execution" without a threshold comparison. Our §2.4 casts this as a limitation of Voyager, which is slightly unfair to the original.

**Fix proposal (paper-side):**
1. Before camera-ready, author-side-re-verify each specific percentage against the primary source. Cite the exact table / figure.
2. Soften §2.4 Voyager framing: "Voyager promotes skills on their first successful use, without a statistical threshold; this produces a long tail of single-use skills when tasks distribution is noisy." This is more accurate and avoids a polemical framing.
3. For CAMO, "roughly 10-100×" is a literature paraphrase; verify or use the primary paper's exact claim with a table-line citation.

---

## Paper claims to soften before submission

Replacements below. The paper at L<N> currently says (verbatim) → proposed replacement:

1. **L12 Abstract** — "From this substrate, seven capabilities — queryable history, counterfactual branching, composable policy, replayable trajectories, multi-agent coordination, boundary-checked contracts, and live production steering — derive as properties rather than features."
   → "From this substrate, four capabilities — queryable history, counterfactual branching, composable policy, and boundary-checked contracts — derive as properties in the shipped Phase 1 runtime; a further three (replayable trajectories extended to real LLMs, multi-agent coordination via STM, live production steering via a capability-gated REPL) are specified, with Phase 2 scheduled for 2026-Q3."

2. **L118 Prop 1** — "branch(D, t, Δ) shares all non-modified entries with asOf(D, t) and is constructed in O(|Δ| log |D|) time and space."
   → "Under a persistent HAMT representation, branch(D, t, Δ) shares all non-modified entries with asOf(D, t) and is constructed in O(|Δ| log |D|) time and space. The Phase 1 reference implementation uses a list-backed store; branch is O(|D|)-copy with correct semantics. Switching to a persistent-trie Store is a Protocol-boundary swap (§5.1)."

3. **L190 §5.1 latency targets** — "Target p95 latencies: as-of ≤ 50 ms; branch ≤ 200 ms; history(e) ≤ 100 ms for entities with ≤ 1000 datoms."
   → "Reference-implementation p95 latencies (measured on InMemoryStore over a 10k-datom corpus on an M3 MacBook): as-of T_1 ms, branch T_2 ms, history(e) T_3 ms. Persistent-trie backend is projected to hit {50, 200, 100} ms targets at 1M-datom scale; production Postgres path benchmarked in §6.X." [Fill T_i after F6 runs.]

4. **L188 §5.1 projection** — "The materialized projection is a Kuzu graph plus a mem0 vector index."
   → "The projection is accessed through a ProjectionAdapter Protocol; Phase 1 ships a reference DictProjection, with Kuzu and mem0 backends implemented as adapters in Phase 2."

5. **L200 §5.2 audit** — "every effect emits a datom; every handler is policy-reviewable"
   → "When the audit handler wraps the full 15-op catalog (the default for regulated deployments, enforced by the well-formedness check), every effect emits a datom; every handler is policy-reviewable."

6. **L109 §4.1 / L327 §7.1 / L335 §7.2 — ed25519 claims**
   → Replace all three mentions of "ed25519 signature" with "content-addressed integrity hash (sha-256); ed25519 per-transaction signing ships with Phase 2 key management." Remove the "20-40 ms" latency figure until measured.

7. **L168 §4.4 Prop 3**
   → "…if all non-determinism in the agent is mediated by catalog-K effects *and* the per-step rng-consumption vector is recorded, then replay(T, I) produces a deterministic counterfactual trajectory that diverges from T exactly at I.step."

8. **§6.1 LongMemEval table**
   → Pick: either (a) build the harness and fill the numbers (recommended), or (b) rewrite the section as "§6.1 Memory benchmark plan — we will integrate Persistence's Fact module with LongMemEval following Memento's harness; preliminary runs are scheduled for the camera-ready."

9. **§6.3 Regulator-replay**
   → Rescope to 50-trajectory synthetic corpus (F9 Track 2) and state this explicitly: "We evaluate on a 50-trajectory synthetic project-finance corpus generated via the Fact module's write API; production-scale evaluation is deferred."

10. **§6.5 Case A/C/D** — all describe production deployments with [TBD] numbers and withheld identities.
    → "§6.5 Deployment vignettes. We describe three anonymized deployments whose quantitative reporting is deferred to the camera-ready pending co-authorship agreement, plus one named reference deployment (Adaptive Trader v2) whose baseline numbers are reported below." This turns Cases A/C/D into motivating stories and keeps only Case B as a numeric case study, which is the only one the code supports.

11. **§7.4 Neurosymbolic framing**
    → Remove "Datalog queries" and "Z3-verifiable proof-of-thought leaves" from the symbolic-substrate list. Replace with "bitemporal datom queries (Datalog-shaped; a Datalog surface is Phase 2), EDN AST grammars, policy-as-data, Malli-style specs; Z3-discharged `verify` leaves are designed but unshipped."

---

## Paper claims currently under-sold

1. **The 15-op catalog + well-formedness check is a real, tested formal contribution.** The paper (§4.2, §5.2) treats these as plumbing. They are more than that: a *machine-checkable completeness property on the handler stack* (`Runtime.is_well_formed`) is exactly the kind of formal property NeSy values — discrete, decidable, implementable. Promote to a contribution bullet.

2. **The spec module's `explain_for_llm` + self-healing loop is a concrete contribution to LLM-agent tooling.** Paper §5.6 buries this. It deserves its own paragraph in §3/§4 — "we formalize a conform→explain→retry contract that turns spec violations into structured LLM prompts, with a generative testing harness for every registered spec." This is closer to "neurosymbolic" than anything §7.4 currently claims.

3. **Byte-identical trajectory determinism on a seeded toy agent is an unusually strong and honest determinism check.** The paper glosses over it. Highlight that `trajectory_hash(cf) == trajectory_hash(factual)` holds for NO-OP interventions — this is the *structural* guarantee CAMO aspires to.

4. **The merkle-hashed audit chain with `verify_chain(entries)` is shipped and tested.** Paper §5.2 mentions "Merkle integrity" in one phrase; code has a real `verify_chain` function. Promote to a methods detail with a verification snippet.

5. **The `:persistence.plan/node` spec is registered in Phase 1 *before* the Plan module ships.** This is precisely the right "parse-don't-validate" move — the data shape is locked before code depends on it. Paper should explicitly say "the plan/node spec is the commitment device that lets Phase 1 and Phase 2 agree on structure without blocking deployment." That's a methodology contribution.

6. **Branch isolation actually works.** `branch()` creates a new `InMemoryStore` — so hypothetical writes *cannot* leak into the parent store. That's a stronger isolation guarantee than many copy-on-write systems and is worth a sentence in §4.1 or §5.1.

---

## Benchmark readiness

Between 2026-04-21 and 2026-06-16 (8 weeks, 56 days). Rough estimate of what must be built for each §6 table to have real numbers:

### §6.1 LongMemEval — 2-3 weeks of focused engineering

Needed: (a) LongMemEval v1 clone + data download; (b) `PersistenceMemoryBackend` class that routes agent-turn memory through `fact.DB.transact` (write) and `as_of_valid` + embedding retrieval (read); (c) mem0 projection adapter (see F7); (d) run harness with GPT-4o or Claude as the question-answerer; (e) measure accuracy + latency against published baselines.
**Gating risk:** without the mem0 adapter, retrieval is pure linear scan over datoms; even on 10k-turn conversations this may be unusable.
**Fallback:** drop the benchmark, keep qualitative discussion. (F8 Track b.)

### §6.2 CAMO-style counterfactual fidelity — 1 week

Needed: (a) a synthetic-trajectory generator that produces 1000 paired rollouts (factual + intervention); (b) a harness measuring prefix-alignment, intervention-faithfulness, suffix-variance across 100 re-replays. The replay engine exists; the generator and the metric plumbing do not. Per-trajectory determinism is already tested, so prefix-alignment = 100% is free. Suffix variance needs real stochasticity to meaningfully vary — for a toy agent it will be trivially zero; for a real LLM it needs an ANTHROPIC_API_KEY budget.
**Gating risk:** getting real LLM trajectories in the loop (see F5 — `_advance_rngs_to_match` needs to be generalized first).
**Fallback:** ship numbers on the toy agent with full explicitness.

### §6.3 Regulator-replay — per F9, pick a track

Track 2 (50-trajectory synthetic corpus): 2 weeks. **Do this.** The novelty claim justifies the effort, and a synthetic corpus is defensible.

### §6.4 Plan optimization — NOT achievable in 8 weeks

Plan module does not exist. Even a stripped-down AST evaluator + MIPROv2 wrapper is 3-4 weeks of work, plus the three evaluation tasks (HotpotQA, Binance, PF scoring) are themselves multi-week harnesses. Recommend dropping §6.4 entirely for this paper; position it as future work, possibly the core contribution of a Phase-2 companion paper. Freeing those 3-4 weeks lets §6.3 actually ship.

### §6.5 case studies

- Case A (project finance) — can be grounded in a reference **synthetic** BankabilityAI-shape corpus (same one used for §6.3 regulator-replay). Numbers for "bitemporal query latency" become real measurements on synthetic data. Identity withholding is then not a constraint.
- Case B (Adaptive Trader v2) — baseline numbers (8 trades, PF 0.43, -$26.87) are real. The Persistence-migration numbers (entry-delay sweep, DPO lift) are entirely TBD and require a 1-2-week migration effort. If this is the named deployment, prioritize it.
- Case C (insurance) and Case D (hospitality) — flag as vignettes only; remove the [TBD] percentages.

**Realistic path to a defensible Table-1 complete §6:** ship §6.2 (toy), §6.3 (synthetic, 50 traj), §6.5-B (migrate Adaptive Trader, run 2-week DPO loop). Drop §6.1 to qualitative, drop §6.4 entirely, rescope §6.5-A/C/D to vignettes.

---

## Residual risks for NeSy submission

**R1 — The "derived properties of one substrate" thesis is aspirational for 3 of 7 capabilities (F1). If §6 tables remain half-empty and Plan/Txn/REPL are not shipped, a reviewer will read the paper as "four nice modules plus vapor." Mitigation: rewrite the contribution statement to scope to Phase 1 honestly; promote spec's `explain_for_llm` + `verify_chain` + well-formedness-checked handler stack as the core contribution; defer the unified-seven-capabilities claim to a future paper.**

**R2 — The novel benchmark (§6.3 regulator-replay) is the paper's headline novelty but has neither dataset nor harness today. If it doesn't ship with even 50 synthetic trajectories, the contribution reduces to a protocol proposal and the paper's novelty ceiling drops materially. Mitigation: commit to F9 Track 2 by 2026-04-28 and dedicate Weeks 1-2 of the writing window to it.**

**R3 — Proposition 1's big-O claim is false for the shipped code (F2). A rigorous reviewer will check; a sympathetic one will frown. Mitigation: rewrite Prop 1 as an idealized-implementation theorem (F2 Option 1) or ship a HAMT-backed Store (F2 Option 2). Either is feasible; doing neither is not.**

---

*— R4, 2026-04-21.*
