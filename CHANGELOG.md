# Changelog

All notable changes to Persistence OS are tracked here. Versions follow
`<semver>` with a `-aN` pre-release suffix until the paper lands.

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
