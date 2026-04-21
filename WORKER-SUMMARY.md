# W-integration — ARIS Round 1 fix pass

**Branch:** `W-integration`
**Worktree:** `/Users/nawfalsaadi/Projects/persistence-os/.claude/worktrees/W-integration`
**Base:** `main` @ `5f97882`

## Findings addressed (all in scope)

| Finding | Title | Fix commit |
|---|---|---|
| R1 F7 / R3 F2 | `replay.EffectHandler` not wired into `effect.Runtime`; NON_REPLAYABLE_OPS silently missed on op-prefix mismatch | `52611dc` + `1f1e778` |
| R3 F5 | `Mem0Interceptor.add/update` passed unknown kwargs to `mem0.Memory`, would TypeError on live VPS | `ef920c2` |
| R3 F10 | `_tx_counter` module-level global broke multi-process Postgres and restored-from-disk SQLiteStore | `e4753da` |

## Commits

```
52611dc effect: align op namespace to leading-colon (":llm/call" etc)
1f1e778 replay: bridge EffectHandler into effect.Runtime (R1 F7, R3 F2)
ef920c2 fact: Mem0Interceptor uses real mem0.Memory signature (R3 F5)
e4753da fact: tx allocation lives on Store, not a module global (R3 F10)
```

## Files changed

### `52611dc` — op namespace alignment (23 files, +299/-299)
- `src/persistence/effect/catalog.py` — 15 catalog keys + `OpSpec.name`
- `src/persistence/effect/demo.py` — `perform()` / `Handler` / EDN policy literals
- `src/persistence/effect/handlers/{audit,cache,clock,dry_run,pii_redact,policy,rate_limit,raw,retry}.py` — every `wraps=` / `clauses=` entry
- `tests/effect/test_{audit,cache,canonical,catalog,composition,dry_run,pii_redact,policy_eval,policy_handler,rate_limit,retry,runtime}.py` — every `perform()` literal

### `1f1e778` — replay/effect bridge (4 files, +518/-76)
- `src/persistence/replay/effect_handler.py` — new `_serve_or_miss` core, new `RefusedInReplay`, new `make_replay_handler(mode, wraps, cache, calls) -> effect.Handler`
- `src/persistence/replay/__init__.py` — export `make_replay_handler`, `RefusedInReplay`, `NON_REPLAYABLE_OPS`, `PROMPT_HASH_OPS`
- `tests/integration/__init__.py` — new package
- `tests/integration/test_effect_replay_bridge.py` — new file, 6 e2e tests including the load-bearing record → replay → hash-equality test

### `ef920c2` — Mem0Interceptor real-mem0 signature (4 files, +452/-18)
- `src/persistence/fact/interceptors/mem0_adapter.py` — rewrote `add` / `update` to target real `mem0.Memory.add(messages, *, user_id=..., metadata=...)` and `Memory.update(memory_id, data, metadata=None)`; datom fields carried on `metadata=` dict
- `tests/fact/test_interceptor.py` — `FakeMem0` made strict (mirrors real mem0 signatures), old `**kw` accept-all fake retired
- `tests/integration/test_mem0_signature.py` — new file, 6 tests including `@pytest.mark.integration` signature-superset check against the installed `mem0ai` package
- `pytest.ini` — registered `integration` marker

### `e4753da` — `_tx_counter` per-Store (4 files, +224/-17)
- `src/persistence/fact/store.py` — added `Store.next_tx()` protocol method, `InMemoryStore.next_tx()` (max+1 from in-memory log), `SQLiteStore.next_tx()` (`SELECT COALESCE(MAX(tx), 0) + 1 FROM datom_log`)
- `src/persistence/fact/db.py` — removed module-level `_tx_counter = itertools.count(1)`, `DB.transact` calls `self.store.next_tx()`
- `tests/fact/conftest.py` — removed `_reset_tx_counter` autouse fixture (fresh Store per test ⇒ fresh counter)
- `tests/fact/test_tx_allocation.py` — new file, 8 tests including multi-store-same-file, close-reopen round-trip, module-global absence

## Test results

- **Before:** 356 passed
- **After:** 376 passed (+20 new, 0 regressions)
- Full run time: 1.96s

```
============================= 376 passed in 1.96s ==============================
```

New test files:
- `tests/integration/test_effect_replay_bridge.py` — 6 tests
- `tests/integration/test_mem0_signature.py` — 6 tests (2 marked `@pytest.mark.integration`, both pass with `mem0ai` installed in `.venv`)
- `tests/fact/test_tx_allocation.py` — 8 tests

Key load-bearing invariants still green:
- `tests/replay/test_determinism.py::test_noop_intervention_produces_byte_identical_trajectory` — passes
- `tests/integration/test_effect_replay_bridge.py::test_record_then_replay_byte_identical_trajectory` — new, passes; `trajectory_hash(replayed) == trajectory_hash(recorded)` under the real `effect.Runtime`
- `tests/effect/test_audit.py::test_tampering_an_entry_breaks_the_chain` — passes (Merkle chain intact)
- Effect demo (`python -m persistence.effect.demo`) and replay demo (`python -m persistence.replay.demo`) both run end-to-end

## Integration points for W-boundary / W-rigor / W-paper

- **Op namespace is now leading-colon everywhere** in `persistence.effect` + `persistence.replay`. W-boundary can align the spec registry's `:audit/verdict` enum, `:persistence.fact/datom` keys, and `audit_entry_to_datom`'s `:datom/a = ":audit/<op>"` shape to match without colliding with this worker's scope.
- **Audit datom shape is still the old string-prefix form** (`datom/e`, `datom/a = "audit/:llm/call"`). W-boundary owns fixing both the key prefixes (`:datom/e`) and the audit attr form (`:audit/<op>` vs concatenated `audit/:llm/call`). Not touched here.
- **`make_replay_handler(mode=record)` produces a proper `effect.Handler`** that sits anywhere in the handler stack. The Phase-2 production chain can now be literally:
  ```
  audit → policy → replay(mode=record) → cache → retry → rate-limit → raw
  ```
  And replay mode swaps the same handler to `replay(mode=replay, cache=..., calls=...)` and drops everything below it.
- **`Store.next_tx()`** is now the tx-allocation contract. W-rigor's concurrency tests and Phase 2's `persistence.txn` STM module can call it directly without worrying about a hidden global.

## Explicitly out of scope (per coordination note in task brief)

Deferred — owned by other workers, not addressed here:

- **Datom / audit key colon-prefix alignment** (R1 F1, F2, F6, F13 · R3 F1, F3) — W-boundary.
- **`:persistence.plan/node` vector-vs-map** (R1 F4) — W-boundary.
- **`:persistence.replay/fact` keyword-keys alignment** (R1 F5) — W-boundary.
- **`effect/__init__.py` `__all__`** (R3 F7) — W-boundary.
- **`pyproject.toml` deps (`hypothesis`, `pytest-asyncio`)** (R3 F9) — W-boundary.
- **Audit-chain deletion/reorder tampering tests** (R2 F2 extended) — W-rigor.
- **`DB.transact` retroactive valid-to guard** (R1 F3) — W-rigor.
- **Wall-clock lint rule** (R2 F5) — W-rigor.
- **`ContextVar` concurrency test** (R2 F4) — W-rigor.
- **Paper §4.1 Prop 1 HAMT / ed25519 / "seven capabilities"** (R4 F1, F2, F4, F7) — W-paper.
- **SQLite `AUTOINCREMENT` → portable `GENERATED AS IDENTITY`** (R3 F4) — not in my brief; likely W-boundary's territory since they're touching migration-adjacent schema work. Flagging for coordinator.

## Verification commands

```bash
cd /Users/nawfalsaadi/Projects/persistence-os/.claude/worktrees/W-integration
source /Users/nawfalsaadi/Projects/persistence-os/.venv/bin/activate
python3 -m pytest tests/ -q                     # 376 passed in ~2s
python3 -m pytest tests/integration/ -v          # 12 passed (6 bridge + 6 mem0)
python3 -m pytest tests/fact/test_tx_allocation.py -v  # 8 passed
PYTHONPATH=src python3 -m persistence.effect.demo       # end-to-end demo
PYTHONPATH=src python3 -m persistence.replay.demo       # replay demo
```

## Merge

```bash
cd /Users/nawfalsaadi/Projects/persistence-os
git merge --no-ff W-integration \
  -m "Merge W-integration: R1 F7 / R3 F2 / R3 F5 / R3 F10 fixes"
```

Merge order per R0 consolidation plan: `boundary → integration → rigor → paper`. If W-boundary lands first and renames audit datom keys, rebase this branch on top; no code conflicts expected (disjoint files) other than potentially in `audit.py` (datom conversion function) and `tests/effect/test_audit.py` (datom-related assertions).
