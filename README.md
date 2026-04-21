# Persistence

A bitemporal, effect-typed, homoiconic cognitive runtime for accountable neurosymbolic agents.

**Status:** pre-alpha. Research-grade. Active development.
**License intent:** AGPL-3 (runtime) + CC-BY-4.0 (paper & benchmarks). Commercial option for vertical integrators.
**Target publication:** [NeSy 2026](https://2026.nesyconf.org/), Lisbon, 1–4 September 2026.

---

## Thesis

Accountability, counterfactual replay, composable safety, compositional skill learning, multi-agent coordination, and live production steering are **derived properties of one substrate** — not five engineered features. The substrate treats every piece of agent state as an immutable, content-addressed, bitemporal datom. Effects route through a composable handler stack. Plans are EDN abstract syntax trees. Skills are content-addressed AST subtrees. Transactions compose via STM. Specs constrain every boundary. A REPL module exposes live inspection and speculative branching against running agents.

## Shipping status (Phase 1)

**v0.1.0a1 — shipped 2026-04-20, 356 tests green.** Four of the seven modules land in Phase 1:

| Module | Phase 1 | Tests |
|---|:---:|---:|
| `persistence.fact` | shipped | 65 |
| `persistence.effect` | shipped | 92 |
| `persistence.spec` | shipped | 152 |
| `persistence.replay` | shipped | 47 |
| `persistence.plan` | Phase 2 (spec-registered: `:persistence.plan/node`, `:persistence.plan/skill`) | — |
| `persistence.txn` | Phase 2 (designed in `docs/agent*-spec.md`) | — |
| `persistence.repl` | Phase 2 (designed; `as-of` rewind is already usable via Fact) | — |

Phase 1 ships correct semantics on list-backed `InMemoryStore` and `SQLiteStore`; Postgres / Kuzu / mem0 projections, Zstd segments, and ed25519 per-transaction signing are Phase 2.

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
| `persistence.fact`   | Bitemporal 8-tuple datom store. Phase 1: `InMemoryStore` + `SQLiteStore` + `DictProjection`. Phase 2: Postgres log + Kuzu + mem0 projection adapters. |
| `persistence.effect` | Algebraic effect handler stack (15-op catalog, Koka-style named/masked). `Runtime.is_well_formed` + `Unhandled` enforce Proposition 2 at runtime. |
| `persistence.plan`   | (Phase 2) EDN plan AST + skill library + optimizer ladder (MIPROv2 → MCTS → evolutionary → finetune). Phase 1 registers `:persistence.plan/node` and `:persistence.plan/skill` specs ahead of the runtime. |
| `persistence.replay` | Counterfactual trajectory engine + DPO pair extractor. Ships byte-identical NO-OP trajectory hash (§4.5 paper). |
| `persistence.txn`    | (Phase 2) Software transactional memory over Fact-backed refs. |
| `persistence.spec`   | Malli-style boundary contracts (parse-don't-validate) with `explain_for_llm` self-healing hints. |
| `persistence.repl`   | (Phase 2) Live production inspection / edit / rewind / branch. Rewind via `as-of` is already shipped in Phase 1. |

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
└── tests/         ← per-module test suites
```

*(`bench/` — LongMemEval, counterfactual fidelity, regulator-replay — lands in Phase 2 alongside camera-ready paper numbers. Not yet shipped.)*

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
- **Provenance sealing:** Phase 1 seals each datom with a SHA-256 content hash and Merkle-chains audit entries (`verify_chain`). Per-transaction ed25519 signing is Phase 2 work.
- **Skill visibility:** `:visibility :private` enforces never-to-cloud on skill-library entries.
- **Vertical adapters stay private.** The runtime is AGPL-3; your `verticals/*` implementations are yours.

## Build status

Tracked in `ai-box/conductor/tracks/persistence-os-foundation_20260420/`:

- **Phase 0 (bootstrap):** done
- **Phase 1 (fact + effect + spec + replay):** **done** (v0.1.0a1, 2026-04-20, 356 tests green)
- **Phase 2 (plan + txn + repl + Postgres/Kuzu/mem0 projections + per-step rng recording + ed25519):** scheduled 2026-Q3
- **Phase 3 (optimizer ladder + REPL UI):** scheduled 2026-Q4
- **Phase 4 (LongMemEval + CAMO + regulator-replay harnesses + camera-ready paper numbers):** scheduled for NeSy 2026 camera-ready (2026-07-20)

Paper: `paper/persistence-nesy-2026-draft.md` v0.2 (ARIS Round-1 corrections applied).

## Related work beachheads we unify

- Zep / Graphiti (bitemporal agent memory)
- Pangolin / Wang 2025 (algebraic effects for LLM scripts)
- DSPy (declarative agent programs, MIPROv2 optimization)
- Voyager / Memento-Skills (executable skill libraries)
- CAMO / AgentHER / AgenTracer (counterfactual replay via aligned randomness)

See `paper/persistence-nesy-2026-draft.md` §2 for the full survey and positioning.

## Getting started

```bash
git clone git@github.com:nawsaafa/persistence-os
cd persistence-os
pip install -e .
pytest -q      # runs the 356-test suite (Phase 1: fact + effect + spec + replay)
```

`make dev` / `make bench` targets and a `bench/` directory land in Phase 2 alongside the camera-ready paper numbers.

---

*"The database is a value." — Rich Hickey. Persistence applies this idea one level up: the agent's cognition is a value.*
