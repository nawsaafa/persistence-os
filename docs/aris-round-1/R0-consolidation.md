# ARIS Round 1 — Consolidation

**Date:** 2026-04-21
**Gate:** min score ≥ 7.0 per `plan.edn`
**Verdict:** ❌ **FAIL** — dispatching targeted fix pass before Round 2.

| Reviewer | Grade | Gate |
|---|---:|---|
| R1 Correctness | 7.4 | ✅ pass |
| R2 Rigor | 6.4 | ❌ fail |
| R3 Composability | **4.5** | ❌ fail |
| R4 Research alignment | 6.5 | ❌ fail |
| **Minimum** | **4.5** | ❌ **FAIL** |

## Convergent themes (flagged by ≥ 2 reviewers)

### Theme A — Boundary incoherence (R1 F1–F2,F6,F13 · R3 F1,F3,F6)
Three incompatible `datom` schemas across fact/effect/spec:

- `fact.Datom`: snake_case Python attrs (`e`, `a`, `tx:int`, tz-datetime).
- `effect.audit_entry_to_datom`: slash-keyed dict with sha256 string `tx`, epoch-float `tx_time`, `None` policy-id.
- `:persistence.fact/datom` spec: leading-colon EDN keywords, UUID entity, int tx, tz-datetime inst, keyword provenance source.

Every module's tests pass in isolation; the first cross-module call `spec.conform(":persistence.fact/datom", audit_entry_to_datom(e))` fails on every field. Same class of defect on `:audit/verdict` (`":allow"` vs `"allow"`), `:persistence.replay/trajectory` (every required key shape-mismatched), and `:persistence.plan/node` (keys map vs vector form from agent2 spec §1).

### Theme B — Replay ↔ Effect integration broken (R1 F7 · R3 F2)
`replay.EffectHandler` is a standalone class with incompatible signature (`call(op, args, fn)` thunk vs `effect.Runtime`'s `Handler(args, k, ctx)` continuation) and different op namespace (`"llm/call"` vs `":llm/call"`). NON_REPLAYABLE_OPS guard silently misses on the prefix mismatch. Blocks any Phase 2 vertical that wants to record→replay real trajectories.

### Theme C — Paper overclaims vs shipped code (R2 F1 · R4 F1,F2,F4,F7)
- **§4.1 Proposition 1** claims O(|Δ| log |D|) `branch` via HAMT structural sharing. Actual `db.branch()` does a full `list(as_of(t).datoms)` + deep-copy into a fresh `InMemoryStore` — O(n), zero sharing. **Prop 1 is false as shipped.**
- **ed25519 signing** claimed in §4.1, §7.1 (with fabricated "20-40 ms" overhead), §7.2 — zero `ed25519` hits in `src/`. Only sha-256 hashing exists.
- **Seven unified capabilities** asserted in Abstract/§3/Fig.1 — only 4 shipped (fact, effect, spec, replay). Plan/Txn/REPL still stubs.
- **§5.1** claims Kuzu graph + mem0 vector index; only `DictProjection` reference exists.

### Theme D — Cross-module wire-up never tested e2e (R3 F5,F7,F8,F9)
- `Mem0Interceptor.add()` passes `e=, a=, v=, valid_from=` into `mem0.Memory.add()` which accepts none of those — VPS call will TypeError on first hit. Tests use a duck-typed fake.
- `persistence.effect.__init__.py` has `__all__ = []` → no `from persistence.effect import perform, Runtime` possible.
- Zero callers of `spec.conform`/`spec.parse` outside the spec package — parse-don't-validate declared, not practiced.
- `pyproject.toml` missing `hypothesis` + `pytest-asyncio` — clean install breaks the spec suite.

### Theme E — Rigor gaps on load-bearing claims (R2 F2,F3,F4,F5)
- Audit-chain tamper test only covers field mutation, not deletion/reorder.
- Multi-step interventions, `step >= len(facts)`, empty-trajectory replay silently succeed.
- `ContextVar` concurrency claim has zero tests; `Runtime._masks` is a plain mutable list — shared Runtime across asyncio tasks will corrupt mask state.
- `time.time()` / `datetime.now()` / `random.random()` ban is aspirational; violated in `fact/db.py:38`, `fact/interceptors/mem0_adapter.py:65,97`, `spec/_primitives.py:13`, `spec/_combinators.py:26`, `spec/_canonical.py` — unlinted.

## Non-convergent but important (single reviewer, high severity)

- **R1 F3** — `DB.transact` allows retroactive corrections with negative `valid_to < valid_from` interval. Motivating use case in agent1-fact-spec §0.
- **R2 F2 extended** — deletion/reorder tamper paths.
- **R3 F4** — SQL migration claims Postgres portability but uses `AUTOINCREMENT` (SQLite-only).
- **R3 F10** — `_tx_counter` is a module-level global; breaks multi-process / restored-from-disk stores.
- **R4 F9** — `bench/` directory + `Makefile` referenced in README do not exist.
- **R4 F10** — §7.4 lists "Z3-verifiable proof-of-thought" — zero Z3 integration.

## What's undersold (per R4)

- `Runtime.is_well_formed()` + `Unhandled` enforcement of Proposition 2 — real machine-checkable property.
- `spec.explain_for_llm` + self-healing hint contract — concrete neurosymbolic contribution.
- `trajectory_hash(cf) == trajectory_hash(factual)` on NO-OP — stronger than CAMO's aspirational seed-replay.
- `verify_chain` Merkle audit chain — shipped and tested.
- `:persistence.plan/node` registered before the Plan module exists — a real parse-don't-validate methodology choice.

## Round 2 fix pass — proposed dispatch (4 parallel workers, isolated worktrees)

| Worker | Scope | Findings addressed | Target |
|---|---|---|---|
| **W-boundary** | Single source of truth for datom/audit/trajectory/plan-node shape. Adapter layer (`fact.serialize`/`deserialize`, `audit.to_datom`) that round-trips through the registered specs. Fix `effect/__init__.py` `__all__`. Add `pyproject.toml` deps. | R1 F1,F2,F5,F6,F13 · R3 F1,F3,F6,F7,F9 | R1 → 8.5+ · R3 → 7.5+ |
| **W-integration** | Make `replay.EffectHandler` a real `effect.Runtime` handler (continuation signature, `:op` namespace). Wire NO-OP e2e test through effect.perform → replay → compare. Fix `Mem0Interceptor` kwargs against real `mem0.Memory.add()` contract. Fix `_tx_counter` global. | R1 F7 · R3 F2,F5,F10 | R3 → 8.0+ |
| **W-rigor** | Audit-chain deletion/reorder tests. Multi-step replay interventions + empty-trajectory coverage. `ContextVar` concurrency test (2+ asyncio tasks, shared Runtime). Lint rule against `time.time`/`datetime.now`/`random.random` in `src/persistence/`. Fix `DB.transact` retroactive valid-to guard. | R1 F3 · R2 F2,F3,F4,F5 | R2 → 8.0+ |
| **W-paper** | Soften §4.1 Prop 1 (drop HAMT claim or mark Phase 2). Remove ed25519 from §4.1/§7.1/§7.2 or stub implementation. Reframe "seven capabilities" → "4 shipped, 3 designed, all derivable from substrate." Rescope §6.3 regulator-replay to 50 synthetic trajectories. Drop §6.4 plan-opt benchmark. Elevate undersold contributions (Prop 2 machine-check, explain_for_llm, trajectory-hash, Merkle chain). | R4 F1,F2,F4,F6,F7,F8,F9,F10 | R4 → 8.0+ |

All four run in parallel with TDD + verification-before-completion + Serena-first + ARIS-findings-only-scope. Merge order: boundary → integration → rigor → paper (same as Phase 1 plus paper last). Target min grade after fix pass: **≥ 8.0** → Round 2 expected to land ~8.5 → Round 3 to ~9.0.

## Reports

- `R1-correctness.md`
- `R2-rigor.md`
- `R3-composability.md`
- `R4-research.md`
