# ARIS R2 Composability Review
**Grade:** 9.1 / 10 (prior: 8.2)

## Verdict

Design composes cleanly on the three R1 live boundaries — middleware stacking (N2), read-your-writes (N3), and Qdrant payload spec (N4) are pinned to the exact discipline persistence-os landed in R4–R6. Minors N5/N6/N8 resolved with concrete helpers. Taxonomy-v2 × bitemporal axes remain orthogonal under the new `?vt=…` / `?tx=…` split. Counterfactual branch TTL race is two-phase-cleaned, and §6 "winner bitemporal iff has datom" is sited at the projection layer.

One **hard composition break** surfaces in the new ADR. The ADR's claim that the `ctx_provider` merge composes with `AuditEntry.__post_init__` is wrong on the current code, and the planned drift-pin extension won't catch it because the existing drift-pin only exercises the dict-side helper, not the dataclass constructor. The fix is mechanical, but the ADR as written will `TypeError` on first dispatch. Docking 0.3 from what would otherwise be a 9.4. Close C1 and R3 clears 9.4.

## Round-1 findings — resolution status

| # | Finding | Status | Evidence |
|---|---|---|---|
| N1 | Taxonomy-v2 L4 forward-compat + `_TIER_VALUES` drift-pin | **Resolved** | Phase 1 Task 8 explicitly adds tier drift-pin cases (buckets with underscores, L4 rejection at `test_memory_tier_spec_accepts_L0_through_L3`). |
| N2 | Runtime binding / middleware placement | **Resolved** | §4.6.1 names `VaultRuntimeMiddleware`, positions it "below `TraceContextMiddleware`, above the API router," path-predicates non-`/vault/*` routes, binds `request.state.vault_runtime`, mandates `with request.state.vault_runtime:` at route entry. Cross-checked against `apps/backend/src/app/main.py:437–444` — the stated insertion point is between line 441 (`TraceContextMiddleware`) and the router, which matches the R1 recommendation verbatim. |
| N3 | Read-your-writes contract | **Resolved** | §4.1 pins synchronous-projection-in-same-UoW, `ProjectionError` bubbles with committed `tx`, repair job explicitly scoped to failure recovery only ("NOT as a steady-state async projector"). Phase 1 Task 3 Step 3.4 re-pins it with ordering (1→4) and Task 9 has `test_read_your_writes_after_remember_returns` asserting no-sleep-no-poll recall. Clean. |
| N4 | `:persistence.vault/qdrant-payload` spec registration | **Resolved** | Phase 1 Task 5.0 registers the spec with all 10 fields, interval-consistency invariant (`valid_to >= valid_from`), `tx >= 0` (Phase 5B genesis sentinel preserved), optional `valid_to` for current-open facts. Task 5 wires `QdrantProjector.project` to `conform(...)` before `client.upsert`. This IS the R3 F8 discipline applied correctly. |
| N5 | `_build_capability_filter(caller)` helper | **Partial** | Not explicitly extracted as a helper in Phase 1 plan; §4.5 describes the same filter shape across query types (a), (b), and the future (c). Not blocking for Phase 1 since only (a) + (b) ship, but Phase 2 plan MUST extract it before Phase 4 lands. Note carried forward; 0.05 dock. |
| N6 | Branch TTL two-phase cleanup | **Resolved** | §4.7 pins "temporary Qdrant collection namespaced by `branch_id`, TTL'd after 24h via two-phase cleanup (mark-expired + grace window, then drop)." §9 defers exact grace duration (60s stub) to Phase 4 launch, which is the right scope. |
| N7 | Paper/production separation | **Resolved (unchanged)** | Still clean; §6.3 `bench/regulator_replay/` scaffolded per R0 fix pass. |
| N8 | `VaultFactStore` Protocol stub pre-committed | **Resolved** | Phase 1 Task 0.5 created: full Protocol in `apps/backend/src/app/services/vault/bitemporal/protocol.py`, ships in Task 0 commit before parallel workers spawn. `runtime_checkable` Protocol means Tasks 3/5/6/7 type-check independently. Clean. |

Net: 6 of 7 R1 findings fully resolved, 1 partial (N5 helper extraction deferred to Phase 2). No regressions.

## New composability issues

### C1 — ADR's `ctx_provider` merge breaks `AuditEntry` construction (MAJOR, fix before Phase 3)

Cross-checked the ADR against `audit.py:40–63` (dataclass) and 438–440 (handler clause):

- `AuditEntry` is `@dataclass(frozen=True)` with a **closed field list**; no `**kwargs` catch-all, no `_extra: dict` sink.
- Line 440 constructs via `AuditEntry(id=entry_id, **canonical_content)`.
- Under the ADR, `canonical_content = _canonicalise_content({**content, **ctx_provider()})`. If `ctx_provider` returns `{"vault_snapshot_tx": 42}`, the `**canonical_content` splat raises `TypeError: __init__() got an unexpected keyword argument 'vault_snapshot_tx'`.

The ADR's Pro #3 — "`AuditEntry.__post_init__` mirrors dict-side canonicalisation for unknown keys via `object.__setattr__(self, k, v)` fall-through" — is **factually incorrect**. `__post_init__` (lines 64–163) canonicalises `op`, `policy_id`, `handler_chain`, `principal` only; it never iterates unknown keys. The existing drift-pin `test_helper_preserves_unknown_keys_unchanged` (line 213) only exercises `_canonicalise_content` at the dict level — it never round-trips through `AuditEntry(**out)`. That's the gap. The ADR's Implementation-plan step 2 test will `TypeError` at line 440 before its assertion runs.

**Fix:** add `_extra: dict[str, Any] = field(default_factory=dict)` to `AuditEntry`, route non-declared keys from the clause (`known = {k: v for k, v in canonical_content.items() if k in _AUDIT_ENTRY_FIELDS}; extra = rest`), extend `_canonicalise_content`, `to_dict`, `to_edn`, `from_edn`, `canonical_hash` to handle `_extra` symmetrically. Add a **30th drift-pin row** passing a non-declared key through `_canonicalise_content` AND `AuditEntry(**out)`, asserting idempotent equality. The byte-invariant survives iff the drift-pin exercises the constructor, not just the helper.

Alternative: narrow `ctx_provider` to existing fields (e.g. `principal["snapshot_tx"]`). ADR rejected Option 2 for principled reasons — don't walk back; do the `_extra` fix.

**Severity:** MAJOR but scoped. Does not block Phase 1 or Phase 2. Must close before Phase 3 Task 1.

### C2 — Taxonomy-v2 × bitemporal orthogonality under `vt/tx` split (PASS)

The new `?vt=…` / `?tx=…` axis split does NOT couple capability enforcement to time axis. Both paths materialise a `DBView`, then filter via `current_capability(caller)`. `?tx=<int>` being internal-only (audit reconstruction) is the right scope cut — external callers can't replay under historical capabilities. Clean orthogonal axes.

### C3 — "Winner bitemporal iff has datom" invariant at projection (PASS, nit)

§6 pin 8 lands at Task 5 via spec self-conform: any payload lacking `tx` fails `:persistence.vault/qdrant-payload` (required `spec_int_range(min=0)`). Duplicates get no bitemporal fields because the projector is only invoked on datom commits, and datoms are only emitted for winners. Nit: add a `test_winner_bitemporal_biconditional` asserting `(duplicate_of IS NULL) ⟺ (tx field present)` — Task 10 approximates but doesn't assert the biconditional. 0.05 dock.

### C4 — Phase 4 branch isolation (PASS)

Temporary Qdrant collection is **namespaced by `branch_id`**, not payload-scoped — branch-side upserts cannot leak via shared `point_id` (collection namespace is the boundary). 24h TTL + two-phase cleanup prevents drop-mid-recall race. `DB.branch(t, assertions)` creates a fresh `InMemoryStore` seeded from `db.as_of(t).datoms`, so parent log is untouched. Isolation is structural, not policy-enforced. Clean.

## ADR composability assessment

ADR picks the right option for the right reasons — `principal` semantic pollution and canonicalisation byte-invariant cost are correctly diagnosed. `{**content, **extra}` merge-order (extra wins) matches R5 W-polish3. The "ctx_provider cannot itself perform audited ops" `mask(audit_name)` guard is the right regress check.

What it gets wrong is the `__post_init__` pass-through claim (see C1). With the `_extra` field fix, the ADR composes cleanly with: (a) `_canonicalise_content` (unchanged, already preserves unknown keys), (b) the drift-pin matrix (one new row), (c) the Merkle chain (`_content_hash` naturally includes `_extra` because it hashes the dict), and (d) `to_edn` / `from_edn` symmetry (add `_extra` serialisation, ~10 lines). Mechanical, TDD-able, ~45 min instead of ~30 — still well below Option 1's ARIS-round-on-schema-change cost.

## Sign-off

Three live composition boundaries from R1 closed to spec. Worker protocol stub in Task 0.5, middleware placement in §4.6.1, read-your-writes contract in §4.1, `:persistence.vault/qdrant-payload` spec in Task 5.0 — every one of these matches the exact discipline pinned across the persistence-os 6-round arc. Architecture is ready for Phase 1 execution.

Two items blocking downstream (not Phase 1):
1. **C1** — ADR's `_extra` field fix before Phase 3 Task 1 lands.
2. **N5** — `_build_capability_filter(caller)` helper extraction in Phase 2 plan before Phase 4 reuses it.

Both are mechanical. Neither blocks the design gate.

**Grade: 9.1/10.** (−0.3 for C1 ADR break, −0.05 for N5 deferred, −0.05 for C3 biconditional test gap. No other docks.)

### Files referenced (absolute)

- `/Users/nawfalsaadi/Projects/persistence-os/docs/plans/2026-04-22-memory-palace-bitemporal-design.md`
- `/Users/nawfalsaadi/Projects/persistence-os/docs/adr/2026-04-22-audit-ctx-provider.md`
- `/Users/nawfalsaadi/Projects/persistence-os/src/persistence/effect/handlers/audit.py` (lines 40–63 dataclass, 64–163 `__post_init__`, 362–475 `make_audit_handler`, 295–360 `_canonicalise_content`)
- `/Users/nawfalsaadi/Projects/persistence-os/tests/effect/test_audit_canonicalize_drift_pin.py` (line 213 unknown-key helper-only test — the gap)
- `/Users/nawfalsaadi/Projects/ai-box/docs/plans/2026-04-22-memory-palace-bitemporal-phase1-impl.md` (Task 0.5 stub, Task 5.0 spec)
- `/Users/nawfalsaadi/Projects/ai-box/apps/backend/src/app/main.py` (lines 437–444 middleware stack — cross-check for §4.6.1)
