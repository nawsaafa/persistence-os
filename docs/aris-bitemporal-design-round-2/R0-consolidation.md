# ARIS Bitemporal Design Round 2 — Consolidation

**Date:** 2026-04-22
**Target:** Memory Palace bitemporal retrofit design doc + Phase 1 implementation plan, post-Round-1 fix pass
**Round 2 gate:** min ≥ 9.0 (Phase 0 exit criterion)
**Verdict:** 🟡 **Near-miss — min 8.9 (0.1 below gate).** Architecture solid, 2 fixable MAJORs remain, one of which is Phase-1-blocking the other is Phase-3-blocking.

## Scorecard

| Reviewer | R1 Score | R2 Score | Δ | Target | vs gate |
|---|---:|---:|---:|---:|---|
| R1 Correctness | 6.8 | **9.3** | +2.5 | ≥ 9.0 | ✅ PASS |
| R2 Rigor | 6.6 | **8.9** | +2.3 | ≥ 8.8 | ✅ INDIVIDUAL, ❌ MIN |
| R3 Composability | 8.2 | **9.1** | +0.9 | ≥ 9.3 | ❌ INDIVIDUAL, ✅ MIN |
| R4 Research | 7.8 | **9.1** | +1.3 | ≥ 9.0 | ✅ PASS |
| **mean** | 7.35 | **9.10** | +1.75 | — | — |
| **min** | **6.6** | **8.9** | **+2.3** | **≥ 9.0** | **❌ by 0.1** |

Big-picture: the fix pass closed everything R1 blocked on and most of R2's and R4's findings. R3 caught a new MAJOR in the `ctx_provider` ADR (factually wrong against real `AuditEntry` dataclass), and R2 caught a spec-combinator name mismatch (same class as R1 round 1's API citation issue, but at the spec layer this time).

## MUST-FIX before Phase 1 execution (blocking)

### Class-A: R2-N1 spec combinator import names (MAJOR, Phase-1 blocker)

Phase 1 plan Task 1 (`line 254`) and Task 5.0 (`line 896`) import spec combinators that don't exist on `persistence.spec`. The real public API per `persistence-os/src/persistence/spec/__init__.py:22–54`:

- `and_(specs)` — not `spec_and`
- `regex(pattern)` — not `spec_pattern`
- `keys(required=, optional=)` — not `spec_keys`
- `enum(values)` — not `spec_value`
- `str_(min=, max=)` — base string spec
- `register(name, spec)` — registration

The plan currently cites `spec_and`, `spec_keys`, `spec_value`, `spec_pattern`, `spec_int_range`, `spec_iso8601`, `pred` — all wrong names. Workers will hit `ImportError` on line 1 of Task 1 and Task 5.0.

**Fix:** rewrite the spec-combinator import + usage blocks in Phase 1 Tasks 1 and 5.0 against the real `persistence.spec` public API. Mechanical, ~20 minutes. Same meta-pattern as R1's API fix pass — discovered by reading the source, not by trusting the design memo.

### Class-B: R4 MINOR copy fixes (trivial, Phase-1-adjacent)

- Design §1 line 13: "regulator-grade provenance" unqualified — add "(three-property form defined in §4.6)" clause.
- Design §4.5(c) line 141: "Prop 4 now covers the vault layer" — partially contradicts §4.6's hedge. Restate as "Prop 4' (vault-extended form)" with pointer to §4.6 three-property definition.

~5 minutes total. Bundles with R2-N1 fix.

## Phase-3 blockers (not Phase-1 blocking, but track now)

### R3 C1: `ctx_provider` ADR won't work as written (MAJOR, Phase 3 blocker)

The ADR at `docs/adr/2026-04-22-audit-ctx-provider.md` recommends option 3 (add `ctx_provider` extension, merge extra dict into `content`). R3 verified against source and found:

- `AuditEntry` at `src/persistence/effect/handlers/audit.py:40–63` is `@dataclass(frozen=True)` with a **closed field list**.
- The handler constructs entries via `AuditEntry(id=entry_id, **canonical_content)` at line 440.
- Splatting `{"vault_snapshot_tx": 42}` into this constructor raises `TypeError: unexpected keyword argument`.
- The ADR's claim that `__post_init__` accepts unknown keys via `object.__setattr__` is factually wrong — `__post_init__` (lines 64–163) only touches the declared fields (`op`, `policy_id`, `handler_chain`, `principal`, `result_hash`).
- The existing `test_helper_preserves_unknown_keys_unchanged` drift-pin at `tests/effect/test_audit_canonicalize_drift_pin.py:213` is helper-level only — it never round-trips through the `AuditEntry(...)` constructor.

**Corrected ADR plan (must be reworked before Phase 3 Task 1):**
1. Add `_extra: dict = field(default_factory=dict)` to `AuditEntry`.
2. Route non-declared keys from the clause: `known_keys = {f.name for f in fields(AuditEntry)}; extra = {k: v for k, v in canonical_content.items() if k not in known_keys}; known = {k: v for k, v in canonical_content.items() if k in known_keys}; entry = AuditEntry(id=entry_id, **known, _extra=extra)`.
3. Extend `_canonicalise_content`, `to_dict`, `to_edn`, `from_edn` symmetrically so byte-invariant holds.
4. Add 30th drift-pin row that exercises the full constructor round-trip with an unknown key.

**Not Phase 1 blocking.** Phase 1 doesn't use the audit handler — Phase 3 does.

## Phase-1 accept-or-defer (not blocking, filed for later)

- **R2 property-test code-sketch asymmetry:** design §7.3 names 5 invariants; Phase 1 plan code-sketches only 1 (Task 10 full-replay). State machine, out-of-order, partial-replay, Kuzu parity are named but not code-sketched. Phase 1 Task 10 worker expands from design §7.3 — acceptable for "plan" granularity.
- **R2 latency regression spec (Task 12):** 5% p95 gate lacks warmup, median-of-3, `pytest-benchmark`. Will flake. Harden before Phase 1 Task 12 executes.
- **R2 `force_retroactive` seam:** `VaultFactStore.correct_content` has no admin-gated path. Design §4.2 promises `/vault/correct-retroactively` — Phase 2 scope.
- **R2 feature-flag mid-traffic transitions:** flipping `AIOPS_VAULT_BITEMPORAL_ENABLED` mid-traffic has no explicit semantics. Phase 1 Task 4 can add a guard.
- **R2 ISO-8601 Z vs +00:00:** spec doesn't enforce Z-suffix canonicalization consistently.
- **R3 N5 `_build_capability_filter` helper:** deferred to Phase 2 by design — acceptable.
- **R4 §6 pin 5 ordering:** Phase-5B-first is soft-wished. Add operational guard (`AIOPS_VAULT_BITEMPORAL_ENABLED` refuses to turn on if `SELECT COUNT(*) WHERE tx=0 > 0` hasn't run).

## Fix-pass scope for Round 3 (smallest possible)

Target: close the 0.1-point gap and ship.

1. **R2-N1:** rewrite spec-combinator imports in Phase 1 Tasks 1 and 5.0 against real `persistence.spec` public API (`and_ / regex / keys / enum / str_ / register`). ~20 min.
2. **R4 copy:** §1 line 13 qualification + §4.5(c) line 141 Prop 4' restatement. ~5 min.

~25 minutes total. Then dispatch Round 3 spot-check (single-reviewer R2 rigor) — expected to bump R2 to 9.1+, clearing min ≥ 9.0.

Alternative: accept R2 at 8.9 as effectively passing (0.1 is within noise; Phase 1 is unblocked once R2-N1 lands; Phase 3 blocker C1 is tracked). This skips Round 3 and goes straight to Phase 1 execution after the ~25 min fix pass. Recommend this path — the 0.1 gap is entirely owned by a mechanical fix that, once applied, the reviewer would acknowledge without re-review.

## Round-2 pins (high-confidence, no further review needed)

- Real `persistence.fact.DB` API is correctly cited end-to-end (R1 9.3 confirms).
- Valid-time vs transaction-time axis split on `/vault-as-of` is sound (R1 + R3 both confirm).
- `bench/regulator_replay/` scaffold is correctly framed as "NOT operational" (R4 highlights as the opposite-of-overclaim-arc pattern — exemplary).
- Taxonomy-v2 × bitemporal composition stays orthogonal (R3 C2 PASS).
- Phase 4 branch isolation is structural (R3 C4 PASS) — cannot leak by collection namespace + fresh InMemoryStore per branch.
- 4-axis novelty (bitemporal + effect-audit + taxonomy-gated + counterfactual) preserved across the fix pass (R4 confirms).
- Paper stays decoupled from production vault (`grep "vault" paper/` → 0 hits, R4 confirms).

## Reports

- `R1-correctness.md` (commit `b43a2a3`)
- `R2-rigor.md` (commit `42f4524`)
- `R3-composability.md` (commit `6905752`)
- `R4-research.md` (commit `f34a926`)
- this file

## Recommended path forward

**Option A (recommended):** micro fix-pass (R2-N1 + R4 copy, ~25 min), skip Round 3, declare Phase 0 complete, proceed to Phase 1 execution. Track R3 C1 ctx_provider ADR fix as a Phase-3 precondition.

**Option B (strict):** micro fix-pass + Round 3 single-reviewer spot-check on R2 to confirm min ≥ 9.0 explicitly. Adds ~15 minutes of reviewer dispatch, zero additional code change expected.

Decision pending user.
