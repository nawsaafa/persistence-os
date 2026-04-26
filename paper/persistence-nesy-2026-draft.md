# Toward Accountable Neurosymbolic Runtimes: The Persistence OS Substrate

**Target venue:** NeSy 2026 — 20th International Conference on Neurosymbolic Learning and Reasoning, Faculty of Sciences of the University of Lisbon (FCUL), 1–4 September 2026.
**Target track:** Main Track Phase 2. Abstract due 9 June 2026, paper 16 June 2026 (AoE). Notification 8 July. Camera-ready 20 July. Full paper ≤ 10 pages excluding references.
**Status:** Draft v0.6 — internal, not for external distribution until ARIS review round ≥ 2 passes.
**License intent:** AGPL-3 for the runtime, commercial option for vertical integrators. Paper artifacts (benchmark harness + regulator-replay dataset) released under CC-BY-4.0.

---

### Revision history

- **v0.6 (2026-04-25) — Phase 2.B persistence.plan v0.3.0a1 ship + R4 Round-2 carry-forward closures.** Closes the two R4 Round 2 carry-forward minors held against v0.5.1 and locks the Phase 2.B coercion-registry ship into the draft. (i) §1 Neurosymbolic positioning paragraph promoted from a single sentence to an explicit four-piece inventory of Phase-1 symbolic-substrate capabilities (bitemporal-datom Datalog-shaped queries, EDN AST grammars with the v0.3.0a1 static-plus-manifest coercion registry, policy-as-data, Malli-style specs) plus a sharp call-out of the two items deliberately scoped out (Datalog rule engine, Z3-discharged `verify` leaves) — same content as §7.4 but visible to a NeSy reviewer in the introduction rather than buried in the limitations chapter. (ii) §2.3 KR-citation paragraph adds Valmeekam et al. 2022 *LLMs Still Can't Plan* alongside the existing Valmeekam 2023 / Kambhampati 2024 citations to ground the LLMs-as-planners-are-unsound thesis on its primary attack-paper rather than only the survey-style follow-ups. (iii) References list updated. (iv) Test count moves 783 → 797 (full-repo at `be7e37f`); `persistence.plan` shipped version moves v0.2.0a3 → v0.3.0a1 in abstract and §1 *what this paper reports, honestly* paragraph. The v0.3.0a1 ship is the R3-M4 coercion registry: lets plan authors put `datetime` / `date` / `bytes` / `Decimal` / `UUID` / `frozenset` / `edn_format.Symbol` directly into `Node.attrs` without `Node.id` raising `TypeError`, with id-time-only coercion (the author's `node.attrs` stays faithful), strict `TypeError` on unregistered types, and `PLAN_CANONICAL_VERSION = 1` introduced as the manifest constant for schema-evolution callers. No proposition-level changes; Prop 5 still loads on byte-identical round-trip plus content-addressing across 16 node kinds, now extended over the seven default-coerced types as well via the registry walker.
- **v0.5.1 (2026-04-24) — ARIS Round 2 patch pass.** Closes the shared R1+R2 Round 2 MAJOR and the two R4 Round 2 half-measures flagged against v0.5, no code changes: (i) three orphan citations (Liu 2023 *LLM+P*, Valmeekam 2023 *PlanBench*, Kambhampati 2024) added to the References list so the §2.3 in-text citations resolve; (ii) §6.5 Case B opens with a 10-line EDN playbook AST excerpt plus a tie-back sentence mapping `:llm-call` leaves to neural agency and `:seq`/`:branch`/`:case`/`:verify` to symbolic control flow under Proposition 5 content-addressing and the §4.2 effect handler stack — the body no longer reverts to trading-ops vocabulary immediately after the neurosymbolic reframe; (iii) §2.3 adds a NeSy-proper positioning sentence with citations to Logic Tensor Networks (Badreddine et al. 2022), DeepProbLog (Manhaeve et al. 2018), and NeuPSL (Pryor et al. 2023), making explicit that Persistence is orthogonal to neural-symbolic reasoning hybrids at the inference layer and targets agent-level symbolic scaffolding with LLM leaves. Tests unchanged at **783 passed + 7 xfailed**.
- **v0.5 (2026-04-24) — ARIS Round 1 R1+R2+R4 MAJORs closed; refresh pass over v0.4.** Scope: (i) test-count drift reconciled (the suite is **783 passed + 7 xfailed** at main `b9cbf37`, not the 776 / 151 / 152 numbers v0.4 carried); (ii) `persistence.plan` shipped version bumped to **v0.2.0a3** after `a9289ab` closed a Prop-5 round-trip falsifier discovered by hypothesis at `max_examples=200` (the reserved `id` / `:id` attr key was accepted at construction but stripped at parse, so `parse(unparse(n)).id ≠ n.id` for user-supplied-id nodes — now both rejected at construction AND stripped at parse as defense-in-depth); (iii) `__version__` bumped to 0.2.0a3 in `src/persistence/__init__.py`; (iv) `LICENSE` (full AGPL-3) added at repo root so the paper's license claim resolves; (v) Prop 5 parenthetical updated to match the v0.2.0a3 construct-vs-parse invariant; (vi) hypothesis test-budget sentence split into the honest 2-at-200 / 4-at-50 shape; (vii) §2.6 Persistence NO-OP cell annotated `●*` with an explicit toy-agent-vs-LLM-leaf footnote; (viii) §6.3 and §6.5 target-vs-expected language tightened (`[TBD — target ≥ 99%; hypothesis H1]` rather than the outcome-predicting `[TBD — expected ≥ 99% from §4.5 corollary]`); (ix) Pangolin citation reconciled — in-text `(Cheng et al. 2025)` to match the author-year references entry (venue verification deferred to camera-ready); (x) §5.5 hardening-track list honestly reflects what shipped in v0.2.0a2 (R3-M2 shared `SpecError` base, R3-M3 `:original-tag` escape hatch, R3-M5 schema-evolution contract) vs what is still open (R3-M1 `fold()`, R3-M4 coercion registry, substrate-wide 128-bit id consistency), plus a one-sentence acknowledgment that v0.2.0a3 closed the Prop-5 reserved-attr-key falsifier. Framing pass (R4 M1–M6): abstract leads with the neurosymbolic thesis (symbolic substrate carries formal properties on which neural agency composes) rather than production verticals; §1 opens with the NeSy research question; §4.4 adds a homoiconic-neurosymbolic-AST paragraph after Proposition 5; §2.3 inserts a short KR-citations paragraph (LLM+P, PlanBench, Kambhampati) positioning Persistence between pure-LLM and pure-PDDL planning; §6.3 adds a sentence framing regulator-replay as *symbolic-neural joint reconstruction*; §6.5 Case B opening rewritten to lead with the neurosymbolic playbook-AST-plus-neural-leaf structure before the trading-bot vocabulary. No substantive claim change — the paper's technical content is unchanged; this pass aligns stated numbers with the artifact and reshapes the top-down framing so a NeSy reviewer sees the neurosymbolic positioning first.
- **v0.4 (2026-04-24) — Phase 2.B persistence.plan v0.2.0a1 shipped; test suite 579 → 783 (+7 xfailed).** Plan module moves from *designed* to *shipped (minimal)*: parse, unparse, walk, and spec-validation over a uniform `[tag {attrs} *children]` EDN AST across all 16 node kinds. Three substrate-grade claims are now warranted by the test suite: (i) content-addressed Merkle DAGs via `sha256(canonical(node))[:32]` — 128-bit prefix, NaN/Inf rejected at canonicalization, user `:id` stripped at parse to prevent hash poisoning; (ii) byte-identical parse/unparse round-trip pinned by a hypothesis property at `max_examples=200` across all 16 kinds with nested attrs and float32 values; (iii) spec validation against `:persistence.plan/node` with 32 per-kind malformed cases plus 7 `xfail` scoping v0.2.C per-kind tightening. ARIS gate (R1 Correctness 8.2 / R2 Rigor 7.4→8.8 / R3 Composability 8.3; R4 skipped — no external wiring) passed at min 8.2 over 7.8 threshold, with a documented fix-pass (8 commits) closing 7 R2 MAJORs including a `:id`-clobber canonical-hash poisoning defect surfaced by R1 + R2 in parallel. Propositional addition: **Proposition 5 (Plan content-addressing with descendant propagation)** at §4.4. Plan-execution, Pareto-vector fitness, the edit API, `:code` sandbox, skill records, and optimizer ladders (MIPROv2, MCTS, evolutionary search) remain Phase 2.C.
- **v0.3 (2026-04-21) — W-wire + W-polish2 + W-polish3 landed; test suite 356 → 579.** Merkle-chain `verify_chain` invariant upgraded with canonicalisation of sibling keyword-keyed audit-entry fields (`policy_id` / `handler_chain` / `principal`) so the factory path and the round-trip (`from_edn ∘ to_edn`) both verify — closes ARIS R5 N1 MAJOR (factory-path regression) and R5 N2 MINOR (sibling canonicalisation asymmetry); Datom wire form hardened with `lstrip(":")` idempotency (R1 N11). No substantive text changes beyond test-count updates; full R5 consolidation in `docs/aris-round-5/`.
- **v0.2 (2026-04-21) — ARIS R4 corrections.** Retitled away from "unified" framing; reframed "seven capabilities as shipped" to "four shipped / three designed"; softened §4.1 Proposition 1 to match the list-backed `InMemoryStore` reference implementation (dropped the HAMT structural-sharing claim from the Phase 1 contribution, promoted it to a Phase 2 upgrade path); removed ed25519 from Phase 1 (§4.1, §7.1, §7.2) and deleted the fabricated "20–40 ms" overhead figure; rescoped §5.1 projection to `DictProjection` with Kuzu/mem0 as Phase 2 adapters; rescoped §6 to a Reproduction Plan with [TBD] cells, a 50-trajectory synthetic regulator-replay target, and Case B (Adaptive Trader v2) as the only numeric case study; dropped §6.4 Plan-optimization benchmark (Plan module is Phase 2); removed Datalog and Z3 from the shipped neurosymbolic-substrate list and moved them to "adjacent systems we draw on"; elevated Proposition 2 (machine-checkable well-formedness via `Runtime.is_well_formed`), the byte-identical trajectory-hash invariant on NO-OP interventions, the Merkle-hashed `verify_chain` audit contract, and `spec.explain_for_llm` / self-healing hints to front-line contributions.
- **v0.1 (2026-04-20) — initial draft.** Seeded by `agent{1..4}-*.md` research specs and the Phase 1 conductor track.

---

## Abstract

We present **Persistence**, a neurosymbolic substrate where symbolic state — bitemporal datoms, content-addressed plan ASTs, policy-as-data, and boundary specs — carries formal properties (immutability, replay determinism, audit-chain Merkle integrity, plan content-addressing with descendant propagation) on top of which neural agency composes. The central design invariant is that every piece of agent state — memory, audit, plan, skill, transaction — is an immutable, content-addressed, bitemporal *datom*; effects route through a composable handler stack whose entries are themselves datoms; plans are EDN abstract syntax trees stored as Merkle-DAGs; counterfactual replay is a first-class query over the log. Large-language-model (LLM) agents are moving into regulated, high-stakes domains — project finance, insurance, algorithmic trading, clinical operations, hospitality — where auditability, counterfactual analysis, and controllable learning are not optional, and contemporary agent frameworks fragment these requirements across incompatible systems: temporal knowledge graphs for memory (Zep, Graphiti), algebraic effect systems for tool use (Pangolin), declarative program synthesis for reasoning (DSPy), skill libraries for continual learning (Voyager, Memento-Skills), and seed-replay kludges for counterfactuals (CAMO, AgentHER). Persistence's claim is that these properties can be *derived* from a single symbolic substrate rather than re-engineered per system. This paper reports **Phase 1 and the first Phase-2 increment of the reference implementation (v0.1.0a1 + v0.4.0a1, 832 tests green, 7 xfailed)**, which ships five of the seven runtime modules — Fact, Effect, Spec, Replay, and Plan (minimal: parse / unparse / walk / spec-validation with 128-bit content-addressing) — and demonstrates five substrate-derived capabilities: queryable bitemporal history, counterfactual branching, composable policy-gated effects, boundary-checked neurosymbolic contracts, and content-addressed homoiconic plan ASTs with byte-identical round-trip. Two further modules (Transactions, REPL) remain specified-but-not-shipped; Plan-module *execution* (effect-dispatched evaluation, Pareto-vector fitness, MIPROv2 / MCTS / evolutionary optimization, 4-gate skill promotion) is Phase 2.C. We formalize the substrate (bitemporal datom model, effect-handler stack with a machine-checkable well-formedness property, counterfactual replay with byte-identical NO-OP invariance, Merkle-hashed audit chain, parse-don't-validate boundary specs with LLM-self-healing hints) and prove five formal propositions that hold on the shipped code; the headline two are (i) any handler stack is well-formed iff every catalog operation is covered — checkable in linear time by `Runtime.is_well_formed`; (ii) `replay(T, I)` with a NO-OP intervention yields a trajectory whose canonical hash is byte-identical to the factual trajectory's — a stronger determinism guarantee **for the NO-OP intervention case** than CAMO's aspirational seed replay (for non-trivial interventions, byte-identity no longer applies; the suffix diverges as soon as the intervened action changes observations). We describe a Reproduction Plan for four benchmarks — LongMemEval, CAMO-style counterfactual fidelity, a novel 50-trajectory synthetic regulator-replay, and one named production case study (Adaptive Trader v2) — to be populated by the camera-ready. The neurosymbolic positioning is load-bearing: the substrate is explicitly symbolic (bitemporal datom model with a Datalog-shaped query surface, EDN-grammar plan-ASTs, policy-as-data, Malli-style specs) while agency remains neural; the properties this paper proves are properties of the symbolic layer under the neural one.

---

## 1. Introduction

**Research question.** When neural agency is scaffolded by a symbolic substrate, what formal properties does the combined system inherit that neither layer provides alone? We argue that six substrate invariants — immutable bitemporal datoms, algebraic-effect handlers with audit semantics, homoiconic plan ASTs, transactional shared state, machine-readable boundary specs, and a REPL — yield derived capabilities (accountability, replay, counterfactual fidelity, composable safety, compositional skill learning, multi-agent coordination) that are consequences of the substrate, not independently-engineered features. This is the neurosymbolic bet Persistence makes: the *symbolic* layer is designed to carry formal guarantees as proved invariants of the log; the *neural* layer (LLM decisions, embeddings, generative skills) stays expressive and open-ended above it; the interesting properties emerge at the interface.

Large-language-model agents are no longer research demonstrations. In the past eighteen months, agents have reached production in project-finance assessment, insurance quote aggregation, hospitality operations, algorithmic trading, clinical intake, legal drafting, and hundreds of smaller verticals. The production gap has exposed a class of problems the framework literature has not addressed — and, seen through the NeSy lens, each is a place where the missing ingredient is symbolic-substrate structure under the neural layer:

1. **Accountability.** Regulators asking "what did the agent believe at 14:03 on April 14, and why did it decide X?" receive hand-waved answers assembled from prose logs. There is no formal substrate for temporal provenance.
2. **Counterfactual reasoning.** Post-incident analysis — *"what if the agent had waited ten minutes?"* — is either absent or simulated by re-running the agent with a perturbed prompt, which changes far more than the intended variable.
3. **Controllable learning.** Agents that "improve themselves" by rewriting markdown skill files or mutating memory in place accumulate semantic drift and procedural drift (Hannecke et al. 2026).
4. **Composable safety.** Guardrail frameworks (NeMo Guardrails, LLM Guard, Rebuff) are single-layer interceptors. Multi-tenant, regulated agents require stackable policy, dry-run, cache, rate-limit, and audit layers with well-defined interaction semantics.
5. **Plan opacity.** Prose chain-of-thought is inspectable but not editable. JSON tool-call graphs are editable but not expressive. Neither is a first-class program the agent can reason over.

The field has converged on partial solutions. Zep's Graphiti introduces bitemporal knowledge graphs (Kurtic et al. 2025); Pangolin and Wang et al. (2025) formalize algebraic effects for LLM programming; DSPy treats agents as declarative programs (Khattab et al. 2023); Voyager (Wang et al. 2023) and Memento-Skills (2026) construct executable skill libraries; CAMO (2026) and AgentHER (2026) formalize counterfactual replay via aligned randomness. Each is a beachhead. None are composed. The resulting integration burden — five substrates, five consistency models, five failure modes — erodes the properties each was designed to provide.

**Contribution.** We present **Persistence**, a cognitive-runtime substrate built around a single claim: accountability, replay, counterfactuals, composable safety, and compositional skill learning can be derived from *one* substrate rather than re-engineered per system. The substrate treats every piece of agent state — memory facts, audit entries, plan-AST nodes, skill-library entries, transaction commits — as an immutable, content-addressed, bitemporal *datom*. Effects route through a composable handler stack whose entries are themselves datoms. Plans are EDN abstract syntax trees stored as datom graphs; skills are named, content-addressed subtrees. Trajectories are ordered sequences of effect datoms under a shared run-id, making counterfactual replay a first-class query over the log. Transactions compose via software transactional memory over datom-backed refs. Specs constrain every boundary. A REPL module exposes inspection, editing, rewind, and speculative branching against running agents.

**What this paper reports, honestly.** Phase 1 of the reference runtime (v0.1.0a1, tagged 2026-04-20) shipped four of the seven modules — Fact, Effect, Spec, Replay — with 579 passing tests. The first Phase-2 increment ships the fifth module, Plan, in a deliberately minimal form: parse, unparse, depth-first walk, and spec validation over a uniform `[tag {attrs} *children]` EDN AST, with 128-bit content-addressing via `sha256(canonical(node))[:32]`. The Plan module merged at v0.2.0a1 on 2026-04-24 and received three patch releases over the following day: v0.2.0a2 hardened spec-error composition, alias preservation via `:original-tag`, and schema evolution; v0.2.0a3 closed a reserved-attr-key round-trip falsifier surfaced by hypothesis at `max_examples=200`; v0.3.0a1 (2026-04-25) shipped a static-plus-manifest coercion registry that lets `datetime` / `bytes` / `Decimal` / `UUID` / `frozenset` / `edn_format.Symbol` participate in canonical hashing without breaking content-addressing determinism, and introduced a `PLAN_CANONICAL_VERSION` manifest constant for schema-evolution callers (see v0.6 revision history). The combined suite at v0.1.0a1 + v0.3.0a1 runs **797 tests green, 7 xfailed**. The Plan-module *execution* runtime (effect-dispatched evaluation, optimizer ladder, 4-gate skill promotion) remains Phase 2.C. Together the five shipped modules demonstrate five substrate-derived capabilities as shipped: queryable bitemporal history (`as_of`, `as_of_valid`, `history`), counterfactual branching (`branch`), composable policy-gated effects (15-op catalog + handler stack + `policy_eval`), boundary-checked contracts (`spec.conform`, `spec.parse`, `spec.explain_for_llm`), and content-addressed homoiconic plan ASTs with byte-identical round-trip (`plan.parse`, `plan.unparse`, `Node.id`). Two further capabilities are specified-but-not-shipped: multi-agent coordination (Txn), and live production steering (REPL); compositional skill learning sits on top of shipped Plan parse/validation with Phase-2.C execution and the 4-gate promotion still pending. The `:persistence.plan/node` and `:persistence.plan/skill` specs were already registered in the Phase 1 registry — a deliberate parse-don't-validate move that froze the data shape ahead of the code that would consume it (§4.7); Phase 2.B confirms that discipline by shipping a parser that round-trips byte-identically to EDN and conforms against the pre-committed spec. We call this reporting discipline out because the deliberate gap between *"substrate shipped"* and *"vertical modules shipped"* is itself part of the methodological contribution: **the substrate is claimed once, and each derived capability is a property we can check against the log, not an engineered feature per agent.**

**Neurosymbolic positioning.** Persistence is neurosymbolic not as a label but as a design principle: *neural agency* (LLM decisions, embeddings, generative skills) operates over an explicitly *symbolic substrate* (bitemporal datom queries with a Datalog-shaped surface, EDN AST grammars for plans, policy-as-data, Malli-style specs). The neural layer handles the open-ended; the symbolic layer handles accountability, composition, and guarantees. Concretely, Phase 1 ships four symbolic-substrate pieces and we deliberately scope two further symbolic capabilities *out* of Phase 1 to keep the boundary between shipped artifact and aspiration sharp:

- **Bitemporal datom queries with a Datalog-shaped surface.** `as_of`, `validAsOf`, `history`, `branch` are relational queries over the (e, a, v, τ, …) tuple store; Phase 1 implements them as Python comprehensions over the log (`src/persistence/fact/db.py`), with a Datalog rule engine adjacent future work.
- **EDN AST grammars for plans.** `:persistence.plan/node` and `:persistence.plan/skill` are registered in the Phase 1 spec registry; the Phase 2.B Plan module ships parse / unparse / walk / spec-validation over a uniform `[tag {attrs} *children]` AST across all 16 node kinds with 128-bit content-addressing and (as of v0.3.0a1) a static-plus-manifest coercion registry that lets `datetime`, `bytes`, `Decimal`, `UUID`, `frozenset`, and `edn_format.Symbol` participate in canonical hashing.
- **Policy-as-data.** Shipped (`policy_eval.py`, ~200 LOC); guards, allow-lists, and rate limits are themselves datoms, queryable bitemporally and rewindable on a branch.
- **Malli-style specs with LLM self-healing hints.** `persistence.spec` ships 186 tests and an `explain_for_llm` surface for parse-don't-validate boundary contracts (§4.7).

The two items we explicitly do *not* claim as Phase 1 shipped are a Datalog rule engine and Z3-discharged `verify` leaves following Proof-of-Thought (Fan et al. 2024); both are adjacent systems with compatible primitives, scheduled as future work (§7.4 enumerates them in detail). This separation matters for a NeSy reviewer: the substrate's formal properties hold on what is shipped, and the future work is positioned as additive rather than load-bearing. The framing contrasts both with pure-neural approaches (prose CoT, embeddings-only memory) that surrender formal properties, and with pure-symbolic approaches that surrender expressiveness.

**Outline.** §2 surveys the five beachheads Persistence draws from. §3 states the six substrate invariants and maps them to Phase-1 shipped / Phase-2 designed boundaries. §4 formalizes the substrate — datoms (§4.1), effect handlers and the well-formedness property (§4.2), the Merkle-hashed audit chain (§4.3), plans (§4.4), trajectories and replay with the byte-identical NO-OP invariant (§4.5), transactions (§4.6), and specs with the self-healing LLM contract (§4.7). §5 describes the reference implementation. §6 presents a Reproduction Plan for the four evaluations — camera-ready will replace [TBD] cells with measurements. §7 discusses limitations, privacy architecture, adoption path, and the neurosymbolic framing. §8 concludes.

---

## 2. Related Work

### 2.1 Agent memory

Agent memory has evolved rapidly. Vector-store RAG gave way to graph-structured memory (Graphiti, Neo4j-based agent graphs) as the benefits of relational traversal became clear. Zep (Kurtic et al. 2025) introduced bitemporal edges with `valid_at` and `invalid_at` timestamps, with reported accuracy lifts over MemGPT on LongMemEval and substantial latency reductions; Memento (2026) reached comparable figures with a similar design. (Exact percentage claims in §2.1 will be cross-checked against the primary sources in the camera-ready.) Mem0 and A-Mem pursued a different direction — *mutable* memory that evolves via LLM-driven consolidation — which Hannecke et al. (2026, the SSGM paper) showed produces unbounded drift without a formal write-gate. The Stability and Safety of Governed Memory framework proves `O(N·ε_step)` bounded drift in systems with an append-only episodic ledger paired with a mutable projection, vs. unbounded drift in pure-mutable systems.

Persistence sits on the immutable end of this spectrum, with a critical extension: it treats *every* datom — not only memory facts, but audit entries, plan-AST nodes, skill versions, transaction commits — as bitemporal. Memory is one consumer; the substrate serves all of them.

### 2.2 Algebraic effects in LLM programming

Algebraic effects, long studied in functional programming (Koka, Eff, Links), were recently brought to LLM scripting by Wang (2025, *Composable Effect Handling for Programming LLM-integrated Scripts*) and the Pangolin language (Cheng et al. 2025). <!-- TODO: verify Pangolin venue before camera-ready — the draft references section records LMPL 2025 but this has not been primary-source-verified. --> Both separate *what* an effect is from *how* it is handled, enabling stacked interceptors for retry, caching, multi-shot sampling, and observability. Neither system, however, integrates effect handlers with a temporal memory substrate, nor with policy-as-data, nor with a formal audit chain. Persistence does: when the audit handler wraps the full catalog (the default configuration for regulated deployments), every effect emits a datom; every handler is policy-reviewable; every run is replayable. §4.3 formalizes the Merkle-hashed audit contract.

### 2.3 Structured agent programming

DSPy (Khattab et al. 2023, ongoing) treats agents as declarative programs of typed modules with signatures, compiled by optimizers (BootstrapFewshot, MIPROv2) that search over instruction and demonstration slots. SmolAgents (Hugging Face, 2024) argues the opposite extreme — code *is* the best action language because LLMs already know Python. Open Agent Specification (2025) and declarative LLM-workflow DSLs formalize agent workflows as DAGs.

The symbolic-planning and knowledge-representation literature deserves a citation here. LLM+PDDL integrations (Liu et al. 2023, *LLM+P*; Valmeekam et al. 2023, *PlanBench*) argue for routing LLMs through classical planners; the same line — articulated as direct evidence of unsoundness — runs through Valmeekam et al. 2022 *LLMs Still Can't Plan* and Kambhampati 2024, who repeatedly argues that LLMs-as-planners are unsound without a symbolic verifier. Distinct from the LLM+PDDL line, the neurosymbolic reasoning literature — Logic Tensor Networks (Badreddine et al. 2022), DeepProbLog (Manhaeve et al. 2018), and NeuPSL (Pryor et al. 2023) — integrates differentiable neural components with symbolic inference at a finer granularity than Persistence targets; our contribution is orthogonal: a substrate for agent-level symbolic scaffolding with LLM leaves, not a neural-symbolic reasoning hybrid at the inference layer. Persistence occupies neither pole: it is neither pure-LLM planning nor pure-PDDL verification, but a homoiconic AST in which leaf nodes may be LLM calls, symbolic code, or external planners — committing to the AST shape via `:persistence.plan/node` without committing to the leaf implementation. The symbolic substrate carries the structural guarantees (content-addressing, descendant propagation, spec conformance); the leaves stay free to be neural, procedural, or SMT-discharged.

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
| Counterfactual replay (byte-identical NO-OP) | | | | | partial | ●* | |
| Boundary specs + LLM self-healing hints | | | partial | | | ● | |
| Declarative plan AST | | | ● | | | | ○ |
| Plan-AST optimization | | | partial | | | | ○ |
| Skill library (4-gate promotion) | | | | ● | | | ○ |
| Multi-agent STM | | | | | | | ○ |
| Live production REPL | | | | | | | ○ |
| Regulator-replay fidelity | | | | | | [designed — see §6.3] | |

*Byte-identical NO-OP replay in Phase 1 is verified for toy agents with fixed rng-consumption; the per-step-rng-recording extension for LLM leaves is Phase 2 (§4.5, §6.2).*

---

## 3. The Persistence Thesis

We state six substrate invariants. Invariants 1, 2, and 5 are fully realized in Phase 1; invariant 3 is realized at the *shape + parse + validate* layer by Phase 2.B (with the execution runtime in Phase 2.C); invariants 4 and 6 remain frozen at the spec-registry boundary with runtime implementation scheduled for Phase 2.

1. **Every fact is immutable, temporal, content-addressed.** Memory, audit, plan, skill, transaction — all datoms. Provenance, counterfactual branching, and audit trails are derived, not engineered. *[Phase 1: shipped — `persistence.fact`.]*
2. **Every action is an effect.** LLM calls, tool calls, memory writes, and side effects pass through a composable handler stack. Policy, safety, caching, dry-run, and retry are handler instances. *[Phase 1: shipped — `persistence.effect`, 15-op catalog.]*
3. **Every plan is an EDN AST.** Agents read, edit, and evolve their own plans. Skills are named AST subtrees promoted under statistical evidence. Plan optimization is structural search. *[Phase 1: `:persistence.plan/node` and `:persistence.plan/skill` specs registered (`src/persistence/spec/_canonical.py`). Phase 2.B (shipped, v0.2.0a1): `persistence.plan` module with parse / unparse / walk / spec-validation over a uniform `[tag {attrs} *children]` AST across all 16 node kinds; 128-bit content-addressing via `sha256(canonical(node))[:32]`; byte-identical parse/unparse round-trip pinned by hypothesis property at `max_examples=200`. Execution runtime (effect dispatch, optimizer ladder, 4-gate skill promotion) is Phase 2.C.]*
4. **Every shared state change is a transaction.** STM coordinates multi-agent belief updates without locks or message passing. *[Phase 1: design frozen in `docs/agent*-spec.md`; Txn-module runtime is Phase 2.]*
5. **Every LLM boundary has a spec.** Parse-don't-validate at trust boundaries. Generative testing from specs. Self-healing via spec-error feedback. *[Phase 1: shipped — `persistence.spec`, 186 tests.]*
6. **Everything is REPL-live.** Inspection, editing, rewind, and speculative branching apply to running production agents, not only development loops. *[Phase 1: rewind semantics subsumed by `as_of` on the shipped Fact module; REPL-module runtime is Phase 2.]*

**Thesis.** These six invariants, implemented together as a single runtime with a shared substrate, produce a system where accountability, replay, counterfactuals, composable safety, compositional skill promotion, coordinated multi-agency, and live production steering are *properties of the substrate* — not features engineered separately per agent. Phase 1 and the first Phase-2 increment together demonstrate five of these as shipped properties: accountability via the Merkle-hashed audit chain on the Fact log; replay via byte-identical NO-OP on the Replay engine; counterfactual branching via `branch()` on the Fact store; composable safety via the Effect handler stack with a machine-checkable well-formedness property; and homoiconic plan-as-data via the Phase 2.B Plan module's content-addressed parse/walk/spec-validation surface. Phase 2.C ships the remaining two capabilities (multi-agent coordination via Txn, live production steering via REPL) plus Plan-execution (optimizer ladder and 4-gate skill promotion) on top of the same substrate, with no substrate schema changes required — that non-requirement is the testable form of the thesis.

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

This is the paper's strongest formal contribution on the Phase-1 artifact: a decidable, linear-time, machine-checked completeness property over the runtime's neurosymbolic interface, with runtime enforcement via `Unhandled`. The check is exercised in the Phase 1 test suite. Properties above the effect layer (audit chain integrity §4.3, replay determinism §4.5) build on well-formedness of the deployed stack; policy composition is *convenient* to express once well-formedness holds but is not itself a substrate invariant (§4.3 treats audit-universality as a stack-configuration contract, not a substrate property).

Handlers compose *masked* (following Koka's named/masked semantics): $\text{mask}_a(h)$ prevents $h$ from intercepting operations of attribute $a$ within its own body, eliminating re-entrant loops when, e.g., a policy handler itself needs to call the LLM.

### 4.3 The Merkle-hashed audit chain

When the `audit` handler wraps a set of operations $W \subseteq K$, each effect on $\kappa \in W$ emits a datom recording (op, args, verdict, provenance, `prev_hash`) before the continuation fires. The chain is built by taking `sha256(canonical_serialize(entry) || prev_hash)` at each link. Phase 1 ships:

- `make_audit_handler(wraps, …)` with configurable $W$ and a default of `("llm/call",)`.
- `verify_chain(entries) → bool`, which re-derives each `prev_hash` from the canonical serialization and detects field-mutation tamper.
- `audit_entry_to_datom(entry) → datom-shaped record` that flows the audit entry into the Fact log.

**Integrity contract.** `verify_chain` detects any single-field mutation inside an entry (tested). Deletion/reorder coverage is flagged in the Round-1 rigor review and is a hardening target for Round 2. Authenticity — proving *who* signed — is distinct from integrity and is not claimed for Phase 1: the current `signature` slot stores a SHA-256 content hash, and per-transaction ed25519 signing is Phase 2 work (§7.2).

**Proposition 4 (Audit-chain immutability).** For any audit-chain $C = \langle e_0, e_1, \dots, e_n \rangle$ produced by `make_audit_handler`'s Merkle-hashed chain-append clause, `verify_chain(C) = True` iff for all $i$, $e_i.\text{id} = \text{sha256}(\text{canonical}(e_i.\text{fields} \setminus \{\text{id}\})) $ and $e_i.\text{prev\_hash} = e_{i-1}.\text{id}$ — i.e. no entry has been mutated, deleted, reordered, or truncated from the middle. This is exercised end-to-end by `tests/effect/test_audit.py::test_tampering_an_entry_breaks_the_chain`, `test_deleting_an_audit_entry_breaks_the_chain`, and `test_reordering_audit_entries_breaks_the_chain`; tail-truncation is allowed by construction (`test_truncating_audit_entries_from_tail_preserves_chain`) and must be detected by regulators comparing a separately-recorded expected length.

**Universality contract.** "Every effect emits a datom" is an invariant of the deployed stack, not the substrate: it holds when and only when the audit handler's $W$ covers the full catalog. Phase 1 exposes `Runtime.is_well_formed(catalog)` to check coverage of $W$ against $K$; a `Runtime.assert_universal_audit` hardening is scheduled for Round 2. For regulated deployments (Case A in §6.5), the configured stack wraps all 15 ops.

### 4.4 Plans (Phase 2.B shipped: parse/walk/spec-validation; execution Phase 2.C)

A plan is a labeled tree where internal nodes are *control operators* ($\text{seq}$, $\text{par}$, $\text{choice}$, $\text{loop}$, $\text{race}$, $\text{let}$, $\text{branch}$) and leaves are *effect invocations* ($\text{tool-call}$, $\text{llm-call}$, $\text{code}$, $\text{checkpoint}$) or *cognitive operators* ($\text{reflect}$, $\text{verify}$, $\text{call-skill}$). Every node $n$ carries a content identity $\text{id}(n) = \text{sha256}(\text{canonical}(n))[:32]$ used as its Merkle-DAG handle. The `:persistence.plan/node` spec is registered in `src/persistence/spec/_canonical.py` (Phase 1); the Phase 2.B `persistence.plan` module (v0.2.0a1, merged 2026-04-24) ships parse, unparse, depth-first walk, and spec validation against this shape. The shipped spec encodes each node as an EDN vector `[:tag {attrs} & children]` rather than a map — a deliberate Lisp-style choice that makes plan ASTs *homoiconic*: a plan literal is indistinguishable from the EDN data that describes it, and the homoiconicity contract's allowed self-edits (read / splice / compose / rewrite / fork / promote) reduce to list-splicing operations over well-typed vectors rather than reflective key-dispatch on a `:node/kind` field. This is what makes the parse-don't-validate methodology (§4.7) first-class rather than cosmetic.

**Proposition 5 (Plan content-addressing with descendant propagation).** For any plan node $n = \langle \text{tag}, \text{attrs}, \text{children} \rangle$ constructed through the shipped `Node` constructor, the identity $\text{id}(n) = \text{sha256}(\text{canonical}(n))[:32]$ is a 128-bit prefix of a cryptographic hash over the canonical JSON serialization of $n$'s tag, its alphabetically-sorted attr map (with non-finite floats rejected at construction, non-string / colon-prefixed / empty / reserved (`id`, `:id`) attr keys rejected at construction, and user-supplied top-level `:id` additionally stripped at parse as defense-in-depth against external EDN hash-poisoning), and its children's ids recursively. Consequently, for any descendant modification of a tree $T$ at depth $d$ producing $T'$, every ancestor id on the root-to-leaf path differs between $T$ and $T'$. Property-checked at `max_examples=200` on two headline properties (round-trip id preservation and descendant-mutation propagation) and `max_examples=50` on four supporting properties by `tests/plan/test_property.py`, with hypothesis strategies spanning all 16 node kinds, nested attr maps to depth 2, and float32 numeric values. *Byte-identity of the canonical form under round-trip* is pinned by `test_round_trip_preserves_id`: for any hypothesis-generated $n$, $\text{id}(\text{parse}(\text{unparse}(n))) = \text{id}(n)$ — a property that, before v0.2.0a3, was falsified by `Node`-constructed nodes carrying a user-supplied `id` attr that the parser silently stripped; the construct-vs-parse asymmetry is now closed by rejecting reserved keys at both sides. Spec conformance against `:persistence.plan/node` is enforced at parse time when `strict=True` and is exercised by 32 per-kind malformed parametrize cases (8 leaf-kinds × non-dict-attrs + 8 container-kinds × malformed-child + 16 kinds × wrong-tag-case); the 7 remaining `xfail` cases scope the Phase-2.C per-kind required-attr tightening and do *not* mask the shipped claim. Birthday-collision probability at 1% reaches $\approx 2.6 \times 10^{18}$ plans at the 128-bit width — a width argued explicitly in the `CHANGELOG-plan.md` entry for commit `190668a` rather than left unstated.

*Neurosymbolic reading.* Proposition 5 is the formal identity law for a **homoiconic neurosymbolic AST**. The skeleton (tag, attrs, children) is symbolic — editable, optimizable, regulator-readable; the leaves (`:llm-call`, `:code`, `:reflect`) are neural. Descendant propagation means every structural edit of the symbolic layer produces a new ancestor-id path — a verifiable identity surface that Phase-2.C plan optimizers (MIPROv2, MCTS-over-Plan) walk over the symbolic scaffold while leaves remain neural. To our knowledge, this is the first content-addressing identity law stated and property-checked for an LLM-leaf-bearing plan AST; the symmetric construct-and-parse rejection of reserved-id keys makes the identity a *substrate* property rather than a parser contract.

The homoiconicity contract defines the agent's allowed self-edits: $\text{read}$, $\text{splice}$, $\text{compose}$, $\text{rewrite}$, $\text{fork}$, $\text{promote}$. Forbidden without explicit capability: editing $\text{verify}$ nodes (which would defeat any future proof-of-thought guarantees, per Proof-of-Thought 2409.17270), modifying ancestors of the currently-executing frame (prevents self-suicide), introducing unbounded $\text{loop}$.

A **skill** is a quadruple $\langle \text{name},\ \text{version},\ \text{ast},\ \text{stats} \rangle$ with stats $\{\text{uses}, \text{success}, \text{cost}\}$, encoded by the `:persistence.plan/skill` spec. A subtree $T$ of plan $P$ is to be promoted to a named skill iff all four gates hold (Phase 2 runtime):

1. $\text{uses}(T) \geq 3$
2. $T$ appears in $\geq 2$ distinct parent plans
3. Rolling success rate of $T$ $\geq 0.8$
4. The LLM-generated docstring of $T$ retrieves $T$ as top-1 over the existing skill library.

Gate (4) is the cheapest way to enforce semantic non-collision: if an LLM-written description of the skill cannot retrieve the skill itself from the library, the skill's function is not well-defined in the context of its peers.

### 4.5 Trajectories and replay

A **trajectory** is an ordered sequence of effect datoms sharing a run-id, plus a seed vector $\sigma = \langle \sigma_{llm}, \sigma_{tool}, \sigma_{env} \rangle$. An **intervention set** is $I = [\langle \text{step},\ \text{field},\ \text{new-value} \rangle, \dots]$ — a (possibly empty) list of per-step modifications to the counterfactual, sorted by `step`. The single-triple case is the Phase-1 default; the shipped replay engine and the `:trajectory/intervention` slot (registered as `seq_of(:persistence.replay/intervention)`) both accept the multi-entry form.

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

**Self-conforming producers (shipped).** Phase 1 specs are bidirectional contracts, not merely input guards. Every load-bearing producer — `audit_entry_to_datom`, `AuditEntry.to_edn`, `Trajectory.to_edn`, `datom_to_wire`, `wire_to_datom` — calls `spec.conform(...)` against its own return value and raises `ValueError` on mismatch. A defect in a producer fails loudly at the producer's site, not later inside a consumer that read the bad wire form. This is a stronger invariant than consumer-side validation: the boundary contract is machine-checked at emission time, which means a paper-stated property about a wire shape is enforced by the code that emits it, not by downstream discipline.

**Forward-compatible spec-first commitment.** In Phase 1 we register `:persistence.plan/node` and `:persistence.plan/skill` in the spec registry *before* the Plan module exists. This is a deliberate parse-don't-validate methodology choice: the data shape is locked before code depends on it, which lets Phase 1 and Phase 2 workers agree on the plan AST's structure without blocking on implementation order. The `:persistence.plan/node` spec is the commitment device; it enforces the paper's §4.4 contract without requiring the runtime to exist.

---

## 5. Implementation

Persistence is implemented across seven modules. The reference implementation is in Python for integration parity with existing agent stacks. Phase 1 ships four modules (Fact, Effect, Spec, Replay); Phase 2 ships the remaining three (Plan, Txn, REPL).

### 5.1 Fact (Phase 1 — shipped)

The Fact module is designed for an append-only Postgres datom log with five covering indexes (EAVT, AEVT, AVET, VAET) plus a bitemporal VT-E range index and a log-ordered index (Datomic Index Model; Tonsky 2023). The shipped Phase 1 reference implementation ships a `Store` Protocol with two backends: `InMemoryStore` (for tests and the CLI demo) and `SQLiteStore` (for zero-ops persistent deployments). The Phase 1 SQL migration (`migrations/0001_datom_log.sql`) ships against SQLite 3.37+ today; the bitemporal datom wire form is migration-compatible, with a Postgres adapter planned for Phase 2. Zstd-compressed segments and content-addressed storage are explicitly Phase 2 work (per `CHANGELOG.md`).

Writes pass through a transactor that computes auto-retractions for cardinality-one attribute overwrites, preserving historical values with `invalidated-by` pointers. The projection surface is a `ProjectionAdapter` Protocol (`reset`, `apply`) with a reference in-process `DictProjection` in Phase 1; production Kuzu and mem0 projection adapters are Phase 2 work (per `CHANGELOG.md` "Deferred"). A legacy-write `mem0_adapter` interceptor is shipped in Phase 1 — it emits a datom before delegating to an operator-supplied mem0 client — but it is an *interceptor* rather than a projection.

**Concurrent-writer safety.** `SQLiteStore.allocate_and_append(datoms)` runs the `MAX(tx) + 1` allocation and the row INSERTs inside a single `BEGIN IMMEDIATE` transaction; `InMemoryStore.allocate_and_append` does the same under its `threading.Lock`. This gives the `Store` Protocol a single atomic allocate-and-append primitive that the transactor routes through, closing the TOCTOU window that a prior `next_tx()`-then-`append(...)` split would have left open under multi-writer load. The guarantee is exercised by `tests/fact/test_concurrent_transact.py`: 16 threads × 50 transacts under a `threading.Barrier` produce 800 unique tx ids with zero collisions. This is the concurrency invariant that Proposition 3 (§4.5, NO-OP byte-identity) implicitly depends on: without a race-free allocator, two concurrent replays sharing a backing store would produce colliding tx ids and break the trajectory-hash identity.

**Latency targets (Reproduction Plan).** Phase 1 tests check correctness, not latency. The paper's per-operation p95 targets — `as_of` ≤ 50 ms, `branch` ≤ 200 ms, `history(e)` ≤ 100 ms for entities with ≤ 1000 datoms — are Phase-2 measurements over a persistent-trie backing store at 1M-datom scale. Phase 1 reference-implementation numbers over the `InMemoryStore` and `SQLiteStore` backends at 1k / 10k / 100k datom corpora will be reported in §6 of the camera-ready; they are `[TBD]` in this draft (see §6.6).

### 5.2 Effect (Phase 1 — shipped)

The Effect module implements a handler-stack runtime. Handlers are declared in EDN with clauses keyed by operation. The canonical stack for regulated domains is:

```
audit → policy → dry-run → cache → retry → rate-limit → raw
```

Each handler is a pure function over the operation, arguments, and continuation; `audit` emits a datom into the Fact module, chaining via `prev-hash` for Merkle integrity. Policies are declarative EDN interpreted by a ~200-line evaluator (`policy_eval.py`, measured) supporting principal attributes, op-matching, and conditional effects (`:op=`, `:op-in`, `:mode=`, `:and`, `:or`, `:not`, `:contains?`, `:matches?`). The `Runtime.is_well_formed` and `Runtime.uncovered_ops` functions (§4.2) are part of the public API.

### 5.3 Spec (Phase 1 — shipped)

The Spec module provides Malli-equivalent functionality in Python: predicate-generator pairs, composable registry, conformance. Specs are EDN data. A generative testing harness produces example instances for every registered spec; failed conforms generate spec-error messages used as self-healing retry hints to the LLM (`spec.explain_for_llm`, §4.7). Specs attach to every boundary. Ten canonical specs are registered, including `:persistence.fact/datom`, `:persistence.effect/audit-entry`, `:persistence.replay/trajectory`, `:persistence.plan/node`, and `:persistence.plan/skill` — the last two registered ahead of the Plan module.

### 5.4 Replay (Phase 1 — shipped)

The Replay module records trajectories as sequences of effect datoms and implements the replay operator from §4.5. A replay handler intercepts effects during branching, returning cached responses indexed by args-hash. DPO pairs are to be extracted when paired trajectories differ in outcome beyond a configurable threshold: prefix must match exactly; suffix divergence feeds the `chosen`/`rejected` dataset automatically. `gen_regression_test(trajectory)` emits a pytest-source snapshot-test string that asserts against the loaded trajectory (this is honest snapshot-replay rather than agent-re-run; full regression re-execution is Phase 2). The byte-identical NO-OP determinism invariant (§4.5 corollary) is the headline test result for this module.

### 5.5 Plan (Phase 2.B — shipped; Phase 2.C — designed)

The Plan module v0.2.0a1 (merged to `main` at commit `b459fe5`, tagged 2026-04-24) ships a minimal parse/walk/spec surface over the uniform `[tag {attrs} *children]` EDN AST covering all 16 registered node kinds (`:seq`, `:par`, `:choice`, `:case`, `:loop`, `:race`, `:let`, `:branch`, `:ref`, `:tool-call`, `:llm-call`, `:code`, `:checkpoint`, `:reflect`, `:verify`, `:call-skill`). Four Python files (`_ast.py`, `_errors.py`, `_parse.py`, `_interpret.py`) implement: a frozen-slots `Node` dataclass with content-addressed `id` (§4.4 Proposition 5), an `edn_format`-backed parser with optional alias lowering (`:phase`/`:workstream` → `:seq` for reading track plans), a symmetric canonical unparser that round-trips byte-identically for canonical inputs, a depth-first `walk(node, visitor) -> list[str]` trace primitive, and `PlanSpecError`-wrapped conformance against `:persistence.plan/node`. Attr keys are plain strings internally (no leading colon), re-prefixed at the spec/emit boundary; user-supplied top-level `:id` is stripped at parse so that content-addressing is a function of the structural content alone. NaN/Inf in attrs are rejected at `Node.id` computation (`allow_nan=False`) so that two semantically-distinct nodes cannot hash-collide via non-finite floats.

The v0.2.0a1 release passed an ARIS gate (R1 Correctness 8.2 / R2 Rigor 7.4→8.8 after an 8-commit fix-pass / R3 Composability 8.3; R4 Research skipped for lack of external-system wiring at this scope), with the R2 fix-pass closing seven rigor MAJORs including a co-located `:id`-clobber defect (user attrs entering the canonical form unchecked) and a 64→128-bit identity-width argument. Subsequent v0.2.0a2 and v0.2.0a3 patches landed the R3 hardening items and closed a reserved-attr-key Prop-5 round-trip falsifier discovered by hypothesis at `max_examples=200` (construct-vs-parse asymmetry: user-supplied `id` / `:id` attr keys were silently stripped at parse but accepted at construction — now rejected at both sites). 172 plan-module tests (7 xfailed) ship alongside the full-repo suite of **797 passed + 7 xfailed**. Test layout: `test_ast.py` (node construction + id determinism + attr-key shape + descendant propagation), `test_parse.py` (all-kind parse + malformed + spec validation + user-id stripping), `test_interpret.py` (walk order + visitor + unimplemented-kind guards), `test_meta_target.py` (the Plan module parses the conductor track's own `plan.edn`), `test_misc.py` (unicode, deep nesting, round-trip id stability), `test_property.py` (six hypothesis properties: two headline at `max_examples=200` — round-trip id preservation and descendant-mutation propagation — plus four supporting at `max_examples=50`).

**Phase 2.C (designed, not shipped).** The execution runtime adds a `fold(node, reducer, init) -> (acc, trace)` primitive that supersedes `walk` for speculation, rollback, and checkpointing; an effect-dispatched evaluator emitting OpenTelemetry spans keyed by `node.id`; the optimizer ladder (MIPROv2 on `:llm-call` leaves delegating to DSPy, UCT-MCTS on `:choice` / `:branch` with Pareto dominance on `{success, latency, cost}`, evolutionary search on topology with LLM-synthesized mutations); and the 4-gate skill-promotion pipeline of §4.4. Finetuning is the final tier for stable hot skills with ≥ 1000 uses.

**Hardening-track status honesty.** Four items shipped across v0.2.0a2 (commit `7bac436`) and v0.3.0a1 (commit `be7e37f`) and are no longer future work: (i) a shared `persistence.spec.SpecError` base class so `PlanSpecError` composes with future sibling errors (R3-M2); (ii) a `:original-tag` escape hatch preserving aliased tags for MCTS/optimizer consumers that need the lossless-read path (R3-M3); (iii) an explicit schema-evolution contract documenting how ids change when new default-valued attrs are introduced on existing node kinds (R3-M5); (iv) a `Node.id` coercion registry for non-JSON-native types covering `datetime` / `date` / `bytes` / `Decimal` / `UUID` / `frozenset` / `edn_format.Symbol` (R3-M4), shipped as a static-plus-manifest registry with `PLAN_CANONICAL_VERSION = 1` introduced — coercion is id-time-only so `node.attrs` keeps the author's input while the canonical form sees the deterministic projection. Two items remain open and are Phase-2.C work: (i) the `fold()` primitive itself (R3-M1); (ii) substrate-wide 128-bit id-width consistency across Fact, Replay, and Plan. A separate v0.2.0a3 patch (commit `b9cbf37`) closed a Prop-5 round-trip falsifier: hypothesis at `max_examples=200` generated a `Node` whose attr map included a user-supplied `id` key; the parser stripped it at parse but the constructor had accepted it, producing `parse(unparse(n)).id ≠ n.id` — reserved `id` / `:id` attr keys are now rejected at both construction and parse.

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

The abstract submission (2026-06-09) reports the Phase-1 shipped artifact with formal properties (§4.2 Prop 2; §4.5 Corollary) checked in the bundled test suite (797 passed + 7 xfailed, `pytest -q` from a clean clone). The numeric evaluations below are a **Reproduction Plan**: each subsection below names the harness, the dataset, the license, and the intended submission point (abstract vs. camera-ready). Tables marked [TBD] carry target numbers only; measured numbers land in the camera-ready (2026-07-20) once Phase 2 verticals and the per-step-rng-recording replay extension are in place.

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

This evaluation has two components. The *byte-identical NO-OP* property (§4.5 Corollary) is **already testable on the Phase 1 artifact** — `tests/replay/test_determinism.py::test_noop_intervention_produces_byte_identical_trajectory` passes; we cite it directly in the abstract as the structural baseline stronger than CAMO's statistical aligned-randomness. The *distributional CAMO protocol* — 1000 paired rollouts with single-variable interventions, measuring prefix alignment (Hamming distance from factual), intervention faithfulness (probability the counterfactual at step $k$ reflects the intervention), and suffix variance over 100 re-replays — requires (i) the per-step rng-state recording extension to the replay engine (Phase 2) and (ii) a meaningful LLM budget for stochastic agents. Both are feasible within the 2026-06-09 → 2026-07-20 camera-ready window if scoped to a single base agent.

**Reproduction Plan.** Abstract ships the NO-OP corollary. Camera-ready ships the full 1000-trajectory table on a toy agent with honest per-step rng recording and, if budget permits, a scaled-down 100-trajectory table on Claude Haiku as the stochastic agent.

### 6.3 Regulator-replay benchmark — 50 synthetic trajectories (novel)

We propose **regulator-replay fidelity**, a new benchmark. Given an agent decision trajectory produced in a regulated domain, can a third-party auditor — given only the datom log and the plan AST — deterministically reconstruct the production decision? The metric is the fraction of decisions whose reconstruction matches production byte-for-byte under `effect.canonical.canonical_dumps` serialization.

**Scope (rescoped from the v0.1 draft).** For this paper we target a **50-trajectory synthetic project-finance corpus**, not a 200-trajectory production corpus. The synthesis pipeline runs a BankabilityAI-shaped scoring agent (WACC, gearing, concession-fee, sector classification) over synthetic inputs, persists the resulting trajectories through the Phase 1 Fact module, and hands the log to an independent reconstruction script. The dataset generator, the reconstruction harness, and the 50-trajectory corpus will be released together under **CC-BY-4.0** before camera-ready. Full production-scale evaluation is deferred to an extended-version companion paper.

**Why this rescope.** This is the paper's flagship novelty. A protocol proposal without numbers would collapse the contribution from "we introduce and evaluate a new benchmark" to "we propose a benchmark." Fifty synthetic trajectories is the minimum tractable scope that keeps "evaluate" in the contribution.

**Symbolic-neural coupling.** Unlike pure-trace replay approaches (CAMO, AgentHER), regulator-replay requires the *symbolic plan AST at decision time* to be reconstructible alongside the neural effect trace; the benchmark measures this symbolic-neural joint reconstruction as a single fidelity metric. A reconstruction counts as faithful only when both the AST Merkle-id path and the effect-trace canonical form recover byte-identically from the log.

| Regulator-replay configuration | Factual deterministic | Counterfactual byte-identity @ NO-OP | Reconstruction fidelity |
|---|:---:|:---:|:---:|
| 50 synthetic PF trajectories, DictProjection | **[TBD — camera-ready]** | **[TBD — target ≥ 99%; hypothesis H1]** | **[TBD — camera-ready]** |

**Hypothesis H1.** Reconstruction fidelity ≥ 99% on the 50-trajectory synthetic corpus; deviations diagnostic of catalog well-formedness (non-determinism admitted into $K$ during trajectory synthesis) rather than substrate limits. The 1% gap, where present, is attributable to a gap in the Phase-1 well-formedness check rather than the content-addressed audit chain.

### 6.4 *(Removed from this paper.)*

The Plan-optimization comparison (HotpotQA, Replayed Binance 4h trades, project-finance scoring) requires the Plan module, which is Phase 2. It is therefore out of scope for this paper and will be the core contribution of a Phase-2 companion paper once the Plan module ships.

### 6.5 Case studies — one named deployment, three anonymized vignettes

**Case B — Adaptive Trader v2 (named deployment, reported with numbers).** Case B exercises Persistence on a neurosymbolic trading agent where the *playbook AST* (symbolic: entry conditions, regime classifiers, risk gates) composes with a *neural decision layer* (Claude-as-sole-decision-maker) through an effect-captured non-determinism seam. Every decision writes a datom; every playbook version is content-addressed; every counterfactual replay walks the symbolic tree while re-sampling the neural leaves.

```edn
;; Adaptive Trader v2 playbook AST (excerpt)
[:seq {:regime "trend-following"}
  [:llm-call {:tool "market-classifier" :model "claude-opus"}
    [:context {:window "4h" :symbols ["BTCUSDT" "ETHUSDT"]}]]
  [:verify {:gate "risk-limit" :max-pos-pct 0.20}]
  [:branch {:on :regime-classification}
    [:case {:when "trend"}
      [:llm-call {:tool "entry-sizer" :playbook "momentum-v7"}]]
    [:case {:when "range"}
      [:llm-call {:tool "entry-sizer" :playbook "mean-revert-v3"}]]]
  [:effect {:audit true :kill-switch :position-limit}]]
```

Every node in this excerpt is content-addressed by Proposition 5; the neural leaves (`:llm-call` nodes) compose with the symbolic control flow (`:seq`, `:branch`, `:case`, `:verify`) under the effect handler stack of §4.2. A single decision writes a datom whose payload includes the playbook's root `:id`, making the audit trail lineage-complete under structural edits.

Concretely, *Adaptive Trader v2* is a production cryptocurrency trading agent running on Binance Futures with a $400 USDT live-capital cap and ARIS-reviewed risk governance. The factual dry-run baseline, recorded across 13 continuous days immediately preceding the Persistence migration, closed at profit factor 0.43 over 8 entries with -$26.87 PnL — a clear NO-GO signal that motivated the redesign. We instrument every decision through Persistence's effect handler stack (audit, kill-switch, position-limit, ARIS-gate, dry-run), persisting trajectories to the Fact module. A nightly cron runs the Replay module over each losing trajectory with an entry-delay wait-sweep `{0, 5, 10, 15, 30, 60}` minutes. DPO-pair extraction (Phase 1 replay engine) is staged behind the per-step-rng-recording extension (Phase 2), and preliminary prompt-tuning results on the 8-trade baseline are therefore `[TBD — camera-ready]`; the baseline numbers themselves are real. Adaptive Trader v2 serves as the reference deployment throughout this paper because every Persistence invariant — immutable decision trail, effect-captured non-determinism, EDN playbook AST, counterfactual-validated skill promotion — is stress-tested against real-money risk. A forthcoming companion paper will report the extended live-capital evaluation.

| Adaptive Trader v2 (Case B) | Pre-Persistence baseline | With Persistence (Phase 1) |
|---|:---:|:---:|
| Days of dry-run | 13 | [TBD — camera-ready] |
| Entries | 8 | [TBD] |
| Profit factor | 0.43 | [TBD] |
| PnL (USDT) | -26.87 | [TBD] |
| Audit-chain entries per decision | n/a (prose logs) | [TBD] |
| Regulator-replay fidelity on the 8 baseline trajectories | n/a | [TBD — target ≥ 99%] |

**Case A — Project-finance assessment SaaS (anonymized vignette).** A bitemporal migration of a production assessment platform serving development finance institutions. The platform produces regulator-facing bankability scores for infrastructure projects; every assumption in the scoring pipeline (WACC, gearing, concession-fee, sector classification) must be reconstructible under audit. Phase 1 lands the Fact module as the authoritative scoring-assumption log; Phase 2 integrates Plan for the scoring pipeline AST. Numeric reporting deferred to a co-authored extended-version paper once the client IP-sharing agreement closes.

**Case C — Insurance aggregator (anonymized vignette).** A licensed insurance comparator operating under a regulatory regime requiring full quote-issuance traceability. Bitemporal client state resolves compliance queries of the form *"when did the agent learn of carrier price change X, and what quotes did it issue between the change and our ingestion?"* — a bitemporal *exclusive-OR* of valid-time and transaction-time, structurally impossible without both clocks. Phase 1 shipped; Plan-driven quote pipeline is Phase 2. Numeric reporting deferred pending licensure co-disclosure.

**Case D — Hospitality operations (anonymized vignette).** A multi-tenant hotel operations agent connected via WhatsApp. Regression trajectories are auto-generated from customer feedback sessions on the production messaging channel; every pull request is designed to replay all known-failing trajectories through the new handler stack in CI. Phase 1 ships the trajectory capture; full regression re-execution is Phase 2 (see §5.4). Numeric reporting deferred pending operator-side deployment gate.

Case B (Adaptive Trader v2) is the only named deployment in this paper. Case A, C, and D identities are withheld pending client-side co-authorship decisions; their reproducibility artifacts are available on request to NeSy program chairs under NDA.

### 6.6 Reproduction posture

- **Artifact.** The Phase-1 + Phase-2.B artifact (`persistence-os @ v0.1.0a1 + v0.3.0a1`) is bundled with the paper submission. `pytest -q` from a clean clone runs the **797-passed / 7-xfailed** test suite in under one minute.
- **Benchmark timeline.** §6.1 (LongMemEval) and §6.2 (CAMO 1000-trajectory table) ship in the camera-ready (2026-07-20). §6.3 (50-trajectory synthetic regulator-replay) ships in the camera-ready with the generator script. §6.5 Case B (Adaptive Trader v2) ships post-Persistence-migration numbers in the camera-ready.
- **Licensing.** Runtime: AGPL-3. Paper + benchmark harness + regulator-replay dataset: CC-BY-4.0.
- **Abstract submission scope.** At the 2026-06-09 abstract deadline, §6 reports the formal properties (Prop 2 and the §4.5 NO-OP corollary) as *already-checked on the shipped artifact*; all numeric tables carry `[TBD]` honestly.

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
- **Malli-style specs with LLM self-healing hints** — shipped (`persistence.spec`, 186 tests; `explain_for_llm`, §4.7).

Two items we explicitly do *not* claim as Phase 1 shipped are Datalog and Z3. We separate them cleanly from shipped capability:

- **Datalog engine.** The datom model is Datalog-ready — classical Datalog rules over `(e, a, v)` triples sit naturally on top of our query surface — but the Phase 1 query layer is Python list comprehensions, not a rule engine. A Datalog surface (a compact pattern-matching helper over the datom set) is an adjacent system we draw on and is straightforward Phase 2 work.
- **Z3-discharged `verify` leaves.** Proof-of-Thought (Fan et al. 2024) describes a compelling integration point for Z3 at `verify`-node leaves, where plan fragments discharge proof obligations against an SMT solver. This is a future Plan-module feature; no Z3 code ships in Phase 1.

The symbolic substrate we *do* ship — bitemporal log, handler stack with `is_well_formed` / `Unhandled`, Merkle-hashed audit chain, boundary specs with `explain_for_llm` — carries the properties we care about: accountability, composition, formal guarantees, and a concrete LLM-self-healing contract. The neural layer (LLM decisions, embeddings, generative skills) carries expressiveness. This distinguishes Persistence from pure-neural approaches (no guarantees) and pure-symbolic approaches (no expressiveness), and positions it as a candidate substrate for neurosymbolic systems beyond LLM agents, including embodied agents, multi-agent planning, and human-in-the-loop scientific discovery.

---

## 8. Conclusion

We presented Persistence, a cognitive-runtime substrate for accountable neurosymbolic agents. Phase 1 plus the first Phase-2 increment of the reference implementation (v0.1.0a1 + v0.3.0a1, **797 tests green + 7 xfailed**) ships five modules — Fact, Effect, Spec, Replay, Plan (minimal) — demonstrating five substrate-derived capabilities: queryable bitemporal history, counterfactual branching with parent-store isolation, composable policy-gated effects with a machine-checkable well-formedness property, boundary-checked contracts with LLM-self-healing hints, and content-addressed homoiconic plan ASTs with byte-identical round-trip and descendant-propagation identity. Five formal propositions hold on the shipped artifact; the headline two are: well-formedness of any handler stack is decidable in linear time by `Runtime.is_well_formed` and enforced at runtime by `Unhandled`; replay of a NO-OP intervention produces a trajectory whose canonical hash is byte-identical to the factual trajectory's. The Merkle-hashed audit chain (`verify_chain`) shipped and is the regulator-grade foundation on which Case A (project finance) and Case C (insurance) will build in Phase 2. Three further modules (Plan, Txn, REPL) are specified, partially registered in the spec registry ahead of the runtime (a deliberate parse-don't-validate methodology), and scheduled for Phase 2 (2026-Q3).

The core claim is that accountability, counterfactual replay, composable safety, compositional skill learning, multi-agent coordination, and live production steering can be derived from one substrate rather than five — and that the four already shipped are *properties of the log, checkable against the artifact*, not features engineered per agent.

**Future work.** We plan to extend the runtime to (1) a persistent-trie `Store` backend that makes Proposition 1's O(log n) branch share a shipped property, not an idealized-implementation theorem; (2) per-step rng-state recording in the replay engine, which unblocks byte-identical replay for LLM trajectories; (3) ed25519 per-transaction signing via a `sign_transaction` primitive verified in `verify_chain`; (4) a Datalog query surface over the datom set; (5) Z3-discharged `verify` leaves in the Plan-module AST following Proof-of-Thought (Fan et al. 2024); (6) embodied-agent extensions where effects include physical-world interactions with partial observability; and (7) distributed deployment preserving bitemporal semantics across multi-region fact stores.

We release the Phase-1 runtime under AGPL-3, with paper artifacts (benchmark harness, 50-trajectory synthetic regulator-replay dataset) under CC-BY-4.0.

---

## Acknowledgments

[To be populated on submission.]

## References

*(Canonical bibliography to be typeset in the camera-ready; draft references below. Specific percentage claims cited in §2.1, §2.4, §2.5 are to be re-verified against the primary sources.)*

- Andrychowicz, M. et al. (2017). Hindsight Experience Replay. *NeurIPS*.
- Badreddine, S., Garcez, A. d'Avila, Serafini, L., & Spranger, M. (2022). *Logic Tensor Networks.* Artificial Intelligence, 303, 103649.
- Hannecke, M. et al. (2026). Governing Evolving Memory in LLM Agents: The SSGM Framework. arXiv:2603.11768.
- Kambhampati, S. (2024). *Can Large Language Models Reason and Plan?* Annals of the New York Academy of Sciences, 1534(1), 15–18.
- Kurtic, E. et al. (2025). Zep: A Temporal Knowledge Graph Architecture for Agent Memory. arXiv:2501.13956.
- Khattab, O. et al. (2023, ongoing). DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines.
- Liu, B., Jiang, Y., Zhang, X., Liu, Q., Zhang, S., Biswas, J., & Stone, P. (2023). *LLM+P: Empowering Large Language Models with Optimal Planning Proficiency.* arXiv:2304.11477.
- Manhaeve, R., Dumančić, S., Kimmig, A., Demeester, T., & De Raedt, L. (2018). *DeepProbLog: Neural Probabilistic Logic Programming.* *NeurIPS*.
- n1n.ai (2026). Building a Bitemporal Knowledge Graph for LLM Agent Memory — Memento Case Study.
- Memento-Teams (2026). Memento-Skills: Framework for Self-Designing Agents. arXiv:2603.18743.
- Pryor, C., Dickens, C., Augustine, E., Albalak, A., Wang, W. Y., & Getoor, L. (2023). *NeuPSL: Neural Probabilistic Soft Logic.* *IJCAI*.
- Shangyi Cheng et al. (2025). Pangolin: Programming Large Language Models with Algebraic Effects. LMPL 2025.
- Valmeekam, K., Sreedharan, S., Marquez, M., Olmo, A., & Kambhampati, S. (2022). *Large Language Models Still Can't Plan (A Benchmark for LLMs on Planning and Reasoning about Change).* *NeurIPS Foundation Models for Decision Making Workshop*.
- Valmeekam, K., Marquez, M., Sreedharan, S., & Kambhampati, S. (2023). *PlanBench: An Extensible Benchmark for Evaluating Large Language Models on Planning and Reasoning about Change.* *NeurIPS Datasets & Benchmarks Track*.
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

*End of draft v0.6. ARIS R4 corrections applied 2026-04-21; ARIS R5 polish landed 2026-04-21 (W-wire + W-polish2 + W-polish3); Phase 2.B `persistence.plan` v0.2.0a1 shipped 2026-04-24 with v0.2.0a2 + v0.2.0a3 patches landing same-day; v0.3.0a1 (R3-M4 coercion registry, `PLAN_CANONICAL_VERSION = 1`) shipped 2026-04-25 closing the last R3 MAJOR deferred from the v0.2.0a1 ARIS gate; v0.5 refresh pass closed ARIS Round 1 R1+R2+R4 MAJORs (test-count drift 776/151/152 → 783/158/186; `__version__` 0.1.0a1 → 0.2.0a3; AGPL-3 LICENSE added; Prop 5 parenthetical matches v0.2.0a3; R4 framing pass — neurosymbolic thesis first, Case B reframed, KR citations added, §6.3 symbolic-neural coupling clarified); v0.5.1 closed the shared R1+R2 citation MAJOR + Case B EDN excerpt + NeSy positioning sentence; v0.6 promoted the §7.4 NeSy inventory into §1, added Valmeekam 2022 to the §2.3 KR-citation paragraph, and locked the v0.3.0a1 ship into abstract / §1 / §5.5 / §6 / §8. Test counts now reflect 797 passed + 7 xfailed at main `be7e37f`. Evaluation numbers marked [TBD] will be populated in the camera-ready per `conductor/tracks/persistence-os-foundation_20260420/`. Case A, C, and D identities remain anonymized pending client co-authorship agreements; Case B (Adaptive Trader v2) is named.*
