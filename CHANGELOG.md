# Changelog

All notable changes to Persistence OS are tracked here. Versions follow
`<semver>` with a `-aN` pre-release suffix until the paper lands.

## v0.5.0a1 — 2026-04-27 (Module 5: Txn — atomic multi-datom commit)

### Added
- `persistence.txn` module: atomic multi-datom commit, snapshot-read
  isolation, retry-safe effects via effects-as-intents pattern.
- `db.ref(eid)` / `db.new_ref(initial=...)`: Ref dataclass (frozen,
  slotted, eq/hash over (eid, db_id)).
- `with db.dosync() as tx:` (context-manager) and `@db.dosync` (decorator,
  canonical retryable form). Decorator supports `max_retries` and
  `deadline` kwargs.
- `tx.deref` / `tx.assoc` / `tx.alter` / `tx.effect` / `tx.now`.
- Mandatory immutable values for refs (pyrsistent.PMap/PVector/PSet,
  frozenset, tuple, primitives, frozen dataclass). `RefValueNotImmutable`
  raised on mutable input.
- `persistence.txn.freeze()` helper for dict→PMap, list→PVector
  migration.
- `EffectInIoBlock` raised when raw `effect.perform()` called inside a
  dosync body — use `tx.effect(op, **kwargs)` instead.
- 8 boundary specs registered under `:persistence.txn/*`.
- `DB.transact_batch()`: equivalent to `transact()` for correctness,
  folds N auto-retraction lookups into a single log pass.

### Dependencies
- `pyrsistent>=0.20` added to project dependencies.

### Deferred to later releases
- `tx.commute` (commutative writes) → v0.5.1
- `tx.ensure` (read-set padding) → v0.5.1
- Atoms (single-cell CAS) → v0.5.2
- Agents (async ordered single-cell) → v0.5.3
- Nested `dosync` semantics → v0.5.4

### Design pins held
- `PLAN_CANONICAL_VERSION` stays at 1.
- Zero proposition impact (Prop 1–5 unchanged).
- 832 + 7 xfailed v0.4.0a1 baseline preserved; +80 new tests
  (912 + 7 xfailed total).
- No-GIL forward-compatible (rev N): every mutation guarded by explicit
  lock, `@pytest.mark.no_gil_safe` test in conflict suite.

### Predecessor
- `v0.4.0a1` at `bce93da` — substrate primitives (Dispatcher, Provenance,
  fork, causal_history).

## [0.4.0a1] — 2026-04-25 — v0.4 substrate-primitives (Phases A + C + D)

### Added

- **`Provenance` TypedDict** (`persistence.fact.datom`) — `total=False`
  TypedDict with 7 known keys: `source`, `tx_time`, `handler_id`,
  `canonical_call`, `parent_provenance_hash`, `superseded_by_tx`, `extra`.
  Unknown keys are routed into `extra` by the `provenance_from_dict()`
  coercion helper, which lifts all known keys and collects the remainder
  under the `extra` catch-all. `Datom.provenance` field is now typed
  `Provenance` (documented `# type: ignore[assignment]` covers the
  `default_factory` escape-hatch required by pyright strict structural
  typing). Wire-roundtrip canonical-hash test pins that typed `Provenance`
  produces the same `provenance_hash` as the previous untyped dict shape —
  **`PLAN_CANONICAL_VERSION` stays at 1**; zero canonical-form changes by
  design.
- **`CausalDAG` + `DB.causal_history()`** (`persistence.fact`) —
  `CausalDAG` is a frozen dataclass (`seeds: list[Datom]`,
  `parents: dict[str, list[str]]`). `DB.causal_history(e, max_depth=16)`
  is a single-level walker that reads **both** `parent_provenance_hash`
  (Phase D.4 alias) and `:prev-hash` (legacy) from datom provenance for
  cross-module portability. Multi-level walking deferred to v0.5.
- **`ProjectionAdapter.fork()` + `DictProjection.fork()`**
  (`persistence.fact.projection`) — `fork(branch_id) -> ProjectionAdapter`
  Protocol method returns a fresh empty adapter; caller drives `rebuild()`
  to populate. `DictProjection.fork()` is the reference implementation.
  Standalone refactor also renamed `apply()` parameter `d` → `datom` for
  Protocol-conformance under pyright strict structural typing.
- **New public exports** from `persistence.fact`: `CausalDAG`, `Provenance`,
  `provenance_from_dict`.
- **`Dispatcher` class** (`persistence.plan._dispatch`) with
  `register(tag, handler)` / `has_handler(tag)` / `dispatch(node, env)`.
  Handler-per-tag registration replaces match-on-tag conditional cascades.
  Walk-order property test under Hypothesis. New public exports from
  `persistence.plan`: `Dispatcher`, `Handler`.
- **`_walk.py` rename** (`persistence.plan`) — `_interpret.py` renamed to
  `_walk.py`; back-compat shim re-exports `walk` from the old name.
- **Audit handler `parent_provenance_hash` alias**
  (`persistence.effect.handlers.audit`) — `audit_entry_to_datom` now writes
  a `parent_provenance_hash` alias alongside `:prev-hash`. Both keys point
  to the same value, bridging the audit chain hash to the typed `Provenance`
  schema so `DB.causal_history()` can walk the chain transparently.

## [0.1.0a1] — 2026-04-20 — Module 1: `persistence.fact`

### Added

- **8-tuple Datom dataclass** (`persistence.fact.Datom`) matching
  `docs/agent1-fact-spec.md` §1 and paper §4.1. Frozen, slotted, refuses
  naive datetimes at construction.
- **`DB` + `DBView` query surface** (`persistence.fact.DB`) implementing
  every method from the spec §2 API:
    - `transact` with auto-retraction of superseded cardinality-one
      asserts — a new assert emits a companion `retract` whose `valid_to`
      closes the prior interval.
    - `as_of` (transaction-time slice)
    - `as_of_valid` (valid-time slice, asserts only)
    - `history` (full lineage for an entity, sorted by tx)
    - `since` (incremental sync / replication)
    - `branch` (counterfactual, isolated in-memory store, hypothetical
      datoms tagged `provenance.source = "branch"`)
- **Storage backends** behind a `Store` Protocol:
    - `InMemoryStore` — reference for tests and the CLI demo
    - `SQLiteStore` — persistent, zero-ops deployment
  with a **portable SQL migration** (`migrations/0001_datom_log.sql`) that
  creates the five covering indexes (EAVT, AEVT, AVET, VAET) plus the VT-E
  bitemporal range index and the log-ordered tx-time index called for in
  agent1-fact-spec §4. The same file runs on SQLite 3.37+ and Postgres 14+.
- **Projection rebuilder** (`persistence.fact.projection`) — a
  `ProjectionAdapter` Protocol (`reset()` + `apply(datom)`), a reference
  `DictProjection`, and a `rebuild(db, adapter)` driver. Kuzu / mem0
  adapters are separate concerns; this module provides the seam.
- **mem0 interceptor adapter** (`persistence.fact.interceptors.mem0_adapter`)
  wrapping a duck-typed mem0 client so every `add` / `update` emits a datom
  before the legacy write. `InterceptorError` is raised (blocking the
  legacy write) if the datom emission fails; if the legacy write fails,
  the datom still persists — operators rebuild the projection from the
  log.
- **CLI demo** (`python -m persistence.fact.demo`) reproducing the
  agent1-fact-spec §8 BankabilityAI WACC counterfactual verbatim.
- **Memory Palace integration doc** (`docs/memory-palace-integration.md`)
  covering the Python import pattern, SQL migration step, rollback
  procedure, and a six-step VPS test plan.

### Verified

- **65 tests green** under `pytest tests/fact/ -v`, spanning both
  InMemoryStore and SQLiteStore backends.
- **`python -m persistence.fact.demo`** prints the three-line factual /
  historical / counterfactual output that matches the spec prototype byte
  for byte.
- **`as-of(db, t)` idempotence invariant** from the conductor track's
  `[:verify {:claim "as-of(db, t) is idempotent for t >= now"}]` gate is
  exercised by an explicit test case (`TestAsOfIdempotence`).

### Deferred to later modules / phases

- Kuzu + mem0 production projection adapters (Phase 2 — agent1-fact-spec §7).
- Historical backfill for Memory Palace (Phase 2, same section).
- Postgres CI smoke test — no credentials available in the worktree; SQL
  migration is identical across backends and operators run the Postgres
  path manually per the integration doc.
- Zstd segment compression, content-addressed storage (§4 storage layout).
- ed25519 provenance signing — batched at the transaction level per §9.
