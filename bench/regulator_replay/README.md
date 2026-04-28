# bench/regulator_replay — Synthetic Regulator-Replay Benchmark

**Status:** READY FOR EVAL BLOCK 2026-05-28 against frozen `v1.0.0` substrate. Operational against current `v0.7.0a1` substrate at 50/50 byte-identity, audit-reconstructibility = 1.000, tamper-matrix 400/400 caught.

**License:** the corpus, generator, harness, and reports are released under [CC BY 4.0](LICENSE). See attribution clause at the bottom of `LICENSE`.

**Scope:** this directory stays inside `persistence-os/`. The benchmark runs against an in-memory `persistence.fact.DB` + in-process effect handlers — never the live `ai-box` vault. This is the one-way dependency pin R3 caught in ARIS round 1.

## What the benchmark does

Simulates a regulator's post-hoc audit of an agent session.

1. **Generate** 50 synthetic trajectories (`seeds 0..49`), each ~40 mixed `:vault/remember` + `:vault/recall` + `:llm/call` ops. Every trajectory contains ≥ 1 retraction, ≥ 1 tier-change, and ≥ 1 retroactive correction (`force_retroactive=True`).
2. **Record** each op via `persistence.effect.Runtime` + the audit handler. Output per trajectory is two files:
    - `trajectories/<seed>.audit.jsonl` — the Merkle-chained `AuditEntry` log
    - `trajectories/<seed>.log.jsonl` — the operation record (args + intent tag) for an auditor reading without re-executing
3. **Replay** each `<seed>.audit.jsonl` from disk: `verify_chain` must accept it; `audit-reconstructibility` reports the pass fraction (target = 1.000, the Prop 1 contract).
4. **Tamper-check** flips a sampled byte at fractional offsets `{5%, 20%, 35%, 50%, 65%, 80%, 92%, 97%}` per file and asserts `verify_chain` rejects every flip (parse failure or Merkle rejection both count as caught).
5. **Report** writes a JSON summary to `reports/run-<UTC-iso>.json` plus updates the corpus identity card at `MANIFEST.json`.

## Determinism contract

- Sole randomness source: `numpy.random.Generator(seed)` per trajectory.
- Sole clock source: a counter clock handler local to the generator (`+1.0` per `:clock/now`).
- No `time.time` / `datetime.now` / `random.random` / `os.urandom` in trajectory bodies.
- Re-running `python -m bench.regulator_replay.generator` with the same seeds produces byte-identical `.audit.jsonl` + `.log.jsonl` files. The `MANIFEST.json` `corpus_root` SHA-256 is the load-bearing identity.

## How to run

Regenerate the full 50-trajectory corpus + manifest:

```bash
.venv/bin/python -m bench.regulator_replay.generator --seeds 0-49 --length 40
```

Replay + verify + tamper-check:

```bash
.venv/bin/python -m bench.regulator_replay.harness
```

Run the smoke tests (3 mini-trajectories, length=10):

```bash
.venv/bin/pytest bench/regulator_replay/tests/ -q
```

## Layout

```
bench/regulator_replay/
├── README.md
├── LICENSE                  CC-BY-4.0
├── MANIFEST.json            corpus identity card (corpus_root SHA + per-trajectory SHAs + invocation)
├── __init__.py              CORPUS_VERSION constant
├── generator.py             deterministic generator + manifest builder
├── harness.py               replay + tamper-detection + report
├── tests/
│   ├── test_generator.py    determinism + intent coverage + chain verifies
│   └── test_harness.py      replay accepts clean / tamper-detection / end-to-end report shape
├── trajectories/            generated 50 × {audit,log}.jsonl pairs
└── reports/                 per-run JSON reports
```

## What an external auditor sees

1. Verify `MANIFEST.json` `corpus_root` matches the SHA-256 over all `trajectories/*.jsonl` files in lexicographic order.
2. Verify each `trajectories/<seed>.sha256` listed in the manifest matches the SHA-256 over the audit + log byte-concatenation for that seed.
3. For each `<seed>.audit.jsonl`, re-run `persistence.effect.verify_chain` on the loaded entries — expect every chain valid.
4. Run the harness; expect `n_pass == 50`, `audit_reconstructibility == 1.000`, `tamper_matrix.all_caught == True`.
5. Re-run the generator from the recorded `invocation` field in the manifest; expect byte-identical output (corpus is reproducible from seeds + substrate version alone).

## Non-goals

- Running against live Juba OS / `ai-box` vault data. Benchmark is synthetic, reproducible, shareable.
- Claiming it reflects real-world complexity. It reflects *the shape* of regulator-replay; production-volume stress testing is a separate artifact.
- Any coupling to `ai-box/`. Importing from `ai-box` here is a lint failure.

## References

- Paper §6.3 — citation path now promoted from "planned" to "included" upon merge of this branch
- ARIS round 1 R4 critical finding (paper-vs-code drift) — `docs/aris-bitemporal-design-round-1/R4-research.md`
- Parent retrofit design — `docs/plans/2026-04-22-memory-palace-bitemporal-design.md`
