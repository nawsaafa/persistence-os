# ARIS Round 2 — Reviewer R1 — Correctness vs. Spec

**Repo head:** `a28d8f5` on `main`
**Pytest:** `463 passed in 2.15s` — verified at review start.
**Reviewer discipline:** Serena-first navigation; every "FIXED" claim verified against code at HEAD, not summary prose.

## Summary grade: **8.3 / 10**

The Round-2 fix pass materially moved the needle on the three finding classes that held R1 below 8: the audit↔fact boundary now round-trips through the registered spec (F1/F2/F6/F13 cluster), `DB.transact` no longer silently writes negative bitemporal intervals (F3), and the replay↔effect bridge now exists as a real `effect.Handler` with the correct `(args, k, ctx)` continuation signature (F7). All three of those were the specific classes of defect R1 flagged as "will bite Phase 2 at exactly the integration points the spec said this layer was supposed to guarantee."

The score is held below 8.5 by three residual concerns: (1) **F4 (plan/node map-vs-vector) is wholly deferred** — not fixed, not even re-triaged in any Round-2 worker summary, which means the forward-compat defect R1 flagged as a Phase-2 day-one blocker survives intact into Round 2; (2) **a new correctness defect emerges post-merge** — `:persistence.effect/audit-entry` is now an orphan spec that no production code actually produces, so the F6 "make `:audit/policy-id` optional" fix papered over a deeper incoherence (the `AuditEntry` dataclass `to_dict()` shape has zero overlap with what `:persistence.effect/audit-entry` registers); (3) the slash-to-dot op-name encoding hack in `audit_entry_to_datom` is acknowledged in the code as "co-inverse only because no op in the catalog contains a literal `.`" — a latent defect shipped into the merged main branch. Round 2 target of ≥ 8.5 is missed by 0.2 but the artefact is clearly one fix-pass away from it.

---

## 1. Round-1 finding remediation table

| Finding | Severity | Status | Evidence | Quality |
|---|---|---|---|---:|
| **F1** — audit→datom keys drop leading colon | CRITICAL | **FIXED** | `src/persistence/effect/handlers/audit.py:325-335` emits `":datom/e"` etc.; `tests/effect/test_audit.py::test_audit_entry_datom_conforms_to_fact_spec` (passes) | 9 |
| **F2** — audit datom fields fail type-check (sha256 vs UUID, string tx, float tx-time, bare-string source) | MAJOR | **FIXED** | Spec relaxed via `_datom_e = or_(uuid_(), _sha256_spec)` and `_datom_tx = or_(int_(), _sha256_spec)` at `src/persistence/spec/_canonical.py:253-254`; `_recorded_at_to_inst` at `handlers/audit.py:205-223` coerces float → tz-aware datetime; `_principal_to_keyword_map` at `handlers/audit.py:226-240` colonises principal keys. Round-trip proven end-to-end (`tests/effect/test_audit.py::test_audit_entry_datom_conforms_to_fact_spec`, `tests/spec/test_canonical.py::TestFactDatom::test_content_hash_{e,tx}_accepted` + rejection counterparts). The narrow relaxation (UUID **or** sha256) preserves content addressing — this is the right fix, not silent coercion. | 9 |
| **F3** — `DB.transact` auto-retraction produces negative `valid_to < valid_from` on retroactive assert | MAJOR | **FIXED** | `RetroactiveCorrectionError` raised when `vf < prior.valid_from` without opt-in at `src/persistence/fact/db.py:152-162`; opt-in path clamps retract interval at `db.py:163-170`. `tests/fact/test_db.py::TestTransact::test_retroactive_correction_without_opt_in_raises[{memory,sqlite}]`, `…_with_opt_in_produces_bounded_valid_to[{memory,sqlite}]`, `…_at_same_valid_from_is_allowed[{memory,sqlite}]` all pass. Semantics are clean: refuse by default, clamp on opt-in so `[new.vf, prior.vf]` is non-negative. R1 asked for refuse-or-correct; this ships both. | 10 |
| **F4** — `:persistence.plan/node` spec models map but spec §1 uses vector | MAJOR | **DEFERRED** | `src/persistence/spec/_canonical.py:387-399` still registers a map shape with `:node/id` + `:node/kind` required. Not addressed in any of W-boundary, W-integration, W-rigor, W-paper summaries. R1 classified this as a "forward-compat spec error embedded in a deferred-module contract … Phase 2 will rediscover this on day one." Still true. Neither the spec NOR the plan-spec doc was edited to resolve the incoherence. | 2 |
| **F5** — `:persistence.replay/fact` requires keyword-prefixed state keys, engine emits bare strings | MAJOR | **FIXED** | `:state`/`:obs`/`:action`/`:llm-in`/`:llm-out`/`:random-draws` relaxed to `map_of(str_(), _any_value)` at `src/persistence/spec/_canonical.py:449-463`; same for `:trajectory/goal` / `:trajectory/outcome` at lines 475, 480. `tests/spec/test_canonical.py::TestReplayFact::test_string_keyed_state_accepted` passes. R1 explicitly recommended "relax `:state` to `map_of(str_(), _any_value)`" — that is precisely the fix. | 10 |
| **F6** — `:audit/policy-id` required but audit handler defaults to `None` | MAJOR | **PARTIAL** | `:audit/policy-id` moved to `optional={}` at `src/persistence/spec/_canonical.py:335-338`; `tests/spec/test_canonical.py::TestEffectAuditEntry::test_policy_id_optional` passes. **However**, this is a surface fix: see new finding **N1** below — `:persistence.effect/audit-entry` is an orphan spec. The `AuditEntry` dataclass's `to_dict()` shape shares zero field names with the spec's required keys (`:audit/id`, `:audit/run-id`, `:audit/op`, `:audit/args`, `:audit/args-hash`, `:audit/verdict`, `:audit/result`, `:audit/latency-ms`, `:audit/cost`, `:audit/valid-from`, `:audit/recorded-at`, `:audit/handler-chain`, `:audit/principal`, `:audit/prev-hash`). Making `policy-id` optional doesn't change that no code path ever produces or consumes the spec's shape. | 5 |
| **F7** — `replay.EffectHandler` not wired to `effect.Runtime` (thunk vs continuation) | MAJOR | **FIXED** | `make_replay_handler(mode, wraps, cache, calls) -> effect.Handler` at `src/persistence/replay/effect_handler.py:210-303` returns a real `Handler` with `(args, k, ctx)` clause signature. `_serve_or_miss` core shared with legacy thunk API (`effect_handler.py:81-152`). Load-bearing e2e test `tests/integration/test_effect_replay_bridge.py::test_record_then_replay_byte_identical_trajectory` passes — `trajectory_hash(replayed) == trajectory_hash(recorded)` under a real `effect.Runtime` with record then replay chain. `RefusedInReplay` fires on `NON_REPLAYABLE_OPS` (proven by `test_replay_refuses_net_fetch_on_cache_miss`). Op namespace unified to leading-colon form (`":net/fetch"`, `":tool/call"`), so `NON_REPLAYABLE_OPS` membership check no longer silently misses — R1 said "the first real replay-under-production-stack will require a redesign", the redesign shipped. | 10 |
| **F8** — `replay._advance_rngs_to_match` hard-codes one-draw-per-step | MINOR | **DEFERRED** | `src/persistence/replay/engine.py:55-66` still hardcodes `rngs["llm"].random()` + `rngs["env"].random()`. R1 classified this as "documented deviation, ship fix before Trader v2 wires in." No Round-2 worker claims ownership; the `Fact.random_draws` field exists but nothing consumes it in `_advance_rngs_to_match`. Acceptable per R1's own guidance (it is a documented deviation), **but** this should be re-filed as a hard blocker for Phase 2 / Trader v2 before it bites. | 6 |
| **F9** — datom provenance doesn't require `:signature`, paper claimed per-datom ed25519 | MINOR | **FIXED (via paper softening)** | R1 explicitly called this "Not a bug so much as a gap between the paper's claim … and the implementation's willingness to accept unsigned datoms as spec-conformant." W-paper removed the ed25519 Phase-1 claim entirely (paper §4.1 / §7.1 / §7.2 now list SHA-256 content hash + Merkle chain as the Phase-1 authenticity story, with ed25519 scheduled to Phase 2). `paper/persistence-nesy-2026-draft.md:161` now reads: "Authenticity … is not claimed for Phase 1: the current `signature` slot stores a SHA-256 content hash, and per-transaction ed25519 signing is Phase 2 work." Paper now matches what the code actually does. Correct resolution. | 9 |
| **F10** — `Runtime.is_well_formed` ignores masks | MINOR | **DEFERRED** | `src/persistence/effect/runtime.py:131-136` still computes `is_well_formed` as pure static set difference over `self.handlers`, without considering `_masked_names()`. No rename to `is_statically_well_formed`, no overload that accepts a mask set. R1 flagged this as "one-liner, cosmetic, but keeps the paper honest." Paper Prop 2 now reads "checkable in linear time by `Runtime.is_well_formed`" (abstract) which is the *static* version R1 said would be defensible if the method were renamed — the paper's phrasing is defensible with the current method name, so this is effectively absorbed into the paper softening. Acceptable. | 6 |
| **F11** — `fact.demo` re-reads `datetime.now()` after branch | MINOR | **SIDE-EFFECT FIXED** | W-rigor removed the wall-clock read via the `ClockFn` injection seam (`src/persistence/fact/db.py:86-96`, `DB(store, clock=...)`). `demo.py` is in the lint whitelist so the re-read still happens there, but the *production* seam R1 recommended (pin-able tx_time) now exists. Adequate. | 8 |
| **F12** — `DBView.entity` tie-breaker `(valid_from, tx)` is `max wins`, not `last-thing-learned` | MINOR | **DEFERRED (docstring update only)** | `src/persistence/fact/db.py:316-338` still uses `(d.valid_from, d.tx) > (cur.valid_from, cur.tx)`. Docstring at `db.py:305-319` pins the semantics ("greatest `valid_from`; ties broken by `tx`") which is what R1 asked for at minimum. No `entity(policy=...)` overload yet. OK for Phase 1. | 7 |
| **F13** — Policy verdict enum mismatch (spec `":allow"` vs handler `"allow"` vs audit `"ok"/"error"`) | MINOR | **FIXED** | `src/persistence/effect/verdicts.py` (new) defines `PYTHON_VERDICTS` / `EDN_VERDICTS` sets and `as_edn` / `as_python` translators. Translators are idempotent, raise on unknown vocabulary. `audit_entry_to_datom` at `handlers/audit.py:337` calls `as_edn` at the wire boundary only; internal runtime keeps bare Python strings (zero existing-test churn). Clean layering: spec = EDN, runtime = Python, translator = single source of truth. | 10 |

**Aggregate remediation score:**
- 7 CRITICAL/MAJOR FIXED clean (F1, F2, F3, F5, F7, F13, plus most of F6).
- 1 MAJOR DEFERRED (F4 — untouched).
- 1 MAJOR PARTIAL (F6 — spec relaxation OK, orphan-spec issue new).
- 3 MINOR DEFERRED (F8, F10, F12 — with R1's explicit blessing for F10 / F12; F8 is a known bomb for Trader v2).
- 2 MINOR FIXED (F9 via paper, F11 via injection seam).

---

## 2. New findings in round 2

Defects introduced or newly uncovered by the merged code at HEAD. Not flagged in R1; surface now because the fix pass rewrote the boundary layer and the boundary layer is exactly where these live.

### N1 — `:persistence.effect/audit-entry` is an orphan spec [severity: MAJOR] [effect ↔ spec]

**Location:** `src/persistence/spec/_canonical.py:317-339` (spec registration); `src/persistence/effect/handlers/audit.py:34-66` (`AuditEntry` dataclass).

**Observed:** The canonical `:persistence.effect/audit-entry` spec requires 14 keys: `:audit/id`, `:audit/run-id`, `:audit/parent`, `:audit/op`, `:audit/args`, `:audit/args-hash`, `:audit/verdict`, `:audit/result`, `:audit/latency-ms`, `:audit/cost`, `:audit/valid-from`, `:audit/recorded-at`, `:audit/handler-chain`, `:audit/principal`, `:audit/prev-hash`. A full-repo grep for any of `:audit/id`, `:audit/run-id`, or `:audit/op` turns up hits **only** in `src/persistence/spec/_canonical.py` (registration) and `src/persistence/spec/demo.py` (demo) — i.e. nothing in `persistence.effect` produces that shape, nothing reads it. The real `AuditEntry` dataclass has Python-attr fields (`id`, `prev_hash`, `op`, `args_hash`, `verdict`, `latency_ms`, `recorded_at`, `result_hash`, `error`, `policy_id`, `handler_chain`, `principal`, `run_id`, `parent`). The only production call that translates an `AuditEntry` to a spec-conformant dict is `audit_entry_to_datom` (which targets `:persistence.fact/datom`, not `:persistence.effect/audit-entry`).

**Gap:** R1 F6 was fixed by making `:audit/policy-id` optional. But the deeper incoherence is that **no code path ever produces the shape that `:persistence.effect/audit-entry` registers**. The spec is decorative: it exists so the spec module has a `test_good`/`test_verdict_must_be_enum`/`test_op_must_be_in_catalog`/`test_policy_id_optional` row but no effect-module writer or reader exercises it. This is a strictly-worse variant of R3 F8 (the "zero `spec.conform` callers outside spec package" finding): with the datom boundary spec, W-boundary added `audit_entry_to_datom → spec.parse(":persistence.fact/datom", …)` as a real production check. The `:persistence.effect/audit-entry` spec has no such adapter.

**Why this is new:** R1 F6 described the symptom (every in-module test produces `policy_id=None` which fails the spec). The fix pass made the spec permissive enough to conform to a (hand-written, spec-shaped) audit entry — but didn't bridge the dataclass shape to the spec shape. R1 didn't catch this because it looked at F6 through the lens of "does conform pass" rather than "does any real AuditEntry conform." Round 2 surfaces it because the boundary rewrite in W-boundary made explicit that spec↔dataclass adapters are the right pattern, and this spec didn't get one.

**Fix proposal:**
- Option A (thin): add `audit_entry_to_effect_entry(entry: AuditEntry) -> dict` producing the `:persistence.effect/audit-entry` wire form, plus the symmetric decoder. Exercise via a test that asserts `spec.conform(":persistence.effect/audit-entry", audit_entry_to_effect_entry(e)).is_ok` for at least the `make_audit_handler` factory default.
- Option B (honest deprecate): if the spec is truly obsolete (the fact-datom shape supersedes it as the audit wire form), delete the registration and the corresponding `TestEffectAuditEntry` class. Leaving it in `CANONICAL_SPECS` while nothing produces it invites a Phase-2 worker to build against a spec no other module respects.

**Severity rationale:** MAJOR because the spec is listed in `CANONICAL_SPECS` + CHANGELOG as a shipped boundary, the paper §4.3 cites it in the audit contract, and the fact that nothing produces it means any Phase-2 integrator (Trader v2's post-trade analyzer, for instance) reading the CHANGELOG will build against a spec that has no real callers — then discover at integration time that the Python `AuditEntry` has a different shape.

---

### N2 — audit op-keyword encoding is lossy on ops containing `.` [severity: MINOR] [effect]

**Location:** `src/persistence/effect/handlers/audit.py:313-321` (`a_keyword = ":audit/" + op_bare.replace("/", ".")`) + `handlers/audit.py:355-357` (decoder).

**Observed:** The encoder replaces the inner `/` of an op name with `.` so `":llm/call"` becomes `":audit/llm.call"` — a valid EDN keyword under `_KEYWORD_RE`. The decoder inverts by `replace(".", "/")`. The code's own comment admits: "co-inverse only because no op in the catalog contains a literal `.`." I verified directly: `op=":llm.special/call"` encodes to `":audit/llm.special.call"` and decodes to `":llm/special/call"` — not lossless.

**Gap:** All 15 current catalog ops are dot-free, so this is latent. But (a) nothing enforces the "no dot in catalog" invariant in tests, (b) `validate_args(":nope.x", {})` rejects unknown ops but `audit_entry_to_datom` will cheerfully encode any op — so a direct `perform(":foo.bar/baz", ...)` (bypassing catalog) produces a lossy audit trail. Phase 2 cognitive ops (`:reflect`, `:verify`, `:call-skill`) or downstream operator-defined ops ("`:finance.wacc/set`") will break this.

**Fix proposal:**
- Use `urlencode`-style escaping or a dedicated separator that is guaranteed not to appear in op names (e.g. `"__"` or the private-use EDN keyword convention `":audit/\\llm/call"`).
- Or: promote the op to a separate field (`:audit-op` alongside `:datom/a = ":audit/entry"`) rather than smuggling it into the keyword suffix.
- Add a test `test_audit_datom_encoding_is_roundtrip_for_any_valid_op` that property-tests the encoder-decoder pair over generated valid op names.

**Severity rationale:** MINOR because no catalog op today contains `.`, but the acknowledged fragile invariant lives in committed production code. One catalog addition from a silent bug.

---

### N3 — `Store.next_tx()` read-modify-write is not atomic across concurrent `transact` callers [severity: MAJOR] [fact]

**Location:** `src/persistence/fact/db.py:129` (`tx = self.store.next_tx()`) + `db.py:211` (`self.store.append(new_datoms)`); `src/persistence/fact/store.py:198-213` (`SQLiteStore.next_tx`).

**Observed:** The W-integration fix for R3 F10 removed the module-level `_tx_counter` and moved tx allocation to `Store.next_tx()`. `SQLiteStore.next_tx` runs `SELECT COALESCE(MAX(tx), 0) + 1 FROM datom_log` under `self._lock` (an in-process threading.Lock), then releases the lock, then `DB.transact` builds the datoms, then calls `append` which reacquires the lock. **Between `next_tx()` and `append()`, another `transact` on another `DB` instance pointed at the same SQLite file can also call `next_tx` and receive the same value.** Two threads in one process: no lock held across `next_tx → append`. Two processes (the Gunicorn case in the docstring): no cross-process lock at all — both processes independently compute `MAX(tx)+1` and write identical `tx` values into different rows.

**Gap:** The test `test_two_sqlite_stores_on_same_file_do_not_collide` at `tests/fact/test_tx_allocation.py:98-132` is sequential — it opens store1, writes, opens store2, reads `store2.next_tx()`, writes — so of course it sees the prior write. There is no concurrency test. The docstring of `next_tx` explicitly invokes the Gunicorn scenario ("multi-worker Gunicorn, each worker opens its own connection") as the motivating case. That scenario fails under the shipped implementation.

**Why this is new:** R3 F10 was "`_tx_counter` module-level global breaks multi-process." The fix moved allocation onto the Store, which is a necessary precondition but not sufficient. Round 1 did not flag the atomicity gap because the broken-global version had a *different* failure mode (counter reset on restart, not concurrent duplicate allocation). The gap surfaces only because we now have per-store allocation and still lack allocation atomicity.

**Fix proposal:**
- `SQLiteStore`: wrap `next_tx` + `append` inside a single `BEGIN IMMEDIATE` transaction. Expose a `transact(datoms) -> int` protocol method that returns the allocated tx and writes in one SQL transaction. `DB.transact` uses it instead of `next_tx` + `append`.
- For cross-process safety beyond SQLite, rely on the database's sequence/identity column for tx allocation (the deferred R3 F4 portability issue becomes the *same* fix — `GENERATED AS IDENTITY` gives monotonic allocation atomically).
- Add a test `test_concurrent_transacts_on_same_sqlite_file_allocate_distinct_tx` using `concurrent.futures.ThreadPoolExecutor` with 20 threads each writing one datom; assert `len({d.tx for d in store.all_datoms()}) == 20`.

**Severity rationale:** MAJOR because the fix summary for R3 F10 explicitly motivates multi-worker Gunicorn and Postgres portability as the target use cases. The shipped implementation handles neither. For a bitemporal substrate whose load-bearing invariant is "every datom has a unique monotonic tx," silent tx collision is the kind of defect that silently corrupts every `as_of(t)` query downstream of the collision point.

---

### N4 — `:persistence.plan/node` still map-shaped (F4 carry-over) [severity: MAJOR] [spec]

See F4 row above. Restating as N4 because **no Round-2 worker took ownership**, and under the task brief "identify any NEW correctness defect in the merged code" this counts as a live R2 finding since the paper now cites `:persistence.plan/node` as a "deliberate parse-don't-validate move" (paper §4.7, §1 contribution) — promoting the spec to a front-line contribution while it still disagrees with `agent2-plan-spec.md §1`'s vector form. The W-paper softening **increased** the cost of this defect by elevating the spec-first registration as a contribution. Now the paper and the code disagree with the plan spec.

Fix belongs in either W-boundary-2 (rewrite to `tuple_of(_plan_kind, keys(...), seq_of(_any_value))`) or a W-plan-spec worker that edits both `docs/agent2-plan-spec.md` and `_canonical.py` consistently. See F4 above for the fix proposal.

---

## 3. Overall correctness grade

**8.3 / 10**

Ladder per MEMORY.md ARIS target (R1 = 7.4 → target R2 ≥ 8.5 → R3 ≥ 8.9 → R4 ≥ 9.0):

- **Aggregate remediation (F1–F13):** 7 clean fixes, 2 partial/deferred-with-blessing, 3 deferred, 1 deferred-and-regressed-in-visibility (F4). Pure remediation arithmetic lands around 8.1.
- **Paper-code alignment:** substantially improved. Every Phase-1 claim the paper now makes is backed by shipped tests (`Runtime.is_well_formed` decidability, NO-OP trajectory-hash byte-identity, Merkle-chain integrity). That lifts correctness because the reader's ground-truth mapping from claim to artefact is now clean. +0.4.
- **New findings:** N1 (orphan audit-entry spec) + N3 (tx allocation atomicity) are MAJOR. Both are the kind of thing a NeSy reviewer would flag as "shipped substrate invariants are not actually enforced." -0.2.
- **Regression check:** the post-merge `caec580` fix (op-name encoding after W-boundary + W-integration conflict) and `e50dc09` (`# noqa: wall-clock` on `Trajectory.from_edn`) are both clean adjustments — no regressions introduced by the merge order itself. Tests green (463 passed, zero skips, zero xfails).

Net: **8.3**. This is between R1's 7.4 and the Round-2 target of 8.5. The artefact is clearly one polish pass away from 8.5, not two — **this is the 8.0-8.9 "one more polish" band**, not the 7.0-7.9 "another full fix pass" band.

---

## 4. Go / no-go for Round 3

**GO, with a scoped polish round.**

**Rationale:** The load-bearing defects from R1 — audit↔fact boundary (F1/F2/F6/F13), retroactive interval corruption (F3), replay↔effect unpluggability (F7) — are all fixed. The deferred items (F8, F10, F12) are either blessed by R1 as non-blocking or (F8) targeted at Phase 2 work that hasn't shipped yet. The remaining correctness headwinds are narrow:

- **F4 / N4** (plan/node shape): one file, 10 lines in `_canonical.py`, plus a conforming test. Paper reference in §4.7 needs the spec to actually be vector-shaped or the doc to be revised.
- **N1** (orphan `:persistence.effect/audit-entry`): either add the `audit_entry_to_effect_entry` adapter + conform test, or deprecate the spec. Either path is a one-session fix.
- **N3** (tx atomicity): `BEGIN IMMEDIATE` around `next_tx + append` inside the Store. Concurrency test. One file, one test file.
- **N2** (op-name encoding): secondary separator or op-as-dedicated-field. Property test.

All four are PARTIAL/DEFERRED items with clear fixes, not unknowns. None of them require re-litigating what the substrate is or how the modules compose. Round 3 should be a tight polish pass (one worker, ~1 session) rather than a full parallel-worker fix round.

**What Round 3 must NOT do:** Don't regress the wins. The audit↔fact boundary, retroactive guard, replay bridge, and ContextVar fix are all load-bearing — the polish pass should scope strictly to the residual cluster and leave the rewritten boundary alone.

**Target after Round 3:** ≥ 8.8. Round 4 then targets ≥ 9.0 with paper copy-edit + bench numbers. That sequence keeps the NeSy 2026 abstract deadline (9 June) achievable.

**Gate pass:** Round 2 correctness review passes with 8.3 — above the 8.0 "one more polish" floor, below the 8.5 strict target. The min-across-reviewers gate still depends on R2-rigor / R3-composability / R4-research converging above 8.0 on the same merged branch. If the sibling reviewers converge on similar scores, the min-gate for Round 3 dispatch is met.
