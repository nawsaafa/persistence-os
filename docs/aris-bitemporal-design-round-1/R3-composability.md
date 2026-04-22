# ARIS Round 1 — R3 Composability Review

**Subject:** `docs/plans/2026-04-22-memory-palace-bitemporal-design.md` + Phase 1 plan
**Reviewer:** R3 (composability, arc 4.5→9.7 on persistence-os foundation)
**Grade:** **8.2 / 10** — strong orthogonality story, three concrete composition gaps.

## Summary

The design composes cleanly with taxonomy-v2 and the datom log on the axes that matter (§4.4 is genuinely orthogonal), and the "log canonical, projections derived" framing is the right shape. But three live composition boundaries are underspecified: the `Runtime._masks` ContextVar under FastAPI, the commit-vs-projection visibility window, and the spec-registration discipline at the Qdrant payload boundary. None are fatal; each needs to be pinned before Phase 3 for the review to trust the "self-conform at every wire boundary" claim that got persistence-os to 9.7.

## Findings

### N1 — Taxonomy-v2 orthogonality holds, L4 works (pass)

Tier values ride immutably in `:datom/provenance` and in Qdrant payload (§4.3). Enforcement is `can_see(caller, fact) = current_capability(caller) ...` — **current** capabilities vs **immutable** tags. Add L4 tomorrow: old datoms still read L0/L1/L2/L3; the `:persistence.vault/memory-tier` spec (Phase 1 Task 1, `_TIER_VALUES`) gets `"L4"` added, **forward-compatible** writes; `max_tier >= fact.tier` rule still scores correctly. Datom log stays valid because no L4 datom existed before the spec change — no retro-typing. **This is the Datomic win done right.** One caveat: `_TIER_VALUES = frozenset({"L0", "L1", "L2", "L3"})` in `specs.py` is a hard enum; add a drift-pin test referencing the taxonomy-v2 tier CSV so the two don't diverge silently (seen this class of drift in W-polish3 / `_canonicalise_content`).

### N2 — `persistence.effect.Runtime` × FastAPI middleware (gap, must fix before Phase 3)

I read `src/persistence/effect/runtime.py`. `Runtime._mask_var` is a per-Runtime ContextVar named `persistence_effect_runtime_masks_{id(self):x}` (line 122), and the active-runtime pointer is a separate module-level ContextVar `_active` (line 247). This works **iff** every request gets its own `with_runtime(rt):` scope — starlette/anyio create a fresh `contextvars.Context` per request by default, so the ContextVars isolate correctly there. **The composition risk is that the existing AI-box middleware stack (main.py:439–444: TenantContext, SecurityHeaders, TraceContext, Logging, Timing, RequestId) runs OUTSIDE any `with_runtime(...)` block** — Phase 3 must push the `with_runtime` entry into a middleware layer below those, or `perform(":audit/emit", ...)` in a handler raises `Unhandled` for traced requests that happen to run before the vault handler is reached. Design §4.6 says "every `/vault/recall` runs inside a Runtime" but doesn't name *where* the Runtime is bound. **Recommend:** add a §4.6.1 naming `VaultRuntimeMiddleware` inserted between `TenantContextMiddleware` and the API router, explicitly below TraceContext so the audit handler can reach the request trace id without itself performing `:trace/get` through the stack. This is the exact class of bug the 6-round persistence-os arc pinned three times (R3 N2 wire-identity) — when one layer's "normalise" leaks into another layer's "expect exact form."

### N3 — Commit/projection visibility window (gap, product-level)

Design §4.1 says "If Qdrant or Kuzu projection fails after the datom commits, a repair job replays from the log." Design §4.5(a) says present-state reads hit Qdrant directly. **What does a `/vault/recall` see between `DB.transact` returning `tx=42` and the Qdrant upsert completing?** Three cases:

1. Projection succeeds synchronously → no window, fine.
2. Projection fails → repair job fixes asynchronously (every 5min per Task 7). For up to 5 minutes, `/vault/recall` misses the memory that `/vault/remember` just returned `tx=42` for. **This breaks read-your-writes.** iOS UX will look broken.
3. Concurrent recall during the upsert → depends on Qdrant's write visibility semantics (not specified).

The design doesn't call out the contract. **Recommend:** pin a "dual-write atomicity" contract in §4.1 — either (a) projection is inside the same unit-of-work and `remember` returns only after both succeed (current Task 9 test `test_datom_commits_even_when_qdrant_projection_raises` asserts datom persists but raises `ProjectionError` — so the caller knows), or (b) accept eventual consistency and surface `tx` to the iOS client so it can poll `/vault/as-of?t=NOW&tx_min=42`. **(a) is simpler, (b) composes better with the counterfactual story.** Either is fine; silence is not.

### N4 — Qdrant payload: spec-registered? (gap, persistence-os discipline)

Phase 1 Task 1 registers four `:persistence.vault/memory-*` specs for datom attribute *values*. The Qdrant **payload** schema in §4.3 has 10 fields with specific wire-form constraints (`valid_to` omitted vs eq-null; `tx=0` genesis sentinel; ISO 8601 with `Z` suffix; sha256-prefixed hash). **There is no registered `:persistence.vault/qdrant-payload` spec.** The Qdrant upsert path is a wire boundary — persistence-os Round 4 pinned "every wire boundary has a registered spec and self-conforms at output" as the non-negotiable discipline (`fact/wire.py` L18: "Both functions run the spec check every call. This is the R3 F8 boundary"). The design omits this. Either (a) register `:persistence.vault/qdrant-payload` and have `QdrantProjector._compose_payload` conform before `client.upsert`, or (b) explicitly document this as a scope cut ("Qdrant client has its own schema validation, good enough"). Phase 5B bundling is coordinated via vault memory only — there's no executable contract. **Recommend (a), Phase 1 Task 5 scope.** Otherwise this is a latent drift-pin waiting to happen when Phase 5B ships 14,914 rows and Phase 1 Task 5 projects row 14,915.

### N5 — Read path enforcement/vector-search separation (pass with nit)

§4.5's three query types all funnel through taxonomy-v2 at query time. The Qdrant filter composition is `valid_from ≤ t AND (valid_to IS NULL OR valid_to > t)` for (b), plus the taxonomy filter on top. Adding a fourth query type (Phase 4 counterfactual) requires swapping the collection name and overlaying intervention datoms — the enforcement layer is collection-agnostic iff it's invoked via a reusable `_build_capability_filter(caller)` helper, not inlined in each endpoint. **Nit:** Phase 1 plan doesn't mention extracting this helper; Phase 2 will either do it or we'll see duplication in Phase 4. Pin the helper now.

### N6 — Counterfactual TTL × active query (pass, minor)

24h TTL on temporary Qdrant collections races with an active branch query only if the cleanup is a hard `drop_collection` while a query is mid-flight. **Recommend:** two-phase cleanup — mark `status=expired` + 1h grace, then drop. Phase 4 scope.

### N7 — Paper/production separation (pass)

`persistence-os/bench/` does **not exist yet** and no files in `ai-box/apps/backend/` reference `bench/`, `persistence-os`, `persistence.fact`, or `persistence.effect` (verified by Grep). The `persistence` dep is introduced only in Phase 1 Task 0 via pyproject pin to `v0.1.0a1`. Coupling direction is one-way (ai-box depends on persistence-os, never reverse). Paper §6.3's Dockerised `regulator_replay/` dataset stays in persistence-os repo per design §2 and risk table. **Clean.**

### N8 — Worker-team file overlap (pass)

Phase 1 Task 0 (team lead) edits `apps/backend/pyproject.toml`. Tasks 1–4 (backend-api): `services/vault/bitemporal/specs.py`, `fact_store.py`, `services/vault/service.py`, `api/v1/vault.py`, `config.py`. Tasks 5–7 (backend-data): `services/vault/bitemporal/projectors/qdrant_projector.py`, `kuzu_projector.py`, `repair.py`. Tasks 8–12 (tests): `tests/test_*.py` + `tests/conftest.py`. **One soft overlap:** Task 3 modifies `services/vault/service.py` (backend-api) and Task 9 modifies `tests/conftest.py` which backend-api Task 3 also wants fixtures from (plan defers that to Task 9, explicitly). That's fine if workers merge in the stated order (backend-data → backend-api → tests, Task 13.1) — Tests worker depends on both upstreams. **One hard dependency:** `VaultFactStore` signature from Task 2 is consumed by Task 3 (service wiring), Task 5 (projector `project(datoms=..., tx=...)`), and Tasks 9–11 (fixtures). This is a transitive API contract that must be pinned before workers spawn, not discovered at merge. **Recommend:** write the `VaultFactStore` Protocol/ABC stub in Task 0 and commit it; workers import the stub, not the other workers' WIP.

## Bottom line

Design is 8.2/10 on composability. Taxonomy-v2 orthogonality, paper/production separation, and worker file layout are solid. The three gaps (N2 middleware stacking, N3 commit/projection window contract, N4 Qdrant payload spec registration) are exactly the class of issue the persistence-os 6-round arc taught us to close *at design time* — because they cost 4×R3 F-findings to pin later. Add §4.1 contract pin, §4.6.1 runtime-binding section, register `:persistence.vault/qdrant-payload`, and pre-commit the `VaultFactStore` stub in Task 0. Do that and this clears 9.3 in R2.

**Files referenced (absolute):**

- `/Users/nawfalsaadi/Projects/persistence-os/docs/plans/2026-04-22-memory-palace-bitemporal-design.md`
- `/Users/nawfalsaadi/Projects/persistence-os/src/persistence/effect/runtime.py` (lines 100–128, 247–260)
- `/Users/nawfalsaadi/Projects/persistence-os/src/persistence/fact/wire.py` (line 18 discipline)
- `/Users/nawfalsaadi/Projects/persistence-os/src/persistence/spec/_canonical.py` (registration pattern)
- `/Users/nawfalsaadi/Projects/ai-box/apps/backend/src/app/main.py` (lines 439–444 middleware stack)
- `/Users/nawfalsaadi/Projects/ai-box/docs/plans/2026-04-22-memory-palace-bitemporal-phase1-impl.md`
- `/Users/nawfalsaadi/Projects/ai-box/conductor/tracks/memory-palace-bitemporal-retrofit_20260422/STATUS.md`
