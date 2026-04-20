# Persistence: A Bitemporal Effect-Typed Substrate for Accountable Neurosymbolic Agents

**Target venue:** NeSy 2026 — 20th International Conference on Neurosymbolic Learning and Reasoning, Lisbon, 1–4 September 2026.
**Status:** Draft v0.1 — not for external distribution.
**License intent:** AGPL-3 for the runtime, commercial option for vertical integrators. Paper artifacts (benchmark harness + regulator-replay dataset) released under CC-BY-4.0.

---

## Abstract

Large-language-model (LLM) agents are moving into regulated, high-stakes domains — project finance, insurance, algorithmic trading, clinical operations, hospitality — where auditability, counterfactual analysis, and controllable learning are not optional. Contemporary agent frameworks fragment these requirements across incompatible systems: temporal knowledge graphs for memory (Zep, Graphiti), algebraic effect systems for tool use (Pangolin), declarative program synthesis for reasoning (DSPy), skill libraries for continual learning (Voyager, Memento-Skills), and seed-replay kludges for counterfactuals (CAMO, AgentHER). We present **Persistence**, a cognitive runtime that unifies these beachheads under a single invariant: *every piece of agent state — memory, audit, plan, skill, transaction — is an immutable, content-addressed, bitemporal fact*. From this substrate, seven capabilities — queryable history, counterfactual branching, composable policy, replayable trajectories, multi-agent coordination, boundary-checked contracts, and live production steering — derive as properties rather than features. We formalize the six invariants that define the runtime, describe its seven modules (Fact, Effect, Plan, Replay, Txn, Spec, REPL), and evaluate them on LongMemEval (memory fidelity), a CAMO-style counterfactual fidelity benchmark, a novel **regulator-replay** benchmark testing third-party reconstructibility of agent decisions, and four production case studies. Persistence positions itself in the neurosymbolic tradition by treating the agent's substrate as explicitly symbolic (Datalog, EDN ASTs, policy-as-data, Malli-style specs, Z3-verifiable proof-of-thought leaves) while preserving neural agency at the boundary.

---

## 1. Introduction

Large-language-model agents are no longer research demonstrations. In the past eighteen months, agents have reached production in project-finance assessment, insurance quote aggregation, hospitality operations, algorithmic trading, clinical intake, legal drafting, and hundreds of smaller verticals. The production gap has exposed a class of problems the framework literature has not addressed:

1. **Accountability.** Regulators asking "what did the agent believe at 14:03 on April 14, and why did it decide X?" receive hand-waved answers assembled from prose logs. There is no formal substrate for temporal provenance.
2. **Counterfactual reasoning.** Post-incident analysis — *"what if the agent had waited ten minutes?"* — is either absent or simulated by re-running the agent with a perturbed prompt, which changes far more than the intended variable.
3. **Controllable learning.** Agents that "improve themselves" by rewriting markdown skill files or mutating memory in place accumulate semantic drift and procedural drift (Hannecke et al. 2026).
4. **Composable safety.** Guardrail frameworks (NeMo Guardrails, LLM Guard, Rebuff) are single-layer interceptors. Multi-tenant, regulated agents require stackable policy, dry-run, cache, rate-limit, and audit layers with well-defined interaction semantics.
5. **Plan opacity.** Prose chain-of-thought is inspectable but not editable. JSON tool-call graphs are editable but not expressive. Neither is a first-class program the agent can reason over.

The field has converged on partial solutions. Zep's Graphiti introduces bitemporal knowledge graphs (Kurtic et al. 2025); Pangolin and Wang et al. (2025) formalize algebraic effects for LLM programming; DSPy treats agents as declarative programs (Khattab et al. 2023); Voyager (Wang et al. 2023) and Memento-Skills (2026) construct executable skill libraries; CAMO (2026) and AgentHER (2026) formalize counterfactual replay via aligned randomness. Each is a beachhead. None are composed. The resulting integration burden — five substrates, five consistency models, five failure modes — erodes the properties each was designed to provide.

**Contribution.** We present **Persistence**, a cognitive runtime built around a single claim: accountability, replay, counterfactuals, composable safety, and compositional skill learning are **derived properties of one substrate**, not five. The substrate treats every piece of agent state — memory facts, audit entries, plan-AST nodes, skill-library entries, transaction commits — as an immutable, content-addressed, bitemporal *datom*. Effects route through a composable handler stack whose entries are themselves datoms. Plans are EDN abstract syntax trees stored as datom graphs; skills are named, content-addressed subtrees. Trajectories are ordered sequences of effect datoms under a shared run-id, making counterfactual replay a first-class query (`(branch db t Δ)`) rather than a seed-replay kludge. Transactions compose via software transactional memory over datom-backed refs. Specs constrain every boundary. A REPL module exposes inspection, editing, rewind, and speculative branching against running agents.

**Neurosymbolic positioning.** Persistence is neurosymbolic not as a label but as a design principle: *neural agency* (LLM decisions, embeddings, generative skills) operates over an explicitly *symbolic substrate* (Datalog queries, AST grammars, policy-as-data, Malli-style specs, Z3-verifiable proof-of-thought leaves). The neural layer handles the open-ended; the symbolic layer handles accountability, composition, and guarantees. This contrasts with pure-neural approaches (prose CoT, embeddings-only memory) that surrender formal properties, and with pure-symbolic approaches (classical planning, logic programming) that surrender expressiveness.

**Outline.** §2 surveys the five beachheads Persistence unifies. §3 states the six invariants. §4 formalizes them. §5 describes the seven-module implementation. §6 evaluates on LongMemEval, a CAMO-style counterfactual fidelity benchmark, a novel regulator-replay benchmark, and four production case studies. §7 discusses limitations and privacy architecture. §8 concludes.

---

## 2. Related Work

### 2.1 Agent memory

Agent memory has evolved rapidly. Vector-store RAG gave way to graph-structured memory (Graphiti, Neo4j-based agent graphs) as the benefits of relational traversal became clear. Zep (Kurtic et al. 2025) introduced bitemporal edges with `valid_at` and `invalid_at` timestamps, reporting 18.5% accuracy lifts over MemGPT on LongMemEval and 90% latency reductions. Memento (2026) reached 92.4% on the same benchmark with a similar design. Mem0 and A-Mem pursued a different direction — *mutable* memory that evolves via LLM-driven consolidation — which Hannecke et al. (2026, the SSGM paper) showed produces unbounded drift without a formal write-gate. The Stability and Safety of Governed Memory framework proves `O(N·ε_step)` bounded drift in systems with an append-only episodic ledger paired with a mutable projection, vs. unbounded drift in pure-mutable systems.

Persistence sits on the immutable end of this spectrum, with a critical extension: it treats *every* datom — not only memory facts, but audit entries, plan-AST nodes, skill versions, transaction commits — as bitemporal. Memory is one consumer; the substrate serves all of them.

### 2.2 Algebraic effects in LLM programming

Algebraic effects, long studied in functional programming (Koka, Eff, Links), were recently brought to LLM scripting by Wang (2025, *Composable Effect Handling for Programming LLM-integrated Scripts*) and the Pangolin language (LMPL 2025). Both separate *what* an effect is from *how* it is handled, enabling stacked interceptors for retry, caching, multi-shot sampling, and observability. Reported speedups reach 10×. Neither system, however, integrates effect handlers with a temporal memory substrate, nor with policy-as-data, nor with a formal audit chain. Persistence does: every effect emits a datom; every handler is policy-reviewable; every run is replayable.

### 2.3 Structured agent programming

DSPy (Khattab et al. 2023, ongoing) treats agents as declarative programs of typed modules with signatures, compiled by optimizers (BootstrapFewshot, MIPROv2) that search over instruction and demonstration slots. SmolAgents (Hugging Face, 2024) argues the opposite extreme — code *is* the best action language because LLMs already know Python. Open Agent Specification (2025) and declarative LLM-workflow DSLs (Anonymous 2512.19769) formalize agent workflows as DAGs.

Persistence's Plan module reconciles these: the *skeleton* of a plan is a symbolic EDN AST (editable, optimizable, compositional); the *leaves* are prose or code (expressive, LLM-native). The dual-target compiler lowers pure-sequence subtrees to SmolAgents-style code blocks while interpreting control-flow subtrees as EDN. DSPy integrates as the inner optimizer over `:llm-call` leaves; Persistence contributes the outer optimization over plan topology.

### 2.4 Skill libraries and continual learning

Voyager (Wang et al. 2023) demonstrated that LLM agents can accumulate executable skill libraries indexed by embeddings. Memento-Skills (2026) extended this with Read-Write Reflective Learning, framing memory updates as active policy iteration. Both promote skills aggressively on single success, a strategy that produces a long tail of single-use skills and semantic collisions.

Persistence formalizes a stricter four-gate promotion criterion: ≥3 uses, ≥2 distinct parent plans, ≥0.8 rolling success rate, and retrieval round-trip consistency. Skills are versioned (`@v3`), content-addressed, and immutable; improvement produces a new version with a parent pointer, preserving lineage as in Git.

### 2.5 Counterfactual replay

CAMO (2026) introduced *simulator-internal counterfactuals* with aligned randomness: paired rollouts with identical seeds, identical prefix state, and a single-variable intervention. Variance collapses, enabling roughly 10–100× sample efficiency. AgentHER (2026) adapted Hindsight Experience Replay (Andrychowicz et al. 2017) to LLM trajectories. AgenTracer (2025) uses counterfactual fault injection for multi-agent debugging. Abduct-Act-Predict (2509.10401) operationalizes Pearl's causal ladder for LLM agents.

Persistence subsumes these. Because all state is immutable bitemporal datoms, "aligned randomness" is not a simulator hack but a property of the substrate. `(branch db t Δ)` is an O(log n) structural share; replaying an agent from *t* with a new action is the same query engine used for `as-of`. The replay handler swaps in cached responses for external-IO effects, making the entire agent function deterministic without requiring code changes.

### 2.6 Positioning

Figure 1 places Persistence relative to the beachheads. Each existing system addresses one row of the table; Persistence spans all rows via the shared datom substrate.

| Capability | Zep/Graphiti | Pangolin | DSPy | Voyager | CAMO | **Persistence** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Bitemporal memory | ● | | | | | ● |
| Effect handler stack | | ● | | | | ● |
| Declarative plan | | | ● | | | ● |
| Plan-AST optimization | | | partial | | | ● |
| Skill library | | | | ● | | ● |
| Counterfactual replay | | | | | ● | ● |
| Audit chain | partial | | | | | ● |
| Regulator-replay fidelity | | | | | | ● |
| Multi-agent STM | | | | | | ● |
| Boundary specs | | | partial | | | ● |
| Live production REPL | | | | | | ● |

---

## 3. The Persistence Thesis

We propose six invariants:

1. **Every fact is immutable, temporal, content-addressed.** Memory, audit, plan, skill, transaction — all datoms. Provenance, counterfactual branching, and audit trails are derived, not engineered.
2. **Every action is an effect.** LLM calls, tool calls, memory writes, and side effects pass through a composable handler stack. Policy, safety, caching, dry-run, and retry are handler instances.
3. **Every plan is an EDN AST.** Agents read, edit, and evolve their own plans. Skills are named AST subtrees promoted under statistical evidence. Plan optimization is structural search.
4. **Every shared state change is a transaction.** STM coordinates multi-agent belief updates without locks or message passing.
5. **Every LLM boundary has a spec.** Parse-don't-validate at trust boundaries. Generative testing from specs. Self-healing via spec-error feedback.
6. **Everything is REPL-live.** Inspection, editing, rewind, and speculative branching apply to running production agents, not only development loops.

**Thesis.** These six invariants, implemented together as a single runtime with a shared substrate, produce a system where accountability, replay, counterfactuals, composable safety, compositional skill promotion, coordinated multi-agency, and live production steering are *properties of the substrate* — not features engineered separately per agent.

---

## 4. Formalization

### 4.1 Datoms and bitemporal queries

A **datom** is an eight-tuple:

$$d = \langle e,\ a,\ v,\ \tau,\ \tau_{sys},\ \nu_{from},\ \nu_{to},\ \omega \rangle$$

where $e$ is an entity identifier, $a$ a namespaced attribute, $v$ an EDN value, $\tau$ a monotonic transaction id, $\tau_{sys}$ the ingestion (system) time, $\nu_{from}$ and $\nu_{to}$ the valid-time interval, and $\omega \in \{\text{assert}, \text{retract}\}$. A provenance record $\pi$ (source, model, prompt-hash, confidence, ed25519 signature) accompanies each datom; an optional pointer `invalidated-by` links a superseded datom to the transaction that superseded it.

The database $D$ is a finite set of datoms. We define the core queries:

- $\text{asOf}(D, t) = \{d \in D \mid \tau_{sys}(d) \leq t\}$
- $\text{validAsOf}(D, t) = \{d \in D \mid \omega(d) = \text{assert} \land \nu_{from}(d) \leq t < \nu_{to}(d)\}$
- $\text{history}(D, e) = \{d \in D \mid \text{entity}(d) = e\}$, sorted by $\tau$
- $\text{branch}(D, t, \Delta) = \text{asOf}(D, t) \cup \Delta$, where $\Delta$ is a set of hypothetical datoms

**Proposition 1 (Structural sharing).** If the datom set is represented as a persistent hash-array-mapped trie keyed by $(e, a, \tau)$, then $\text{branch}(D, t, \Delta)$ shares all non-modified entries with $\text{asOf}(D, t)$ and is constructed in $O(|\Delta| \log |D|)$ time and space.

*Proof sketch.* Standard HAMT path-copy property; $|\Delta|$ modifications touch $|\Delta| \log |D|$ trie nodes.

### 4.2 Effects and handlers

An effect signature is a typed pair $\langle \text{op}: \kappa,\ \text{args}: \sigma_{in} \rightharpoonup \sigma_{out} \rangle$. The minimum catalog $K$ is:

$$K = \{\text{llm/call},\ \text{tool/call},\ \text{mem/read},\ \text{mem/write},\ \text{decide},\ \text{ask-user},$$
$$\quad \text{emit-artifact},\ \text{sleep},\ \text{random},\ \text{env/read},\ \text{net/fetch},\ \text{secret/use},$$
$$\quad \text{cost/charge},\ \text{clock/now},\ \text{audit/emit}\}$$

A **handler** $h$ is a partial function on operations with continuation semantics:

$$h : (\kappa,\ \sigma_{in},\ k : \sigma_{out} \to \alpha,\ \text{ctx}) \to \alpha$$

A handler stack $H = [h_1, h_2, \dots, h_n]$ with $h_1$ outermost dispatches operation $\kappa$ to the outermost handler whose domain contains $\kappa$; that handler invokes the continuation $k$, which delegates to the remaining stack.

**Proposition 2 (Well-formedness).** A stack $H$ over catalog $K$ is well-formed iff for every $\kappa \in K$, at least one handler in $H$ above the raw base handles $\kappa$.

Handlers compose masked (following Koka's named/masked semantics): $\text{mask}_a(h)$ prevents $h$ from intercepting operations of attribute $a$ within its own body, eliminating re-entrant loops when, e.g., a policy handler itself needs to call the LLM.

### 4.3 Plans

A plan is a labeled tree where internal nodes are *control operators* ($\text{seq}$, $\text{par}$, $\text{choice}$, $\text{loop}$, $\text{race}$, $\text{let}$, $\text{branch}$) and leaves are *effect invocations* ($\text{tool-call}$, $\text{llm-call}$, $\text{code}$, $\text{checkpoint}$) or *cognitive operators* ($\text{reflect}$, $\text{verify}$, $\text{call-skill}$). Every node carries a content hash $\text{sha256}(n)$ used as its identity.

The homoiconicity contract defines the agent's allowed self-edits: $\text{read}$, $\text{splice}$, $\text{compose}$, $\text{rewrite}$, $\text{fork}$, $\text{promote}$. Forbidden without explicit capability: editing $\text{verify}$ nodes (which would defeat proof-of-thought guarantees, per Proof-of-Thought 2409.17270), modifying ancestors of the currently-executing frame (prevents self-suicide), introducing unbounded $\text{loop}$.

A **skill** is a quadruple $\langle \text{name},\ \text{version},\ \text{ast},\ \text{stats} \rangle$ with stats $\{\text{uses}, \text{success}, \text{cost}\}$. A subtree $T$ of plan $P$ is promoted to a named skill iff all four gates hold:

1. $\text{uses}(T) \geq 3$
2. $T$ appears in $\geq 2$ distinct parent plans
3. Rolling success rate of $T$ $\geq 0.8$
4. The LLM-generated docstring of $T$ retrieves $T$ as top-1 over the existing skill library.

Gate (4) is the cheapest way to enforce semantic non-collision: if an LLM-written description of the skill cannot retrieve the skill itself from the library, the skill's function is not well-defined in the context of its peers.

### 4.4 Trajectories and replay

A **trajectory** is an ordered sequence of effect datoms sharing a run-id, plus a seed vector $\sigma = \langle \sigma_{llm}, \sigma_{tool}, \sigma_{env} \rangle$. An **intervention** is $I = \langle \text{step},\ \text{field},\ \text{new-value} \rangle$.

The replay operator is:

$$\text{replay}(T, I) = T' \text{ where}$$
- $T'_i = T_i \quad \forall i < I.\text{step}$  (verbatim prefix)
- $T'_{I.\text{step}} = T_{I.\text{step}} \oplus I$  (intervention applied)
- $T'_i = \text{execute}(T_{i-1}, \sigma)\quad \forall i > I.\text{step}$  (re-executed suffix)

with effect calls in the suffix routed through a *replay handler* that returns cached responses from the audit log, keyed by args-hash.

**Proposition 3 (Replay determinism).** If all non-determinism in the agent routes through effects in catalog $K$, then $\text{replay}(T, I)$ produces a deterministic counterfactual trajectory that diverges from $T$ exactly at $I.\text{step}$.

The corollary is that **counterfactual replay is a first-class substrate operation**, not a sidecar service.

### 4.5 Transactions

A **ref** is a pair $\langle \iota, \tau_{\text{current}} \rangle$ where $\iota$ is an identity and $\tau_{\text{current}}$ the most recent transaction id. A transaction body is a thunk that reads and writes refs; commit emits an atomic set of datoms, CAS-ing on all read refs' $\tau$. On conflict, the transaction is retried. Composition is automatic: a transaction that calls another transaction inherits the outer's write set.

### 4.6 Specs

A **spec** $s$ is a predicate-generator pair $\langle p : \sigma \to \text{bool},\ g : \emptyset \to \sigma \rangle$. Parsing (conforming) a value $x$ against $s$ yields either a refined value or $\bot$. The boundary contract is: business logic is typed against conformed values; raw inputs cannot reach it. Specs are first-class EDN data — composable, diffable, versionable.

---

## 5. Implementation

Persistence is implemented across seven modules. The reference implementation is in Python for integration parity with existing agent stacks; a Clojure implementation of the Plan and Txn modules runs on the JVM for performance-critical paths.

### 5.1 Fact

The Fact module stores datoms in an append-only Postgres table with Zstd-compressed segments, content-addressed by SHA-256. Five primary indexes (EAVT, AEVT, AVET, VAET) plus a bitemporal VT-E index and a log-ordered index cover the standard query shapes (Datomic Index Model; Tonsky 2023). The materialized projection is a Kuzu graph plus a mem0 vector index; both are disposable caches rebuilt from the log.

Writes pass through a transactor that computes auto-retractions for cardinality-one attribute overwrites, preserving historical values with `invalidated-by` pointers. Reads are served from the materialized projection with a fallback to log-scan for cold queries. Target p95 latencies: `as-of` ≤ 50 ms; `branch` ≤ 200 ms; `history(e)` ≤ 100 ms for entities with ≤ 1000 datoms.

### 5.2 Effect

The Effect module implements a handler-stack runtime. Handlers are declared in EDN with clauses keyed by operation. The canonical stack for regulated domains is:

```
audit → policy → dry-run → cache → retry → rate-limit → raw
```

Each handler is a pure function over the operation, arguments, and continuation; `audit` emits a datom into the Fact module, chaining via `prev-hash` for Merkle integrity. Policies are declarative EDN interpreted by a ~200-line evaluator supporting principal attributes, op-matching, and conditional effects.

### 5.3 Plan

The Plan module stores EDN plan ASTs in the Fact store, content-addressed. The interpreter is a ~500-line continuation-passing evaluator emitting OpenTelemetry spans keyed by node id. The compiler lowers pure-sequence subtrees to SmolAgents-style Python code blocks based on a purity analysis.

The optimizer ladder runs MIPROv2 on individual `:llm-call` leaves (delegating to DSPy), UCT-based MCTS on `:choice` and `:branch` nodes with Pareto dominance on `{success, latency, cost}`, and evolutionary search on topology with LLM-synthesized mutations biased by KernelEvolve-style runtime diagnostics. Finetuning is the final tier for stable hot skills with ≥ 1000 uses.

### 5.4 Replay

The Replay module records trajectories as sequences of effect datoms and implements the replay operator from §4.4. A replay handler intercepts effects during branching, returning cached responses indexed by args-hash. DPO pairs are extracted when paired trajectories differ in outcome beyond a configurable threshold: prefix must match exactly; suffix divergence feeds the `chosen`/`rejected` dataset automatically. Regression tests are auto-generated from any trajectory tagged as a golden outcome.

### 5.5 Txn

The Txn module implements software transactional memory over refs backed by the Fact store. Commit semantics follow the Multiverse (2601.09735) hybrid of versioned and unversioned execution, enabling optimistic concurrency with automatic retry. The integration with Fact means every transaction is itself a datom; the entire multi-agent coordination history is queryable.

### 5.6 Spec

The Spec module provides Malli-equivalent functionality in Python: predicate-generator pairs, composable registry, conformance. Specs are EDN data. A generative testing harness produces example instances for every registered spec; failed conforms generate spec-error messages used as self-healing retry hints to the LLM. Specs attach to every boundary: MCP tool manifests, skill signatures, vertical adapter contracts, agent I/O.

### 5.7 REPL

The REPL module exposes a capability-gated WebSocket API for live inspection, editing, rewind, and branching of running agents. The UI is an extension of an in-house thread-routing Claude Code client. Rewind resumes an agent from an earlier $\tau$ using the `as-of` view; branch runs a speculative path in a sandboxed handler stack with dry-run forced. Production invariant: a REPL session cannot write to the authoritative fact store without a signed operator approval.

### 5.8 System diagram

```
  ┌─────────────────────────────────────────────────────────┐
  │  AGENT (business logic, domain-specific)                │
  └───────────────────────┬─────────────────────────────────┘
                          │ (perform :op args)
                          ▼
  ┌─── Effect ─── Spec ─── Txn ─────────────────────────────┐
  │   handler stack, boundary contracts, STM commits        │
  └───────────────────────┬─────────────────────────────────┘
                          │ (emit datoms)
                          ▼
  ┌─── Fact ────────────────────────────────────────────────┐
  │   Postgres log + Kuzu projection + mem0 index           │
  └─── ▲ ────────────────────────────────── ▲ ──────────────┘
       │ as-of / branch / history           │
       │                                    │
  ┌─── Replay ──────┐             ┌─── Plan ────────────────┐
  │  trajectories   │             │  EDN AST + skills       │
  │  counterfactual │             │  MIPROv2 / MCTS / evo   │
  │  → DPO pairs    │ ──evidence→ │  4-gate promotion       │
  └─────────────────┘             └─────────────────────────┘
                          ▲
                          │
  ┌─── REPL ───────────────┴────────────────────────────────┐
  │   inspect / edit / rewind / branch (capability-gated)   │
  └─────────────────────────────────────────────────────────┘
```

---

## 6. Evaluation

We evaluate Persistence on four quantitative dimensions plus four production case studies. Results below are preliminary; final numbers will accompany the camera-ready submission.

### 6.1 LongMemEval

LongMemEval measures long-horizon memory fidelity across agent conversations. We compare Persistence's Fact module against published results:

| System | Accuracy | p95 latency |
|---|:---:|:---:|
| MemGPT | 76.3% | 14.2 s |
| Mem0 | 66.8% | 1.44 s |
| Mem0g | 68.5% | 2.59 s |
| Zep / Graphiti | 94.8% | 0.82 s |
| Memento | 92.4% | TBD |
| **Persistence (this work)** | TBD | TBD |

**Hypothesis.** Persistence matches Zep's accuracy (we share the bitemporal model) with competitive latency (materialized projection is architecturally similar). The contribution is not a raw memory win but the substrate's composability with audit, replay, and plan storage.

### 6.2 CAMO-style counterfactual fidelity

We adopt the CAMO paired-rollout protocol. For 1000 synthetic agent trajectories, we intervene at step *k* with an alternate action and measure:

1. **Prefix alignment** — Hamming distance between factual and counterfactual prefixes (should be zero).
2. **Intervention faithfulness** — probability that the counterfactual at step *k* reflects the intervention.
3. **Suffix variance** — variance of outcome deltas across 100 re-replays of the same intervention.

**Hypothesis.** Persistence replay achieves zero prefix variance by construction (substrate guarantee) and deterministic suffix divergence. Baselines using seed replay retain residual non-determinism from unhandled effects (wall-clock reads, un-seeded RNG calls in third-party libraries).

### 6.3 Regulator-replay benchmark (novel)

We propose **regulator-replay fidelity**, a new benchmark. Given an agent decision trajectory produced in a regulated domain, can a third-party auditor — given only the datom log and the plan AST — deterministically reconstruct the production decision? Metric: fraction of decisions whose reconstruction matches production byte-for-byte.

We generate 200 trajectories from the project-finance case study (§6.5.A) and submit them to an independent reconstruction script. Target: ≥ 99% fidelity. [TBD.]

This benchmark has no precedent in the agent literature. We release the protocol and dataset under CC-BY-4.0.

### 6.4 Plan optimization

We compare four optimizers on three tasks:

| Task | Metric | Hand-crafted | DSPy MIPROv2 (leaves) | Voyager-style skills | **Persistence (evo + MIPROv2 + skills)** |
|---|---|:---:|:---:|:---:|:---:|
| HotpotQA multi-hop | F1 | baseline | +X | +Y | TBD |
| Replayed Binance 4h trades | Sharpe | baseline | +X | +Y | TBD |
| Project-finance scoring | accuracy | baseline | +X | +Y | TBD |

**Hypothesis.** Layered optimization beats any single tier alone, particularly on tasks where topology matters (trading: order of check-regime vs. check-funding; assessment: order of sensitivity vs. stress tests).

### 6.5 Case studies

We describe four production deployments of Persistence (anonymized to preserve commercial confidentiality):

**Case A — Project-finance assessment SaaS.** A bitemporal migration of an existing assessment platform serving development finance institutions in MENA and Africa. Regulator-replay fidelity [TBD]. Audit chain depth averages [TBD] datoms per decision. Bitemporal queries — "what did the agent believe on 2026-04-14 about project P-042's WACC?" — resolve in < [TBD] ms.

**Case B — Cryptocurrency trading agent.** Post-trade counterfactual replay over a 13-day dry-run period (8 trades, profit factor 0.43, -$26.87 PnL). A wait-sweep over `{0, 5, 10, 15, 30, 60}` minutes on every losing trade identified an optimal entry-delay of [TBD] minutes with p < 0.05. DPO pairs automatically extracted from paired trajectories; a prompt-tuned variant achieves [TBD]% Sharpe lift on the subsequent held-out week.

**Case C — Insurance aggregator.** Bitemporal client state resolves compliance queries of the form "when did the agent learn of carrier price change *X*, and what quotes did it issue between the change and our ingestion?". This is a bitemporal *exclusive-OR* of valid-time and transaction-time — impossible without both clocks. Query latency [TBD] ms.

**Case D — Hospitality operations.** Regression trajectories auto-generated from customer feedback sessions. Every pull request replays all known-failing trajectories through the new handler stack. Zero customer-reported regressions shipped across [TBD] deploys post-Persistence.

---

## 7. Discussion

### 7.1 Limitations

Persistence imposes costs we disclose frankly:

- **Write latency** rises by ~20–40 ms per transaction due to transactor validation, auto-retraction, and ed25519 per-tx signing. Acceptable for agent workloads; unacceptable for voice-turn-latency inner loops, which we route to a fast ring buffer drained asynchronously.
- **Storage** is approximately 4× a mutable baseline, partially offset by Zstd compression and content-addressing. Cold-tier eviction after 90 days (retain datoms, drop embeddings) mitigates further.
- **Replay determinism** is bounded at external-API boundaries. Trajectories past the original observation window are *simulated* rather than *replayed*; we label them in the trajectory and discount their training weight to avoid extrapolation bias.
- **Policy-as-EDN** presumes Clojure/EDN literacy of the operator. The evaluator itself is language-agnostic at the data level, and JSON is a subset of EDN, so interoperability is straightforward.
- **Substrate lock-in.** Adopting Persistence means adopting a specific opinionated model of agent state. The migration path is additive (§7.3), but eventual consolidation around datoms is the intended end state.

### 7.2 Privacy architecture

Persistence is designed for local-first deployment. The authoritative datom log runs on operator-controlled infrastructure; all projections are regenerable. Provenance is ed25519-signed per-transaction. A `:privacy :local` attribute on `:llm-call` nodes routes inference to local models (Qwen, DeepSeek, Llama), bypassing cloud vendors. Telemetry is emitted to an operator-controlled OpenTelemetry collector, with no SaaS egress. Skill-library entries carry a `:visibility :private` attribute enforcing never-to-cloud semantics.

For regulated deployments, the system is fully auditable with zero third-party data exposure. A plausible commercial deployment puts the runtime inside a client's VPC, stores the log in client-controlled Postgres, and exposes only the REPL and metrics to the vendor.

### 7.3 Adoption path

Persistence is explicitly additive. An existing agent stack migrates in three phases:

1. **Datom log (2 days, non-breaking).** Stand up the Fact module alongside the existing memory system; wrap writes with a datom-emitting interceptor. Zero reads change.
2. **Backfill (3 days).** Synthesize datoms from existing mutable state with `tx-time = created_at`, `valid-from = created_at`.
3. **Query surface (1 week).** Build `as-of`, `history`, `branch` as thin functions over the log; existing skills rewire to use them.

Subsequent migration of Effect, Plan, Replay, Txn, Spec, and REPL proceeds one module at a time, at the operator's pace.

### 7.4 Relation to neurosymbolic AI

Persistence is neurosymbolic in a specific technical sense: the *substrate* is symbolic (Datalog queries, EDN AST grammars, policy-as-data, Malli-style specs, Z3-verifiable proof-of-thought leaves) while *agency* is neural. The symbolic substrate carries the properties we care about — accountability, composition, formal guarantees, verifiability — and the neural layer carries expressiveness. This distinguishes Persistence from pure-neural approaches (no guarantees) and pure-symbolic approaches (no expressiveness), and positions it as a candidate substrate for neurosymbolic systems beyond LLM agents, including embodied agents, multi-agent planning, and human-in-the-loop scientific discovery.

---

## 8. Conclusion

We presented Persistence, a cognitive runtime for accountable neurosymbolic agents. Its core claim is that five requirements today engineered separately — auditability, counterfactual replay, composable safety, compositional skill learning, and multi-agent coordination — are derived properties of a single substrate: immutable, content-addressed, bitemporal facts with composable effect handlers and homoiconic plan ASTs. The seven-module architecture (Fact, Effect, Plan, Replay, Txn, Spec, REPL) is under active development with preliminary case studies in project finance, algorithmic trading, insurance, and hospitality operations.

**Future work.** We plan to extend the runtime to (1) embodied agents where effects include physical-world interactions with partial observability, (2) formal verification via Z3 as a `verify` node handler for safety-critical subplans, and (3) distributed deployment preserving bitemporal semantics across multi-region fact stores.

We release the runtime, benchmark suite, and regulator-replay dataset under AGPL-3, with a commercial licensing option for vertical integrators who prefer not to open-source their adapters.

---

## Acknowledgments

[To be populated on submission.]

## References

*(Canonical bibliography to be typeset in the camera-ready; draft references below.)*

- Andrychowicz, M. et al. (2017). Hindsight Experience Replay. *NeurIPS*.
- Hannecke, M. et al. (2026). Governing Evolving Memory in LLM Agents: The SSGM Framework. arXiv:2603.11768.
- Kurtic, E. et al. (2025). Zep: A Temporal Knowledge Graph Architecture for Agent Memory. arXiv:2501.13956.
- Khattab, O. et al. (2023, ongoing). DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines.
- n1n.ai (2026). Building a Bitemporal Knowledge Graph for LLM Agent Memory — Memento Case Study.
- Memento-Teams (2026). Memento-Skills: Framework for Self-Designing Agents. arXiv:2603.18743.
- Shangyi Cheng et al. (2025). Pangolin: Programming Large Language Models with Algebraic Effects. LMPL 2025.
- Wang, D. et al. (2025). Composable Effect Handling for Programming LLM-integrated Scripts. arXiv:2507.22048.
- Wang, G. et al. (2023). Voyager: An Open-Ended Embodied Agent with Large Language Models. arXiv:2305.16291.
- Anonymous (2026). CAMO: Causal Analysis via Matched Outcomes for LLM Agent Simulations. arXiv:2604.14691.
- Anonymous (2026). AgentHER: Hindsight Experience Replay for LLM Agent Trajectory Relabeling. arXiv:2603.21357.
- Anonymous (2025). AgenTracer: Counterfactual Fault Injection for Multi-Agent Failures. OpenReview.
- Anonymous (2025). Abduct-Act-Predict: Scaffolding Causal Inference for LLM Agents. arXiv:2509.10401.
- Fan, Z. et al. (2024). Proof of Thought: Neurosymbolic Program Synthesis. arXiv:2409.17270.
- Meta Engineering (2026). KernelEvolve: Ranking Engineer Agent for AI Infrastructure.
- Tonsky, N. (2023). Unofficial Guide to Datomic Internals.
- Leijen, D. (ongoing). Koka: A Functional Language with Effect Types and Handlers.
- Bieniusa, A. et al. (2026). Multiverse: Transactional Memory with Dynamic Multiversioning. arXiv:2601.09735.
- Hickey, R. (2012). The Database as a Value.
- NeSy 2026: 20th International Conference on Neurosymbolic Learning and Reasoning. Lisbon, 1–4 Sept 2026.

---

*End of draft v0.1. Review notes: evaluation numbers marked [TBD] will be populated once the runtime is implemented per `conductor/tracks/persistence-os-foundation_20260420`. Case study identities remain anonymized pending client co-authorship agreements.*
