# R2 — Rigor Review, Round 1

**Doc under review:** `persistence-os/docs/plans/2026-04-22-memory-palace-bitemporal-design.md`
**Adjunct:** `ai-box/docs/plans/2026-04-22-memory-palace-bitemporal-phase1-impl.md`
**Reviewer:** R2 (test strategy, invariants, edge cases, failure modes)
**Baseline:** persistence-os Phase-1 review loop — wall-clock AST lint (BANNED_CALLS), ContextVar-per-Runtime, 16-thread Barrier atomic-commit stress, parametrised canonicalisation drift-pin matrix (29 tests), Prop-4 combined-violation / two-consecutive-middle-deletion.

---

## Summary

The design thesis is sound and the retrofit story is clear. The **testing section (§7) is the weakest part of the doc** — it reads like a checklist of intentions, not a rigor plan. Phase 1's §7 has 9 bullets across 5 subsections where persistence-os had dozens of named drift-pin files plus a lint self-test. Several of the design's own invariants lack a property test that would catch their violation. The Phase-1 implementation plan compounds this by silently inventing API methods on `persistence.fact.DB` that do not exist. The concurrency, retraction, and flag-transition stories have real edge cases that are currently invisible to the test plan.

Grade ceiling is limited until these are addressed.

---

## MAJOR findings

### M1. Phase-1 plan invokes persistence.fact API that does not exist

Phase-1 plan Task 2 (`fact_store.py`) calls `self._db.q_latest(...)`, `self._db.q_entity(...)`, `self._db.q_as_of(...)`, Task 10 calls `self._fact_store._db.latest_tx()` and `q_by_tx(tx)`, Task 9 calls `self._fact_store._db.q_all()`. **None of these methods exist on `persistence.fact.DB`.** Verified by grep against `src/persistence/fact/db.py`: the actual API is `as_of(t)`, `as_of_valid(vt)`, `history(e)`, `log()`, `since(t)`, `branch(t, assertions)`, `entity(e)`. Further, Task 2's test asserts `tx = store.remember_fact(...)` returns `int` and `assert tx >= 1`, but `DB.transact()` returns **a new `DB` instance, not an int** (db.py:99–105 signature `... -> "DB"`), and accepts `facts: list[dict]` — not `Datom` literals as Task 2 passes. Task 2 is un-runnable as written.

**Consequence:** the test plan was not exercised against the real module. Every claim in §7.3 ("Qdrant projection is a deterministic function of the datom log") depends on correctly reading datoms by tx from `DB`, and no such primitive is specified.

**Fix:** either add `DB.q_by_tx`, `DB.latest_tx`, `DB.q_entity(e)` to persistence-os (with their own TDD cycle; R2 P-concurrency rigor required) before Phase 1 starts, OR rewrite Tasks 2/9/10/11 against the real `as_of`/`history`/`log` surface. The plan note at line 436 waves at this but gates nothing.

### M2. §7 has no property test for the doc's own central invariant

Design §4.1: *"The vault you query is a deterministic function of the log. This is the property that makes the audit story hold; it is not optional."* §7.3 gestures at this with one sentence ("rebuild Qdrant from scratch by replaying the log; assert byte-for-byte equality") but specifies no Hypothesis strategy, no random-interleaving of retractions, no property over the **order-independence** claim (modulo commit ordering). Persistence-os's Prop 4 is a named 29-test parametrised matrix; the vault equivalent is one bullet.

Concretely missing:
- **Hypothesis-generated datom streams** (assert/retract/assert-again) where rebuild equality is asserted across random prefixes, not just full replay.
- **Out-of-order projection replay** — does projecting tx=5 before tx=3 converge to the same state once both land? §4.1 claims yes ("idempotent by `(entity_id, tx)`") but no test pins it.
- **Partial-replay idempotency** — replay [1..k] then [1..n] where n>k, assert equals [1..n] directly.

### M3. Canonicalisation drift is not addressed for vault payload

Persistence-os ARIS Round 5/6 added a *parametrised drift-pin matrix* after `_canonicalise_content` and `AuditEntry.__post_init__` diverged. The vault retrofit will introduce **three new canonicalisation surfaces** and the doc does not name one drift-pin test:

1. `:memory/content` — trimmed? NFC-normalised? trailing-newline-stripped? The embedding is computed **before** canonicalisation in the current hot path, but the datom's `v` is the canonical form — `embedding_hash` can diverge from the actual stored vector if these disagree.
2. `bucket` and `tier` strings in `:datom/provenance` vs Qdrant payload — Phase-1 Task 1 pins `_BUCKET_RE = r"^(nawfal|system)-[a-z0-9][a-z0-9-]*$"`. Is this the same regex taxonomy-v2 uses? If taxonomy-v2 ever permits a trailing slash or underscore, historical facts won't validate against the stricter vault spec → backfill 5B will fail silently or noisily.
3. `audit_id` / `entity_id` / `embedding_hash` — sha256 prefix `sha256:` lowercase? Hex alphabet `[a-f0-9]` or `[A-Fa-f0-9]`? Task 1's regex says lowercase only; nothing in Phase 5B constrains the re-embed pipeline to emit lowercase hashes.

**Fix:** ship a `test_vault_canonicalize_drift_pin.py` with ≥10 parametrised cases mirroring the persistence-os pattern (plant `content="hi "` / `"hi\n"` / `"HI"` / NFC vs NFD, check hash equality).

### M4. Taxonomy drift: what if `nawfal-dev` becomes `nawfal-infra`?

Design §4.4 is explicit: tier/bucket are **immutable with the fact**. Good. But §3 rejects snapshotting capability maps, and Phase-1 Task 1 pins bucket regex at write time. Concrete failure mode:

- 2026-04-15: memory m1 written with `bucket="nawfal-dev"`, passes spec.
- 2026-05-01: taxonomy-v2 Phase 7 splits `nawfal-dev` into `nawfal-dev` and `nawfal-infra`. Capability map retargets infra-typed facts.
- Property test §7.3 rebuilds Qdrant from log, asserts equality. Pass.
- But `/vault/recall` for an `infra-capable-only` caller now applies glob `nawfal-infra-*` against m1's immutable `bucket="nawfal-dev"` payload → **user has a capability that semantically includes m1 but the filter rejects it.**

Design §4.4 handles this implicitly ("enforcement at query time uses current caller's capabilities") but the test plan needs an explicit case asserting historical-bucket facts remain reachable after a taxonomy rename. Today it doesn't.

### M5. Retraction edge cases are absent

Design §4.2 shows happy-path retraction (content-with-value → content-with-new-value). The test plan names one retraction test (§7.2 "retracted memories invisible at t > valid_to"). Missing edge cases — each of which has a named analogue in persistence-os's intervention edge-case suite (task #35):

- **Double retraction.** Retract content that is already retracted. Does `retract_and_replace` at Phase-1 Task 2.2 raise? The impl uses `q_latest` (which doesn't exist) — behaviour undefined. Needs: idempotent retraction OR explicit `AlreadyRetractedError` + test.
- **Retract non-existent memory.** `retract_and_replace(memory_id="never-existed")` — current code raises `ValueError`. Test coverage: zero.
- **Partial retraction.** User retracts content but not embedding_hash. Phase-1 Task 2's `retract_and_replace` forces both in the same tx. What if a consumer wants to replace content while preserving the old embedding (e.g., style-only edit)? Invariant "bitemporal fields present iff has corresponding datom" (§6 pin 8) becomes ambiguous: is it iff per-attribute or iff per-entity?
- **Retract across buckets.** Memory moves from `nawfal-dev` to `nawfal-self`. Retract old tier L1 + assert new L2 in one tx — allowed? Design §4.4 says tier/bucket are immutable with the fact, but nothing stops a retract-and-re-assert from doing this in practice.

### M6. Concurrency: two `/vault/remember` on the same `memory_id`

Design §8 claims R2 P-concurrency's 16-thread stress "applies directly" because `allocate_and_append` is `BEGIN IMMEDIATE`. It applies **to tx allocation**, not to the end-to-end vault write. Two concurrent `/vault/remember` calls with the **same generated `memory_id`** (collision; rare but bounded by UUID entropy only) or **same audit_id under Runtime retries** will:

1. Both call `_embed()` → two embedding calls, two `embedding_hash`es (non-deterministic Gemini outputs? — unverified here).
2. Both call `transact([content=A])` and `transact([content=B])` → both succeed, two datoms at different tx.
3. Both project to Qdrant; Qdrant point id = `memory_id` → **lost update** — the second write silently overwrites payload of the first, but both datoms are in the log.
4. §7.3 property test (rebuild from log) now has **ambiguous expected state** — rebuild replays in tx order and produces `content=B`. But which "live" state does it compare against? Race-dependent.

**Fix:** require `memory_id` derivation to be deterministic from `(user_id, content_hash, tx_time_bucket)` OR explicit `If-Match: None` conditional write semantics. Test: 16-thread Barrier stress with same payload → exactly-one datom accepted, others get `ConflictError`.

Related: **repair job racing with live projection.** Phase-1 Task 7 says "trigger: cron every 5 min, plus manual endpoint". If call-A fails Qdrant upsert, its datom is committed; repair runs; mid-repair, call-B with a retraction of A's content lands. Repair reads `(e=A, :memory/content, tx=42)` from log, upserts to Qdrant **without** seeing the retraction at tx=43. Qdrant now shows the retracted state as live. Needs: repair job must project **all** tx up to `latest_tx()` at start, not just the failed one.

---

## MEDIUM findings

### Med1. Feature flag transitions are undefined

`AIOPS_VAULT_BITEMPORAL_ENABLED` flip mid-traffic:
- **Flip OFF during write.** Write started in bitemporal mode: datom committed. Flip. Projection path reads flag again → takes classic path → no Qdrant bitemporal payload → state drift (log has datom, Qdrant doesn't have `tx`).
- **Flip ON with half-written legacy memories.** Taxonomy-v2 Phase 5B is supposed to backfill all 14,914 winners with `tx=0`. Is 5B atomic? If it lands 9,000 of 14,914 before a rollback, flipping the flag ON exposes a Qdrant where some points have `tx=0` and some have no tx field. Query `must_not has_field(valid_to)` now mixes legacy and new semantics.
- **Flip during recall mid-query.** Less dangerous (stateless), but no test asserts the flag is read once per request, not once per Qdrant call.

No test covers any of these.

### Med2. Timestamp canonicalisation edge cases

§4.3: `valid_from` / `valid_to` / `tx_time` stored as "ISO 8601 UTC string, Z-suffixed." Persistence.fact uses tz-aware `datetime`. Unnamed risks:
- **Microsecond precision.** Python `datetime` has µs; ISO string `isoformat()` preserves it, but `Z`-suffix formatting is not the default (`timezone.utc` serialises as `+00:00`). If some code path writes `+00:00` and another writes `Z`, **string-sort ordering ≠ datetime ordering** on mixed corpus (`+` < `Z` in ASCII). This is exactly the class of bug the persistence-os AST lint exists to prevent.
- **Ordering under same-ms ties.** Qdrant range filter on ISO strings with identical timestamps: which wins? Tie-breaker must be `tx`. Design doesn't say.
- **Leap second / DST.** Moot because UTC, but worth a one-line assertion in the test plan to close the question.

### Med3. Repair job dedupe key under partial success

Phase-1 Task 7: idempotent by `(entity_id, tx)`. But Qdrant upsert is split — vector write (existing path) and payload write (projector). Failure modes:
- Vector upsert succeeded, payload failed, process crashed before recording the failure. Marker ("Qdrant done") is not explicitly defined in Task 7. What table? Is it the audit log? Is it a separate `projection_state` table? Without a durable marker, "idempotent by `(entity_id, tx)`" means "we re-upsert, which is idempotent at Qdrant level" — **fine for payload, but re-embedding is expensive and non-deterministic**. Repair should skip re-embed and only re-write payload.
- No test for: projection succeeds, marker write fails. Repair re-runs. Qdrant upsert is idempotent by point id, so safe — but only if projector NEVER regenerates vectors. Design §5 Task 5 says projector writes payload only; confirm this in a test, don't leave as a comment.

---

## MINOR findings

- **§7.3 "byte-for-byte equality (modulo ordering)".** "Modulo ordering" is doing heavy lifting. Which ordering? Dict key order in JSON payload? Float representation? Specify: assert `{k: v for k, v in sorted(payload.items())}` equality.
- **§7.4 "tamper any vault datom → audit chain fails".** Good invariant, but "tamper" needs a test harness: mutate `v`, mutate `provenance`, mutate `tx_time`. Three cases, not one.
- **§7.5 branch TTL test.** "TTL'd collections auto-deleted after 24h" — time-machine test requires freezegun or similar. Not in the plan's dep list.
- **Design §4.3 `embedding_hash` "proof payload matches what the datom recorded".** Verified how? No test described asserts `sha256(qdrant_vector_bytes) == datom[:memory/embedding-hash]`. Without this, the whole "proof" claim is unearned.
- **Phase-1 Task 12 recall latency regression <5%.** Measured how? p95 of 100 calls on cold Qdrant, warm, with/without index? Needs a variance spec — single-run 5% gate will flake.
- **No lint self-test.** Persistence-os has `test_wall_clock_ban.py` with plant-and-catch. Vault canonicalisation needs equivalent: plant a known drift case, assert the drift-pin test catches it.

---

## Verified claims

- §6 pin 2 (`tx=0` reserved genesis): consistent with persistence-os `SQLiteStore.allocate_and_append` starting `next_tx` at 1 — **verified** against `src/persistence/fact/store.py`.
- §8 row "BEGIN IMMEDIATE" for tx contention: **verified** — persistence.fact store does hold BEGIN IMMEDIATE during allocate.
- §4.4 "taxonomy-v2 PG table authoritative, no duplication into fact": good separation of concerns, no test strategy needed beyond integration — **accepted**.
- `lstrip(":")` canonicalisation pattern reuse (Phase-1 plan ref line 751): **verified** in `src/persistence/fact/datom.py:89` and `src/persistence/effect/handlers/audit.py:128–157`. Applies cleanly to vault provenance.

---

## Overall verdict

**Grade: 6.6 / 10**

Moves up from R2's prior-loop starting point of 6.4 because the design doc itself is rigorous in architecture (datom-as-WAL, winner-only bitemporal, enforcement-at-query-time) and reuses persistence-os primitives faithfully. Does not move higher because:

- M1: Phase-1 plan is literally un-runnable against real `persistence.fact.DB` — a blocking defect for any implementer.
- M2/M3: two of the three central invariants lack named property tests equivalent to persistence-os's drift-pin matrix.
- M5/M6: retraction and concurrency stories have real edge cases not in §7.
- Med1: feature-flag transitions are completely untested.

**Blocking items for round 2:** M1 (fix API calls), M2 (name the property tests), M3 (ship drift-pin matrix), M5 (double-retract + retract-non-existent), M6 (concurrent same-memory-id stress).

**Nice-to-have for round 2:** Med1, Med2, Med3, all MINORs.

Close these and grade lifts into the 8s. Leave M1 un-addressed and no grade above 7 is defensible — the Phase-1 plan cannot ship as written.
