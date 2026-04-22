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
persistence.effect runtime (audit handler wraps the call)
    │
    ▼
persistence.fact.DB.transact([datoms])      ← canonical, atomic, Merkle-chained
    │       returns tx number (int64, monotonic, BEGIN IMMEDIATE)
    │
    ├──► Qdrant upsert  (vector + payload: {tier, bucket, valid_from, valid_to, tx, ...})
    └──► Kuzu upsert    (entities + edges, bitemporal properties)
    │
    ▼
audit entry (Merkle-linked to prior, references tx #)
```

The datom commit is the transaction boundary. If Qdrant or Kuzu projection fails after the datom commits, a repair job replays from the log — projection is idempotent by `(entity_id, tx)`. **The vault you query is a deterministic function of the log.** This is the property that makes the audit story hold; it is not optional.

### 4.2 Datom schema for vault

A single `/vault/remember` call appends 4–7 datoms in one `allocate_and_append` transaction. Example for `remember("I moved to Casablanca in 2022", tier=L2, bucket=nawfal-self)`:

| `e` | `a` | `v` | `tx` | `tx_time` | `valid_from` | `valid_to` | `op` | `provenance` |
|---|---|---|---|---|---|---|---|---|
| `mem-123` | `:memory/content` | "I moved to Casablanca in 2022" | 42 | now | now | null | assert | {source, audit_id, tier=L2, bucket, user_id, llm_model, confidence} |
| `mem-123` | `:memory/embedding_hash` | `sha256:…` | 42 | now | now | null | assert | ↑ |
| `mem-123` | `:memory/entity_refs` | `[ent-casa, ent-2022]` | 42 | now | now | null | assert | ↑ |
| `mem-123` | `:memory/cluster_id` | `cluster-life-events` | 42 | now | now | null | assert | ↑ |

**Retraction preserves history.** A correction at tx=67 ("actually 2021") appends two datoms:

- `(mem-123, :memory/content, "…2022", tx=67, valid_to=now, op=retract)` — marks the prior fact's `valid_to`
- `(mem-124, :memory/content, "…2021", tx=67, valid_from=now, valid_to=null, op=assert)` — new assertion

The original assertion is never deleted. `verify_chain` stays intact. This answers BOTH "what did the vault believe on 2026-04-10" (2022) AND "when did we learn the correction" (2026-04-15 at tx 67). This is the Datomic/Conjur win that makes competing agent memory libraries look flat.

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

**(a) Present-state** — 99% of traffic, zero latency impact:
- `/vault/recall("wacc calculation")` → Qdrant vector search with filter `valid_to=omitted AND (taxonomy-v2 rule)`. Kuzu graph walk unchanged. Present-state is just the special case `t=now`.

**(b) Time-travel** — new, cold path:
- `/vault/as-of?t=2026-04-10&q="wacc calculation"` → Qdrant vector search with filter `valid_from ≤ t AND (valid_to IS NULL OR valid_to > t)`. ~2× present-state cost because an extra indexed comparison runs pre-vector; still <100ms. Taxonomy-v2 applies at query time using the caller's current capabilities.

**(c) Audit reconstruction** — rare, offline:
- Replay an agent session's audit entries. For each `:vault/recall`, extract `vault_snapshot_tx`, re-query the datom log at that tx, recompute `result_hash`, must match. `verify_chain` semantics extended from the audit-log Merkle chain to the vault state at query time. Prop 4 now covers the vault layer.

### 4.6 Audit integration (Phase 3 scope)

Every `/vault/recall` and `/vault/remember` runs inside a `persistence.effect.Runtime` wrapped in the existing audit handler. Audit entry fields:

- `op`: `:vault/recall` or `:vault/remember`
- `args_hash`: hash of the query (for recall) or content (for remember)
- `result_hash`: hash of (memory IDs returned, vault snapshot tx at query time)
- `policy_id`: tier enforcement policy in force at call time
- `handler_chain`: `(audit, vault, taxonomy, fact)`
- `principal`: `{user_id, tenant}`
- `vault_snapshot_tx`: the max tx visible to this query (the "as-of" of the read)

The Merkle chain is `verify_chain`-able. **Regulator replay** becomes: given audit entry E, look up `vault_snapshot_tx`, re-run the query against the datom log at that tx, recompute `result_hash`, must match.

### 4.7 Counterfactual memory branches (Phase 4 scope)

`persistence.replay` mapped onto vault state. A branch is the datom log forked at `tx=k` with one or more intervention datoms (retractions/replacements), projected to a **temporary** Qdrant collection for agent replay. "If the agent had not known X at time T, would it have decided Y?" Deterministic, hash-verifiable.

API surface: `POST /vault/branch-from` with `{t, interventions: [...]}` returns a branch_id; `GET /vault/branch/{id}/recall?q=…` runs recall against the branched substrate. Branch state lives in a separate Qdrant collection namespaced by `branch_id`, TTL'd after 24h.

## 5. Phase breakdown (full scope, no leave constraint)

| Phase | Scope | Owner | Duration est. |
|---|---|---|---|
| **0. Design doc + ADR + ARIS** | This doc, plus 2 ARIS rounds on the design before code | Single-session | 2-3 days |
| **1. Datom schema + dual-write vault** | Vault writes append to datom log + project to Qdrant/Kuzu. Present-state read path unchanged. Feature-flagged behind `AIOPS_VAULT_BITEMPORAL_ENABLED`. | Parallel: backend-api + backend-data + tests | 5-7 days |
| **2. `/vault-as-of` endpoint + iOS/dashboard time-picker** | Cold-path read API, UI surfaces. Taxonomy-v2 applied at query time. | Parallel: backend-api + frontend + iOS | 3-4 days |
| **3. Audit integration** | Wrap `/vault/recall` + `/vault/remember` in persistence.effect audit handler. `vault_snapshot_tx` in every audit entry. Extends `verify_chain` iff to vault layer. | Single workstream, backend-api + tests | 3 days |
| **4. Counterfactual branches** | `/vault-branch-from?t=X&interventions=[...]`. Temporary Qdrant collections. Paper §6.3 walkthrough. Investor demo. | Parallel: backend-data + tests + research | 4-5 days |
| **5. Migration + Kuzu backfill** | Phase 5B (taxonomy-v2) bundle handles Qdrant genesis. Residual: Kuzu bitemporal property backfill from datom log. | Single workstream | 2 days |

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

- Qdrant projection is a deterministic function of the datom log: rebuild Qdrant from scratch by replaying the log; assert byte-for-byte equality with the live collection (modulo ordering).
- Kuzu projection same property.

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

## 9. Open questions — none blocking

All design-level questions resolved:
- Driver priority: 2+3 with 1 as downstream beneficiary
- Source of truth: datom log canonical (Option B)
- Backfill vs fresh: backfill, handled by Phase 5B bundle
- Qdrant migration scope: bundled with Phase 5B re-embed event
- Pre-leave target: not applicable, full scope now
- Field schema: pinned in §6 with Phase 5B coordination
- Counterfactual API: `/vault/branch-from` in Phase 4, deferred detail to implementation plan

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
