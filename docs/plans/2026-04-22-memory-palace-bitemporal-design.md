# Memory Palace Bitemporal Retrofit — Design

**Date:** 2026-04-22
**Author:** Nawfal Saadi (with Claude assistance)
**Track:** `ai-box/conductor/tracks/memory-palace-bitemporal-retrofit_20260422/`
**Status:** Design approved; awaiting implementation plan
**Prereq:** Persistence OS Phase 1 FROZEN CLEAN at `e8347c6` (tagged `v0.1.0a1`), Phase-2 pre-cleanup at `ac72680` (625 tests). Taxonomy-v2 Phase 6 merged on ai-box main at `518f720b`; Phase 5B (re-embed) in design under `ai-box/conductor/tracks/memory-taxonomy-v2_20260417/`.

## 1. Thesis

The vault memory system becomes bitemporal with the `persistence.fact` datom log as the canonical write-ahead log. Qdrant and Kuzu become deterministic projections of the log — rebuildable from scratch, incrementally maintained in steady state. Taxonomy-v2's tier/bucket capability model composes orthogonally: tier/bucket values ride in `:datom/provenance` (immutable with the fact); enforcement stays in the existing PG capability table at query time. The retrofit adds `/vault-as-of` time-travel, regulator-grade audit chain over vault operations, and counterfactual memory branches — without affecting the hot path of `/vault/recall`.

This is the Phase 2 flagship move for Persistence OS and the flagship differentiator for the Juba OS / AI Box memory system: every agent framework on the market treats memory as a vector-store sidecar; we treat it as a first-class time-travelling substrate with regulator-grade provenance.

## 2. Drivers (in priority order)

1. **Regulatory / audit** — per-memory provenance, preserved retraction, read-logging against a specific vault snapshot, `verify_chain` iff integrity extended from audit log to vault state.
2. **Product feature** — `/vault-as-of?t=X` surfaces in iOS + dashboard; bitemporal journal for personal use (Nawfal's Conjur-philosophy self-reflection).
3. **Research paper (downstream, SEPARATE track)** — NeSy 2026 §6.3 walkthrough + Case B (Adaptive Trader v2) evidence. The paper's `bench/regulator_replay/` demo dataset is Dockerised and NOT plumbed into production vault.

## 3. Non-goals (YAGNI)

- Replacing Qdrant or Kuzu. Both stay as hot-path indexes.
- Changing recall latency on the hot path.
- Modifying taxonomy-v2 semantics. The tier/bucket capability table is authoritative; we read from it, don't duplicate.
- Building a new research benchmark dataset. The paper track owns that separately.
- Snapshotting historical capability maps (rejected as complexity bomb; see §5.3).

## 4. Architecture

### 4.1 Core write path

```
POST /vault/remember
    │
    ▼
persistence.effect.Runtime  (audit handler wraps the call)
    │
    ▼
VaultFactStore.remember(facts)
    │
    │  db2 = db.transact(list[dict], provenance={...})  ← returns NEW DB
    │        real tx pulled from db2.log()[-1].tx
    │        atomic, BEGIN IMMEDIATE inside SQLiteStore.allocate_and_append
    │
    ├──► Qdrant upsert  (vector + payload: {tier, bucket, valid_from, valid_to, tx, ...})
    └──► Kuzu upsert    (entities + edges, bitemporal properties)
    │
    ▼
audit entry (Merkle-linked to prior, references tx)
```

API surface notes — these match `persistence.fact.DB` at `ac72680`, not an earlier design sketch:

- **`DB.transact(facts: list[dict], provenance: dict | None = None, *, force_retroactive: bool = False) -> DB`** — returns a new `DB` bound to the same store. Callers pipe `db = db.transact(...)`. The `tx` integer is allocated atomically inside `store.allocate_and_append` under `BEGIN IMMEDIATE`; the caller retrieves it via the returned `DB`'s log tail (`db2.log()[-1].tx`) or from the first datom of the appended batch.
- **Facts are dicts**, not pre-constructed `Datom`s: each has `e`, `a`, `v`, `valid_from`, optional `valid_to`, optional `op` (default `"assert"`). The store stamps `tx`, `tx_time`, and `prompt_hash` provenance.
- **Cardinality-one auto-retraction is built in** — `DB.transact` finds the prior open assert for `(e, a)` and appends a companion retract in the same tx. `VaultFactStore` relies on this; it does NOT author manual companion retracts.
- **Retroactive corrections raise `RetroactiveCorrectionError`** unless the caller passes `force_retroactive=True`. Vault retractions are present-dated by default (see §4.2). `force_retroactive=True` is only invoked through an explicit `/vault/correct-retroactively` admin path.

**Read-your-writes contract.** Projection is synchronous in the same unit of work: `VaultFactStore.remember` commits the datoms, then upserts Qdrant + Kuzu, then returns. If either projection upsert fails after the datom commit, a `ProjectionError` bubbles to the caller with the committed `tx`; the memory is recoverable via `/vault/as-of?tx_min=<tx>` and the repair job backfills the projection. The repair job exists for failure recovery only, NOT as a steady-state async projector — `/vault/recall` MUST see a memory immediately after `/vault/remember` returns 200.

**Per-memory serialisation.** `DB.transact` linearises at the store level (`BEGIN IMMEDIATE`), but two simultaneous `/vault/remember` on the same `memory_id` (same `e`) can still race the Qdrant upsert and produce a lost-update. `VaultFactStore` serialises per `memory_id` via a process-local keyed lock (`asyncio.Lock` keyed by hash of `memory_id`). The repair job acquires the same lock before re-projecting a given memory, so repair cannot resurrect retracted content over a live commit. Pinned in Phase 1 plan Task 3.

**The vault you query is a deterministic function of the log** (modulo the Gemini embedding model itself — see §4.7). This is the property that makes the audit story hold; it is not optional.

### 4.2 Datom schema for vault

A single `/vault/remember` call appends 4–7 datoms in one `allocate_and_append` transaction. Example for `remember("I moved to Casablanca in 2022", tier=L2, bucket=nawfal-self)`:

| `e` | `a` | `v` | `tx` | `tx_time` | `valid_from` | `valid_to` | `op` | `provenance` |
|---|---|---|---|---|---|---|---|---|
| `mem-123` | `:memory/content` | "I moved to Casablanca in 2022" | 42 | now | now | null | assert | {source, audit_id, tier=L2, bucket, user_id, llm_model, confidence} |
| `mem-123` | `:memory/embedding_hash` | `sha256:…` | 42 | now | now | null | assert | ↑ |
| `mem-123` | `:memory/entity_refs` | `[ent-casa, ent-2022]` | 42 | now | now | null | assert | ↑ |
| `mem-123` | `:memory/cluster_id` | `cluster-life-events` | 42 | now | now | null | assert | ↑ |

**Retraction preserves history — present-dated by default.** A correction at tx=67 dated now ("actually 2021") goes through `DB.transact` as a single new assert with `valid_from=now`:

- Caller transacts `[{e: mem-123, a: :memory/content, v: "…2021", valid_from: now}]`.
- `DB.transact` finds the prior open assert `(mem-123, :memory/content, "…2022")` and appends a companion retract with `valid_from=<prior.valid_from>` and `valid_to=now` in the same tx.
- Net effect: two datoms appended at tx=67 — the companion `retract` closing the prior open interval, and the new `assert` opening a fresh interval from `now` forward.

The original assertion is never deleted. `verify_chain` stays intact. This answers BOTH "what does the vault believe held at 2026-04-10" (2022, because `valid_from(old) ≤ 2026-04-10 < valid_to(old) = now(tx=67)`) AND "when did we learn the correction" (2026-04-15 at tx=67). Transaction-time (`db.as_of(tx_time)`) and valid-time (`db.as_of_valid(world_time)`) are distinct axes; §4.5 pins which one the `/vault-as-of` endpoint serves.

**Retroactive corrections** (e.g. "actually, the WACC we told you last week was wrong — the real value was lower starting from two weeks ago") require `force_retroactive=True`. Exposed only through an admin-gated `/vault/correct-retroactively` path, never through the default `/vault/remember`. Rationale: retroactive writes are forensically significant and must be gated — `DB.transact` raises `RetroactiveCorrectionError` if a new assert's `valid_from` is earlier than the prior's `valid_from` without the explicit opt-in, and Phase 1 surfaces that refusal as a 409 to the client.

**Retraction edge cases pinned in Phase 1 Task 2 tests:**

- **Double retraction** — two `/vault/correct` calls in rapid succession on the same memory: second `DB.transact` refuses via `RetroactiveCorrectionError` if the second correction is dated earlier than the first's commit; otherwise the second correction's `valid_from` closes the intermediate state's open interval.
- **Non-existent memory retraction** — `/vault/forget(memory_id=mem-nonexistent)` raises `MemoryNotFound` *before* calling `DB.transact`; no datom is appended, no audit entry written.
- **Content-hash retraction** — the retract datom's `v` is the prior fact's content (hash-by-value), not the new content. `DB.transact` handles this automatically via `_find_prior_assert`; `VaultFactStore` MUST NOT author retracts manually.
- **Tier/bucket change via retract-and-reassert** — demoting a memory from L2 to L1 is two datoms in one tx: the companion retract carries the old tier in its provenance (immutable), the new assert carries the new tier. Historical queries at `t < now` see the old tier; capability enforcement at query time is against the current caller (§4.4).

This is the Datomic/Conjur win that makes competing agent memory libraries look flat.

### 4.3 Qdrant payload schema (on winners only — see §7 Phase 5B bundle)

For every winner memory (no `duplicate_of`), the Qdrant payload carries:

| Field | Type | Purpose |
|---|---|---|
| `valid_from` | ISO 8601 UTC string, `Z`-suffixed | World-time this fact became true |
| `valid_to` | ISO 8601 UTC string, **or omitted** | World-time this fact stopped being true; omit when currently-valid (faster `must_not has_field` filter than eq-null) |
| `tx` | int64 | Datom log transaction number (0 = genesis sentinel) |
| `tx_time` | ISO 8601 UTC string | System-time the datom was committed |
| `op` | string, `"assert"` or `"retract"` | Canonical operation |
| `entity_id` | UUID string | Datom entity ID, = Qdrant point ID |
| `embedding_hash` | string, `sha256:…` | Hash of the Gemini vector bytes; proof payload matches what the datom recorded |
| `tier` | string, `"L0"` / `"L1"` / `"L2"` / `"L3"` | Taxonomy-v2 sensitivity (immutable with fact) |
| `bucket` | string, e.g. `"nawfal-self"` | Taxonomy-v2 scope (immutable with fact) |
| `audit_id` | string (sha256 prefix) | Pointer to the audit entry that produced this write |

Qdrant's payload filtering handles the `valid_from ≤ t AND (valid_to IS NULL OR valid_to > t)` query for `/vault-as-of`. Indexed on `tx`, `tier`, `bucket`, `valid_to` absence.

### 4.4 Taxonomy-v2 × bitemporal composition

Both axes sit on the datom, but each is enforced where it already lives:

- **Tier/bucket values** ride in `:datom/provenance` and in Qdrant payload. Immutable with the fact. A memory written as L2 is *always* tagged L2, regardless of future capability changes.
- **Enforcement** at query time uses the **current caller's capabilities**, not historical ones. A demoted agent cannot time-travel into their privileged past; a freshly-unlocked Mom session can reach old L2 memories. Rule: `can_see(caller, fact) = current_capability(caller).matches(fact.bucket) AND current_capability(caller).max_tier >= fact.tier`.
- This matches regulatory intuition: who is asking now, under what rights, against what immutable historical facts. It also avoids the complexity bomb of snapshotting the capability map per-query.
- The `taxonomy-v2` PG capability table is authoritative and queried live on every read. No duplication into `persistence.fact`.

### 4.5 Read paths — three query types

`persistence.fact.DB` exposes two time axes — **transaction-time** via `db.as_of(t)` (what the DB had learned by system-time t) and **valid-time** via `db.as_of_valid(vt)` (facts the DB believes held in the world at time vt). They are NOT interchangeable; `/vault-as-of` must pick one.

**Decision:** `/vault-as-of?t=X` serves **valid-time**. Rationale — the product promise is "what did the vault believe on 2026-04-10 about Casablanca?", which means "facts asserted to hold in the world on 2026-04-10," not "facts the DB had on record by end-of-day 2026-04-10." Regulatory audit replay (§4.6) uses transaction-time (`vault_snapshot_tx`) and is a separate endpoint / query mode. The distinction is explicit in the URL contract:

- `/vault/as-of?vt=2026-04-10&q=...` → `db.as_of_valid(2026-04-10)` then project / Qdrant filter by `valid_from ≤ vt AND (valid_to IS NULL OR valid_to > vt)`.
- `/vault/as-of?tx=<int>&q=...` → `db.as_of(tx_time_of_tx)` for audit reconstruction; not exposed to end users, used by the audit verify path only.
- `/vault/as-of?t=X` with no axis qualifier is rejected with 400; clients must pick `vt` or `tx`.

**(a) Present-state** — 99% of traffic, zero latency impact:
- `/vault/recall("wacc calculation")` → Qdrant vector search with filter `valid_to=omitted AND (taxonomy-v2 rule)`. Kuzu graph walk unchanged. Present-state is the special case "no time qualifier provided."

**(b) Time-travel (valid-time)** — new, cold path:
- `/vault/as-of?vt=2026-04-10&q="wacc calculation"` → Qdrant vector search with filter `valid_from ≤ vt AND (valid_to IS NULL OR valid_to > vt)`. Mirror of `DB.as_of_valid(vt)` at the projection layer. ~2× present-state cost because an extra indexed comparison runs pre-vector; still <100ms target. Taxonomy-v2 applies at query time using the **caller's current capabilities** (not historical).

**(c) Audit reconstruction (transaction-time)** — rare, offline:
- Replay an agent session's audit entries. For each `:vault/recall`, extract `vault_snapshot_tx`, compute `tx_time_of_tx` from the datom log, call `db.as_of(tx_time_of_tx)`, re-run the query against the resulting `DBView`, recompute `result_hash`, must match. `verify_chain` semantics extended from the audit-log Merkle chain to the vault state at query time. Prop 4 now covers the vault layer.

### 4.6 Audit integration (Phase 3 scope)

Every `/vault/recall` and `/vault/remember` runs inside a `persistence.effect.Runtime` wrapped in the existing audit handler. Audit entry fields:

- `op`: `:vault/recall` or `:vault/remember`
- `args_hash`: hash of the query (for recall) or content (for remember)
- `result_hash`: hash of (memory IDs returned, vault snapshot tx at query time)
- `policy_id`: tier enforcement policy in force at call time
- `handler_chain`: `(audit, vault, taxonomy, fact)`
- `principal`: `{user_id, tenant}`
- `vault_snapshot_tx`: the max tx visible to this query (the transaction-time "as-of" of the read; carried in ctx via the `ctx_provider` extension — see ADR below)

The Merkle chain is `verify_chain`-able. **Audit replay** becomes: given audit entry E, look up `vault_snapshot_tx`, compute its `tx_time`, call `db.as_of(tx_time)`, re-run the query against the resulting view, recompute `result_hash`, must match.

**Audit property claim.** "Regulator-grade" is a marketing framing; the technical property this design delivers is narrower and more precise:

- **Merkle-chained:** each audit entry's `id` is `sha256(canonicalise(content))` with `content.prev_hash` pointing at the previous entry's `id` (inherited from `persistence.effect.handlers.audit`).
- **`verify_chain` iff integrity:** Prop 4, extended — `verify_chain(entries)` returns `ok` if and only if (a) no entry has been tampered with and (b) no entry has been reordered or deleted. Extension to vault: `ok` additionally requires that for every `:vault/recall` entry, re-running the query at `db.as_of(tx_time_of(vault_snapshot_tx))` against the same datom log reproduces `result_hash`.
- **Tier-preserved:** tier and bucket ride in immutable datom provenance; audit entries reference `memory_id` and `tx`, so tier is never "lost" across retractions.

These three properties, taken together, are what makes the system legible to a regulator. Phase 3 tests (§7.4) pin each one. The phrase "regulator-grade" is reserved for external-facing copy that explicitly lists the three properties; internal docs use "Prop 4 extended over vault layer."

**ADR: `vault_snapshot_tx` injection via `ctx_provider`.** Today `make_audit_handler` freezes its `ctx` at construction — `run_id`, `principal`, `policy_id` are all fixed for the handler's lifetime. `vault_snapshot_tx` must vary per invocation (each `/vault/recall` sees a different tx). Options considered: (1) add `vault_snapshot_tx` as a first-class field on `AuditEntry` — triggers its own ARIS because of the canonicalisation byte-invariant; (2) stuff it into `principal` — semantically wrong, `principal` is about who, not what-snapshot; (3) **add a `ctx_provider: Callable[[], dict] | None = None` extension to `make_audit_handler`** — evaluated per clause invocation inside the `audit_name` mask, result merged into `content` before `_canonicalise_content`. Chosen: option 3. Smaller surface, no schema change, composes with existing `drift-pin` test (extra key on dict side mirrors extra key on dataclass side, which already preserves unknown keys via `__post_init__`). A small PR to `persistence.effect` ships with Phase 3 alongside a new drift-pin test case for `ctx_provider`-injected fields. Spec'd against `:persistence.effect/audit-entry` compatibility — `vault_snapshot_tx` registered as an optional field.

#### 4.6.1 Middleware placement

The FastAPI app today wires middleware in `ai-box/apps/backend/src/app/main.py` (around line 439). Bitemporal retrofit adds a `VaultRuntimeMiddleware` that:

- Constructs a `persistence.effect.Runtime` per request scoped to `/vault/*` routes.
- Installs the audit handler with `ctx_provider=lambda: {"vault_snapshot_tx": _peek_tx()}` where `_peek_tx` reads from the `VaultFactStore` singleton.
- Binds the runtime to `request.state.vault_runtime`; route handlers `with request.state.vault_runtime:` before calling `VaultFactStore.recall / remember`.
- Position in the stack: **below `TraceContextMiddleware`** (so trace IDs are visible to audit entries) and **above the API router** (so every `/vault/*` route inherits the runtime). Non-`/vault/*` routes skip the middleware via a path predicate so we don't pay the construction cost for every `/health` ping.

Phase 3 Task 1 adds the middleware; the route handlers for `/vault/recall` + `/vault/remember` + `/vault/as-of` + `/vault/branch-from` all sit inside the `with` scope.

### 4.7 Counterfactual memory branches (Phase 4 scope)

`persistence.replay` mapped onto vault state. A branch is the datom log forked at `tx=k` with one or more intervention datoms (retractions/replacements), projected to a **temporary** Qdrant collection for agent replay. "If the agent had not known X at time T, would it have decided Y?"

**Determinism contract — qualified.** The branch is:

- **Deterministic at the datom layer** — `DB.branch(t, assertions)` seeds a fresh `InMemoryStore` from `db.as_of(t).datoms`, replays the interventions, and produces an identical `list[Datom]` byte-for-byte across invocations (subject to `_coerce_dt` normalising tz-naive inputs, which is stable). Same `(t, interventions)` → same datom log → same datom-level `result_hash`.
- **Hash-verifiable at the datom layer** — the branch's `DB.log()` can be Merkle-hashed; two replays produce the same root. This is the property the paper §6.3 walkthrough cites.
- **NOT deterministic at the vector layer.** Gemini embeddings are subject to model drift — re-embedding the same content string on a different model version produces a different 3072-d vector. The branch's temporary Qdrant collection inherits existing embeddings from the parent collection (copied by `entity_id`, NOT re-embedded), so read-path determinism holds *as long as the parent collection's embeddings are not rotated mid-branch*. The Phase 5B re-embed event counts as a rotation; branches outliving a re-embed are invalidated and must be re-created.

Short version: determinism modulo the embedding model; hash-verifiability at the datom layer, not the vector layer.

API surface: `POST /vault/branch-from` with `{t, interventions: [...]}` returns a branch_id; `GET /vault/branch/{id}/recall?q=…` runs recall against the branched substrate. Branch state lives in a separate Qdrant collection namespaced by `branch_id`, TTL'd after 24h via two-phase cleanup (mark-expired + grace window, then drop — prevents racing with an in-flight recall).

## 5. Phase breakdown (full scope, no leave constraint)

| Phase | Scope | Owner | Duration est. | Depends on |
|---|---|---|---|---|
| **0. Design doc + ADR + ARIS** | This doc, plus 2 ARIS rounds on the design before code | Single-session | 2-3 days | — |
| **1. Datom schema + dual-write vault** | Vault writes append to datom log + project to Qdrant/Kuzu. Present-state read path unchanged. Feature-flagged behind `AIOPS_VAULT_BITEMPORAL_ENABLED`. | Parallel: backend-api + backend-data + tests | 5-7 days | Phase 5B re-embed landed (~2026-04-26) — winners carry genesis bitemporal payload before Phase 1 projector goes live |
| **2. `/vault-as-of` endpoint + iOS/dashboard time-picker** | Cold-path read API, UI surfaces. Taxonomy-v2 applied at query time. | Parallel: backend-api + frontend + iOS | 3-4 days | Phase 1 landed |
| **3. Audit integration** | Wrap `/vault/recall` + `/vault/remember` in persistence.effect audit handler. `vault_snapshot_tx` in every audit entry via `ctx_provider` extension. Extends `verify_chain` iff to vault layer. | Single workstream, backend-api + tests | 3 days | Phase 1 landed; `ctx_provider` PR to `persistence.effect` merged |
| **4. Counterfactual branches** | `/vault-branch-from?t=X&interventions=[...]`. Temporary Qdrant collections. Paper §6.3 walkthrough. Investor demo. | Parallel: backend-data + tests + research | 4-5 days | Phase 1 landed; `bench/regulator_replay/` scaffold in persistence-os |
| **5. Migration + Kuzu backfill** | Phase 5B (taxonomy-v2) bundle handles Qdrant genesis. Residual: Kuzu bitemporal property backfill from datom log. | Single workstream | 2 days | Phase 1 landed |

**Total:** ~3-4 weeks of calendar time with parallel execution. Research-paper track runs independently, not blocked by implementation.

## 6. Constraints on Phase 5B (taxonomy-v2 re-embed bundle)

The parallel taxonomy-v2 session is bundling bitemporal payload fields into their Phase 5B re-embed migration. The 8 pinned answers below are HARD CONSTRAINTS on Phase 1 of this track. See vault memory `aac67386-6a67-4798-9b32-81d6080f80cd` for the full reply.

1. **Field schema (winners only, `duplicate_of IS NULL`)**: `valid_from, valid_to, tx, tx_time, op, entity_id, embedding_hash`, plus existing taxonomy-v2 fields (`tier, bucket, user_id, ...`).
2. **Genesis `tx = 0`** — reserved sentinel. My Phase 1 `allocate_and_append` starts at `tx=1`; `WHERE tx > 0` distinguishes real writes.
3. **Omit `valid_to`** at genesis (faster Qdrant filter than eq-null). `invalidated_by` not in Qdrant payload at all (system-time, datom-log-only).
4. **Kuzu out of scope for Phase 5B, in scope for Phase 5 of this track** — backfill Kuzu bitemporal from datom log during projection build-out.
5. **Order**: Phase 5B ships first (~2026-04-26), Phase 1 layers on top.
6. **`re_embedded_at` ≠ `tx`** — different semantics, keep both. `tx_time` at genesis coincidentally equals `re_embedded_at` but stays semantically distinct.
7. **280 needs_review rows**: leave alone, no bitemporal fields, not migrated.
8. **Winner-only bitemporal fields** — duplicates (`duplicate_of` set) get NO bitemporal fields; they are forensic breadcrumbs, not canonical. Phase 1 projector enforces the invariant "bitemporal fields present iff has corresponding datom."

## 7. Testing strategy

### 7.1 Unit (Phase 1)

- Datom schema for vault entities — every field in `:memory/*` conforms to a registered `persistence.spec` entry.
- `_canonicalise_content` (inherited from persistence.effect) applies to vault provenance without re-canonicalising stable fields.
- Qdrant payload encoder: round-trips ISO-8601 timestamps without precision loss.

### 7.2 Integration (Phase 1-3)

- Dual-write atomicity — datom commits + both projections succeed; if either projection fails, repair job replays and restores parity.
- Repair job idempotency — replaying a tx that already projected is a no-op.
- `/vault/recall` present-state latency regression (<5% p95 delta vs. pre-retrofit baseline).
- `/vault/as-of` correctness — retracted memories invisible at `t > valid_to`, visible at `t ∈ [valid_from, valid_to)`.

### 7.3 Property (Phase 1)

Projection determinism is the load-bearing property of this design. The Phase 1 property-test suite mirrors the `persistence.effect` 29-case drift-pin (`tests/effect/test_audit_canonicalize_drift_pin.py`) at equivalent strength for the projection layer. Specifically:

- **Hypothesis strategy `random_vault_op_sequence`** — generates sequences of `remember / correct / forget` ops over a bounded entity set (N ≤ 20 memories, sequence length ≤ 100), including retraction-then-re-assert and interleaved concurrent-on-different-memories patterns. Valid-time dates drawn from a bounded range; retroactive corrections excluded (tested separately).
- **Invariant `qdrant_projection_is_log_function`** — for any generated sequence, two executions produce byte-identical Qdrant payloads per `(entity_id, tx)` after `key`-sorting the collection. Assertion: `sorted(qdrant_live) == sorted(replay_qdrant_from_log(db.log()))`. Breaks if the projector depends on ordering, wall-clock, or uninitialised state.
- **Invariant `kuzu_projection_is_log_function`** — same property for Kuzu node + edge tables, keyed by `(entity_id, tx)`.
- **Invariant `partial_replay_is_idempotent`** — replaying a subset of the log (say, 50% of tx range) followed by the remaining 50% produces the same final projection as replaying 100% in one pass. Rules out path-dependence.
- **State-machine test `concurrent_write_plus_repair`** — interleaves `DB.transact` and `repair_job.replay_unprojected` in a Hypothesis state machine; asserts no lost updates, no resurrected retractions, final projection matches `replay_qdrant_from_log(db.log())`.
- **Out-of-order projection replay** — force-shuffle the log ordering passed to the replay function; assertion: sorting by `tx` inside the replayer produces the same projection as sorted input. (Breaks if the replayer silently assumes ordering.)

The persistence.effect drift-pin is the prior-art template; Phase 1 Task 10 implements the Hypothesis strategy and Task 11 implements the state machine. "Modulo ordering" in the invariant names means set-equality after key-sorting, not byte-identity of an unsorted dump — the phrase is precisely qualified, not hand-waved.

### 7.4 Audit (Phase 3)

- `verify_chain` at vault layer: given an audit entry's `vault_snapshot_tx`, re-run the query against the log, recompute `result_hash`, assert equality.
- Prop 4 extended: tamper any vault datom → audit chain fails.

### 7.5 Counterfactual (Phase 4)

- Branch-and-restore: create branch, mutate, assert base collection unchanged.
- Branch expiration: TTL'd collections auto-deleted after 24h.
- Determinism: same `(t, interventions)` → same `result_hash` across invocations.

## 8. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Projection divergence (datom log disagrees with Qdrant/Kuzu) | Repair job replays from log; property tests assert determinism; scheduled weekly consistency check |
| Qdrant payload schema change event | Shadow collection with new schema + swap; reversible via Phase 5B `duplicate_of` convention |
| `tx` counter contention under concurrent writes | `SQLiteStore.allocate_and_append` under `BEGIN IMMEDIATE`; 16-thread stress test from ARIS R2 P-concurrency applies directly |
| Taxonomy-v2 capability changes break historical queries | Enforcement at query time with current capabilities; tier/bucket on facts immutable |
| Retraction storm (many corrections in short window) | Batch `allocate_and_append` — already supported by persistence.fact |
| `/vault-as-of` slower than product target | Qdrant payload indexing on `valid_from`, `valid_to`, `tx`; benchmark before GA |
| Paper-vs-product coupling | Separate tracks enforced; `bench/regulator_replay/` stays in persistence-os repo, not ai-box |

## 9. Open questions — none freeze-blocking

Freeze-resolved design questions:
- Driver priority: 2+3 with 1 as downstream beneficiary
- Source of truth: datom log canonical (Option B)
- Backfill vs fresh: backfill, handled by Phase 5B bundle
- Qdrant migration scope: bundled with Phase 5B re-embed event
- Pre-leave target: not applicable, full scope now
- Field schema: pinned in §6 with Phase 5B coordination
- Counterfactual API: `/vault/branch-from` in Phase 4, deferred detail to implementation plan
- `/vault-as-of` time axis: **valid-time** (see §4.5); `tx`-indexed variant internal-only for audit replay
- `vault_snapshot_tx` injection: `ctx_provider` extension to `make_audit_handler` (see §4.6 ADR)
- Middleware placement: `VaultRuntimeMiddleware` below `TraceContextMiddleware`, above API router (see §4.6.1)

Open questions acknowledged but deferred (non-blocking):

- **`audit_id` cardinality per memory write.** A single `/vault/remember` appends N datoms in one tx (N ≤ 7). The corresponding audit entry is one — `audit_id` in Qdrant payload points at that entry. If a caller does multi-memory batch `/vault/remember`, we emit one audit entry per memory (wrapped). Phase 3 pins the batching semantics.
- **Kuzu bitemporal schema.** Kuzu has first-class node + edge properties but no built-in `valid_from / valid_to` modelling idiom. Phase 1 decision: add `valid_from`, `valid_to`, `tx`, `tx_time`, `op` as properties on every node and edge. Phase 5 backfills. Schema migration DDL drafted in Phase 1 Task 7.
- **Per-branch TTL cleanup race.** §4.7 pins the two-phase mark-expired + grace + drop pattern. Exact grace duration (30s? 5min?) needs to be set against measured recall p95 under production load; stubbed at 60s for Phase 4 launch.
- **Phase 5B concurrency with Phase 1 live writes.** Taxonomy-v2 Phase 5B re-embeds 14,914 winners at `tx=0`. If Phase 1 of this track starts writing while Phase 5B is still running, `tx` counter contention is possible. Ordering constraint in §6 pin 5 mitigates (Phase 5B ships first), but the projector must treat `tx=0` rows as read-only genesis — tests pin this in Phase 1 Task 12.

## 10. References

- Persistence OS freeze: `ac72680` on `main`, 625 tests, ARIS min 9.4 across 6 rounds
- Taxonomy-v2 Phase 6 merged: ai-box `518f720b`
- Taxonomy-v2 Phase 5B design (parallel session): `ai-box/conductor/tracks/memory-taxonomy-v2_20260417/`
- Vault coordination reply memory: `aac67386-6a67-4798-9b32-81d6080f80cd`
- Vault design memory: `5b5fd88c-ebba-487f-a3da-df4bf640f544`
- Vault coordination-outbound memory: `b7c0faed-da12-407a-966f-3da13c718c8b`
- Serena memories: `persistence-os/memory-palace-bitemporal-retrofit-design`, `persistence-os/phase-5b-bundling-coordination-pins`
- Vision memory: `~/.claude-nawfal-2/projects/-Users-nawfalsaadi-Projects/memory/project_juba_os_vision.md`
- Persistence OS paper draft: `paper/persistence-nesy-2026-draft.md` v0.3

## 11. Next step

Transition to `writing-plans` skill: produce Phase 1 implementation plan with exact file paths, complete code sketches, and worker-team skill mandates. Each of Phases 1-5 gets its own implementation plan document.
