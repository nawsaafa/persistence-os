# Changelog

All notable changes to Persistence OS are tracked here. Versions follow
`<semver>` with a `-aN` pre-release suffix until the paper lands.

## v0.9.0a1 (unreleased)

Phase 2 of the persistence-coder product roadmap. First agents
built ON the v0.8.5a1 substrate.

### Phase 2.4c — Lockfile snapshot for v0.9.0a1 distribution (2026-05-11)

Final harden-track phase before the `v0.9.0a1` GA tag. Three locked
decisions, all codex-consensus REJECT-FOR-NEW-OPTION (3/3 controller
proposals flipped):

- **LD-1 (codex consensus NEW-OPTION-Z): Wheel-build + fresh-venv install + coder CLI smoke.**
  `pyproject.toml` version bumped `0.8.5a1 → 0.9.0a1`. `uv.lock` regenerated
  with FD-version-bump-side-effects audit (no top-level dep drift on
  `edn_format`, `pyrsistent`, `cryptography`). New `tests/sdk/test_lockfile_distribution_smoke.py`
  G1 test exercises `uv lock --check` (dev-env reproducibility) + `uv build`
  + fresh-venv `pip install dist/*.whl` (consumer-side install — two
  separate contracts) + `python -m persistence.coder --task hello` with
  banner-mask + no-traceback invariants. R0-fold B3b: HTTP smoke is NOT
  in base G1 — `persistence.http` requires `[http]` extras; sister
  test G1[http] is a W3 rescope. G1 is POSIX-only (`bin/python`,
  `bin/pip`); Windows is out of scope per internal-alpha posture (codex
  R1 I4 noted, intentional).

- **LD-2 (codex consensus NEW-OPTION-Z): Hand-authored preflight manifest.**
  `tests/preflight_manifest.toml` introduces the v0.9.0a1 agent
  allowed-entrypoints contract — a closed allowlist of curated SDK
  methods the persistence-coder agent (and Phase 7 skill consumers)
  are allowed to call. Per LD-2 codex decider: *"Allowed entrypoints
  is policy, not reflection."* The manifest is hand-authored
  source-of-truth (NOT auto-generated from `_facade.py` introspection).
  Adding/removing/renaming a curated method requires explicit manifest
  edit + reviewer sign-off. G2 test `tests/sdk/test_preflight_manifest.py`
  verifies subset-resolution against `Substrate.open(...)` namespace
  surface (3 tests: subset-resolution + escape-callsites-empty +
  anti-snapshot-regression). R0-fold I1: stability tier fields removed
  from manifest schema entirely (cross-check is future SDK5 generator
  concern). R1-fold I1: anti-regression test now a REAL runtime check
  via `inspect.getsource` scan for snapshot-equality patterns
  (replacing original no-op `pass`).

- **LD-3 (codex consensus NEW-OPTION-D → SINGLE-ROOT calendar drift):**
  T-probe found that 18 `tests/repl/` failures were NOT a 2.3d
  Capability-extension regression. Single root cause: test fixture
  calendar drift. `_DEFAULT_T = _dt(2026, 5, 9, 12, 0, 0)` had become
  past relative to wall clock (2026-05-11+), and `db.transact` records
  `tx_time` from wall clock while `db.as_of(test_clock)` filters by
  `tx_time <= query_time` → fresh datoms filtered out. Fix shifted
  fixture timeline `_dt(2026, 5, 9, ...) → _dt(2099, 1, 1, ...)`
  across 8 REPL test files; 2 expires_at sites in `test_caps.py`
  bumped `2027 → 2100`. The 4 `tests/plan/` failures in the original
  baseline were a Python-3.14-env-specific artifact (dspy isolation
  harness drift in 3.14 only); in the proper uv-managed Python 3.12
  venv, `tests/plan/` has 0 failures.

#### Unplanned fixes surfaced by G1 distribution smoke

- **F1**: `uv.lock` had been gitignored in commit `b4a3afc` (leftover
  dev-hygiene oversight). Removed from `.gitignore`; lockfile is now
  tracked. Phase 2.4c is literally the lockfile-snapshot phase — the
  gitignore contradicted the phase intent.
- **F2 (codex consensus ACCEPT)**: `pydantic>=2.9,<3.0` was only in
  `[project.optional-dependencies] http`, but `persistence.claim._validate`
  imports pydantic at module init to define `BaseModel` schemas and
  `persistence.claim` is on the base import path of `_facade._ClaimNamespace`.
  G1's fresh-venv test surfaced a v0.8.5a1 distribution defect:
  `python -m persistence.coder --task hello` crashed with
  `ModuleNotFoundError: No module named 'pydantic'`. Local dev hid it
  via `[dev]` extras pulling pydantic transitively. Pydantic promoted
  to base `[project] dependencies`; removed from `[http]` (fastapi
  pulls it transitively anyway).

#### ARIS journey

- Design R0 (codex high): **FAIL 7.29/7.0** → R0-fold (3B + 3I + 2N closed)
- Design R0.1 lite (codex high): **PASS-WITH-FIXES 8.1/7.6** → DESIGN FROZEN
- Codex Impl R1: **PASS-WITH-FIXES 8.4/7.5** → R1-fold (3I closed inline; I4 + N1 + N2 documentation-only)

#### Downstream tracks unblocked

- Phase 7 `persistence-orchestrate` Anthropic Skill (ADR-004 in
  `skill-systems-integration_20260430`).
- `persistence-os-decision-ops_20260511` (v0.10.x, blocked by both
  2.4c lockfile AND v0.9.0a1 GA tag — 2.4c side now unblocked).
- `mimir-debate-skill_20260511` (second-order via decision-ops).
- `agent-stack-first-class_20260512` (partial — full unblock requires
  v0.9.0a1 PyPI publish, a separate v0.9.x post-tag track).

#### N1 impl-log addendum

Design doc § LD-1 references test name `test_built_wheel_installs_and_runs_dual_cli`,
but the actual impl uses `test_built_wheel_installs_and_runs_coder_cli` after
R0-fold B3b dropped HTTP smoke from base G1. The design doc is FROZEN
(no edits post-R0.1); this addendum records the impl name divergence
for future readers.

#### Commits

- `a5bb2da` T0 design doc
- `f4992c7` R0-fold (3B+3I+2N)
- `ffc1f91` R0.1 lite PASS — DESIGN FROZEN
- `00859dd` Impl plan
- `27617d5` T1A G1 wheel-build smoke scaffold
- `5a4b232` T0.5 F1: un-gitignore uv.lock
- `683d7e3` T0.6 F2: pydantic → base deps (codex consensus ACCEPT)
- `c16a8a5` T1B G1 PASS: version bump 0.8.5a1→0.9.0a1
- `d1a26d3` T2 LD-2 manifest + G2
- `6be1d4f` T2.1 doc-fix (TOML escape_callsites order)
- `828aed1` T-probe-fix: calendar drift (18 REPL failures closed)
- `53c3701` T9.1 R1-fold (3I closed inline)

### Added — `persistence.coder` (Phase 2.1a)

- **`persistence.coder` package** (`src/persistence/coder/`). 9th
  module under `persistence.*`; FIRST consumer-side module (imports
  from `persistence.sdk` only, never raw substrate modules). Phase
  2.1a delivers the no-op ReAct skeleton: `Coder` dataclass with
  substrate dependency-injected, `run()` walks the base § 3.4
  agent-loop shape and raises `CoderStubNotImplemented` on the first
  un-filled stub. CLI entry `python -m persistence.coder --task "..."`
  exits 1 with a stderr banner. Subsequent sub-phases (2.1b LLM
  provider, 2.1c G1 lockfile contract test, 2.2a/b effects, 2.3a/b/d
  escalation + REPL pause, 2.4a-d harden) fill the methods. ARIS R1
  PASS-WITH-FIXES (codex hard-mode mean 8.0 / min 7.6) on design doc
  `docs/plans/2026-05-03-phase-2.1a-coder-skeleton-design.md`. Per-module
  CHANGELOG at `src/persistence/coder/CHANGELOG-coder.md`.

## v0.8.0a1 — 2026-04-30 (Phase 1 closure: Adapter SDK + multi-process Postgres SERIALIZABLE)

Phase 1 of the persistence-coder product roadmap. Cumulative train of two
parallel design tracks landed on `feat/v0.8.0a1-int` across **SDK1–SDK3**
(adapter SDK foundation + first-party MCP server) and **PG1–PG6 + PG-W1**
(multi-process Postgres SERIALIZABLE backbone with cross-process audit-chain
Merkle continuity). ARIS R2 PASS at mean **8.81 / min 8.0** after the PG-W1
fix-pass (R2 round-1 was 8.59 / 7.2; round-2 lifted Dim-4 audit-chain
Merkle continuity 7.2 → 9.0). Suite **1880 passed / 32 skipped / 7 xfailed
in 27.66s** without `PERSISTENCE_PG_DSN` (+320 over v0.7.0a1; +15 PG-W1
rebind unit tests; +1 PG-DSN-gated G3d cross-process verify_chain test).

### Added — Adapter SDK (SDK1–SDK3)

- **`persistence.sdk` package** (`src/persistence/sdk/`). URI-dispatched
  adapter foundation (#160). Public surface: `Substrate` (the curated
  facade), `mount(uri, **opts) → Substrate`, `@experimental` /
  `@stable(since=...)` stability decorators, `_audit` telemetry, `_uri`
  resolver. URI scheme-based dispatch routes to the underlying store
  driver (in-memory, SQLite, Postgres) without leaking psycopg / aiosqlite
  imports into the curated surface.
- **Substrate body — 6 curated subsurfaces + escape hatch** (#161):
  `txn` (transactional facts), `audit` (chain emission + window read),
  `replay` (counterfactual cursor), `plan` (EDN AST), `repl` (capability-
  gated live REPL handle), `mcp` (first-party MCP server handle), plus
  `unsafe` (typed escape to underlying `db.store` for advanced use).
  Schema Profile **v0.8** introduces gate G13 (curated-surface stability:
  every public symbol on `Substrate` has a stability annotation).
- **First-party MCP server** (`persistence.sdk.mcp`, #163, 127 tests).
  Six tools (`persistence_remember`, `persistence_recall`,
  `persistence_forget`, `persistence_audit_window`,
  `persistence_replay_check`, `persistence_view_at`) + audit-tail
  resource. Coverage matrix: G2 (audit-chain integrity) / G8a–d
  (replay-from-datoms-alone byte identity per tool) / G9a–b
  (capability-token gating) / G11 (forget tombstone semantics) /
  G12a–c+e (view-at temporal isolation) / G13a–d (curated-surface
  stability invariants).

### Added — Postgres SERIALIZABLE backbone (PG1–PG6 + PG-W1)

- **`PostgresStore` SERIALIZABLE** (#162, `src/persistence/store/postgres.py`).
  psycopg 3.x + `psycopg_pool.ConnectionPool`. SERIALIZABLE isolation
  on every transaction with row-locked `tx_allocator` primitive
  (ADR-4 W1-revised) for monotonic tx-id assignment without
  `MAX(tx)+1` race. URI dispatch:
  `postgres://user:pass@host:port/db` mounts a `Substrate` on top of
  `PostgresStore` via the SDK. `unique_violation` retry on the
  `UNIQUE(tx, e, a)` tripwire is the hard primitive (no soft check).
- **`DatomCodec` ABC** (#165, `src/persistence/store/_codec.py`, 317 lines).
  `TextDatomCodec` (SQLite + InMemoryStore, ISO-string tx-time) and
  `NativeDatomCodec` (Postgres, native `TIMESTAMPTZ`). Cross-store
  byte-identity property (`tests/store/test_replay_byte_identity_cross_store.py`,
  633 lines, Hypothesis `@given` at `max_examples=200` parametrised
  over all 3 stores).
- **`audit_chain_lock` table + audit-aware `transact_serializable`**
  (#166). Single-row table (`id INTEGER PK CHECK(id=1), last_seq, last_hash`)
  ordering audit-chain Merkle linkage cross-process via
  `SELECT FOR UPDATE`. Lock ordering inside `transact_serializable`:
  `tx_allocator → audit_chain_lock → INSERT datom_log → UPDATE
  audit_chain_lock`. Store Protocol gains `_txn()` +
  `transact_serializable(facts, *, tx_time?)` additive declarations
  with default impls on `InMemoryStore` + `SQLiteStore` (ADR-13).
  `persist_repl_audit` rewritten +39/−45 to route through
  `transact_serializable([datom], tx_time=recorded_at)` preserving
  `recorded_at` as tx_time.
- **G3 cross-process falsifiability proof** (#167,
  `tests/store/_pg4_harness.py` 738 lines + `test_g3e_falsifiability.py`
  425 lines). `multiprocessing.Barrier` writer harness with `spawn`
  context bypasses `tx_allocator FOR UPDATE` row-serialisation by
  pre-allocating tx-ids, forcing concurrent overlap past the
  conflict-check point. Hypothesis property at `max_examples=50`:
  SERIALIZABLE rejects ≥1 OR READ COMMITTED produces anomaly.
  ADR-14 recasts G3 as G3a Merkle / G3b single-writer / G3c
  cross-process / G3d audit-aware (cross-process verify_chain) /
  G3e READ-COMMITTED falsifiability.
- **Deployment matrix** (#168, `docs/operations/postgres-deployment.md`,
  257 lines). pgbouncer transaction-mode posture, Aurora failover
  considerations, RDS connection-pool config, statement_timeout
  interaction with `unique_violation` retry. `_pool_config.py` (148
  lines) ships recommended `ConnectionPool` kwargs as a helper.
- **`DB.fold()` executor** (#145 / #169, `src/persistence/fact/db.py`,
  `@experimental`). Speculation / rollback / checkpointing primitive:
  `fold(seed, items, fn, *, on_error='abort', checkpoint_every=0,
  provenance=None) → tuple[final_acc, total_committed]`. `FoldError`
  surface. `Substrate.txn.fold` curated re-export.
- **Forward-only migration runner** (#169, `_migration_runner.py`,
  ~310 lines). Replaces inline `_SCHEMA_DDL`. `_migrations` history
  table. Two shipped migrations: `0001_datom_log.sql` + `0002_audit_chain_lock.sql`.

### Fixed — PG-W1 audit-chain prev_hash rebind under lock

- **`rebind_audit_datom_prev_hash`** (`src/persistence/effect/handlers/audit.py`,
  +15 unit tests). Decode → mutate `prev_hash` → recompute `:signature`
  → re-encode helper. No-op when `prev_hash` already matches. Updates
  `:datom/e` (when signature, not `run_id`), `:datom/tx`,
  `provenance[':signature' / ':prev-hash' / 'parent_provenance_hash']`.
- **`_rebind_audit_datom_under_lock`** wired into
  `PostgresStore.transact_serializable` step 3a. Removes the
  `_ = audit_head_hash` discard at line 586 (R2-R1 Dim-4 blocker).
  The locked head is now load-bearing: first audit datom in a batch
  rebinds to `audit_chain_lock.last_hash` under `SELECT FOR UPDATE`;
  subsequent audit datoms thread serially binding to the prior
  recomputed signature. The `last_hash` UPDATE uses the recomputed
  signature, not the original Python-side one.
- **G3d cross-process `verify_chain` test**
  (`tests/store/test_audit_chain_invariants.py`,
  `TestG3dCrossProcessAuditChainContinuity` + `_pg_w1_writer_main`).
  PG-DSN-gated: 4 child processes emit audit entries with the same
  stale `prev_hash` at construction; after the barrier, parent walks
  the persisted log in `seq` order, decodes via `datom_to_audit_entry`,
  and asserts `verify_chain(entries) is True` AND
  `entries[i].prev_hash == entries[i-1].id`. This is the test that
  would have failed pre-PG-W1.
- **Doc drift fix.** `LISTEN/NOTIFY` trigger no longer claimed to
  ship in v0.8.0a1 migrations: §1, §3, §9 of the design doc revised
  to "Defer to v0.9 (not shipped in v0.8.0a1)".

### Design

- **`docs/plans/2026-04-30-v0.8.0-postgres-store-design.md`** (940+ lines,
  17 ADRs):
  - **ADR-1..16**: schema, indexes, type promotion, `MAX(tx)+1` race
    primitive, codec consolidation (text vs native tx-time), Postgres
    extensions survey (pgcrypto/pgvector/pg_trgm/Citus rejected with
    reasons), pool config, audit-chain head ordering, `persist_repl_audit`
    migration to `transact_serializable`, etc.
  - **ADR-17 (PG-W1)** — codifies the audit-chain `prev_hash` rebind
    contract: store-side rebind is the seam because the substrate API
    is "construct `AuditEntry` → persist". Documents the **semantic
    footgun**: rebind changes the audit entry id at persist-time, so
    callers must NOT treat the pre-commit `AuditEntry.id` as stable
    across persistence in cross-process audit-emitting code.
- **`docs/plans/2026-04-30-v0.8.0-adapter-sdk-design.md`** (424 lines,
  12 ADRs).
- **ARIS R2 trajectory** (Postgres design + impl):
  R1 6.73 → R2 8.42 → R3 8.02 → R4 8.28 → R5 8.36 →
  R2-postimpl **8.59** (Dim-4 blocker surfaced) → R2-postPG-W1 **8.81**
  (PASS, mean ≥ 8.5 + min ≥ 7.5). See
  `review-stage/v0.8.0-postgres-store-r2/AUTO_REVIEW.md`.
  SDK design ARIS R2 PASS at mean 8.66 / min 8.40 (separate track).

### Compatibility

- **Pre-release.** SDK and Postgres surfaces are new in v0.8.0a1.
  `persistence.sdk` and `persistence.store.postgres` are the curated
  entry points; substrate-only consumers do NOT need psycopg.
- **Optional installs.** `pip install persistence[postgres]` pulls
  `psycopg[binary,pool]>=3.1`; `pip install persistence[mcp]` pulls
  the MCP transport extras. Existing in-memory + SQLite users see no
  changes.
- **License.** Substrate AGPL-3.0-or-later (unchanged). Open-core
  posture preserved for future commercial track.

### Phase-1 timeline

- Tranches 1–3 shipped 2026-04-29 → 2026-04-30 morning (SDK1+SDK2+PG1,
  SDK3+PG2, PG3+PG6 with sibling-merge resolution).
- Tranche 4 shipped 2026-04-30 (PG4+PG5).
- ARIS R2 post-impl rescore (codex `gpt-5.2` hard-mode high-reasoning,
  R5 reviewer-memory carry-forward) ran on cumulative tip `309c0a9`:
  FIX-FIRST at 8.59 / 7.2.
- PG-W1 fix-pass (5 commits: helper + store wiring + multi-process
  test + ADR-17 + merge) shipped same day; cumulative tip `0c909a6`.
- ARIS R2 round-2 rescore on `0c909a6`: PASS at 8.81 / 8.0.
- This release. **Phase 1 closes; Phase 2 (`persistence-coder` MVP)
  unblocks.**

### Known semantic footgun (deferred to v0.8.0a2)

ADR-17 documents that `_rebind_audit_datom_under_lock` changes the
audit entry id at persist-time. Substrate code paths that construct
`AuditEntry` Python-side and pass it through `transact_serializable`
will see the persisted entry's `:signature` differ from the in-memory
`AuditEntry.id` whenever the locked head differs from the Python-side
`prev_hash` at construction — i.e., under any concurrent multi-process
audit emission. Callers MUST NOT treat the pre-commit `AuditEntry.id`
as stable across persistence in cross-process audit code. A future
v0.8.0a2 may either (a) re-thread the post-rebind id back to the
caller via the `transact_serializable` return shape, or (b) redesign
what "signature" means at the substrate boundary.

## v0.5.2 — 2026-04-29 (Module 5: Txn — Clojure-parity closure)

Module-5 Clojure-parity sub-version that lives next to the v0.7.0a1
substrate trunk (branched from v0.7.0a1 `5deca24`, merged into substrate
trunk via `--no-ff` for the cumulative train into `main`). The package
version stays at v0.7.0a1 on this train; v0.5.2 is the txn-module
internal version stamp tracked in `CHANGELOG-txn.md` (see also
`docs/plans/2026-04-29-v0.5.2-clojure-parity-design.md` for design and
ADR rationale).

Six closures across N6 / N7 / N8 / F1 / F2 / F3:

- **N6** — `tx.alter` Hypothesis `@given` byte-identity at
  `max_examples=200` over a curated `_ALTER_FNS` table.
- **N7** — `tx.effect` Hypothesis `@given` byte-identity at
  `max_examples=200`; audit-chain projection helper using `position`
  surrogate for `prev_hash` (because `txn_commit` UUID hashes into entry
  content); deterministic `_recording_handler` conftest.
- **N8** — `Ref.spec_attr` regex tightened to EDN-keyword grammar
  (rejects leading-digit segments, multi-`/`, empty segments,
  special-leader-then-digit; permits trailing digits, `-foo`/`+bar`/`.baz`).
- **F1** — Atoms (single-cell CAS over datom-refs): `Atom` frozen
  dataclass + `db.atom(eid, *, initial)` + `swap` / `compare_and_set` /
  `reset` / `deref`. CAS uses spanning `store._lock`. Atom-in-dosync
  prohibition raises `AtomInDosyncProhibited` (intentional Clojure-parity
  deviation — preserves audit-chain replayability).
- **F2** — `tx.ensure(ref)` read-set padding: returns `deref`'d snapshot
  AND adds to `ensure_set`. Conflict-detection union at commit reads
  `read_set | ensure_set | write_set`. Provenance emits
  `:persistence.txn/ensure-set` separately so auditors can distinguish
  "deref'd for value" vs "padded for conflict-detection only".
- **F3** — `tx.commute(ref, fn_id, *args)` two-phase eager-at-body +
  reapply-at-commit: curated registry of 4 fns (`inc-by`, `sum-into`,
  `set-union`, `dict-merge-shallow`); `register_commute` gated by
  `PERSISTENCE_TXN_ALLOW_RUNTIME_REGISTRATION` env sentinel; commute
  refs deliberately NOT added to `read_set` (conflict-free by design);
  4 intra-txn cases specified — assoc-then-commute and commute-then-assoc
  both drop commute on `write_set` membership at commit (uniform
  invariant). Provenance emits `:persistence.txn/commute-log` in
  body-order.

### ARIS gate

- **R1 design fitness:** PASS at mean **8.6 / min 8** after 2 W-cycles.
- **R2 code quality:** PASS at mean **8.75 / min 8.6** after 2 rounds
  (codex `gpt-5.2` hard-mode high-reasoning). Round-1 PASSed gate at
  8.53 / 8.2 with 2 MAJORs flagged; W1 closed both in `ed3ad4a`
  (MAJOR-1: `db.atom()` allocation race — spanning `store._lock` at
  `_db_extension.py:240` + regression test
  `test_db_atom_concurrent_allocation_linearises`; MAJOR-2:
  `Transaction.commute()` docstring case-3 contradiction — rewrote
  docstring at `transaction.py:194-211` + adjacent eager-base comment +
  `_build_commute_facts` docstring). Round-2 closed at 8.75 / 8.6, zero
  new MAJORs, 1 MINOR nit closed in `b0f4abe` (literal "exactly one
  winner" assertions in race regression test). Per-axis Round 2:
  Correctness 8.9 / Concurrency Safety 9.0 / Audit-Chain Integrity 8.6 /
  Determinism 8.6 / API & Specs 8.7 / Maintainability 8.7. See
  `review-stage/v0.5.2-clojure-parity-r2/AUTO_REVIEW.md`.
- **R3 + R4** skipped — same warrant as v0.4.0a1 / v0.5.1 / v0.6.0a1
  (zero proposition / paper claim change).

### Suite

88 txn / 1492 full at branch start → **130 txn / 1560 + 7 xfailed full**
at the merge. All threading + Hypothesis tests deterministic across
5+ consecutive runs.

### Predecessor

`v0.7.0a1` substrate trunk at `5deca24`. Tagged `v0.5.2` (annotated,
local-only) at `b0f4abe` per project convention.

## v0.7.0a1 — 2026-04-29 (Module 7: capability-gated live REPL — WS + browser console)

Stream D of the v1.0 ferrari-first roadmap. Adds a live, capability-gated,
audit-emitting REPL surface over a WebSocket transport with a vanilla-JS
browser console UI — the operator-facing surface that Streams A/B/C/E/F
all hang off for production inspection, two-step edits, view-cursor
rewind, and branch-as-cursor-marker. ARIS R2 (code quality) PASS at mean
**8.90 / min 8.4** with two MAJORs closed via design-doc-only edits
(ADR-9 code-name realignment + ADR-13). Suite **1517 passed / 7 xfailed**
(+30 over v0.6.5; +5 D-INT integration tests).

### Added

- **`persistence.repl` package** (`src/persistence/repl/`). Public surface:
  `Capability`, `CapabilitySet`, `mint_token`, `store_token`, `WSServer`,
  plus the eight application-band error constants
  (`ERR_CAPABILITY_DENIED`, `ERR_AUTH_FAILED`, `ERR_TOKEN_INVALID`,
  `ERR_VERIFY_CHAIN_FAILED`, `ERR_REQUEST_HASH_MISMATCH`,
  `ERR_SESSION_EXPIRED`, `ERR_BRANCH_DEPTH_EXCEEDED`,
  `ERR_STALE_CURSOR_EDIT`).
- **WebSocket transport** (`_ws.py`, `_protocol.py`). `aiohttp.web`-based
  single-port server (ADR-1 + ADR-10). JSON-RPC 2.0 envelope (ADR-2)
  with closed application-band code table at `-32001..-32008` (ADR-9).
  Auth handshake on first frame: `repl/auth { token }` → returns
  deterministic `session_id = sha256(token_id + ":" + auth_clock_iso)[:16]`
  and the granted capability set.
- **Capability tokens** (`_caps.py`). Opaque random 256-bit tokens stored
  fact-store-backed (ADR-3). `Capability(op, qualifier)` pairs:
  `inspect:read`, `inspect:audit-tail`, `edit:write`, `rewind:any`,
  `branch:fork`, `auth:login`. `expires_at` enforced at every op-dispatch.
  Idempotent `revoke_token`; revocation propagates on next
  `validate_token` call (mid-op semantics: in-flight ops complete).
- **Four ops** (`_ops.py`). Capability-gated handlers, all dispatching
  through one shim that flattens params per ADR-12:
  - `repl/inspect` — `kind=entity` / `audit-window` / `causal-history` /
    `plan` (read-only; cap `inspect:read` or `inspect:audit-tail`).
  - `repl/edit` — two-step propose-confirm with `request_hash`-strip
    canonical-hash matching (ADR-7); zero substrate-source extension.
    Stale-cursor edit rejected with `-32008` (§5.2 invariant).
  - `repl/rewind` — sticky per-session view-cursor (ADR-5). Cursor only
    affects view; intervening `db.transact` writes still land.
  - `repl/branch` — cursor + depth marker (ADR-13; **NOT a `db.branch`
    store fork**). Advances `view_cursor_tx_time_iso` and increments
    `parent_chain_depth`; rejected with `-32007` past `max_branch_depth`
    (default 16). Safety rests on the stale-cursor edit invariant.
- **Audit emission** (`_audit.py`). Every op writes one `:repl/op`
  AuditEntry whose `principal` rides the REPL fields (`op_kind`,
  `view_cursor_tx_time_iso`, `parent_session_id`, `parent_chain_depth`)
  so Module 2's canonical AuditEntry slot set is intact. `verify_chain`
  returns `True` over a pure-REPL OR mixed programmatic+REPL chain;
  audit-window is hot-path read and exempted from self-emission per
  ADR-11. In-memory FIFO ring (hot cache, default cap 1000) +
  fact-store persistence (durable).
- **Browser console UI** (`static/index.html` / `app.js` / `style.css`).
  Vanilla JS, zero build step (ADR-6). Two-pane layout: command output
  (left) + audit tail (right). XSS-pinned via `textContent` only —
  the unsafe HTML-string DOM setter and unsafe dynamic-code constructors
  do not appear in the source (a regression test scans for absence).
  Token loaded from URL fragment `#token=...` then immediately scrubbed
  via `history.replaceState`. Audit-tail polled at 1Hz via
  `repl/inspect kind=audit-window` (W3 ADR-11 server-side gate
  prevents the poll from logging itself into the audit log).
- **D-INT integration test** (`tests/integration/test_v0_7_repl_e2e.py`,
  5 tests, 722 LOC incl. fixtures + docstrings):
  inspect-after-edit ⋅ rewind-cursor-isolation ⋅ branch-records-cursor-and-depth
  ⋅ replay-from-datoms-alone byte-identity (the W2 chain invariant at
  the REPL boundary, mirrors Stream B's Prop 6 defense via
  `canonical_dumps` projection) ⋅ audit-chain integrity with W3
  self-loop verified end-to-end.

### Design

- **13 ADRs** in `docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md`:
  - ADR-1..10: transport, protocol, capabilities, audit shape, view-cursor
    semantics, UI bundling, propose-confirm, subscription model, error
    envelope, async runtime.
  - **ADR-11** (W3): `repl/inspect kind=audit-window` polls don't
    self-emit — closes the 1Hz audit-tail self-loop that drowned the
    audit pane during browser verification.
  - **ADR-12** (W3): flat handler params, DSL is canonical — handler
    flattens `kind`-stripped params; no nested `params.params` shape on
    the wire.
  - **ADR-13** (W4 fix-pass): `repl/branch` is a cursor + depth marker,
    NOT a `db.branch` store fork — codifies shipped semantics; safety
    rests on §5.2 stale-cursor-edit rejection (any non-null-cursor
    session is read-only because `repl/edit` rejects with `-32008`).
- **R2 code-quality** at mean 8.90 / min 8.4 with zero MAJORs after W4:
  Correctness 8.4 / Robustness 8.8 / Readability 9.2 / Coverage 9.4 /
  Performance 8.7. Eight MINORs deferred to v0.7.x post-tag (browser
  audit-poll cosmetic flat-params drift, exception-class tightening on
  `_OpError` error-string surface, `_SyntheticAuthSession` factory
  hardening, etc.).

### Migration / behavioural notes

- **No substrate-source extension.** Module 7 is a layer over the
  existing `db.transact` / `db.branch` / `verify_chain` primitives; no
  `preview_transact`, no new fact-store APIs. Two-step propose-confirm
  is implemented entirely in `_ops.py` via `canonical_hash(params with
  confirm and request_hash stripped)`.
- **`branch_op` is read-only.** A "branched" session inherits the
  capability set including `edit:write` but cannot mutate state because
  its non-null `view_cursor_tx_time_iso` triggers `-32008`. To write at
  a past coordinate, operators must coordinate out-of-band with the
  substrate's `db.branch(...)` primitive directly (Phase 3 NeSy 2027
  may add a `branch:write` capability that swaps the session's DB).
- **Audit-window polls don't audit themselves.** Operators relying on
  the audit count to detect access-pattern anomalies need to
  account for the gate: only `kind ∈ {entity, audit-window-tail-by-id,
  causal-history, plan}` emit `:repl/op` AuditEntries. (Implementation
  is the inverse — `audit-window` is the lone exempt kind; all other
  inspect kinds DO emit.)

### Compatibility

- **Pre-release.** No prior `repl` API surface existed (was a one-line
  `[stub]` marker in `persistence/__init__.py`). All public exports are
  new in v0.7.0a1. Pre-release suffix `-a1` reflects substrate-internal
  alpha; will graduate to `v0.7.0` when paper Stream H lands its v1.0
  rewrite around the eval block (2026-05-28→06-04).
- **Optional install.** `pip install persistence[repl]` pulls the
  `aiohttp>=3.9.0,<4.0.0` extra. Substrate-only consumers do NOT need
  aiohttp.

### Stream-D timeline

- D1-D8: per-task subagent dispatch (1 fresh implementer + spec-reviewer
  + code-quality-reviewer per task), 8 commits.
- W3: micro-pass after browser verification on `chrome-MCP` tab
  `2101276577` + huashu-design first-pass surfaced 2 ⚠️ critical
  defects (audit-window self-loop + DSL/handler param-shape drift).
  Single subagent, single commit `dd31f37`. ADR-11 + ADR-12.
- D-INT: integration test subagent at `1ce8ac4` (5 tests, all PASS,
  +5 to suite total → 1517).
- D-FINAL.1 R2: PASS-with-W1 at mean 8.90 / min 8.4. Two MAJORs were
  design-doc drift only.
- W4 fix-pass: design-doc-only edits at `4157c64` closing R2 MAJORs
  (ADR-9 code-name realignment + ADR-13 branch-as-cursor-marker).
- D-FINAL.2: this release. **17-day margin to NeSy 2026 abstract
  deadline 2026-06-09 preserved.**

## v0.6.5 — 2026-04-28 (Module 3.X: MCTS — PUCT search + skill-library 4-gate closed loop)

Stream B of the v1.0 ferrari-first roadmap. Adds PUCT tree search over
the content-addressed Plan AST with an LLM-evaluator port, full
`:mcts/iteration` provenance for replay-from-audit-log alone (Prop 6),
and a `mcts_promote()` orchestrator that chains `mcts_search → promote
→ SkillLibrary.register` to close the search → promotion → reuse loop.
Single-flat-file impl per ADR-11 (`_mcts.py`) with provenance helpers
lifted to `_mcts_datoms.py`. ARIS R1 (design fitness) PASS at composite
8.90 / min 8.0; ARIS R2 (code quality) PASS at mean 8.94 / min 8.7.

### Added

- **`Action` algebraic data type** (`persistence.plan._mcts`).
  `SubstituteLeafAction(target_path, new_leaf)` /
  `AddStepAction(target_path, new_child)` /
  `ComposeWithSkillAction(target_path, skill_id)`. All
  `@dataclass(frozen=True, slots=True)`. `apply_action(plan, action,
  *, skill_library=None)` dispatches by `isinstance` (strict — third-
  party action subclasses fall through to `ValueError("unknown action
  kind")`). `_action_hash` composes via `Node.id` for nested Nodes;
  reuses the canonical-JSON helper from `_ast.py`. `MAX_PLAN_DEPTH = 32`
  enforced via two-layer guard (apply-time + ComposeWithSkill skill-
  plan ≤ MAX_PLAN_DEPTH//2 = 16).
- **`MCTSConfig`** `@dataclass(frozen=True, slots=True)`. Defaults:
  `c_puct=1.4`, `max_iter=200`, `max_unique_plans=64`, `expander_k=4`,
  `simple_regret_window=5`, `simple_regret_threshold=False`,
  `selection_temperature=0.0`. `__post_init__` runs bool-isinstance-FIRST
  then positive-only validators on every numeric field (Stream A
  W1.B/G4 anti-pattern preempted: `bool(...)` coercion never accepted).
- **`MCTSNode`** + **`MCTSEdge`** `@dataclass(slots=True)` (non-frozen
  for backup mutation). Per design §4: `MCTSNode` carries
  `plan_id / visits / total_value / children / is_terminal` with
  computed `q_value` property; `MCTSEdge` carries
  `action / action_hash / prior / child_plan_id / visits_through_edge /
  total_value_through_edge`. Prior lives on the edge, not the node.
- **`Expander`** `@runtime_checkable` Protocol with `propose(plan, *,
  k) → Sequence[tuple[Action, float]]`. `_StaticExpander(proposals,
  on_unknown="empty"|"raise")` for deterministic test harnesses.
  `LLMExpander(provider: Callable[[Node, int], Iterable[tuple[Action,
  float]]])` — pure delegation (prior-sum tolerance `_PRIOR_TOL = 1e-6`
  enforced in the `mcts_search` loop, not in the protocol).
- **`Evaluator`** `@runtime_checkable` Protocol with
  `evaluate(plan) → float`. `_StaticEvaluator` + `LLMJudgeEvaluator`
  (provider-callable injection — no registry indirection).
  `_is_finite_score` rejects NaN, Inf, bool, and non-numeric — emits
  `evaluator_returned_non_finite` reject datom (no silent coercion).
- **`mcts_search(initial_plan, *, expander, evaluator, started_at_ms,
  config=None, skill_library=None, db=None) → MCTSResult`**. Single-
  player PUCT loop (SELECT → EXPAND → EVALUATE on the just-expanded
  parent → BACKUP). `MCTSResult` is `frozen=True, slots=True`:
  `winner / winner_plan_id / initial_plan_id / search_id (content-
  addressed sha256) / iter_count / unique_plans_visited / terminated_by /
  root_q / tree_dump`. `tree_dump` canonical-ordered: lex sort by
  `(parent_plan_id, child_plan_id)`, 5-tuple includes `action_hash`.
  `db is None` short-circuits provenance (unit-test escape hatch).
- **Visit-conservation 3-case invariant** (root / interior / leaf-of-
  path) verified per design §16; pinned by
  `tests/plan/test_mcts_visit_conservation.py`.
- **Synthetic time discipline**: `t = started_at_ms + iter_index`. No
  wall-clock leaks; one `db.transact(...)` per iteration so each
  iteration is one audit-chain entry.
- **`:mcts/iteration` provenance schema** (`persistence.plan._mcts_
  datoms`). Per design §13 with kebab-case attr keys throughout.
  `phase ∈ {"start", "select", "expand", "evaluate", "backup",
  "reject", "search"}`. `expand` records carry both `_id` and
  `_canonical` slots for `SubstituteLeafAction.new_leaf` /
  `AddStepAction.new_child` so replay can materialize Node bytes
  without requiring the originating in-process state (W2 M4 closure
  for production-LLMExpander Prop-6 defense).
- **`mcts/prev-hash` Merkle chain**. Each datom commits `sha256(
  canonical_json(prev datom content))`; the search trajectory thus
  forms its own Merkle chain SEPARATE from the Module 2 effect-
  handler audit chain (W1 closure of the R2 audit-chain category-
  error: Prop 1/2/4 composition does NOT lift to the search layer
  for free).
- **Cache-miss-only recording** (ADR-10). Hits do not emit datoms;
  replay re-derives on demand. `MCTSReplayCacheMiss` test-local
  exception fires loud in the replay-loud-stub harness.
- **Reject reasons** (closed `frozenset`):
  `evaluator_returned_non_finite / evaluator_raised /
  plan_too_deep / compose_creates_cycle / skill_not_registered /
  plan_construction_raised`. `_classify_apply_failure` dispatches by
  isinstance — no string-substring matching on error messages
  (W1 micro-pass closure of R2 m1).
- **Cycle detection** on `ComposeWithSkillAction`: `_PlanCycleDetected`
  raised when the candidate plan's content-hash already appears in
  the looked-up skill plan's subtree set; mapped to
  `compose_creates_cycle` reject.
- **`mcts_promote(initial_plan, *, expander, evaluator, started_at_ms,
  skill_library, replay_engine, training_set, metric, scores_before,
  scores_after, threshold, db, config=None) → MCTSPromotionResult`**.
  Composition: `mcts_search → promote() → SkillLibrary.register`. No
  chained `optimize()` per design §12 — promotion gate is the source
  of truth for skill-library admission.
- **B-INT integration test** (`tests/integration/test_v0_6_5_mcts.py`).
  7-step body: setup → `mcts_search` with full provenance → verify
  `:mcts/search` summary → verify Merkle chain → verify reject
  schema → verify expand-output Node round-trip → REPLAY-FROM-DATOMS-
  ALONE with byte-identity assertion on `tree_dump`. Step 7 is the
  load-bearing Prop 6 test: caches reconstructed from `db.log()`
  only, Nodes materialized from `new_leaf_canonical` bytes, no
  cross-state cheating.

### Suite

`1084 → 1272 passed, 7 xfailed` (+188 over `v0.6.0a1` baseline; +153
under `tests/plan/`, +35 under `tests/integration/` and shared
fixtures). pyright + ruff clean on the three Stream B source files.

### Files

- `src/persistence/plan/_mcts.py` (1129 LOC — single flat module)
- `src/persistence/plan/_mcts_datoms.py` (416 LOC — provenance
  helpers + canonical Node round-trip)
- `src/persistence/plan/_mcts_promote.py` (144 LOC — promote
  orchestrator)
- `src/persistence/plan/_errors.py` (deltas: `PlanDepthExceeded`,
  `ExpanderContractError`, `EvaluatorContractError`)
- `src/persistence/plan/__init__.py` (public surface re-exports)
- `tests/plan/test_action_*.py` + `tests/plan/test_mcts_*.py`
  (28 unit files)
- `tests/integration/test_v0_6_5_mcts.py` (1 integration file)

### Design + impl docs

- `docs/plans/2026-04-28-v0.6.5-mcts-design.md` (1535 lines, 25
  sections, 12 ADRs — ARIS R1 PASS round-3 at 8.90 / 8.0 after W1
  + W2 fix-passes)
- `docs/plans/2026-04-28-v0.6.5-mcts-impl.md` (1191 lines, 11-task
  playbook B1–B9 + B-INT + B-FINAL)

## v0.6.0a1 — 2026-04-28 (Module 3: Plan — execution + optimization + 4-gate promotion)

Stream A of the v1.0 ferrari-first roadmap. Closes the
"plan as data → plan as runnable program" boundary by shipping
`execute()`, `optimize()`, `SkillLibrary`, and the four promotion
gates (G1/G2/G3/G4) wired through a single `promote()` orchestrator.
ARIS R1 (design fitness) + R2 (code quality) PASS at mean 8.92 / min 8.4.

### Added

- **`execute(plan, db, *, dispatcher=None) → ExecutionResult`**
  (`persistence.plan._execute`). Walks a `Node` AST, calls the per-tag
  `Handler` registered on a `Dispatcher`, and returns a frozen
  `ExecutionResult(leaves: tuple[LeafResult, ...], failures: tuple[FailureInfo, ...])`.
  `LeafResult` and `FailureInfo` are `@dataclass(frozen=True, slots=True)`.
  Failures are caught per-leaf and reported in `failures`; only handler-
  thrown exceptions of an explicitly-allowed set propagate.
- **Metric registry** (`persistence.plan._metric_registry`).
  `register_metric(name, fn)` / `lookup_metric(name) → MetricRef`
  / `unregister_metric(name)`. Process-local, idempotent re-registration
  rejected. `MetricNotRegistered` raised on lookup miss (now exported
  from `persistence.plan`).
- **`TrainingExample`** + `_canonicalize_training_set(...)`. Sorts
  examples deterministically and pins the canonical EDN form so DSPy
  optimization runs are reproducible across re-imports.
- **`_plan_to_dspy_module(node)`** forward adapter
  (`persistence.plan._optimize`). Lazy-imports DSPy 2.5+; explicit
  `OptimizerNotAvailable` when DSPy missing. Inverse adapter
  rebuilds a `Node` AST from the optimized DSPy program with full
  provenance pinning back to the source plan id.
- **`optimize(plan, training_set, metric, *, db, max_demos=...) → OptimizedPlan`**.
  End-to-end MIPROv2 wrapper: forward → optimize → inverse → emit
  `:plan/optimization` datom on the source plan's provenance. Caller-
  injectable dispatcher (W1.A4) keeps the optimizer pure.
- **`SkillLibrary`** (`persistence.plan._skill_library`).
  `register(skill_id, node)` / `lookup(skill_id) → Node | None`
  / `list_skills() → list[str]`. Cross-instance idempotency via fact-
  store log scan: re-registration of the same `skill_id → Node` content
  is a no-op; conflicting content raises. Backed by a
  `_PromotionRecordLike` `@runtime_checkable` Protocol so A5 stays
  decoupled from A7's `PromotionRecord` dataclass.
- **`gate_g1_replay_byte_identity(plan, replay_engine, db, *, window=None) → bool`**.
  Pulls a deterministic replay window, calls
  `replay_engine.compare(plan, audit_window) → dict` (positional-only
  via `/`), and returns False on `divergence_step != None`. Strict-key
  contract: missing `divergence_step` raises `TypeError`. Empty replay
  corpus → `False` + `UserWarning` (vacuous truth not accepted).
- **`gate_g2_audit_chain(db, *, window=None) → bool`**. Pulls audit
  entries in the window via the bitemporal store, requires
  `provenance[":signature"]` on every entry (raises `ValueError` on
  absence), then defers to `verify_chain()` for Merkle-prev-hash
  contiguity. Empty window → `False` + `UserWarning`.
- **`gate_g3_score_delta(scores_before, scores_after, threshold) → bool`**.
  Strict IEEE-754 `>=` comparison contract on `score_after - score_before`.
  Empty-list inputs raise `ValueError` (no vacuous pass).
- **`gate_g4_stub(g4_fn, *, plan, scores_before, scores_after) → bool`**.
  Stub for human / regulator approval. Calls `g4_fn(...) → dict`,
  reads `result["approved"]`, requires strict `bool` (truthy non-bool
  values raise `TypeError`). Phase-3 NeSy 2027 will replace the stub
  with the regulator-replay corpus surface (Stream F).
- **`PromotionRecord`** + **`promote(plan, db, *, replay_engine, scores_before, scores_after, threshold, g4_fn, ...) → PromotionRecord`**.
  Frozen, `slots=True` dataclass with content-addressed `promotion_id`
  (canonical-JSON sha256 over 10 keys). `promote()` orchestrates
  G1 → G2 → G3 → G4 in sequence and raises `GateFailure(message,
  partial_record)` on the first False gate, where `partial_record`
  carries the snapshot of which gates ran (and what their outcomes
  were) before the failure.
- **`GateFailure`** typed class (`persistence.plan._errors`) with
  class-level `partial_record: Any` attribute and explicit `__init__`.
  `Any` retained to avoid an import cycle with `_promotion`; runtime
  value is always a `PromotionRecord`.
- **End-to-end integration test**
  (`tests/integration/test_v0_6_plan_execution.py`):
  `parse → optimize → promote → register → lookup` on a real DSPy-
  mocked plan, exercising every public surface added in this release.
- **18 new commits** on `feat/v0.6-plan-execution`. Suite:
  `1018 → 1084 passed, 7 xfailed` (+66 over v0.5.1 baseline, +3 W1
  pin tests on the fix-pass).

### W1 fix-pass (post-ARIS)

Closes 3 R2 MAJORs, 3 R2 MINORs, 1 NIT, and 4 R1 design-doc drifts
identified by Codex `gpt-5.2` `model_reasoning_effort=high`:

- **W1.A** G1 strict-key membership check on
  `compare()` dict (raises `TypeError` instead of fail-open on
  missing `divergence_step`).
- **W1.B** G4 `isinstance(approved_raw, bool)` check (rejects truthy
  non-bool values like `"False"` string).
- **W1.C** G2 empty audit window now warns + returns `False`
  (`_G2_EMPTY_WINDOW_WARNING`).
- **W1.D/E/G** Design doc (`docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md`)
  tightened: ExecutionResult shape, Π → derivation persistence
  semantic (full record is in-memory cache; persistent reconstruction
  is Phase 3 NeSy 2027 scope), G1/G2 spec contracts.
- **W1.F-1** `:signature` required in `_datom_to_wire_for_audit`
  (raises `ValueError` on absence — prevents hash-equivalent audit
  entries with mismatched IDs).
- **W1.F-2** Simplified `_raise_gate_failure` to direct
  `raise GateFailure(message, partial_record)`.
- Doc fixes: `_skill_library.py` docstring (`plan.id → Node` → `skill_id → Node`);
  `__init__.py` adds `MetricNotRegistered` to public exports;
  integration teardown narrowed `except Exception:` → `except MetricNotRegistered:`.

### ARIS verdict

- R1 design fitness: PASS (4 MAJORs closed via doc updates).
- R2 code quality: PASS at mean **8.92** / min **8.4**
  (correctness 9.3, robustness 9.0, readability 8.7, test coverage
  9.2, performance 8.4). Gate: mean ≥ 8.5 and min ≥ 7.0.
- R3 paper fitness: deferred to Stream G cumulative ARIS R4 at v1.0.0.

### References

- Design: `docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md`
- Implementation playbook: `docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md`
- Review log: `review-stage/v0.6.0a1-aris/AUTO_REVIEW.md`
- Plan-module CHANGELOG: `src/persistence/plan/CHANGELOG-plan.md`

---

## v0.5.1 — 2026-04-27 (Module 5: Txn — rev O narrowings closure)

Closes the 5 carry-forwards from v0.5.0a1 `CHANGELOG-txn.md` § rev O
in a single tight release. Zero proposition impact. Tag drops the
`-aN` suffix (this is a patch release that closes named TODOs, not
a new module — first non-alpha tag in the persistence-os repo).

### Added
- `:persistence.txn/read-set` (sorted eid list) and
  `:persistence.txn/intent-log` (queue-ordered `[{:op, :kwargs}]` list)
  emitted on every commit datom's provenance. Direct read; no longer
  reconstructable-only.
- `_EdnValueSpec` (recursive: scalars + lists/tuples + str-keyed dicts)
  registered under `:persistence.txn/edn-value`. Replaces the v0.5.0a1
  placeholder `seq_of(_uuid_str_spec)` registration on
  `:persistence.txn/intent-log` with the real per-element map shape
  `keys({":op": str_, ":kwargs": map_of(str_, edn-value)})`. Strict
  conformance at commit time (option (3) — see design doc § N1).
- `AuditEntry.txn_commit: str | None = None` first-class field.
  `audit_entry_to_datom` emits `:effect/txn-commit` on provenance only
  when set (symmetric with `:episode`); `datom_to_audit_entry` decodes
  it back. Closes a latent v0.5.0a1 corruption where the
  `_txn_commit` sentinel polluted every audited replay's `args_hash`.
- `Runtime.perform(op, args, *, txn_commit=None)` — typed kwarg path
  for txn-replayed intents; legacy `args["_txn_commit"]` direct path
  still works (audit handler pops the sentinel before hashing).
- `Ref.spec_attr: str = "value"` field (excluded from eq/hash via
  `field(compare=False)`). Allows per-ref attribute specs:
  `db.ref("acct", spec_attr="account/balance")`. Default `"value"`
  preserves v0.5.0a1 behavior bit-for-bit.
- Hypothesis `@given` byte-identity property at `max_examples=200`
  in `test_replay_byte_identity_property.py`. Single-shot `assoc`
  transactions covered; `tx.alter` / `tx.effect` byte-identity
  coverage deferred to v0.5.2.

### Changed
- Helper extraction: `_commit_attempt(tx) -> bool` in
  `transaction.py` is now the single point where spec-validate /
  facts-build / lock+conflict-check / transact happens. Both
  `with db.dosync()` (CM) and `@db.dosync` (decorator) paths route
  through it. `_build_commit_provenance` and
  `_replay_effect_intents` extracted as siblings.
- `_build_commit_provenance` now runs OUTSIDE the
  `with db.store._lock:` block — conformance has no DB-state
  dependency, so commit-time SpecError no longer holds the lock
  under contention.
- `_raise_spec_error(result)` helper centralizes the
  `from persistence.spec._registry import SpecError; raise
  SpecError(result)` pattern (latent v0.5.0a1 export gap;
  `# type: ignore[arg-type]` documented).

### Fixed
- **Audit-chain hash continuity for v0.5.0a1 → v0.5.1.** The
  AuditEntry `content` dict that feeds `prev_hash` linkage now
  inserts `txn_commit` only when not None (mirrors the wire-form
  `:effect/txn-commit` emit-only-when-set semantics). v0.5.0a1
  audit chains continue to verify byte-equal in v0.5.1 for
  non-txn-replayed entries.
- **Latent `args_hash` corruption from v0.5.0a1.** Audit handler
  now `args.pop("_txn_commit", None)` BEFORE
  `canonical_hash(args)`, so two replays of the same intent across
  different commits produce identical `args_hash`. Pinned by
  `test_args_hash_excludes_txn_commit`.

### Design pins held
- `PLAN_CANONICAL_VERSION` stays at 1 (zero canonical-form change).
- Zero proposition impact (Prop 1–5 unchanged).
- 912 + 7 xfailed v0.5.0a1 baseline preserved; +19 new tests
  (931 + 7 xfailed total).

### ARIS gate
- R1 design fitness: 8.06 → re-pass after W1 fix-pass
- R2 code quality: PASS at 9.19 / 8.5
- R3 + R4 skipped — same warrant as v0.4.0a1 (no proposition /
  paper claim change).

### Predecessor
- `v0.5.0a1` at `9377b86` — Module 5 Txn shipped; rev O narrowings
  documented but deferred to keep the tag inside the paper window.

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
