# persistence-os

**Branch any audit point. Recover any client to a known-good state.
No detail is ever overwritten.**

The bitemporal substrate underneath [Mimir](https://github.com/mimir-os/mimir-os) —
the always-on personal agent operating system. Open-core. AGPL-3. Used in
production via the Mimir Docker image; available standalone for teams building
their own multi-agent runtimes.

Six invariants, seven modules: every fact is an immutable, content-addressed,
bitemporal datom; every action is an effect; every plan is an EDN AST; every
shared state change is a transaction; every LLM boundary has a spec; everything
is REPL-live.

**Status:** v0.8.5a1 substrate-completion bundle (2093 passing / 33 skipped /
8 xfailed on `feat/v0.9-persistence-coder@0c54e25`). v0.9.0a1 alpha targets
June 2026. PyPI release ships with Mimir v0.1.
**License:** AGPL-3 (runtime) + CC-BY-4.0 (paper & benchmarks). Commercial option
for vertical integrators.
**Positioning:** substrate for **team knowledge work + accountable agentic operations.**
Not a creator-factory or AI-avatar enabler — durability and steerability are the axis,
not output volume.

---

## Skill systems live for one execution. persistence-os makes them live across executions.

Every Claude Code agent today either runs without memory (cold start every session)
or with hand-rolled context folders someone has to maintain. persistence-os ships the
primitives that make a skill system durable across runs:

- **Serializable transactions** on the agent's working state (`s.txn.fold` / `s.txn.fork` — speculate, score, commit only the chosen branch).
- **Tamper-evident audit chains** Merkle-linked across processes (`verify_chain` over the per-transaction commit UUIDs).
- **Forward-only schema migrations** on the bitemporal datom log.
- **Plan editing** that lets an agent rewrite its own next step (`s.plan.edit_step` + an MCTS-driven optimizer over the plan AST).

The product is the substrate. `persistence-coder` (Phase 2 of the product roadmap;
skeleton shipped at `c6d448e` on `feat/v0.9-persistence-coder`) is the first vertical
demo.

## Thesis

Accountability, counterfactual replay, composable safety, compositional skill learning,
multi-agent coordination, and live production steering are **derived properties of one
substrate** — not five engineered features. The substrate treats every piece of agent
state as an immutable, content-addressed, bitemporal datom. Effects route through a
composable handler stack. Plans are EDN abstract syntax trees. Skills are
content-addressed AST subtrees. Transactions compose via STM. Specs constrain every
boundary. A REPL module exposes live inspection and speculative branching against
running agents.

## The six invariants

1. Every fact is immutable, temporal, content-addressed.
2. Every action is an effect.
3. Every plan is an EDN AST.
4. Every shared state change is a transaction.
5. Every LLM boundary has a spec.
6. Everything is REPL-live.

## The seven modules

| Module | Status | Purpose |
|---|---|---|
| `persistence.fact`   | shipped | Bitemporal 8-tuple datom store. `InMemoryStore` + `SQLiteStore` + `DictProjection` (Phase 1). Postgres SERIALIZABLE multi-process backbone with cross-process audit-chain Merkle continuity (v0.8.0a1, PG1–PG6 + PG-W1). |
| `persistence.effect` | shipped | Algebraic effect handler stack (Koka-style named/masked). `Runtime.is_well_formed` + `Unhandled` enforce Proposition 2 at runtime. `:code/exec` capability-denial soft-isolation guard added in Phase 2.0b — see hard-rule note below. |
| `persistence.spec`   | shipped | Malli-style boundary contracts (parse-don't-validate) with `explain_for_llm` self-healing hints. |
| `persistence.replay` | shipped | Counterfactual trajectory engine + DPO pair extractor. Byte-identical NO-OP trajectory hash (§4.5 paper). |
| `persistence.txn`    | shipped | Software transactional memory over Fact-backed refs. `DB.fold` (foldl + chosen-marker) and `DB.fork` (speculate-rollback-pick with per-branch child-txn rollback) are co-existing primitives — Phase 2.0c + 2.0c-ext. |
| `persistence.plan`   | shipped | EDN plan AST + skill library + Plan Edit API (Phase 2.0a) + curated `s.plan` SDK namespace (Phase 2.0c-prime) + MCTS-driven optimizer ladder. |
| `persistence.repl`   | scaffolded; op handlers Phase 2 | Live production inspection / edit / rewind / branch. Caps + tokens + sessions + WSServer + JSON-RPC envelope are scaffolded. Op handlers land with the `persistence-coder` MVP. Rewind via `as-of` is already usable through `persistence.fact`. |

The curated SDK facade (`persistence.sdk.Substrate`) exposes the modules above as
`s.fact` / `s.txn` / `s.plan` / `s.replay` / `s.spec` / `s.escape`. Stability decorators
(`@experimental` / `@stable(since=...)`) gate every public name.

> **Hard rule on `:code/exec`:** the v0.9 sandbox is **capability-denial-not-detection**
> (deny-list + child interpreter + setrlimit). It is NOT a confidentiality boundary.
> The real-OS-sandbox track is queued separately for v0.9.x, with the F4 xfail-strict
> marker at `tests/effect/test_code_exec.py` as the falsifiable acceptance signal. ADR-17
> in `docs/plans/2026-04-29-adapter-sdk-contract-design.md` makes the same non-goal
> explicit for first-party MCP in v0.8.

## Background — three independent April-2026 analyses converged the same week

In April 2026, Simon Scrapes' "skill systems" pattern (FD53kEpLh9c) made the
architectural argument: skills must compose into chains, wrapped by an orchestrator
skill that specifies architecture / inputs / handoffs / HITL / display. He pointed at
OpenClaw and Hermes as agentic-OS substrates — *"a skill system is exactly the same way
that OpenClaw and Hermes are able to actually execute tasks on your behalf."*
persistence-os is the **memory** layer for that pattern: the substrate that lets the
orchestrator's instruction set carry forward what worked, what failed, and what to try
next across runs.

Three independent analyses converged the same week:

- **Chase 7-levels (2026-04-29).** L7 (autonomous agents / AI-avatar farms) is brand-damaging for personal/creator use *but* is the deliverable for B2B team-knowledge-work positioning. Drives the explicit "team knowledge work, not creator factory" framing above.
- **Howie Liu HyperAgent fit (2026-04-30 AM).** Ship persistence primitives as Anthropic Skills. Drives the Phase 7 distribution channel (`persistence-orchestrate` meta-skill, blocked by the Phase 2.4c lockfile snapshot of `persistence-coder`).
- **Simon Scrapes skill systems (2026-04-30 PM).** Orchestrator-skill compositional architecture. Drives the curated SDK facade's packaging into a chainable Anthropic Skill.

Research artefacts: `~/Projects/research-output-2026-04-30-simon-scrapes-skills.md` and
the Simon Scrapes video transcript at `~/Projects/transcript-FD53kEpLh9c.txt`.

## Repository layout

```
persistence-os/
├── paper/         ← formal substrate spec (9 sections; NeSy 2026 deadline dropped, paper is no longer time-pressured)
├── src/
│   └── persistence/
│       ├── fact/      ← Module 1
│       ├── effect/    ← Module 2
│       ├── plan/      ← Module 3
│       ├── replay/    ← Module 4
│       ├── txn/       ← Module 5
│       ├── spec/      ← Module 6
│       ├── repl/      ← Module 7 (scaffolded; op handlers Phase 2)
│       ├── store/     ← storage backends (in-memory, SQLite, Postgres SERIALIZABLE)
│       └── sdk/       ← curated facade (`Substrate` + URI-dispatched adapters)
├── docs/          ← architecture specs, ADRs, design docs (incl. 2026-04-29 adapter SDK contract w/ ADR-17)
├── bench/         ← LongMemEval, counterfactual fidelity, regulator-replay (Phase 2)
├── tests/         ← per-module test suites
└── verticals/     ← adapter scaffolds per vertical (PRIVATE — gitignored by default)
```

## Relationship to ai-box

Persistence is a **standalone repository** referenced from `ai-box/` as a git submodule
at `ai-box/vendor/persistence-os`. This keeps the runtime open-sourceable while `ai-box`
itself (the monorepo containing the vertical integrations, conductor tracks, and
operator-specific state) remains private.

```bash
# from ai-box/ root
git submodule add git@github.com:nawsaafa/persistence-os vendor/persistence-os
git submodule update --init --recursive
```

The active product track lives at
`~/Projects/ai-box/conductor/tracks/persistence-os-product_20260429/` (STATUS append
only; do not modify the track file directly).

## Privacy posture

- **Local-first:** authoritative datom log runs on operator-controlled infrastructure.
- **Inference routing:** `:privacy :local` attribute on `:llm-call` nodes routes inference to local models (Qwen, DeepSeek, Llama). Only `:privacy :public` calls reach cloud vendors.
- **No telemetry egress:** OpenTelemetry spans go to an operator-controlled collector.
- **Provenance sealing:** each datom is sealed with a SHA-256 content hash; audit entries are Merkle-chained per-transaction (`verify_chain`). Multi-process Postgres SERIALIZABLE backbone preserves chain continuity across processes (v0.8.0a1, ARIS R2 PASS at mean 8.81 / min 8.0).
- **Skill visibility:** `:visibility :private` enforces never-to-cloud on skill-library entries.
- **Confidentiality posture, v0.8 / v0.9:** see ADR-17 + the `:code/exec` hard rule above. The substrate currently makes no confidentiality guarantees on stored content; v0.9 privacy-arch is the proper venue for that work.
- **Vertical adapters stay private.** The runtime is AGPL-3; your `verticals/*` implementations are yours.

## Build status

Tracked in `~/Projects/ai-box/conductor/tracks/persistence-os-product_20260429/STATUS.md`.

- **Phase 0 (bootstrap):** done.
- **Phase 1 (fact + effect + spec + replay):** done — v0.1.0a1 (2026-04-20).
- **Phase 1 closure (Adapter SDK + multi-process Postgres SERIALIZABLE):** done — v0.8.0a1 (2026-04-30, ARIS R2 PASS at mean 8.81 / min 8.0).
- **Substrate completion (Plan Edit API + `:code/exec` soft-isolation + `s.txn.fold` + `DB.fork` + `s.plan` SDK namespace + audit-stack/atomicity/sandbox-rescope):** done — `v0.8.5a1` annotated sub-tag on `acb237c`, branch `feat/v0.9-2.0d-completion`, NOT pushed (internal-alpha only). ARIS R2 hard-mode codex closed at 6.4 with W3 honest-rescope of `:code/exec` accepted as architecturally correct.
- **Phase 2 of product roadmap (`persistence-coder` MVP, weeks 4–8):** in progress — Phase 2.1a skeleton shipped at `c6d448e` (`feat/v0.9-persistence-coder`); Phase 2.1b → 2.4c remaining. Phase 2.4c (lockfile snapshot) is the gate for Phase 7 of the cross-project skill-systems-integration track.
- **v0.9.x real-OS-sandbox:** queued separately, F4 xfail-strict acceptance signal in `tests/effect/test_code_exec.py`.
- **Phase 7 (`persistence-orchestrate` meta-skill, GA target alongside `v0.9.0a1`):** blocked by Phase 2.4c.

The paper in `paper/` (live source at `paper/tex/persistence-nesy-2026.tex`, rendered
PDF alongside) stands as the formal substrate spec. The NeSy 2026 publication deadline
has been dropped to remove time pressure; the paper is maintained as living spec, not
as a venue artifact.

## Related work beachheads we unify

- Zep / Graphiti (bitemporal agent memory)
- Pangolin / Wang 2025 (algebraic effects for LLM scripts)
- DSPy (declarative agent programs, MIPROv2 optimization)
- Voyager / Memento-Skills (executable skill libraries)
- CAMO / AgentHER / AgenTracer (counterfactual replay via aligned randomness)

See the paper §2 for the full survey and positioning.

## Getting started

```bash
git clone git@github.com:nawsaafa/persistence-os
cd persistence-os
pip install -e .
pytest -q      # 2034+ tests on the substrate-completion branch
```

Substrate work runs in dedicated worktrees under `~/Projects/persistence-os-worktrees/`.
For session orientation in fresh Claude Code agents, see `CLAUDE.md`.

---

*"The database is a value." — Rich Hickey. Persistence applies this idea one level up:
the agent's cognition is a value.*
