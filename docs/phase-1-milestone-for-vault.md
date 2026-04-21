# Persistence OS — Phase 1 Milestone (for vault sync)

*Self-contained summary safe to ingest into the vault memory palace.*
*Generated 2026-04-21 on completion of Phase 1 build.*

## Topic classification hints (for vault-palace router)

- **District:** AI Box / Infrastructure
- **Building:** Persistence OS
- **Room:** Phase 1 — Foundation shipped
- **Tags:** `#persistence-os` `#nesy-2026` `#phase-1-complete` `#datomic-of-thought` `#bitemporal` `#algebraic-effects` `#counterfactual-replay` `#homoiconic-plans` `#parse-dont-validate` `#adaptive-trader-v2-reference` `#ai-box-substrate`

## One-paragraph summary

Persistence OS — the cognitive substrate under AI Box — shipped Phase 1 on 2026-04-21. Four modules (fact, effect, spec, replay) are merged to main at v0.1.0a1 with 356 tests green. The architecture unifies five research beachheads (Zep/Graphiti bitemporal memory, Pangolin algebraic effects, DSPy declarative programs, Voyager skill libraries, CAMO counterfactual replay) into a single runtime where every piece of agent state — memory, audit, plan, skill, transaction — is an immutable content-addressed bitemporal datom. Built by four parallel agent teams in isolated worktrees over a single session. Targeting NeSy 2026 Phase 2 (paper due 2026-06-16, Lisbon Sept 1-4). Adaptive Trader v2 is the named case study; BankabilityAI, Insurance Comparator, and GuestFlow are anonymized.

## The six invariants

1. Every fact is immutable, temporal, content-addressed.
2. Every action is an effect.
3. Every plan is an EDN AST.
4. Every shared state change is a transaction.
5. Every LLM boundary has a spec.
6. Everything is REPL-live.

## What shipped

| Module | Tests | Role |
|---|---:|---|
| fact   |  65 | bitemporal 8-tuple datom store (Zep/Datomic unified) |
| effect |  92 | Pangolin-style handler stack (15-op catalog, 9 handlers) |
| spec   | 152 | Malli-style boundaries (11 combinators, 10 canonical specs) |
| replay |  47 | CAMO-aligned counterfactual trajectories + DPO extraction |

## Why this matters for AI Box

- **Memory Palace** becomes bitemporal: `/vault-as-of`, counterfactual branches, full audit.
- **Adaptive Trader v2** gets the post-trade counterfactual engine the v2 design called for.
- **BankabilityAI** gets regulator-grade audit chain for free (Merkle-hashed).
- **GuestFlow** regression tests auto-generated from Simo feedback trajectories.
- **Insurance Comparator** MIA compliance queries resolve as bitemporal XOR of valid-time and transaction-time.
- **ModelForge** tornado sensitivity becomes N parallel `branch` queries.
- **Conductor tracks** become executable EDN plan ASTs (the Phase 1 track is itself one).

## Research contribution claim (for NeSy 2026)

A substrate claim, scoped honestly to Phase 1: accountability, counterfactual branching, composable safety, and boundary-checked neurosymbolic contracts are derived properties of one shipped substrate (immutable bitemporal datoms + composable effect handlers + Merkle-hashed audit + Malli-style specs with LLM self-healing hints). Three further derived capabilities — compositional skill learning (Plan), multi-agent coordination (Txn), and live production steering (REPL) — are spec-registered ahead of the runtime and ship in Phase 2 (2026-Q3) without substrate schema changes. Two formal propositions hold on the shipped code: (i) `Runtime.is_well_formed(catalog)` decides handler-stack completeness in linear time, with `Unhandled` enforcing it at runtime; (ii) `trajectory_hash(replay(T, I_noop)) == trajectory_hash(T)` — byte-identical canonical-serialization replay on NO-OP, stronger than CAMO's aspirational aligned-randomness. Individual components have precedent (Zep, Pangolin, DSPy, Voyager, CAMO); the substrate-first composition is the novel unification.

## Privacy & distribution

- Runtime: AGPL-3 at publish time
- Paper + benchmarks + regulator-replay dataset: CC-BY-4.0
- Vertical adapters: private forever (gitignored, client IP)
- Self-hosted on Hostinger VPS; local inference for sensitive paths (Qwen/DeepSeek on RTX 5060 Ti)
- No cloud telemetry, SHA-256 content-hashed provenance + Merkle-chained audit (ed25519 per-transaction signing is Phase 2), never-to-cloud skill visibility

## Links

- Repo: `~/Projects/persistence-os/` (commit head `2c96fb7`)
- Paper: `~/Projects/persistence-os/paper/persistence-nesy-2026-draft.md`
- Conductor track: `~/Projects/ai-box/conductor/tracks/persistence-os-foundation_20260420/`
- Serena memory: `persistence-os/phase-1-complete`
- NeSy 2026 Phase 2 deadline: 2026-06-16 AoE

## Next

- ARIS round 1 self-review on Phase 1 (running as of 2026-04-21)
- Phase 2: Memory Palace retrofit + Adaptive Trader cron + Plan module + Txn module
