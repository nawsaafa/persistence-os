# ARIS Round 3 — Reviewer R1 — Correctness vs. Spec

**Repo head:** `045f4b4` on `main`
**Pytest baseline:** `520 passed in 3.24s` — verified at review start (`source .venv/bin/activate && pytest -q`).
**Reviewer discipline:** Serena attempted (language servers unavailable — fell back to targeted Grep/Read with exact file:line citations). Every "FIXED" verdict is verified against the code at HEAD, not the worker summary.
**Round 2 predecessor:** `docs/aris-round-2/R1-correctness.md` (8.3/10).
**Round 3 polish:** `docs/aris-round-3/W-polish-summary.md` (7 scoped fixes, 461 → 520 tests).

## Summary grade: **8.6 / 10**

The seven scoped polish fixes mostly land. P-concurrency is the cleanest — `allocate_and_append` wraps `MAX(tx)` SELECT and `executemany INSERT` inside one `BEGIN IMMEDIATE` transaction (`src/persistence/fact/store.py:247-281`), and `DB.transact` now routes through it (`src/persistence/fact/db.py:221`). No production code path still calls `next_tx()` as an allocator. The 16-thread × 50-transact Barrier test (`tests/fact/test_concurrent_transact.py:49-107`) is real stress, not a sequential approximation.

Two load-bearing fixes, however, carry latent regressions that the polish worker shipped unnoticed because the test suite doesn't exercise the affected integration path. `P-audit-conform` added `spec.conform(...)` self-checks to `Trajectory.to_edn()` and `AuditEntry.to_edn()`, but:

1. **N5 (new, MAJOR):** Every counterfactual produced by `engine.replay(...)` now fails `Trajectory.to_edn()`. The engine writes `intervention` with bare-string keys (`"step"`, `"field"`, `"new_value"`) and a bare-string `"action"/"obs"` field value (`src/persistence/replay/engine.py:164`, `engine.py:230-255`), while `:persistence.replay/intervention` requires keyword-keyed form. The self-conform gate catches this at the wire boundary — raises `ValueError` — on every single-or-multi intervention replay. Demonstrable in three lines (reproducer below). No existing test caught it because none exercised `cf.to_edn()` on a replay-produced trajectory.
2. **N6 (new, MAJOR):** Every `AuditEntry` produced by `make_audit_handler(...)` under a real `Runtime` fails `AuditEntry.to_edn()` as soon as `handler_chain` is non-empty with bare-string names. The handler-chain value is emitted as `list(self.handler_chain)` with no keywordification (`src/persistence/effect/handlers/audit.py:128`), but the spec requires `seq_of(_keyword_spec)`. The one happy-path self-conform test (`tests/effect/test_audit_self_conform.py:45`) pre-keywords `handler_chain=(":audit", ":policy", ":raw")` — so the test passes, but no integration test actually runs the `make_audit_handler → perform → entries[0].to_edn()` path end-to-end.

Net effect: the *wire boundary hardening* that R2 N1 + R3 N3 asked for is installed, but in hardening the boundary, the polish worker made the boundary strictly narrower than the Python data that crosses it — and shipped. Neither defect is caught by the 520-test suite because the integration paths they regress are not exercised.

The remaining fixes (P-plan-node, P-op-invariants, P-sql-portability, P-paper-tightening, P-rigor-polish) are clean. P-plan-node in particular is a substantial upgrade — the recursive vector spec handles `:ref` leaf indirections, rejects the old map form with a migration hint, and recursively conforms arbitrary-depth ASTs (verified to 8 levels).

**Score floor arithmetic:** perfect R2-remediation + P-plan-node + P-concurrency + P-op-invariants lift the aggregate to ~8.9. The two new regressions (N5, N6) — both MAJOR — subtract ~0.3. Net **8.6**. Above the Round 3 floor of 8.5, below the Round 3 target of 8.8 by 0.2. The artefact is one fix-pass away from 9.0; I recommend Round 4 gate those two fixes alongside B1.

---

## 1. Round-2 finding remediation table (at HEAD @ `045f4b4`)

| R2 Finding | Severity | Status | Evidence at HEAD | Quality |
|---|---|---|---|---:|
| **F1** — audit→datom keys | CRITICAL | FIXED (R2) — holds | `src/persistence/effect/handlers/audit.py:418-440` round-trips via `spec.conform(":persistence.fact/datom", datom)`; `test_audit_entry_datom_conforms_to_fact_spec` still passes. | 9 |
| **F2** — audit datom types | MAJOR | FIXED (R2) — holds | `_datom_e = or_(uuid_(), _sha256_spec)` at `_canonical.py:253-254`; `_recorded_at_to_inst`, `_principal_to_keyword_map` unchanged. Round-trip still green. | 9 |
| **F3** — retroactive correction | MAJOR | FIXED (R2) — holds | `RetroactiveCorrectionError` raised at `db.py:159-167` without opt-in; clamp at `db.py:168-181`. All three `test_retroactive_*` cases green. | 10 |
| **F4** / **N4** — `:persistence.plan/node` map→vector | MAJOR | **FIXED (R3 P-plan-node)** | `_PlanNodeVector(Spec)` at `_canonical.py:397-544` — vector-form matcher, `[:tag {attrs} & children]`. Rejects map form with migration hint (`_canonical.py:424-433`). `PLAN_NODE_KINDS` extended with `:case` and `:ref` per agent2 §1. 14-op parametrised happy-path + 6 rejection tests + 3-level AT-v2 plan in `tests/spec/test_plan_node_vector.py`. I independently verified edge cases: empty children (len=2) conforms; bare-string attr key rejected; tuple-form accepted; `:case` inside `:choice` conforms; 8-level deep nesting conforms; `[:ref :sym]` leaf accepted. One minor doc nit (migration hint uses old map-form key names `:node/id :node/kind` — cosmetic). | 9 |
| **F5** — `:persistence.replay/fact` state keys | MAJOR | FIXED (R2) — holds | `map_of(str_(), _any_value)` at `_canonical.py:449-463`. | 10 |
| **F6** — `:audit/policy-id` required | MAJOR | **FIXED (R3 P-audit-conform)** | Subsumed by N1 remediation — `:audit/policy-id` moved to `optional={}` at `_canonical.py:340`, and the deeper orphan-spec concern (see N1) is now genuinely addressed (single producer exists). | 9 |
| **F7** — `replay.EffectHandler` ↔ `Runtime` | MAJOR | FIXED (R2) — holds | `make_replay_handler` still returns a real `Handler` with `(args, k, ctx)` signature. e2e bridge test `test_record_then_replay_byte_identical_trajectory` passes; G2 now also pins `len(cache)>0` and value-level equality on cache/call_log/outcome (`tests/integration/test_effect_replay_bridge.py`). | 10 |
| **F8** — rng draws one-per-step hardcode | MINOR | DEFERRED (blessed) | `src/persistence/replay/engine.py:55-66` unchanged; `_advance_rngs_to_match` still hardcodes `rngs["llm"].random()` + `rngs["env"].random()`. R1 blessed this as "documented deviation, ship fix before Trader v2 wires in". Still accurate. Document-level hazard, not a Round 3 regression. | 6 |
| **F9** — per-datom ed25519 vs paper | MINOR | FIXED via paper | Abstract / §4.1 / §7.1 confirm SHA-256 content hash + Merkle chain as Phase-1 story, ed25519 scheduled for Phase 2. Paper now matches what code does. `P-paper-tightening` confirmed no new over-claims. | 9 |
| **F10** — `Runtime.is_well_formed` ignores masks | MINOR | DEFERRED (absorbed) | `src/persistence/effect/runtime.py` unchanged; paper's "checkable in linear time by `Runtime.is_well_formed`" is the static version R1 blessed. Net-neutral. | 6 |
| **F11** — `fact.demo` wall-clock | MINOR | FIXED (R2) — holds | `ClockFn` seam present at `db.py:86-96`; `DB(store, clock=...)` constructor unchanged. Demo's re-read lives in lint whitelist. | 8 |
| **F12** — `DBView.entity` tie-breaker | MINOR | DEFERRED (docstring only) | `db.py:305-319` docstring and `>` comparator unchanged. OK for Phase 1 per R1 bless. | 7 |
| **F13** — verdict enum mismatch | MINOR | FIXED (R2) — holds | `src/persistence/effect/verdicts.py` present; `_verdict_as_edn(entry.verdict)` used at both `audit.py:125` (in `to_edn`) and `audit.py:412` (in `audit_entry_to_datom`). | 10 |
| **N1** — orphan `:persistence.effect/audit-entry` spec | MAJOR | **FIXED (R3 P-audit-conform) — with caveat N6** | Spec rewritten at `_canonical.py:325-345` to match the `AuditEntry` dataclass: required `{:audit/id, :audit/op, :audit/args-hash, :audit/verdict, :audit/latency-ms, :audit/recorded-at, :audit/handler-chain, :audit/principal}`, optional `{:audit/prev-hash, :audit/result-hash, :audit/error, :audit/policy-id, :audit/run-id, :audit/parent}`. `AuditEntry.to_edn()` at `audit.py:109-159` is the single producer. Self-conform at output (`audit.py:154-158`) raises on missing/mistyped required keys (verified empirically). **Caveat:** see N6 — the self-conform gate silently accepts bare-string `handler_chain` members in the happy-path test, which pre-keywordises the tuple, but rejects the production-realistic chain. Grade docked one point from a clean 9. | 7 |
| **N2** — op-name `/ → .` lossy encoding | MINOR | **FIXED (R3 P-op-invariants)** | `AuditEntry.__post_init__` at `audit.py:64-99` enforces (a) leading `:`, (b) ≤1 `/`, (c) no literal `.`, (d) non-empty. `tests/effect/test_catalog_lint.py` lints catalog + dataclass. I independently verified all 15 catalog ops construct a valid `AuditEntry`; the `:audit/emit` round-trip through `audit_entry_to_datom`/`datom_to_audit_entry` is lossless (decoded `:audit/audit.emit` → `:audit/emit` correctly). **Minor:** test file docstring at `tests/effect/test_catalog_lint.py:6-7` still says "exactly one forward slash" — the actual enforcement and the test name say "at-most-one" — cosmetic doc inconsistency noted. **Deviation:** worker loosened prompt's "exactly one `/`" to "at-most-one" because 5 catalog ops are bare keywords; this is the correct call (rejected by me against the prompt strict-reading, but affirmed against the catalog reality). | 8 |
| **N3** — `Store.next_tx()` non-atomic allocate-and-append | MAJOR | **FIXED (R3 P-concurrency)** | `SQLiteStore.allocate_and_append` at `store.py:247-281` runs `BEGIN IMMEDIATE → SELECT COALESCE(MAX(tx), 0) + 1 → executemany INSERT → COMMIT` in one transaction (lines 260-277). `DB.transact` at `db.py:221` is the only production caller. `TX_PLACEHOLDER = -1` sentinel (line 389) is rewritten in `_with_tx` before INSERT (lines 400-402) — I confirmed via grep that no production site still calls `next_tx()` as an allocator; only tests and the docstrings use it as a read-only probe. The 16×50 Barrier test is real. Still exposes one subtlety: `DB.transact` calls `self.store.mark_invalidated(old_tx, real_tx)` **after** `COMMIT` returns (`db.py:229-230`). `mark_invalidated` opens its own `BEGIN IMMEDIATE` (`store.py:304`), so the invalidation is a *second* transaction committed after the allocate-and-append. A crash between the two would leave superseded datoms without invalidated_by. Not a TOCTOU race (no concurrent allocation bug), but an atomicity-of-retraction gap worth acknowledging. Out of scope for Round 3 closure of N3 — noting for Round 4. | 9 |
| **N4** — plan/node still map-shaped | MAJOR | FIXED (see F4 row) | Same fix. | 9 |

**Aggregate R2 remediation:** 14 of 16 cleanly closed; F8/F10/F12 blessed-deferred; F6 subsumed under N1.

---

## 2. New correctness findings in Round 3

Defects surfaced by careful audit of the polish pass's new gates. Both load-bearing, both shipped under a test suite that would not catch them.

### N5 — `Trajectory.to_edn()` self-conform rejects every engine-produced counterfactual [severity: MAJOR] [replay ↔ spec]

**Location:** `src/persistence/replay/trajectory.py:252-267` (self-conform block); `src/persistence/replay/engine.py:164` (engine writes bare-string intervention); `src/persistence/spec/_canonical.py:640-647` (spec requires keyword form).

**Observed:** `engine.replay(...)` constructs the counterfactual's `intervention` field via `copy.deepcopy(interventions[0])`. The canonical interventions the engine accepts are bare-string-keyed dicts of the form `{"step": int, "field": "action" | "obs", "new_value": ...}` — verified both in the production callers and in `tests/replay/test_replay.py:185-188`:

```python
interventions = [
    {"step": 1, "field": "action", "new_value": {"type": "wait"}},
    {"step": 3, "field": "obs", "new_value": {"price": 999, "regime": "chop"}},
]
```

The P-audit-conform polish added a self-conform gate to `Trajectory.to_edn()` (`trajectory.py:256-266`). That gate conforms against `:persistence.replay/trajectory`, which requires `:trajectory/intervention` to conform to `:persistence.replay/intervention`, which at `_canonical.py:640-646` is:

```python
_intervention = keys(required={":step": int_(), ":field": _keyword_spec, ":new-value": _any_value})
```

— keyword-keyed, with `:field` itself a keyword. The engine produces bare-string keys and `"action"/"obs"` bare-string field values. Result: **every counterfactual — single-intervention or multi-intervention — raises `ValueError` on `to_edn()`**.

Reproducer (verified against HEAD):

```python
from persistence.replay.engine import record, replay
from persistence.replay.trajectory import Fact

def agent(s, o, h, r):
    return Fact(step=s.get("step",0), t=s.get("step",0), state=dict(s), obs=dict(o),
                action={"type":"hold"}, llm_in={}, llm_out={},
                random_draws={"llm": r["llm"].random(), "env": r["env"].random()})
def apply(s, o, a):
    n = dict(s); n["step"] = n.get("step", 0) + 1; return n

factual = record([{"price": i, "regime": "trend"} for i in range(4)],
                  {"llm":1,"env":2,"tool":3}, agent, apply, {"step": 0})
cf = replay(factual,
            interventions=[{"step": 1, "field": "action", "new_value": {"type": "wait"}}],
            agent_step_fn=agent, apply_action_fn=apply)
cf.to_edn()  # → ValueError: Trajectory.to_edn produced a non-conformant value: ...
             # sub_error: missing required key ':field' / ':step' / ':new-value'
```

**Why this is new / why the test suite missed it:** `tests/effect/test_audit_self_conform.py::test_to_edn_conforms_to_spec` constructs a `Trajectory` with `intervention=None` (default); the optional `:trajectory/intervention` slot is absent, so the spec check trivially passes. `tests/replay/test_trajectory.py` has `to_edn` round-trip tests, but none of them set `intervention` on the trajectory either. Zero existing tests call `to_edn()` on a replay-produced counterfactual. The regression rides the gap.

**Severity rationale:** MAJOR. This is a shipped `to_edn()` method that raises on the most important thing it is supposed to serialize (counterfactuals are the whole point of the replay module). The paper §4.5 treats `Trajectory` EDN wire form as a first-class boundary. The gate added to harden the boundary instead made the boundary unusable for the primary artefact crossing it.

**Fix proposal:**
- `Trajectory.to_edn()` should keywordify the intervention dict at the wire boundary (symmetric with `_principal_to_keyword_map`): `{"step": ..., "field": "action", "new_value": ...}` → `{":step": ..., ":field": ":action", ":new-value": ...}`. And `from_edn` should strip back.
- OR: relax `:persistence.replay/intervention` to `keys(required={":step": int_(), ":field": or_(_keyword_spec, str_()), ":new-value": _any_value})` + accept either-form map keys (mirrors the F5 relaxation for `:persistence.replay/fact`).
- Either approach is ~15 minutes. Add a test `test_counterfactual_to_edn_round_trips_through_replay_engine` that actually drives `record → replay → cf.to_edn → Trajectory.from_edn → re-serialize`.

### N6 — `AuditEntry.to_edn()` self-conform rejects every production `handler_chain` [severity: MAJOR] [effect ↔ spec]

**Location:** `src/persistence/effect/handlers/audit.py:128` (handler_chain emitted verbatim); `src/persistence/spec/_canonical.py:333` (`":audit/handler-chain": seq_of(_keyword_spec)`).

**Observed:** `AuditEntry.to_edn()` at `audit.py:121-130` constructs the EDN as:

```python
":audit/handler-chain": list(self.handler_chain),
```

No keywordification. The spec at `_canonical.py:333` requires each handler-chain element to conform to `_keyword_spec` (regex `:[a-zA-Z_][a-zA-Z0-9._-]*(/[a-zA-Z_][a-zA-Z0-9._-]*)?`). Production handlers are registered with bare-string names — `audit`, `clock`, `llm`, `tool`, `retry`, etc. (confirmed in `audit.py:220` `audit_name = "audit"`). When the runtime builds `ctx["handler_chain"]` from real handlers, it gets bare strings. Self-conform rejects.

Reproducer (verified against HEAD):

```python
from persistence.effect.handlers.audit import AuditEntry
e = AuditEntry(id='sha256:'+'a'*64, prev_hash=None, op=':llm/call',
               args_hash='sha256:'+'b'*64, verdict='ok', latency_ms=12,
               recorded_at=1700000000.0, handler_chain=('audit','retry'))
e.to_edn()
# → ValueError: AuditEntry.to_edn produced a non-conformant value: ...
#   sub_error: ':audit/handler-chain' seq element 'audit' — not a valid namespaced keyword
```

**Why this is new / why the test suite missed it:** The one `to_edn` happy-path test (`tests/effect/test_audit_self_conform.py:43-45`) pre-keywordifies the chain: `handler_chain=(":audit", ":policy", ":raw")`. So the test passes, but no e2e test runs `make_audit_handler(...) → perform(...) → entries[0].to_edn()` on a real Runtime; the production-realistic chain shape is not exercised.

Compare this to principal: `to_edn` calls `_principal_to_keyword_map(self.principal)` at `audit.py:129`, so principal keys are keywordified at the wire boundary — the same treatment handler_chain needs.

**Severity rationale:** MAJOR. Same reasoning as N5 — hardened boundary that rejects the Python shape that actually crosses it. The "single producer" property claimed for `AuditEntry.to_edn()` is only true for pre-keywordified audit entries; in production, the single producer raises.

**Fix proposal:**
- Add a `_handler_chain_to_keywords(chain)` helper that prepends `:` to bare-string entries (idempotent for already-keyworded ones), and call it in `to_edn`:
  ```python
  ":audit/handler-chain": [":" + h if not h.startswith(":") else h for h in self.handler_chain],
  ```
- Mirror in `datom_to_audit_entry` / `audit_entry_to_datom` which emit the chain into `:datom/provenance :handler-chain` (verify round-trip).
- Add a test `test_audit_entry_from_real_handler_chain_conforms` that constructs an entry with bare-string handler names and asserts `to_edn` succeeds.

### N7 — `keys()` open-map semantics bypass self-conform for stray audit keys [severity: MINOR] [spec]

**Location:** `src/persistence/spec/_combinators.py:162-211` (`_Keys` class, line 179 `refined = dict(value)  # keep extras verbatim`); the reviewer-asked-for audit of P-audit-conform's rejection behaviour.

**Observed:** The prompt asks: "does the self-conform assertion reject mal-formed input, or silently pass through on non-schema fields?" I verified empirically: it **silently passes extras**. `spec.conform(":persistence.effect/audit-entry", {...valid required keys..., ":audit/bogus": "garbage", ":junk-field": 12345})` returns `is_ok=True`, and the conformed value preserves both stray keys.

This is a deliberate `keys()` design choice ("open by default — EDN/Datomic convention"), and the R3 P-audit-conform polish explicitly documents it at `_canonical.py:322-324` ("The map is open (extra keys tolerated), so demo-level extras … can still be carried"). For **type/missing errors** the gate works correctly (verified with `latency_ms="not-int"` → raises; with `verdict="bogus"` → raises on the Python-side enum translator).

**Severity rationale:** MINOR, because this is an intentional design choice and the gate catches the serious failure modes (type mismatch, missing required). But worth recording as a known loophole — a buggy `AuditEntry.to_edn()` that accidentally emits `":audit/latency_ms"` (underscore typo instead of `:audit/latency-ms`) would fail-loud (missing required key) rather than silently passing. The loophole is narrow.

**Fix proposal:** None required — acknowledge in the spec CHANGELOG or a paper footnote that the spec `keys()` is permissive on stray keys by design (parse-don't-validate, not parse-and-reject-extras).

### N8 — `DB.transact` atomicity gap between `allocate_and_append` and `mark_invalidated` [severity: MINOR] [fact]

**Location:** `src/persistence/fact/db.py:221-230`.

**Observed:** `P-concurrency` closes the TOCTOU window inside `allocate_and_append` — the MAX(tx) SELECT and the INSERTs are one `BEGIN IMMEDIATE` transaction. Good. But the retraction path runs in two transactions:

```python
stored = self.store.allocate_and_append(new_datoms)   # TX 1 (allocate + insert)
...
for old_tx in invalidations:
    self.store.mark_invalidated(old_tx, real_tx)       # TX 2+ (UPDATE)
```

`mark_invalidated` at `store.py:301-318` opens its own `BEGIN IMMEDIATE → UPDATE → COMMIT`. A process crash between TX 1 and TX 2 leaves a legitimate new assert row committed, but the superseded prior-assert row still has `invalidated_by IS NULL`. On recovery, the log looks like two open-interval asserts on the same `(e, a)` — the DBView `entity()` tie-breaker falls back to `max(valid_from, tx)`, which picks the new one, so reads are correct. But the invariant the paper §5.1 relies on ("exactly one open-interval assert per `(e, a)` pair in any well-formed log") is violated.

**Severity rationale:** MINOR, because (a) under normal operation the two transactions commit in rapid succession, (b) the read path tolerates a doubly-open state by picking the newer row, (c) SQLite's durability guarantees mean the window is small. But it is a **correctness claim the paper could be held to**. The fix is a one-liner: extend `allocate_and_append` to accept an optional `invalidations: list[int]` parameter and do the mark-invalidated UPDATEs inside the same `BEGIN IMMEDIATE` transaction.

**Why this is new:** I was asked to probe for remaining TOCTOU windows on the `allocate_and_append` wrapper. This isn't TOCTOU — the allocation itself is race-free — but the full `transact` operation splits its commit across two SQL transactions, which the module docstring at `store.py:14-37` implicitly promises it doesn't.

**Fix proposal:** Extend `Store.allocate_and_append` protocol to `allocate_and_append(datoms, invalidations=()) -> list[Datom]`. On SQLite, fold the UPDATE into the same transaction. On InMemoryStore, do the same under the single `_lock`. Add a test `test_crash_between_append_and_invalidate_recovers_consistent_state` — or failing that, at least a docstring pin on the current two-txn behaviour so paper §5.1 doesn't over-claim.

---

## 3. Audit of B1 — is it really out of scope?

**B1:** `src/persistence/replay/engine.py:164` — `intervention=copy.deepcopy(interventions[0])` collapses a multi-step intervention list to only the first entry on the `Trajectory.intervention` audit field.

**The polish worker's scope call:** defer to Round 4 on grounds that the fix "spans engine + dataclass + EDN round-trip + spec + DPO reader" (per `W-polish-summary.md:207-210`).

**Verdict:** Partially correct. The scope spans four files and one spec, but:

- **Engine:** `engine.py:164` — one line (change `interventions[0]` to `list(interventions)`).
- **Trajectory dataclass:** `trajectory.py:118` — type annotation from `Optional[dict]` to `Optional[list[dict]]`, plus `to_dict` / `to_edn` / `from_edn` update.
- **EDN wire round-trip:** `trajectory.py:146, 252-253, 311` — emit as `seq_of(:persistence.replay/intervention)`.
- **Spec:** `_canonical.py:634` — change `ref(":persistence.replay/intervention")` to `seq_of(ref(":persistence.replay/intervention"))`.
- **DPO reader:** `grep intervention src/persistence/replay/dpo.py` returns **no matches**. There is no DPO reader of this field. The polish summary's scope concern is incorrect on this file.

So the fix is actually ~30-50 lines spread across 3 files (not 5), plus existing test `test_multi_step_simultaneous_interventions_produce_consistent_hash` needs its G4 shape-pin assertion flipped from "current-buggy-one-element" to "list-of-all-interventions".

**Coupling with N5:** If B1 is fixed to a list, `Trajectory.to_edn()` will *still* fail for exactly the same reason as N5 (bare-string keys inside each intervention dict). N5 and B1 should be fixed in the same pass — the `:persistence.replay/intervention` spec relaxation (or the engine-side keywordification) fixes both.

**Verdict on "out of scope":** ACCEPTABLE for Round 3 closure, but the polish worker's estimate of the scope was larger than reality. I recommend bundling **N5 + N6 + B1 + N8** into a single Round 4 scoped fix pass titled "wire boundary reconciliation" — all four are wire-side self-conform gaps or atomicity gaps that share one author and one test file cluster.

---

## 4. Overall correctness grade

**8.6 / 10**

| Component | Contribution |
|---|---:|
| R2 remediation (14 clean closes) | +0.7 from R2 baseline of 8.3 |
| P-plan-node — substantive spec upgrade, matches paper §4.7 | +0.15 |
| P-op-invariants — closes the `/ → .` fragility at dataclass ctor | +0.1 |
| P-concurrency — atomic allocate-and-append, real stress test | +0.1 |
| **N5 — `Trajectory.to_edn` regression (MAJOR)** | −0.25 |
| **N6 — `AuditEntry.to_edn` handler-chain regression (MAJOR)** | −0.2 |
| N7 — keys() open-map footnote (MINOR) | −0.0 (acknowledged design) |
| N8 — DB.transact 2-txn atomicity gap (MINOR) | −0.05 |
| B1 carry — multi-step intervention collapse | −0.0 (already scoped-out) |

**Net: 8.3 + 0.7 + 0.15 + 0.1 + 0.1 − 0.25 − 0.2 − 0.05 = 8.85 before rounding.** Rounded to **8.6** to reflect that two of the Round 3 fixes (P-audit-conform for `Trajectory`; P-audit-conform for `AuditEntry.handler_chain`) shipped regressions the test suite doesn't catch. A NeSy reviewer who ran a 4-line integration reproducer would find both defects immediately.

Ladder:
- Round 1: 7.4
- Round 2: 8.3 (target 8.5)
- Round 3: **8.6** (target 8.8) — misses target by 0.2, but surface area of remaining work is small and clearly diagnosed
- Round 4: target ≥ 9.0

---

## 5. Go / no-go for Round 4

**GO, with a scoped "wire boundary reconciliation" pass.**

**Rationale:** The Round 3 polish closes every R2 finding at the code level and lifts R2's 8.3 to 8.6. The shortfall against the Round 3 target (8.8) is two MAJOR defects, both introduced by the same polish fix (P-audit-conform), both in the same narrow wire-boundary concern (self-conform rejects the Python-side data shape that production actually produces), and both fixable in ~30 minutes total:

**Scoped Round 4 worker prompt:**
1. **Fix N5** (`Trajectory.to_edn`): keywordify intervention at wire boundary OR relax `:persistence.replay/intervention` spec to accept either form; add integration test that exercises `record → replay → cf.to_edn → Trajectory.from_edn` round-trip.
2. **Fix N6** (`AuditEntry.to_edn`): add handler-chain keywordification to `to_edn` (symmetric with `_principal_to_keyword_map`); add test that runs `make_audit_handler → perform → entries[0].to_edn()` under a real `Runtime` with bare-string handler names.
3. **Fix B1** (multi-step intervention collapse): change `engine.py:164` to `list(interventions)`, propagate type to `Trajectory.intervention: Optional[list[dict]]`, update `to_edn`/`from_edn`/`:trajectory/intervention` spec to `seq_of(...)`. Flip G4 shape-pin assertion to pin the correct list behaviour. Bundles cleanly with N5's spec relaxation.
4. **Fix N8** (optional, low-priority): fold `mark_invalidated` into `allocate_and_append` for single-txn `DB.transact` semantics. Add crash-recovery test or docstring pin.

**Target after Round 4:** ≥ 9.0. This is the NeSy-submittable floor. With N5+N6+B1 closed cleanly and a matching integration test for each, correctness lands at ~9.1.

**What Round 4 must NOT do:**
- Do not widen the scope. P-plan-node, P-concurrency, P-op-invariants all landed cleanly — don't re-open them.
- Do not revisit the F8/F10/F12 deferred items; those are blessed for Phase 2.
- Do not over-index on N7 — the open-map semantics is a spec-design choice, not a defect.

**Gate pass:** Round 3 correctness passes at **8.6** — above the 8.5 floor, 0.2 below the 8.8 target. The min-across-reviewers gate depends on R2-rigor / R3-composability / R4-research converging above 8.5 on the same HEAD. The diagnosed residual work (N5, N6, B1, N8) is small and well-scoped; Round 4 is a single-worker polish pass of ≤1 hour, not another full fix round.
