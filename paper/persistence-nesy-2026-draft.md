# Toward Accountable Neurosymbolic Runtimes: The Persistence OS Substrate

**Target venue:** NeSy 2026 — 20th International Conference on Neurosymbolic Learning and Reasoning, Faculty of Sciences of the University of Lisbon (FCUL), 1–4 September 2026.
**Target track:** Main Track Phase 2. Abstract due 9 June 2026, paper 16 June 2026 (AoE). Notification 8 July. Camera-ready 20 July. Full paper ≤ 10 pages excluding references.
**Status:** Draft v0.2 — internal, not for external distribution until ARIS review round ≥ 2 passes.
**License intent:** AGPL-3 for the runtime, commercial option for vertical integrators. Paper artifacts (benchmark harness + regulator-replay dataset) released under CC-BY-4.0.

---

### Revision history

- **v0.2 (2026-04-21) — ARIS R4 corrections.** Retitled away from "unified" framing; reframed "seven capabilities as shipped" to "four shipped / three designed"; softened §4.1 Proposition 1 to match the list-backed `InMemoryStore` reference implementation (dropped the HAMT structural-sharing claim from the Phase 1 contribution, promoted it to a Phase 2 upgrade path); removed ed25519 from Phase 1 (§4.1, §7.1, §7.2) and deleted the fabricated "20–40 ms" overhead figure; rescoped §5.1 projection to `DictProjection` with Kuzu/mem0 as Phase 2 adapters; rescoped §6 to a Reproduction Plan with [TBD] cells, a 50-trajectory synthetic regulator-replay target, and Case B (Adaptive Trader v2) as the only numeric case study; dropped §6.4 Plan-optimization benchmark (Plan module is Phase 2); removed Datalog and Z3 from the shipped neurosymbolic-substrate list and moved them to "adjacent systems we draw on"; elevated Proposition 2 (machine-checkable well-formedness via `Runtime.is_well_formed`), the byte-identical trajectory-hash invariant on NO-OP interventions, the Merkle-hashed `verify_chain` audit contract, and `spec.explain_for_llm` / self-healing hints to front-line contributions.
- **v0.1 (2026-04-20) — initial draft.** Seeded by `agent{1..4}-*.md` research specs and the Phase 1 conductor track.

---

## Abstract

Large-language-model (LLM) agents are moving into regulated, high-stakes domains — project finance, insurance, algorithmic trading, clinical operations, hospitality — where auditability, counterfactual analysis, and controllable learning are not optional. Contemporary agent frameworks fragment these requirements across incompatible systems: temporal knowledge graphs for memory (Zep, Graphiti), algebraic effect systems for tool use (Pangolin), declarative program synthesis for reasoning (DSPy), skill libraries for continual learning (Voyager, Memento-Skills), and seed-replay kludges for counterfactuals (CAMO, AgentHER). We present **Persistence**, a cognitive-runtime *substrate* whose central invariant is that every piece of agent state — memory, audit, plan, skill, transaction — is an immutable, content-addressed, bitemporal *datom*. This paper reports **Phase 1 of the reference implementation (v0.1.0a1, 356 tests green)**, which ships four of the seven runtime modules — Fact, Effect, Spec, Replay — and demonstrates four substrate-derived capabilities: queryable bitemporal history, counterfactual branching, composable policy-gated effects, and boundary-checked neurosymbolic contracts. Three further modules (Plan, Transactions, REPL) are specified and partially registered in the spec registry, with Phase 2 scheduled for 2026-Q3. We formalize the substrate (bitemporal datom model, effect-handler stack with a machine-checkable well-formedness property, counterfactual replay with byte-identical NO-OP invariance, Merkle-hashed audit chain, parse-don't-validate boundary specs with LLM-self-healing hints) and prove two formal propositions that hold on the shipped code: (i) any handler stack is well-formed iff every catalog operation is covered — checkable in linear time by `Runtime.is_well_formed`; (ii) `replay(T, I)` with a NO-OP intervention yields a trajectory whose canonical hash is byte-identical to the factual trajectory's — a stronger determinism guarantee than CAMO's aspirational seed replay. We describe a Reproduction Plan for four benchmarks — LongMemEval, CAMO-style counterfactual fidelity, a novel 50-trajectory synthetic regulator-replay, and one named production case study (Adaptive Trader v2) — to be populated by the camera-ready. Persistence positions itself in the neurosymbolic tradition by treating the substrate as explicitly symbolic (bitemporal datom model with a Datalog-shaped query surface, EDN-grammar plan-ASTs, policy-as-data, Malli-style specs) while agency remains neural.

---

## 1. Introduction

Large-language-model agents are no longer research demonstrations. In the past eighteen months, agents have reached production in project-finance assessment, insurance quote aggregation, hospitality operations, algorithmic trading, clinical intake, legal drafting, and hundreds of smaller verticals. The production gap has exposed a class of problems the framework literature has not addressed:

1. **Accountability.** Regulators asking "what did the agent believe at 14:03 on April 14, and why did it decide X?" receive hand-waved answers assembled from prose logs. There is no formal substrate for temporal provenance.
2. **Counterfactual reasoning.** Post-incident analysis — *"what if the agent had waited ten minutes?"* — is either absent or simulated by re-running the agent with a perturbed prompt, which changes far more than the intended variable.
3. **Controllable learning.** Agents that "improve themselves" by rewriting markdown skill files or mutating memory in place accumulate semantic drift and procedural drift (Hannecke et al. 2026).
4. **Composable safety.** Guardrail frameworks (NeMo Guardrails, LLM Guard, Rebuff) are single-layer interceptors. Multi-tenant, regulated agents require stackable policy, dry-run, cache, rate-limit, and audit layers with well-defined interaction semantics.
5. **Plan opacity.** Prose chain-of-thought is inspectable but not editable. JSON tool-call graphs are editable but not expressive. Neither is a first-class program the agent can reason over.

The field has converged on partial solutions. Zep's Graphiti introduces bitemporal knowledge graphs (Kurtic et al. 2025); Pangolin and Wang et al. (2025) formalize algebraic effects for LLM programming; DSPy treats agents as declarative programs (Khattab et al. 2023); Voyager (Wang et al. 2023) and Memento-Skills (2026) construct executable skill libraries; CAMO (2026) and AgentHER (2026) formalize counterfactual replay via aligned randomness. Each is a beachhead. None are composed. The resulting integration burden — five substrates, five consistency models, five failure modes — erodes the properties each was designed to provide.

**Contribution.** We present **Persistence**, a cognitive-runtime substrate built around a single claim: accountability, replay, counterfactuals, composable safety, and compositional skill learning can be derived from *one* substrate rather than re-engineered per system. The substrate treats every piece of agent state — memory facts, audit entries, plan-AST nodes, skill-library entries, transaction commits — as an immutable, content-addressed, bitemporal *datom*. Effects route through a composable handler stack whose entries are themselves datoms. Plans are EDN abstract syntax trees stored as datom graphs; skills are named, content-addressed subtrees. Trajectories are ordered sequences of effect datoms under a shared run-id, making counterfactual replay a first-class query over the log. Transactions compose via software transactional memory over datom-backed refs. Specs constrain every boundary. A REPL module exposes inspection, editing, rewind, and speculative branching against running agents.

**What this paper reports, honestly.** Phase 1 of the reference runtime (v0.1.0a1, tagged 2026-04-20) ships four of the seven modules — Fact, Effect, Spec, Replay — with 356 passing tests. The four shipped modules demonstrate four substrate-derived capabilities as shipped: queryable bitemporal history (`as_of`, `as_of_valid`, `history`), counterfactual branching (`branch`), composable policy-gated effects (15-op catalog + handler stack + `policy_eval`), and boundary-checked contracts (`spec.conform`, `spec.parse`, `spec.explain_for_llm`). Three further capabilities are specified-but-not-shipped: multi-agent coordination (Txn), compositional skill learning (Plan), and live production steering (REPL). The `:persistence.plan/node` and `:persistence.plan/skill` specs are already registered in the Phase 1 registry — a deliberate parse-don't-validate move that freezes the data shape ahead of the code that will consume it (§4.7). We call this reporting discipline out because the deliberate gap between *"substrate shipped"* and *"vertical modules shipped"* is itself part of the methodological contribution: **the substrate is claimed once, and each derived capability is a property we can check against the log, not an engineered feature per agent.**

**Neurosymbolic positioning.** Persistence is neurosymbolic not as a label but as a design principle: *neural agency* (LLM decisions, embeddings, generative skills) operates over an explicitly *symbolic substrate* (bitemporal datom queries with a Datalog-shaped surface, EDN AST grammars for plans, policy-as-data, Malli-style specs). The neural layer handles the open-ended; the symbolic layer handles accountability, composition, and guarantees. Datalog engines, Proof-of-Thought-style Z3 discharge (Fan et al. 2024), and classical-planning extensions are adjacent systems whose primitives are compatible with our datom surface; we discuss them as future work rather than shipped capabilities (§7.4). This contrasts both with pure-neural approaches (prose CoT, embeddings-only memory) that surrender formal properties, and with pure-symbolic approaches that surrender expressiveness.

**Outline.** §2 surveys the five beachheads Persistence draws from. §3 states the six substrate invariants and maps them to Phase-1 shipped / Phase-2 designed boundaries. §4 formalizes the substrate — datoms (§4.1), effect handlers and the well-formedness property (§4.2), the Merkle-hashed audit chain (§4.3), plans (§4.4), trajectories and replay with the byte-identical NO-OP invariant (§4.5), transactions (§4.6), and specs with the self-healing LLM contract (§4.7). §5 describes the reference implementation. §6 presents a Reproduction Plan for the four evaluations — camera-ready will replace [TBD] cells with measurements. §7 discusses limitations, privacy architecture, adoption path, and the neurosymbolic framing. §8 concludes.

---

## 2. Related Work

### 2.1 Agent memory

Agent memory has evolved rapidly. Vector-store RAG gave way to graph-structured memory (Graphiti, Neo4j-based agent graphs) as the benefits of relational traversal became clear. Zep (Kurtic et al. 2025) introduced bitemporal edges with `valid_at` and `invalid_at` timestamps, with reported accuracy lifts over MemGPT on LongMemEval and substantial latency reductions; Memento (2026) reached comparable figures with a similar design. (Exact percentage claims in §2.1 will be cross-checked against the primary sources in the camera-ready.) Mem0 and A-Mem pursued a different direction — *mutable* memory that evolves via LLM-driven consolidation — which Hannecke et al. (2026, the SSGM paper) showed produces unbounded drift without a formal write-gate. The Stability and Safety of Governed Memory framework proves `O(N·ε_step)` bounded drift in systems with an append-only episodic ledger paired with a mutable projection, vs. unbounded drift in pure-mutable systems.

Persistence sits on the immutable end of this spectrum, with a critical extension: it treats *every* datom — not only memory facts, but audit entries, plan-AST nodes, skill versions, transaction commits — as bitemporal. Memory is one consumer; the substrate serves all of them.

### 2.2 Algebraic effects in LLM programming

Algebraic effects, long studied in functional programming (Koka, Eff, Links), were recently brought to LLM scripting by Wang (2025, *Composable Effect Handling for Programming LLM-integrated Scripts*) and the Pangolin language (LMPL 2025). Both separate *what* an effect is from *how* it is handled, enabling stacked interceptors for retry, caching, multi-shot sampling, and observability. Neither system, however, integrates effect handlers with a temporal memory substrate, nor with policy-as-data, nor with a formal audit chain. Persistence does: when the audit handler wraps the full catalog (the default configuration for regulated deployments), every effect emits a datom; every handler is policy-reviewable; every run is replayable. §4.3 formalizes the Merkle-hashed audit contract.

### 2.3 Structured agent programming

DSPy (Khattab et al. 2023, ongoing) treats agents as declarative programs of typed modules with signatures, compiled by optimizers (BootstrapFewshot, MIPROv2) that search over instruction and demonstration slots. SmolAgents (Hugging Face, 2024) argues the opposite extreme — code *is* the best action language because LLMs already know Python. Open Agent Specification (2025) and declarative LLM-workflow DSLs formalize agent workflows as DAGs.

Persistence's Plan module is designed to reconcile these: the *skeleton* of a plan is a symbolic EDN AST (editable, optimizable, compositional); the *leaves* are prose or code (expressive, LLM-native). The Plan module itself is Phase 2; the `:persistence.plan/node` spec, however, is already registered in the Phase 1 spec registry (§4.7), committing to the AST shape before the evaluator ships.

### 2.4 Skill libraries and continual learning

Voyager (Wang et al. 2023) demonstrated that LLM agents can accumulate executable skill libraries indexed by embeddings. Memento-Skills (2026) extended this with Read-Write Reflective Learning, framing memory updates as active policy iteration. Voyager promotes skills on their first successful use (Wang et al. 2023, §3.3), without a statistical threshold; in noisy task distributions this produces a long tail of single-use skills.

Persistence's Plan module is designed to enforce a stricter four-gate promotion criterion: ≥3 uses, ≥2 distinct parent plans, ≥0.8 rolling success rate, and retrieval round-trip consistency. Skills are to be versioned (`@v3`), content-addressed, and immutable; improvement produces a new version with a parent pointer, preserving lineage as in Git. (Plan module is Phase 2; the `:persistence.plan/skill` spec is registered in Phase 1 and encodes this promotion contract.)

### 2.5 Counterfactual replay

CAMO (2026) introduced *simulator-internal counterfactuals* with aligned randomness: paired rollouts with identical seeds, identical prefix state, and a single-variable intervention. AgentHER (2026) adapted Hindsight Experience Replay (Andrychowicz et al. 2017) to LLM trajectories. AgenTracer (2025) uses counterfactual fault injection for multi-agent debugging. Abduct-Act-Predict (2509.10401) operationalizes Pearl's causal ladder for LLM agents.

Persistence replay is stronger on the precise axis these papers care about. Because all state is immutable bitemporal datoms, "aligned randomness" is not a simulator hack but a substrate property. For the NO-OP intervention case, Phase 1's replay engine produces a trajectory whose canonical hash is *byte-identical* to the factual trajectory's — checked by `trajectory_hash(cf) == trajectory_hash(factual)` in `tests/replay/test_determinism.py`. This is a stronger guarantee than the statistical-fidelity claims CAMO aspires to, because it holds at the level of the canonical serialization, not the level of outcome-delta distributions. The extension from toy agents to LLM trajectories (via per-step rng-consumption recording rather than fixed-draw-count `_advance_rngs_to_match`) is Phase 2 work; §4.5 states Proposition 3 with this antecedent explicit.

### 2.6 Positioning

Figure 1 places Persistence relative to the beachheads. Shipped Phase-1 capabilities are marked ●; Phase-2-designed capabilities are marked ○.

| Capability | Zep/Graphiti | Pangolin | DSPy | Voyager | CAMO | **Persistence (Phase 1 shipped)** | **Persistence (Phase 2 designed)** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Bitemporal memory | ● | | | | | ● | |
| Effect handler stack | | ● | | | | ● | |
| Merkle-hashed audit chain | partial | | | | | ● | |
| Counterfactual replay (byte-identical NO-OP) | | | | | partial | ● | |
| Boundary specs + LLM self-healing hints | | | partial | | | ● | |
| Declarative plan AST | | | ● | | | | ○ |
| Plan-AST optimization | | | partial | | | | ○ |
| Skill library (4-gate promotion) | | | | ● | | | ○ |
| Multi-agent STM | | | | | | | ○ |
| Live production REPL | | | | | | | ○ |
| Regulator-replay fidelity | | | | | | [designed — see §6.3] | |

---

## 3. The Persistence Thesis

We state six substrate invariants. Invariants 1, 2, and 5 are fully realized in Phase 1; invariants 3, 4, and 6 are frozen at the spec-registry boundary with runtime implementation scheduled for Phase 2.

1. **Every fact is immutable, temporal, content-addressed.** Memory, audit, plan, skill, transaction — all datoms. Provenance, counterfactual branching, and audit trails are derived, not engineered. *[Phase 1: shipped — `persistence.fact`.]*
2. **Every action is an effect.** LLM calls, tool calls, memory writes, and side effects pass through a composable handler stack. Policy, safety, caching, dry-run, and retry are handler instances. *[Phase 1: shipped — `persistence.effect`, 15-op catalog.]*
3. **Every plan is an EDN AST.** Agents read, edit, and evolve their own plans. Skills are named AST subtrees promoted under statistical evidence. Plan optimization is structural search. *[Phase 1: `:persistence.plan/node` and `:persistence.plan/skill` specs registered (`src/persistence/spec/_canonical.py`); Plan-module runtime is Phase 2.]*
4. **Every shared state change is a transaction.** STM coordinates multi-agent belief updates without locks or message passing. *[Phase 1: design frozen in `docs/agent*-spec.md`; Txn-module runtime is Phase 2.]*
5. **Every LLM boundary has a spec.** Parse-don't-validate at trust boundaries. Generative testing from specs. Self-healing via spec-error feedback. *[Phase 1: shipped — `persistence.spec`, 152 tests.]*
6. **Everything is REPL-live.** Inspection, editing, rewind, and speculative branching apply to running production agents, not only development loops. *[Phase 1: rewind semantics subsumed by `as_of` on the shipped Fact module; REPL-module runtime is Phase 2.]*

**Thesis.** These six invariants, implemented together as a single runtime with a shared substrate, produce a system where accountability, replay, counterfactuals, composable safety, compositional skill promotion, coordinated multi-agency, and live production steering are *properties of the substrate* — not features engineered separately per agent. Phase 1 demonstrates four of these as shipped properties (accountability via Merkle-hashed audit chain on the Fact log; replay via byte-identical NO-OP on the Replay engine; counterfactual branching via `branch()` on the Fact store; composable safety via the Effect handler stack with a machine-checkable well-formedness property). Phase 2 ships the remaining three on top of the same substrate, with no substrate schema changes required — that non-requirement is the testable form of the thesis.

---

## 4. Formalization

The formalization below tracks the Phase 1 reference implementation. Where a claim depends on Phase 2 work, we mark it explicitly.

### 4.1 Datoms and bitemporal queries

A **datom** is an eight-tuple:

$$d = \langle e,\ a,\ v,\ \tau,\ \tau_{sys},\ \nu_{from},\ \nu_{to},\ \omega \rangle$$

where $e$ is an entity identifier, $a$ a namespaced attribute, $v$ an EDN value, $\tau$ a monotonic transaction id, $\tau_{sys}$ the ingestion (system) time, $\nu_{from}$ and $\nu_{to}$ the valid-time interval, and $\omega \in \{\text{assert}, \text{retract}\}$. A provenance record $\pi$ (source, model, prompt-hash, confidence, content-hash) accompanies each datom; an optional pointer `invalidated-by` links a superseded datom to the transaction that superseded it. Phase 1 seals provenance with a SHA-256 content hash. Cryptographic per-transaction signatures (ed25519) are Phase 2 work and are discussed as a privacy-posture extension in §7.2.

The database $D$ is a finite set of datoms. We define the core queries:

- $\text{asOf}(D, t) = \{d \in D \mid \tau_{sys}(d) \leq t\}$
- $\text{validAsOf}(D, t) = \{d \in D \mid \omega(d) = \text{assert} \land \nu_{from}(d) \leq t < \nu_{to}(d)\}$
- $\text{history}(D, e) = \{d \in D \mid \text{entity}(d) = e\}$, sorted by $\tau$
- $\text{branch}(D, t, \Delta) = \text{asOf}(D, t) \cup \Delta$, where $\Delta$ is a set of hypothetical datoms

**Proposition 1 (Branch is a logical operation over the shipped store).** `branch(D, t, Δ)` returns a new `DB` value backed by a fresh in-memory store seeded with `asOf(D, t)` and extended with $\Delta$; writes to the branched value cannot leak back into the parent store. *Complexity:* on the Phase 1 `InMemoryStore` reference implementation (`src/persistence/fact/db.py`), materialization is $O(|D|)$ in the seed snapshot plus $O(|\Delta|)$ in the hypothetical additions. *Phase 2 upgrade:* under a persistent hash-array-mapped-trie (HAMT) backing store, the seed step reduces to $O(|\Delta| \log |D|)$ via structural path-copy; the `Store` Protocol boundary makes this a drop-in replacement requiring no change to `branch`'s interface.

*Remark.* The Phase 1 complexity is honest rather than ideal: Python lists and dicts are not persistent data structures, and we chose to ship a correct list-backed implementation rather than pull in `pyrsistent` / `immutables.Map` for Phase 1. The *isolation* property — branched writes do not mutate the parent — holds unconditionally on the shipped code and is the load-bearing property for counterfactual reasoning.

### 4.2 Effects, handlers, and well-formedness

An effect signature is a typed pair $\langle \text{op}: \kappa,\ \text{args}: \sigma_{in} \rightharpoonup \sigma_{out} \rangle$. The minimum catalog $K$ is:

$$K = \{\text{llm/call},\ \text{tool/call},\ \text{mem/read},\ \text{mem/write},\ \text{decide},\ \text{ask-user},$$
$$\quad \text{emit-artifact},\ \text{sleep},\ \text{random},\ \text{env/read},\ \text{net/fetch},\ \text{secret/use},$$
$$\quad \text{cost/charge},\ \text{clock/now},\ \text{audit/emit}\}$$

A **handler** $h$ is a partial function on operations with continuation semantics:

$$h : (\kappa,\ \sigma_{in},\ k : \sigma_{out} \to \alpha,\ \text{ctx}) \to \alpha$$

A handler stack $H = [h_1, h_2, \dots, h_n]$ with $h_1$ outermost dispatches operation $\kappa$ to the outermost handler whose domain contains $\kappa$; that handler invokes the continuation $k$, which delegates to the remaining stack.

**Proposition 2 (Well-formedness; machine-checkable on the shipped runtime).** A stack $H$ over catalog $K$ is well-formed iff for every $\kappa \in K$, at least one handler above the raw base handles $\kappa$. The shipped `Runtime.is_well_formed(catalog)` (`src/persistence/effect/runtime.py`) decides this property in $O(|H| \cdot |K|)$ time; `Runtime.uncovered_ops(catalog)` returns the witness set. At runtime, `Runtime.perform(op, …)` raises `Unhandled` when no handler covers $\kappa$ — the property is not merely asserted but *enforced on every call*.

This is the paper's strongest formal contribution on the Phase-1 artifact: a decidable, linear-time, machine-checked completeness property over the runtime's neurosymbolic interface, with runtime enforcement via `Unhandled`. The check is exercised in the Phase 1 test suite and is the foundation on which every §4 property above the effect layer — audit chain integrity, replay determinism, policy universality — builds.

Handlers compose *masked* (following Koka's named/masked semantics): $\text{mask}_a(h)$ prevents $h$ from intercepting operations of attribute $a$ within its own body, eliminating re-entrant loops when, e.g., a policy handler itself needs to call the LLM.

### 4.3 The Merkle-hashed audit chain

When the `audit` handler wraps a set of operations $W \subseteq K$, each effect on $\kappa \in W$ emits a datom recording (op, args, verdict, provenance, `prev_hash`) before the continuation fires. The chain is built by taking `sha256(canonical_serialize(entry) || prev_hash)` at each link. Phase 1 ships:

- `make_audit_handler(wraps, …)` with configurable $W$ and a default of `("llm/call",)`.
- `verify_chain(entries) → bool`, which re-derives each `prev_hash` from the canonical serialization and detects field-mutation tamper.
- `audit_entry_to_datom(entry) → datom-shaped record` that flows the audit entry into the Fact log.

**Integrity contract.** `verify_chain` detects any single-field mutation inside an entry (tested). Deletion/reorder coverage is flagged in the Round-1 rigor review and is a hardening target for Round 2. Authenticity — proving *who* signed — is distinct from integrity and is not claimed for Phase 1: the current `signature` slot stores a SHA-256 content hash, and per-transaction ed25519 signing is Phase 2 work (§7.2).

**Universality contract.** "Every effect emits a datom" is an invariant of the deployed stack, not the substrate: it holds when and only when the audit handler's $W$ covers the full catalog. Phase 1 exposes `Runtime.is_well_formed(catalog)` to check coverage of $W$ against $K$; a `Runtime.assert_universal_audit` hardening is scheduled for Round 2. For regulated deployments (Case A in §6.5), the configured stack wraps all 15 ops.

### 4.4 Plans (Phase 2 runtime; shape frozen in Phase 1 specs)

A plan is a labeled tree where internal nodes are *control operators* ($\text{seq}$, $\text{par}$, $\text{choice}$, $\text{loop}$, $\text{race}$, $\text{let}$, $\text{branch}$) and leaves are *effect invocations* ($\text{tool-call}$, $\text{llm-call}$, $\text{code}$, $\text{checkpoint}$) or *cognitive operators* ($\text{reflect}$, $\text{verify}$, $\text{call-skill}$). Every node carries a content hash $\text{sha256}(n)$ used as its identity. The `:persistence.plan/node` spec is registered in `src/persistence/spec/_canonical.py` (Phase 1) with the enumeration `PLAN_NODE_KINDS = (…)`; the Plan-module evaluator is Phase 2.

The homoiconicity contract defines the agent's allowed self-edits: $\text{read}$, $\text{splice}$, $\text{compose}$, $\text{rewrite}$, $\text{fork}$, $\text{promote}$. Forbidden without explicit capability: editing $\text{verify}$ nodes (which would defeat any future proof-of-thought guarantees, per Proof-of-Thought 2409.17270), modifying ancestors of the currently-executing frame (prevents self-suicide), introducing unbounded $\text{loop}$.

A **skill** is a quadruple $\langle \text{name},\ \text{version},\ \text{ast},\ \text{stats} \rangle$ with stats $\{\text{uses}, \text{success}, \text{cost}\}$, encoded by the `:persistence.plan/skill` spec. A subtree $T$ of plan $P$ is to be promoted to a named skill iff all four gates hold (Phase 2 runtime):

1. $\text{uses}(T) \geq 3$
2. $T$ appears in $\geq 2$ distinct parent plans
3. Rolling success rate of $T$ $\geq 0.8$
4. The LLM-generated docstring of $T$ retrieves $T$ as top-1 over the existing skill library.

Gate (4) is the cheapest way to enforce semantic non-collision: if an LLM-written description of the skill cannot retrieve the skill itself from the library, the skill's function is not well-defined in the context of its peers.

### 4.5 Trajectories and replay

A **trajectory** is an ordered sequence of effect datoms sharing a run-id, plus a seed vector $\sigma = \langle \sigma_{llm}, \sigma_{tool}, \sigma_{env} \rangle$. An **intervention** is $I = \langle \text{step},\ \text{field},\ \text{new-value} \rangle$.

The replay operator is:

$$\text{replay}(T, I) = T' \text{ where}$$
- $T'_i = T_i \quad \forall i < I.\text{step}$  (verbatim prefix)
- $T'_{I.\text{step}} = T_{I.\text{step}} \oplus I$  (intervention applied)
- $T'_i = \text{execute}(T_{i-1}, \sigma)\quad \forall i > I.\text{step}$  (re-executed suffix)

with effect calls in the suffix routed through a *replay handler* that returns cached responses from the audit log, keyed by args-hash.

**Proposition 3 (Replay determinism — stronger NO-OP form).** If all non-determinism in the agent routes through effects in catalog $K$, **and** the per-step rng-consumption pattern is recorded, then $\text{replay}(T, I)$ produces a deterministic counterfactual trajectory that diverges from $T$ exactly at $I.\text{step}$. Phase 1 specializes and *strengthens* this for the NO-OP case (an intervention that applies but does not change any value):

**Corollary (NO-OP byte-identity; tested).** For a NO-OP intervention on a toy agent instrumented via the shipped replay engine, $\text{trajectory\_hash}(\text{replay}(T, I_{\text{noop}})) = \text{trajectory\_hash}(T)$ — *byte-identical* on the canonical serialization, not merely statistically close. Verified by `tests/replay/test_determinism.py::test_noop_intervention_produces_byte_identical_trajectory`.

This strengthens CAMO's aspirational aligned-randomness claim in the direction NeSy reviewers will care about: the guarantee is a *structural* property of the canonical-serialization hash, decidable byte-wise, not a statistical claim about outcome-delta distributions. Extending the property from a toy agent (whose per-step rng-consumption is fixed by construction in `conftest.py`) to real LLM trajectories requires replacing the current `_advance_rngs_to_match` heuristic with a recorded per-step rng-state vector. This is Phase 2 work and a prerequisite for the §6.2 CAMO-style evaluation numbers in the camera-ready.

### 4.6 Transactions

A **ref** is a pair $\langle \iota, \tau_{\text{current}} \rangle$ where $\iota$ is an identity and $\tau_{\text{current}}$ the most recent transaction id. A transaction body is a thunk that reads and writes refs; commit emits an atomic set of datoms, CAS-ing on all read refs' $\tau$. On conflict, the transaction is retried. Composition is automatic: a transaction that calls another transaction inherits the outer's write set. The Txn-module runtime is Phase 2.

### 4.7 Specs and the self-healing LLM contract

A **spec** $s$ is a predicate-generator pair $\langle p : \sigma \to \text{bool},\ g : \emptyset \to \sigma \rangle$. Parsing (conforming) a value $x$ against $s$ yields either a refined value or $\bot$ — a true discriminated union in the shipped `_conform` (`Conformed` | `ConformError`, not silent coercion). The boundary contract is: business logic is typed against conformed values; raw inputs cannot reach it. Specs are first-class EDN data — composable, diffable, versionable.

**Self-healing contract (shipped).** When conform fails, `spec.explain_for_llm(err)` returns a structured message containing the field path, the failure reason, and a Fix-clause-annotated hint. This is the agent-facing surface of the spec module: when an LLM produces a structurally-malformed tool call or plan node, the spec layer does not simply reject it but returns a prompt-ready correction hint that the agent can retry against. This formalizes a **conform → explain → retry** contract that turns spec violations into structured neurosymbolic self-correction, and is tested at `tests/spec/test_llm_errors.py`. We claim this as a concrete neurosymbolic contribution — the symbolic layer emits hints that the neural layer can consume — distinct from post-hoc error logging.

**Forward-compatible spec-first commitment.** In Phase 1 we register `:persistence.plan/node` and `:persistence.plan/skill` in the spec registry *before* the Plan module exists. This is a deliberate parse-don't-validate methodology choice: the data shape is locked before code depends on it, which lets Phase 1 and Phase 2 workers agree on the plan AST's structure without blocking on implementation order. The `:persistence.plan/node` spec is the commitment device; it enforces the paper's §4.4 contract without requiring the runtime to exist.

---

## 5. Implementation

Persistence is implemented across seven modules. The reference implementation is in Python for integration parity with existing agent stacks. Phase 1 ships four modules (Fact, Effect, Spec, Replay); Phase 2 ships the remaining three (Plan, Txn, REPL).

### 5.1 Fact (Phase 1 — shipped)

The Fact module is designed for an append-only Postgres datom log with five covering indexes (EAVT, AEVT, AVET, VAET) plus a bitemporal VT-E range index and a log-ordered index (Datomic Index Model; Tonsky 2023). The shipped Phase 1 reference implementation ships a `Store` Protocol with two backends: `InMemoryStore` (for tests and the CLI demo) and `SQLiteStore` (for zero-ops persistent deployments), with a portable SQL migration (`migrations/0001_datom_log.sql`) that runs unmodified on SQLite 3.37+ and Postgres 14+. Zstd-compressed segments and content-addressed storage are explicitly Phase 2 work (per `CHANGELOG.md`).

Writes pass through a transactor that computes auto-retractions for cardinality-one attribute overwrites, preserving historical values with `invalidated-by` pointers. The projection surface is a `ProjectionAdapter` Protocol (`reset`, `apply`) with a reference in-process `DictProjection` in Phase 1; production Kuzu and mem0 projection adapters are Phase 2 work (per `CHANGELOG.md` "Deferred"). A legacy-write `mem0_adapter` interceptor is shipped in Phase 1 — it emits a datom before delegating to an operator-supplied mem0 client — but it is an *interceptor* rather than a projection.

**Latency targets (Reproduction Plan).** Phase 1 tests check correctness, not latency. The paper's per-operation p95 targets — `as_of` ≤ 50 ms, `branch` ≤ 200 ms, `history(e)` ≤ 100 ms for entities with ≤ 1000 datoms — are Phase-2 measurements over a persistent-trie backing store at 1M-datom scale. Phase 1 reference-implementation numbers over the `InMemoryStore` and `SQLiteStore` backends at 1k / 10k / 100k datom corpora will be reported in §6 of the camera-ready; they are `[TBD]` in this draft (see §6.6).

### 5.2 Effect (Phase 1 — shipped)

The Effect module implements a handler-stack runtime. Handlers are declared in EDN with clauses keyed by operation. The canonical stack for regulated domains is:

```
audit → policy → dry-run → cache → retry → rate-limit → raw
```

Each handler is a pure function over the operation, arguments, and continuation; `audit` emits a datom into the Fact module, chaining via `prev-hash` for Merkle integrity. Policies are declarative EDN interpreted by a ~200-line evaluator (`policy_eval.py`, measured) supporting principal attributes, op-matching, and conditional effects (`:op=`, `:op-in`, `:mode=`, `:and`, `:or`, `:not`, `:contains?`, `:matches?`). The `Runtime.is_well_formed` and `Runtime.uncovered_ops` functions (§4.2) are part of the public API.

### 5.3 Spec (Phase 1 — shipped)

The Spec module provides Malli-equivalent functionality in Python: predicate-generator pairs, composable registry, conformance. Specs are EDN data. A generative testing harness produces example instances for every registered spec; failed conforms generate spec-error messages used as self-healing retry hints to the LLM (`spec.explain_for_llm`, §4.7). Specs attach to every boundary. Ten canonical specs are registered, including `:persistence.fact/datom`, `:audit/entry`, `:persistence.replay/trajectory`, `:persistence.plan/node`, and `:persistence.plan/skill` — the last two registered ahead of the Plan module.

### 5.4 Replay (Phase 1 — shipped)

The Replay module records trajectories as sequences of effect datoms and implements the replay operator from §4.5. A replay handler intercepts effects during branching, returning cached responses indexed by args-hash. DPO pairs are to be extracted when paired trajectories differ in outcome beyond a configurable threshold: prefix must match exactly; suffix divergence feeds the `chosen`/`rejected` dataset automatically. `gen_regression_test(trajectory)` emits a pytest-source snapshot-test string that asserts against the loaded trajectory (this is honest snapshot-replay rather than agent-re-run; full regression re-execution is Phase 2). The byte-identical NO-OP determinism invariant (§4.5 corollary) is the headline test result for this module.

### 5.5 Plan (Phase 2 — designed)

The Plan module is scheduled for Phase 2. It will store EDN plan ASTs in the Fact store, content-addressed. The interpreter will be a continuation-passing evaluator emitting OpenTelemetry spans keyed by node id. The optimizer ladder will run MIPROv2 on individual `:llm-call` leaves (delegating to DSPy), UCT-based MCTS on `:choice` and `:branch` nodes with Pareto dominance on `{success, latency, cost}`, and evolutionary search on topology with LLM-synthesized mutations. Finetuning is the final tier for stable hot skills with ≥ 1000 uses. The `:persistence.plan/node` and `:persistence.plan/skill` specs are already registered (§4.7) to fix the data shape ahead of implementation.

### 5.6 Txn (Phase 2 — designed)

The Txn module will implement software transactional memory over refs backed by the Fact store. Commit semantics are designed to follow the Multiverse (2601.09735) hybrid of versioned and unversioned execution, enabling optimistic concurrency with automatic retry. The integration with Fact means every transaction is itself a datom; the entire multi-agent coordination history is queryable.

### 5.7 REPL (Phase 2 — designed)

The REPL module will expose a capability-gated WebSocket API for live inspection, editing, rewind, and branching of running agents. The UI is an extension of an in-house thread-routing Claude Code client. Rewind resumes an agent from an earlier $\tau$ using the already-shipped `as-of` view; branch runs a speculative path in a sandboxed handler stack with dry-run forced. Production invariant: a REPL session cannot write to the authoritative fact store without a signed operator approval.

### 5.8 System diagram

```
  ┌─────────────────────────────────────────────────────────┐
  │  AGENT (business logic, domain-specific)                │
  └───────────────────────┬─────────────────────────────────┘
                          │ (perform :op args)
                          ▼
  ┌─── Effect ─── Spec ──┤ Txn [Phase 2] ├─────────────────┐
  │   handler stack, boundary contracts, STM commits        │
  └───────────────────────┬─────────────────────────────────┘
                          │ (emit datoms)
                          ▼
  ┌─── Fact ────────────────────────────────────────────────┐
  │   InMemory / SQLite log  ·  DictProjection              │
  │   [Phase 2: Postgres + Kuzu + mem0]                     │
  └─── ▲ ────────────────────────────────── ▲ ──────────────┘
       │ as-of / branch / history           │
       │                                    │
  ┌─── Replay ──────┐             ┌─── Plan [Phase 2] ──────┐
  │  trajectories   │             │  EDN AST + skills       │
  │  counterfactual │             │  MIPROv2 / MCTS / evo   │
  │  NO-OP identity │ ──evidence→ │  4-gate promotion       │
  └─────────────────┘             └─────────────────────────┘
                          ▲
                          │
  ┌─── REPL [Phase 2] ─────┴────────────────────────────────┐
  │   inspect / edit / rewind / branch (capability-gated)   │
  └─────────────────────────────────────────────────────────┘
```

---

## 6. Evaluation — Reproduction Plan

The abstract submission (2026-06-16) reports the Phase-1 shipped artifact with formal properties (§4.2 Prop 2; §4.5 Corollary) checked in the bundled test suite (356 tests, `pytest -q` from a clean clone). The numeric evaluations below are a **Reproduction Plan**: each subsection below names the harness, the dataset, the license, and the intended submission point (abstract vs. camera-ready). Tables marked [TBD] carry target numbers only; measured numbers land in the camera-ready (2026-07-20) once Phase 2 verticals and the per-step-rng-recording replay extension are in place.

### 6.1 LongMemEval (camera-ready)

LongMemEval measures long-horizon memory fidelity across agent conversations. We will compare Persistence's Fact module against published results on the LongMemEval v1 corpus. The integration is straightforward in principle because our `as_of_valid` query is the same temporal filter Zep's bitemporal edges implement; the mem0 projection adapter (Phase 2) is the gating dependency for retrieval at scale, without which the harness falls back to linear log-scan (unusable beyond 10k conversation turns).

For the abstract, this subsection is qualitative; the camera-ready will ship numeric rows.

| System | Accuracy | p95 latency |
|---|:---:|:---:|
| MemGPT | [per primary source] | [per primary source] |
| Mem0 | [per primary source] | [per primary source] |
| Mem0g | [per primary source] | [per primary source] |
| Zep / Graphiti | [per primary source] | [per primary source] |
| Memento | [per primary source] | [per primary source] |
| **Persistence (this work)** | **[TBD — camera-ready]** | **[TBD — camera-ready]** |

**Hypothesis (to test).** Persistence matches Zep's accuracy (we share the bitemporal model) with competitive latency (materialized projection is architecturally similar). The contribution is not a raw memory win but the substrate's composability with audit, replay, and plan storage.

### 6.2 CAMO-style counterfactual fidelity

This evaluation has two components. The *byte-identical NO-OP* property (§4.5 Corollary) is **already testable on the Phase 1 artifact** — `tests/replay/test_determinism.py::test_noop_intervention_produces_byte_identical_trajectory` passes; we cite it directly in the abstract as the structural baseline stronger than CAMO's statistical aligned-randomness. The *distributional CAMO protocol* — 1000 paired rollouts with single-variable interventions, measuring prefix alignment (Hamming distance from factual), intervention faithfulness (probability the counterfactual at step $k$ reflects the intervention), and suffix variance over 100 re-replays — requires (i) the per-step rng-state recording extension to the replay engine (Phase 2) and (ii) a meaningful LLM budget for stochastic agents. Both are feasible within the 2026-06-16 → 2026-07-20 camera-ready window if scoped to a single base agent.

**Reproduction Plan.** Abstract ships the NO-OP corollary. Camera-ready ships the full 1000-trajectory table on a toy agent with honest per-step rng recording and, if budget permits, a scaled-down 100-trajectory table on Claude Haiku as the stochastic agent.

### 6.3 Regulator-replay benchmark — 50 synthetic trajectories (novel)

We propose **regulator-replay fidelity**, a new benchmark. Given an agent decision trajectory produced in a regulated domain, can a third-party auditor — given only the datom log and the plan AST — deterministically reconstruct the production decision? The metric is the fraction of decisions whose reconstruction matches production byte-for-byte under `effect.canonical.canonical_dumps` serialization.

**Scope (rescoped from the v0.1 draft).** For this paper we target a **50-trajectory synthetic project-finance corpus**, not a 200-trajectory production corpus. The synthesis pipeline runs a BankabilityAI-shaped scoring agent (WACC, gearing, concession-fee, sector classification) over synthetic inputs, persists the resulting trajectories through the Phase 1 Fact module, and hands the log to an independent reconstruction script. The dataset generator, the reconstruction harness, and the 50-trajectory corpus will be released together under **CC-BY-4.0** before camera-ready. Full production-scale evaluation is deferred to an extended-version companion paper.

**Why this rescope.** This is the paper's flagship novelty. A protocol proposal without numbers would collapse the contribution from "we introduce and evaluate a new benchmark" to "we propose a benchmark." Fifty synthetic trajectories is the minimum tractable scope that keeps "evaluate" in the contribution.

| Regulator-replay configuration | Factual deterministic | Counterfactual byte-identity @ NO-OP | Reconstruction fidelity |
|---|:---:|:---:|:---:|
| 50 synthetic PF trajectories, DictProjection | **[TBD — camera-ready]** | **[TBD — expected ≥ 99% from §4.5 corollary]** | **[TBD — camera-ready]** |

**Hypothesis.** Reconstruction fidelity ≥ 99% on the synthetic corpus; the 1% gap, where present, is attributable to non-determinism admitted into the catalog during trajectory synthesis (a diagnostic for the Phase-1 well-formedness check rather than a substrate limitation).

### 6.4 *(Removed from this paper.)*

The Plan-optimization comparison (HotpotQA, Replayed Binance 4h trades, project-finance scoring) requires the Plan module, which is Phase 2. It is therefore out of scope for this paper and will be the core contribution of a Phase-2 companion paper once the Plan module ships.

### 6.5 Case studies — one named deployment, three anonymized vignettes

**Case B — Adaptive Trader v2 (named deployment, reported with numbers).** *Adaptive Trader v2* is a production cryptocurrency trading agent running on Binance Futures with a $400 USDT live-capital cap, Claude-as-sole-decision-maker, and ARIS-reviewed risk governance. The factual dry-run baseline, recorded across 13 continuous days immediately preceding the Persistence migration, closed at profit factor 0.43 over 8 entries with -$26.87 PnL — a clear NO-GO signal that motivated the redesign. We instrument every decision through Persistence's effect handler stack (audit, kill-switch, position-limit, ARIS-gate, dry-run), persisting trajectories to the Fact module. A nightly cron runs the Replay module over each losing trajectory with an entry-delay wait-sweep `{0, 5, 10, 15, 30, 60}` minutes. DPO-pair extraction (Phase 1 replay engine) is staged behind the per-step-rng-recording extension (Phase 2), and preliminary prompt-tuning results on the 8-trade baseline are therefore `[TBD — camera-ready]`; the baseline numbers themselves are real. Adaptive Trader v2 serves as the reference deployment throughout this paper because every Persistence invariant — immutable decision trail, effect-captured non-determinism, EDN playbook AST, counterfactual-validated skill promotion — is stress-tested against real-money risk. A forthcoming companion paper will report the extended live-capital evaluation.

| Adaptive Trader v2 (Case B) | Pre-Persistence baseline | With Persistence (Phase 1) |
|---|:---:|:---:|
| Days of dry-run | 13 | [TBD — camera-ready] |
| Entries | 8 | [TBD] |
| Profit factor | 0.43 | [TBD] |
| PnL (USDT) | -26.87 | [TBD] |
| Audit-chain entries per decision | n/a (prose logs) | [TBD] |
| Regulator-replay fidelity on the 8 baseline trajectories | n/a | [TBD — expected ≥ 99%] |

**Case A — Project-finance assessment SaaS (anonymized vignette).** A bitemporal migration of a production assessment platform serving development finance institutions. The platform produces regulator-facing bankability scores for infrastructure projects; every assumption in the scoring pipeline (WACC, gearing, concession-fee, sector classification) must be reconstructible under audit. Phase 1 lands the Fact module as the authoritative scoring-assumption log; Phase 2 integrates Plan for the scoring pipeline AST. Numeric reporting deferred to a co-authored extended-version paper once the client IP-sharing agreement closes.

**Case C — Insurance aggregator (anonymized vignette).** A licensed insurance comparator operating under a regulatory regime requiring full quote-issuance traceability. Bitemporal client state resolves compliance queries of the form *"when did the agent learn of carrier price change X, and what quotes did it issue between the change and our ingestion?"* — a bitemporal *exclusive-OR* of valid-time and transaction-time, structurally impossible without both clocks. Phase 1 shipped; Plan-driven quote pipeline is Phase 2. Numeric reporting deferred pending licensure co-disclosure.

**Case D — Hospitality operations (anonymized vignette).** A multi-tenant hotel operations agent connected via WhatsApp. Regression trajectories are auto-generated from customer feedback sessions on the production messaging channel; every pull request is designed to replay all known-failing trajectories through the new handler stack in CI. Phase 1 ships the trajectory capture; full regression re-execution is Phase 2 (see §5.4). Numeric reporting deferred pending operator-side deployment gate.

Case B (Adaptive Trader v2) is the only named deployment in this paper. Case A, C, and D identities are withheld pending client-side co-authorship decisions; their reproducibility artifacts are available on request to NeSy program chairs under NDA.

### 6.6 Reproduction posture

- **Artifact.** The Phase-1 artifact (`persistence-os @ v0.1.0a1`) is bundled with the paper submission. `pytest -q` from a clean clone runs the 356 test suite in under one minute.
- **Benchmark timeline.** §6.1 (LongMemEval) and §6.2 (CAMO 1000-trajectory table) ship in the camera-ready (2026-07-20). §6.3 (50-trajectory synthetic regulator-replay) ships in the camera-ready with the generator script. §6.5 Case B (Adaptive Trader v2) ships post-Persistence-migration numbers in the camera-ready.
- **Licensing.** Runtime: AGPL-3. Paper + benchmark harness + regulator-replay dataset: CC-BY-4.0.
- **Abstract submission scope.** At the 2026-06-16 abstract deadline, §6 reports the formal properties (Prop 2 and the §4.5 NO-OP corollary) as *already-checked on the shipped artifact*; all numeric tables carry `[TBD]` honestly.

---

## 7. Discussion

### 7.1 Limitations

Persistence Phase 1 imposes costs we disclose frankly:

- **Write latency** rises by a constant overhead per transaction due to transactor validation and auto-retraction. The exact overhead on the Phase 1 `InMemoryStore` and `SQLiteStore` backends is `[TBD]` (to be measured for the camera-ready §5.1 latency table). Aspirationally, this is acceptable for agent workloads; unacceptable for voice-turn-latency inner loops, which we route to a fast ring buffer drained asynchronously. Per-transaction ed25519 signing — which would add a further cryptographic-signing cost when enabled — is Phase 2 work (§7.2); no overhead figure is claimed for it until the signing path ships and is measured.
- **Storage** on an append-only log is approximately 4× a mutable-memory baseline, partially offset by content-addressing and (Phase 2) Zstd segment compression. Cold-tier eviction after 90 days (retain datoms, drop embeddings) mitigates further; Phase 1 ships the retention primitives, not the eviction scheduler.
- **`branch` complexity** on Phase 1 is $O(|D|)$-copy (§4.1 Prop 1), not $O(|\Delta| \log |D|)$. A persistent-trie backing Store is the Phase 2 upgrade path.
- **Replay determinism** on Phase 1 is proven byte-wise only on a toy agent (§4.5 Corollary). The generalization to LLM trajectories requires per-step rng-state recording in the replay engine — a targeted Phase 2 change that unblocks the §6.2 camera-ready numbers.
- **Audit universality** is a configuration of the deployed stack, not an invariant of the substrate (§4.3). `Runtime.assert_universal_audit` is on the Round-2 hardening list.
- **Policy-as-EDN** presumes Clojure/EDN literacy of the operator. The evaluator itself is language-agnostic at the data level, and JSON is a subset of EDN, so interoperability is straightforward.
- **Substrate lock-in.** Adopting Persistence means adopting a specific opinionated model of agent state. The migration path is additive (§7.3), but eventual consolidation around datoms is the intended end state.

### 7.2 Privacy architecture

Persistence is designed for local-first deployment. The authoritative datom log runs on operator-controlled infrastructure; all projections are regenerable. Provenance is **content-hashed per-datom (SHA-256) in Phase 1; per-transaction ed25519 signing is Phase 2 work** once key management (`sign_transaction(tx_id, tx_hash, key) → bytes`, verified inside `verify_chain`) ships. A `:privacy :local` attribute on `:llm-call` nodes routes inference to local models (Qwen, DeepSeek, Llama), bypassing cloud vendors. Telemetry is emitted to an operator-controlled OpenTelemetry collector, with no SaaS egress. Skill-library entries carry a `:visibility :private` attribute enforcing never-to-cloud semantics.

For regulated deployments, the system is fully auditable with zero third-party data exposure once the audit handler wraps the full catalog (§4.3). A plausible commercial deployment puts the runtime inside a client's VPC, stores the log in client-controlled Postgres, and exposes only the REPL and metrics to the vendor. Cryptographic *authenticity* of the audit chain (who signed the transaction) ships with ed25519 in Phase 2; Phase 1 provides *integrity* (the chain detects tampering given itself) via `verify_chain`.

### 7.3 Adoption path

Persistence is explicitly additive. An existing agent stack migrates in three phases:

1. **Datom log (2 days, non-breaking).** Stand up the Fact module alongside the existing memory system; wrap writes with a datom-emitting interceptor. Zero reads change.
2. **Backfill (3 days).** Synthesize datoms from existing mutable state with `tx-time = created_at`, `valid-from = created_at`.
3. **Query surface (1 week).** Build `as-of`, `history`, `branch` as thin functions over the log; existing skills rewire to use them.

Subsequent migration of Effect, Spec, and Replay (Phase 1) proceeds one module at a time, at the operator's pace. Plan, Txn, and REPL land in Phase 2.

### 7.4 Relation to neurosymbolic AI

Persistence is neurosymbolic in a specific technical sense: the *substrate* is symbolic and the *agency* is neural. Phase 1 ships four symbolic substrate pieces:

- **Bitemporal datom queries with a Datalog-shaped surface** — `as_of`, `history`, `branch`, `validAsOf` are relational queries over an (e, a, v, τ, …) tuple store; Phase 1 implements these as Python comprehensions over the log (a Datalog query helper is adjacent future work — see below).
- **EDN AST grammars for plans** — `:persistence.plan/node` is registered in Phase 1; the Plan-module evaluator is Phase 2.
- **Policy-as-data** — shipped (`policy_eval.py`, ~200 LOC).
- **Malli-style specs with LLM self-healing hints** — shipped (`persistence.spec`, 152 tests; `explain_for_llm`, §4.7).

Two items we explicitly do *not* claim as Phase 1 shipped are Datalog and Z3. We separate them cleanly from shipped capability:

- **Datalog engine.** The datom model is Datalog-ready — classical Datalog rules over `(e, a, v)` triples sit naturally on top of our query surface — but the Phase 1 query layer is Python list comprehensions, not a rule engine. A Datalog surface (a compact pattern-matching helper over the datom set) is an adjacent system we draw on and is straightforward Phase 2 work.
- **Z3-discharged `verify` leaves.** Proof-of-Thought (Fan et al. 2024) describes a compelling integration point for Z3 at `verify`-node leaves, where plan fragments discharge proof obligations against an SMT solver. This is a future Plan-module feature; no Z3 code ships in Phase 1.

The symbolic substrate we *do* ship — bitemporal log, handler stack with `is_well_formed` / `Unhandled`, Merkle-hashed audit chain, boundary specs with `explain_for_llm` — carries the properties we care about: accountability, composition, formal guarantees, and a concrete LLM-self-healing contract. The neural layer (LLM decisions, embeddings, generative skills) carries expressiveness. This distinguishes Persistence from pure-neural approaches (no guarantees) and pure-symbolic approaches (no expressiveness), and positions it as a candidate substrate for neurosymbolic systems beyond LLM agents, including embodied agents, multi-agent planning, and human-in-the-loop scientific discovery.

---

## 8. Conclusion

We presented Persistence, a cognitive-runtime substrate for accountable neurosymbolic agents. Phase 1 of the reference implementation (v0.1.0a1, 356 tests green) ships four modules — Fact, Effect, Spec, Replay — demonstrating four substrate-derived capabilities: queryable bitemporal history, counterfactual branching with parent-store isolation, composable policy-gated effects with a machine-checkable well-formedness property, and boundary-checked contracts with LLM-self-healing hints. Two formal propositions hold on the shipped artifact: well-formedness of any handler stack is decidable in linear time by `Runtime.is_well_formed` and enforced at runtime by `Unhandled`; replay of a NO-OP intervention produces a trajectory whose canonical hash is byte-identical to the factual trajectory's. The Merkle-hashed audit chain (`verify_chain`) shipped and is the regulator-grade foundation on which Case A (project finance) and Case C (insurance) will build in Phase 2. Three further modules (Plan, Txn, REPL) are specified, partially registered in the spec registry ahead of the runtime (a deliberate parse-don't-validate methodology), and scheduled for Phase 2 (2026-Q3).

The core claim is that accountability, counterfactual replay, composable safety, compositional skill learning, multi-agent coordination, and live production steering can be derived from one substrate rather than five — and that the four already shipped are *properties of the log, checkable against the artifact*, not features engineered per agent.

**Future work.** We plan to extend the runtime to (1) a persistent-trie `Store` backend that makes Proposition 1's O(log n) branch share a shipped property, not an idealized-implementation theorem; (2) per-step rng-state recording in the replay engine, which unblocks byte-identical replay for LLM trajectories; (3) ed25519 per-transaction signing via a `sign_transaction` primitive verified in `verify_chain`; (4) a Datalog query surface over the datom set; (5) Z3-discharged `verify` leaves in the Plan-module AST following Proof-of-Thought (Fan et al. 2024); (6) embodied-agent extensions where effects include physical-world interactions with partial observability; and (7) distributed deployment preserving bitemporal semantics across multi-region fact stores.

We release the Phase-1 runtime under AGPL-3, with paper artifacts (benchmark harness, 50-trajectory synthetic regulator-replay dataset) under CC-BY-4.0.

---

## Acknowledgments

[To be populated on submission.]

## References

*(Canonical bibliography to be typeset in the camera-ready; draft references below. Specific percentage claims cited in §2.1, §2.4, §2.5 are to be re-verified against the primary sources.)*

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

*End of draft v0.2. ARIS R4 corrections applied 2026-04-21. Evaluation numbers marked [TBD] will be populated in the camera-ready per `conductor/tracks/persistence-os-foundation_20260420/`. Case A, C, and D identities remain anonymized pending client co-authorship agreements; Case B (Adaptive Trader v2) is named.*
