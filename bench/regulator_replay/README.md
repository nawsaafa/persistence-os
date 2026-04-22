# bench/regulator_replay — Synthetic Regulator-Replay Benchmark

**Status:** SCAFFOLD. Not yet operational. Paper §6.3 cites this directory as the walkthrough artifact; the scaffold exists so the citation path is real, not phantom. Implementation lands alongside Phase 4 of the `memory-palace-bitemporal-retrofit` track (~2026-05) and the paper v1.0 pass.

**Scope:** this directory stays inside `persistence-os/`. It is deliberately decoupled from the production `ai-box` vault — the benchmark runs against an in-memory `persistence.fact.DB` + in-process Qdrant mock, not the live Juba OS deployment. This is the one-way dependency pin R3 caught in ARIS round 1.

## What the benchmark does

Simulates a regulator's post-hoc audit of an agent session:

1. **Generate a synthetic agent trajectory** — 10 trajectories × ~40 `:vault/remember` + `:vault/recall` + `:llm/call` ops each. Each trajectory includes 1-2 retractions and at least one tier-changing correction. Deterministic under a fixed seed.
2. **Record each trajectory as an audit log** — wrapped in `persistence.effect.Runtime` with the audit handler; every `:vault/recall` entry carries `vault_snapshot_tx`.
3. **Run reconstruction** — for a given trajectory, replay the audit log from the first entry; at each `:vault/recall`, recompute the query against `db.as_of(tx_time_of(vault_snapshot_tx))`, assert `result_hash` matches.
4. **Report** — per-trajectory reconstruction pass/fail; aggregate pass rate; latency per entry.

Paper claim: "Given audit log, reconstruct N%." The harness measures N and prints the tamper-detection breakdown (which entries, if flipped, break the chain).

## Layout (planned, not yet populated)

```
bench/regulator_replay/
├── README.md                (this file)
├── generator.py             (stub — synthetic trajectory generator, deterministic seeded)
├── harness.py               (stub — reconstruction runner + metrics)
├── trajectories/            (empty — generated fixtures live here)
│   └── .gitkeep
└── reports/                 (empty — run outputs land here)
    └── .gitkeep
```

## Non-goals

- Running against live Juba OS vault data. Benchmark is synthetic, reproducible, and shareable.
- Claiming it reflects real-world complexity. It reflects *the shape* of real audit replay; production-volume stress testing is a separate artifact.
- Any coupling to `ai-box/`. Importing from `ai-box` from this directory is a lint failure.

## Current overclaim-hygiene notes

Paper v0.3 §6.3 cites this dataset as if it were live. Once `generator.py` and `harness.py` are functional (Phase 4 of memory-palace-bitemporal-retrofit), paper v1.0 can promote the citation from "planned" to "included." Until then, paper language is "the walkthrough artifact is the synthetic-replay dataset scaffolded at `bench/regulator_replay/`; generator-harness implementation lands with the memory-palace retrofit."

## References

- Design doc §6.3 in `paper/persistence-nesy-2026-draft.md`
- ARIS round 1 R4 critical finding (paper-vs-code drift) — `docs/aris-bitemporal-design-round-1/R4-research.md`
- Parent retrofit design: `docs/plans/2026-04-22-memory-palace-bitemporal-design.md`
