# Persistence

A bitemporal, effect-typed, homoiconic cognitive runtime for accountable neurosymbolic agents.

**Status:** pre-alpha. Research-grade. Active development.
**License intent:** AGPL-3 (runtime) + CC-BY-4.0 (paper & benchmarks). Commercial option for vertical integrators.
**Target publication:** [NeSy 2026](https://2026.nesyconf.org/), Lisbon, 1–4 September 2026.

---

## Thesis

Accountability, counterfactual replay, composable safety, compositional skill learning, multi-agent coordination, and live production steering are **derived properties of one substrate** — not five engineered features. The substrate treats every piece of agent state as an immutable, content-addressed, bitemporal datom. Effects route through a composable handler stack. Plans are EDN abstract syntax trees. Skills are content-addressed AST subtrees. Transactions compose via STM. Specs constrain every boundary. A REPL module exposes live inspection and speculative branching against running agents.

## The six invariants

1. Every fact is immutable, temporal, content-addressed.
2. Every action is an effect.
3. Every plan is an EDN AST.
4. Every shared state change is a transaction.
5. Every LLM boundary has a spec.
6. Everything is REPL-live.

## The seven modules

| Module | Purpose |
|---|---|
| `persistence.fact`   | Bitemporal 8-tuple datom store (Postgres + Kuzu + mem0 projections) |
| `persistence.effect` | Algebraic effect handler stack (15-op catalog, Koka-style named/masked) |
| `persistence.plan`   | EDN plan AST + skill library + optimizer ladder (MIPROv2 → MCTS → evolutionary → finetune) |
| `persistence.replay` | Counterfactual trajectory engine + DPO pair extractor |
| `persistence.txn`    | Software transactional memory over Fact-backed refs |
| `persistence.spec`   | Malli-style boundary contracts (parse-don't-validate) |
| `persistence.repl`   | Live production inspection / edit / rewind / branch |

## Repository layout

```
persistence-os/
├── paper/         ← NeSy 2026 paper draft + artifacts
├── src/
│   └── persistence/
│       ├── fact/      ← Module 1
│       ├── effect/    ← Module 2
│       ├── plan/      ← Module 3
│       ├── replay/    ← Module 4
│       ├── txn/       ← Module 5
│       ├── spec/      ← Module 6
│       └── repl/      ← Module 7
├── prototypes/    ← research-spec reference prototypes (Python)
├── docs/          ← architecture specs (agent{1..4}-*.md)
├── verticals/     ← adapter scaffolds per vertical (PRIVATE — gitignored by default)
├── tests/         ← per-module test suites
└── bench/         ← LongMemEval, counterfactual fidelity, regulator-replay, plan-opt
```

## Relationship to ai-box

Persistence is a **standalone repository** referenced from `ai-box/` as a git submodule at `ai-box/vendor/persistence-os`. This keeps the runtime open-sourceable while `ai-box` itself (the monorepo containing the vertical integrations, conductor tracks, and operator-specific state) remains private.

```bash
# from ai-box/ root
git submodule add git@github.com:nawsaafa/persistence-os vendor/persistence-os
git submodule update --init --recursive
```

## Privacy posture

- **Local-first:** authoritative datom log runs on operator-controlled infrastructure.
- **Inference routing:** `:privacy :local` attribute on `:llm-call` nodes routes inference to local models (Qwen, DeepSeek, Llama). Only `:privacy :public` calls reach cloud vendors.
- **No telemetry egress:** OpenTelemetry spans go to an operator-controlled collector.
- **Signed provenance:** ed25519 per-transaction.
- **Skill visibility:** `:visibility :private` enforces never-to-cloud on skill-library entries.
- **Vertical adapters stay private.** The runtime is AGPL-3; your `verticals/*` implementations are yours.

## Build status

Tracked in `ai-box/conductor/tracks/persistence-os-foundation_20260420/`:

- **Phase 0 (bootstrap):** in progress
- **Phase 1 (four parallel workstreams):** pending
- **Phase 2 (first integrations):** pending
- **Phase 3 (optimizer + REPL):** pending
- **Phase 4 (benchmarks + paper):** pending

Target: Persistence v0.1 + paper v1.0 within 30 days.

## Related work beachheads we unify

- Zep / Graphiti (bitemporal agent memory)
- Pangolin / Wang 2025 (algebraic effects for LLM scripts)
- DSPy (declarative agent programs, MIPROv2 optimization)
- Voyager / Memento-Skills (executable skill libraries)
- CAMO / AgentHER / AgenTracer (counterfactual replay via aligned randomness)

See `paper/persistence-nesy-2026-draft.md` §2 for the full survey and positioning.

## Getting started (placeholder)

```bash
# after phase 0 completes:
git clone git@github.com:nawsaafa/persistence-os
cd persistence-os
make dev       # stands up Postgres + Kuzu + local venv
make test      # runs per-module test suites
make bench     # runs LongMemEval + counterfactual-fidelity + regulator-replay
```

---

*"The database is a value." — Rich Hickey. Persistence applies this idea one level up: the agent's cognition is a value.*
