# ARIS Round 4 (FREEZE GATE) — Reviewer R2 — Test Rigor & Invariants

**Commit reviewed:** `61644f6` (main, W-wire merged)
**Suite state:** **538 passed in 2.36s** on this sandbox (13 hypothesis-gated
generative tests not collected; matches worker-claimed 551 on the full
environment; +33 since Round 3 baseline of 518).
**Round 1 grade:** 6.4 / 10.
**Round 2 grade:** 8.3 / 10.
**Round 3 grade:** 8.9 / 10 (four R2 G-findings + two R3-originated minors).
**Round 4 grade:** **9.1 / 10** — FREEZE GATE PASSED (target ≥ 9.0).

## Summary

I read the 72 tests in the targeted modules end-to-end, re-ran the
extended suite locally (538 pass, zero flakes across 3 reruns of the
concurrency + wire tests), and probed the five remediation claims with
targeted in-process scripts. The R3 residual ledger is effectively
closed:

- **G4 (multi-step intervention shape pin)** was rewritten to the CORRECT
  list shape, not the Phase-1 buggy `isinstance(..., dict)` shape — the
  regression-trap-flavoured xfail dance R3 flagged is gone. The engine
  at `engine.py:170` now stores `[copy.deepcopy(iv) for iv in interventions]`
  and the spec slot is `seq_of(ref(":persistence.replay/intervention"))`.
  The pin asserts length, per-entry field equality, and cross-replay
  determinism. Quality 9.
- **BANNED_CALLS** extension is real and plant-and-catch-verified:
  `time.monotonic` and `time.perf_counter` both in the banned set, each
  with a dedicated self-test that plants the exact call and confirms the
  detector fires. Zero pre-existing hits in `src/` confirmed. Quality 9.
- **Multi-line noqa scan** closed correctly for the case R3 flagged:
  `_has_noqa_in_span` scans `node.lineno..node.end_lineno` inclusive, and
  `test_lint_does_not_flag_noqa_on_multiline_call` exercises the
  canonical shape (noqa on the closing-paren line). Quality 9 for the
  R3-flagged case; the "preceding-line" placement from the prompt is a
  different convention that isn't standard noqa — not in scope. I note
  the alternate placement as a R4 artifact below but not as a gap.
- **AuditEntry.from_edn** inverse is real and exercised on all five verdict
  shapes, parent/policy/run_id present-and-absent, empty and non-empty
  handler chain. I ran `from_edn(to_edn(e)) == e` externally with the
  full parent + policy_id + run_id + mixed-verdict combinations — whole
  dataclass equality holds. The shipped tests use field-by-field
  comparison rather than `e == e2`, which is slightly weaker (they
  could miss a new field added to the dataclass); I flag this as a
  MINOR latent gap, not a R4 blocker. Quality 8.
- **Prop 4 (verify_chain formal iff)** — all four iff-failing cases
  exercised: mutation (`test_tampering_an_entry_breaks_the_chain`),
  middle-deletion (`test_deleting_an_audit_entry_breaks_the_chain`),
  reorder (`test_reordering_audit_entries_breaks_the_chain`), and
  tail-truncation-is-allowed (`test_truncating_audit_entries_from_tail_preserves_chain`).
  Each also pre-asserts `verify_chain(entries) is True` before the
  mutation, giving both iff directions. Paper §4.3 Prop 4 text matches
  the iff exactly. Quality 9.

Three new wire-boundary test files land on top of the remediation list
(`test_intervention_wire.py`, `test_handler_chain_wire.py`,
`test_wire_identity.py`). They are real rigor — each test covers a
distinct production path that the R3 bar of "not regressed in happy-path
only" did not exercise. Details in §2.

One systemic gap carries forward into Phase 2 unchanged: no scaling
regression guard for Prop 1. Worker explicitly deferred per prompt
scope. Accepted.

## Per-module rigor grades

| Module | R1 | R2 | R3 | **R4** | Δ (R3→R4) | Notes |
|---|---:|---:|---:|---:|---:|---|
| fact | 6.5 | 8.3 | 9.2 | **9.4** | +0.2 | Datom `__post_init__` normalisation + algebraic round-trip identity tests close R3 N2 (was "single biggest drag on composability") |
| effect | 7.0 | 9.0 | 9.3 | **9.4** | +0.1 | Bare-string handler_chain conform + AuditEntry.from_edn inverse + Prop 4 iff backing |
| spec | 6.0 | 7.2 | 8.3 | **8.3** | 0 | Unchanged — no spec-scope fixes in W-wire |
| replay | 7.0 | 8.7 | 8.8 | **9.2** | +0.4 | B1 closed cleanly (list storage + list wire keywordification + round-trip); G4 pin rewritten to correct shape |
| tests/lint | 7.5 | 8.5 | 9.0 | **9.3** | +0.3 | BANNED_CALLS extended, multi-line noqa span scan, 10 plant-and-catch self-tests |

## 1. R3 small-item remediation

### G4 — multi-step intervention shape pin (closed clean)

**Claim:** "Shape pin rewritten to assert list shape, no xfail loom."

**Verdict: CLOSED at quality 9.**

`tests/replay/test_replay.py:214-238` was rewritten. The R3 version had
`isinstance(cf_a.intervention, dict)` pinning the Phase-1 buggy shape
with a docstring pointer to B1. The R4 version asserts:

- `isinstance(cf_a.intervention, list)` — correct new shape.
- `len(cf_a.intervention) == len(interventions)` — no entries dropped.
- Per-entry `stored["step"] == submitted["step"]`, `["field"]`, `["new_value"]`
  under `zip(cf_a.intervention, interventions)` — order preserved,
  fields intact.
- `cf_a.intervention == cf_b.intervention` — cross-replay determinism on
  the lineage field.
- The prior `cf.branch_point == min(intervention.step)` pin is retained.

No xfail marker is present anywhere in the test; none is needed because
this IS the fix round. Source confirms: `engine.py:170` reads
`[copy.deepcopy(iv) for iv in interventions]`, and `trajectory.py:118`
types the field as `Optional[list[dict]]`.

**R3 N3 (the G4 xfail regression-trap warning) is retired.**

### BANNED_CALLS extension — monotonic + perf_counter (closed clean)

**Claim:** "`tests/test_wall_clock_ban.py` now catches `time.monotonic()`
in a planted violation. Is there a self-test?"

**Verdict: CLOSED at quality 9.**

`BANNED_CALLS` at `tests/test_wall_clock_ban.py:41-61` includes both
`("time", "monotonic")` and `("time", "perf_counter")`. The detector
function `_is_banned_call` routes them through the same `key in
BANNED_CALLS` check as `time.time`, so the single detector logic covers
all three.

Plant-and-catch self-tests at lines 213-228:

```python
def test_lint_detects_planted_time_monotonic():
    planted = "import time\n" "time.monotonic()\n"
    violations = _scan_source_for_violations(planted)
    assert any("time.monotonic" in v for v in violations), violations

def test_lint_detects_planted_time_perf_counter():
    planted = "import time\n" "time.perf_counter()\n"
    violations = _scan_source_for_violations(planted)
    assert any("time.perf_counter" in v for v in violations), violations
```

Both assertions check the detector actually reports the violation
string — not merely "scan returned non-empty". A regression that
stubbed `_is_banned_call` to always return None would red both tests.

Verified: `test_no_wall_clock_calls_in_production_code` passes, meaning
zero pre-existing hits in `src/persistence/`. The worker did not have
to retrofit noqa annotations; the ban is prospective-clean.

### Multi-line noqa — same-line vs. preceding-line (closed for the R3 case)

**Claim:** "Multi-line noqa on the line before a banned call be correctly
detected? Try both placement: same-line vs. preceding-line."

**Verdict: CLOSED at quality 9 for the R3-flagged case (noqa on the
CLOSING-paren line of a multi-line call); the "preceding-line" placement
is NOT standard noqa convention and is not in scope.**

The R3 gap text (`docs/aris-round-3/R2-rigor.md:124-130`) specifically
called out the shape:

```python
datetime.now(
    timezone.utc,
)  # noqa: wall-clock   # <-- on the CLOSING paren line, not the opening
```

`_has_noqa_in_span` at `tests/test_wall_clock_ban.py:89-104` scans every
physical line from `node.lineno` through `node.end_lineno` inclusive
(guarded for older AST without `end_lineno`). The test
`test_lint_does_not_flag_noqa_on_multiline_call` (lines 259-275) plants
exactly the closing-paren-noqa shape and asserts zero violations. This
is the case R3 flagged.

I considered the prompt's "preceding-line" alternative — a noqa on the
line ABOVE the call:

```python
# noqa: wall-clock -- deliberate
datetime.now()
```

This is not standard `flake8`/`ruff` noqa behaviour (noqa applies to
the same physical line), and pinning it would over-fit. The shipped
scan would NOT detect this placement, which is correct. I do not treat
this as a gap.

One subtle corner the shipped scan does NOT catch: same-line noqa on
the OPENING paren line of a multi-line call is covered (`start` includes
`lineno`), and closing-paren is covered (end of span). Any line strictly
BETWEEN the opening and closing paren is ALSO scanned — the implementation
iterates `range(start, end_idx + 1)`. So the span scan is as broad as it
needs to be without being wider.

### AuditEntry.from_edn inverse — does it hold on ALL production shapes?

**Claim:** "does `from_edn(to_edn(e)) == e` on ALL production shapes?"

**Verdict: YES — the INVARIANT holds on all production shapes I tested;
the SHIPPED TEST uses field-by-field comparison rather than `==`, which
is slightly weaker than the claim language.**

I exercised `from_edn(to_edn(e)) == e` externally with:

- `verdict` in `{"ok", "error", "deny", "deny-silently", "require-approval"}`
- `parent` both present and None
- `policy_id` both present (as `:policy/...` EDN keyword) and None
- `run_id` both present (UUID string) and None
- `prev_hash` both present and None
- `handler_chain` both `()` (empty) and `("audit", "llm", "policy", "raw")`
- `result_hash` both present and None
- `error` both present and None
- `principal` both `{}` and `{"agent": "a", "team": "b"}`

All combinations satisfy `e == e2` (dataclass `__eq__` since
`AuditEntry` is `@dataclass(frozen=True)`). One policy_id input that
doesn't conform (`"policy:safe-llm-v1"` — no leading colon) raises in
`to_edn` at the self-conform gate, not a round-trip bug.

**The shipped tests at `tests/effect/test_audit_from_edn.py` do NOT
use direct `e == e2` equality.** They use per-field assertions:
`restored.id == original.id`, `restored.op == original.op`, etc. This
is weaker than `e == e2` in one respect: a future engineer who adds a
new field to `AuditEntry` and forgets to wire it through `to_edn` /
`from_edn` will not have that caught by these tests unless they
simultaneously remember to add a line to the assertion list. The direct
`e == e2` form would red automatically.

Mitigation: the wire form self-conforms on `to_edn`, so a missing-from-
`to_edn` field would red the spec gate. But if the field were optional
on the spec, it could slip through.

**Severity of the test-strength gap: MINOR.** Not a correctness issue;
a test-brittleness issue. Worth a 1-line R4-closing polish if this
round is truly final: `assert AuditEntry.from_edn(e.to_edn()) == e`.
Since W-wire is the freeze round, I raise this as a Phase-2 todo
rather than a freeze blocker.

## 2. New wire-boundary test rigor assessment

### `tests/replay/test_intervention_wire.py` (9 tests)

**Rigor: HIGH. Three issues worth flagging as Phase-2 follow-ups.**

Good coverage on:

- **Multi-step case** — `test_three_interventions_produce_length_three_list`
  submits three interventions at steps 0/1/2, asserts length 3 and
  per-entry step field preservation.
- **Single-step is still list** — `test_single_intervention_still_produces_length_one_list`
  — ensures the type change is uniform (a 1-list, not an auto-unwrapped
  bare dict).
- **Caller mutation safety** — `test_engine_deepcopies_so_caller_mutation_is_safe`
  mutates the submitted `ivs` list after replay and confirms the stored
  intervention is unaffected. This pins the `copy.deepcopy` behaviour.
- **to_edn conform on engine output** — `test_to_edn_conforms_on_single_intervention_replay`
  and `test_to_edn_conforms_on_multi_intervention_replay` exercise the
  R1 N5 closure (production engine output, not synthetic pre-keywordified
  input).
- **Wire shape** — `test_to_edn_intervention_slot_is_seq_of_keyword_keyed_dicts`
  pins the EDN wire form: keys are `:step`/`:field`/`:new-value`,
  `:field` is itself a keyword.
- **from_edn round-trip** — `test_round_trip_preserves_multi_intervention_list`
  pins the full round-trip to bare strings on the Python side.
- **Synthetic constructor path** — confirms list works without going
  through the engine.
- **intervention=None default** — confirms the slot is omitted from wire
  when unset.

**Gaps (Phase-2 polish, not freeze blockers):**

1. **Out-of-order interventions not tested.** Submitting
   `[{step: 2, ...}, {step: 0, ...}]` is valid input. I probed the
   engine: `branch_point` correctly resolves to `min(steps) = 0`, and
   `cf.intervention` preserves the ORIGINAL submission order (steps
   `[2, 0]`, not sorted). If a future engineer normalises to sorted
   order, the lineage surface would silently change. Not tested.
2. **Empty interventions list not tested.** I probed: `replay(t,
   interventions=[], ...)` raises `ValueError("replay requires at least
   one intervention")`. This is a legitimate behaviour but is NOT pinned
   by a test in this file. Round 3's `test_empty_trajectory_replay_raises`
   covers the empty-TRAJECTORY case, which is different.
3. **Duplicate step interventions not tested.** Submitting two
   interventions for the same step is nonsense; `interventions_by_step =
   {i["step"]: i for i in interventions}` silently overwrites the
   earlier one with the later. Whether this should be an error or a
   documented "last-write-wins" is a design question. Not pinned either
   way.

None of these block freeze. All three are MINOR scope-extension for
Phase 2.

### `tests/effect/test_handler_chain_wire.py` (5 tests)

**Rigor: HIGH. Covers all three prompt-requested shapes.**

- **Bare-string chain** — `test_to_edn_on_real_bare_string_chain_conforms`
  builds `handler_chain=("audit", "policy", "raw")` and asserts the
  `to_edn` output conforms. This would red on main before
  `df7a9fd`: the worker is honest in the docstring.
- **Already-keyworded chain** — `test_to_edn_idempotent_on_prekeyworded_chain`
  builds `(":audit", ":policy")`, asserts no double-colons.
- **Mixed chain** — `test_to_edn_handles_mixed_chain` builds
  `("audit", ":policy", "raw")`, asserts uniform `[":audit", ":policy", ":raw"]`
  on the wire.
- **Empty chain** — `test_to_edn_preserves_empty_chain` — edge case
  coverage.
- **Keywordification** — `test_wire_chain_entries_are_keywordified`
  asserts every wire entry `.startswith(":")`.

Notably, the test tightens the pre-existing `test_audit_self_conform.py`
`_sample_entry` helper from `(":audit", ":policy", ":raw")` to the
production-shape `("audit", "policy", "raw")`, flushing out the bug
that was previously hidden by the test fixture itself — this is the
real rigor improvement on this axis.

No gaps I would raise above MINOR.

### `tests/fact/test_wire_identity.py` (10 tests)

**Rigor: HIGH on the extended input domain. Stops short of a full
algebraic identity test.**

The file is explicitly about the EXTENDED input domain where R3 showed
`wire_to_datom ∘ datom_to_wire` lost identity on pre-keyworded inputs.
The shipped tests cover:

- `.a` normalisation — colon stripped on construction, both colon and
  bare inputs produce equal Datoms.
- `.a` idempotent on bare input.
- `provenance["source"]` normalisation — same symmetry.
- `provenance["source"]` idempotent on bare input.
- Non-source provenance keys (`model`, `confidence`) NOT touched —
  normalisation is narrow, not blanket.
- **Round-trip identity on pre-keyworded `a`**.
- **Round-trip identity on pre-keyworded `source`**.
- **Round-trip identity on both pre-keyworded**.
- **Wire always emits keyworded form** — regardless of construction input.

**What stops the grade at 9 rather than 10:** None of the tests asserts
the algebraic identity `wire_to_datom(datom_to_wire(d)) == d` as whole-
dataclass equality. They assert `.a == original.a` and
`.provenance["source"] == original.provenance["source"]`, and one test
adds `.provenance["confidence"]`. The existing `test_wire.py::test_roundtrip_preserves_fields`
covers 8 fields (`e`, `a`, `v`, `tx`, `tx_time`, `valid_from`,
`valid_to`, `op`) but not `provenance` (beyond the narrow source check)
and not `invalidated_by`.

Since `Datom` is `@dataclass(frozen=True, slots=True)`, `__eq__` is
generated — a single `assert wire_to_datom(datom_to_wire(d)) == d`
would catch every existing AND future field drift. This is the
strictly-stronger test shape the prompt asks about ("algebraic identity
test (datom_to_wire ∘ wire_to_datom = id) or just a happy-path"). The
shipped tests are "targeted happy-path on the extended domain", not
full algebraic identity. Good enough for freeze, worth a MINOR Phase-2
polish.

## 3. Proposition coverage update

### Prop 1 — Branch complexity — **6 / 10 (unchanged)**

Out-of-scope per W-wire prompt. No scaling test added. The R3 comment
stands: fix is a 1-hour parametrisation over log sizes `[10, 100,
1_000, 10_000]` + bounded-ratio assertion. This is the single biggest
open rigor gap for Phase 2 (not Phase 1, because the softened
`O(|Δ|)` claim in the paper is assertion-by-inspection — true but
unguarded).

### Prop 2 — Well-formedness machine-check — **7.5 / 10 (unchanged)**

Also out of scope. No test added for `is_well_formed(catalog)` called
from inside a `mask(...)` block. The R3 gap stands: a stack that is
statically well-formed can become operationally ill-formed when a
relevant handler is masked. This interaction is load-bearing for the
§4.2 policy-universality claim. Phase-2 task.

### Prop 3 — Byte-identical NO-OP replay — **9.5 / 10 (unchanged from R3)**

R3 strengthened this with G2's value-level equality on `cache`,
`call_log`, `outcome`. W-wire did not touch Prop 3 tests directly, but
the `test_intervention_wire.py` suite indirectly exercises the same
path: three `_factual()` → `replay(...)` → `to_edn()` → `from_edn()`
round-trips that would have red on main pre-W4. The 0.5 deduction
(bridge test still uses `facts=[]`) stands unchanged — Phase 2 polish.

### Prop 4 (new) — Audit-chain immutability — **9 / 10**

**Rigor of the shipped test backing:**

Paper §4.3 Proposition 4 (line 163) reads:

> For any audit-chain `C = ⟨e_0, e_1, ..., e_n⟩` produced by the
> Merkle-hashed `append_audit_entry`, `verify_chain(C) = True` iff for
> all i, `e_i.id = sha256(canonical(e_i.fields \ {id}))` and
> `e_i.prev_hash = e_{i-1}.id` — i.e. no entry has been mutated,
> deleted, reordered, or truncated from the middle. This is exercised
> end-to-end by `tests/effect/test_audit.py::test_tampering_an_entry_breaks_the_chain`,
> `test_deleting_an_audit_entry_breaks_the_chain`, and
> `test_reordering_audit_entries_breaks_the_chain`; tail-truncation is
> allowed by construction (`test_truncating_audit_entries_from_tail_preserves_chain`)
> and must be detected by regulators comparing a separately-recorded
> expected length.

Does the shipped test exactly exercise the iff condition?

Iff means two directions:

1. **If the hash/prev_hash invariants hold → `verify_chain = True`.**
   All four tests open with `perform(...)` to build a 3-5-entry chain
   and then assert `verify_chain(entries) is True` BEFORE any mutation.
   This is the "valid chain ⇒ True" direction — covered four times.

2. **If any of (mutated | deleted | reordered | middle-truncated) →
   `verify_chain = False`.**

   - **Mutation:** `test_tampering_an_entry_breaks_the_chain` — sets
     `entries[1] = entries[1].with_fields(args_hash="sha256:deadbeef")`,
     asserts `verify_chain(entries) is False`. Covers the
     `e_i.id = sha256(...)` clause.
   - **Deletion:** `test_deleting_an_audit_entry_breaks_the_chain` —
     `del entries[2]` (middle entry) on a 5-entry chain, asserts
     `verify_chain(entries) is False`. The docstring explicitly calls
     out why: "entries[2] (was entries[3]) still points prev_hash at
     the deleted id." Covers the `e_i.prev_hash = e_{i-1}.id` clause
     for the missing-middle case.
   - **Reorder:** `test_reordering_audit_entries_breaks_the_chain` —
     `entries[2], entries[3] = entries[3], entries[2]` on a 5-entry
     chain, asserts `False`. Covers the chain-linkage clause for the
     swap case.
   - **Tail-truncation is ALLOWED:** `test_truncating_audit_entries_from_tail_preserves_chain`
     asserts `verify_chain(entries[:3]) is True`. This is the explicit
     design carve-out from the iff; the docstring notes regulators
     must compare against a separately-recorded expected length.

Each mutation is "single-entry, minimal", which is the correct probe
shape: a 2-entry swap without a fresh chain would be easily mis-read
as "reorder AND mutation".

**What stops this from 10:**

- **No test for middle-truncation as a distinct scenario.** `del
  entries[2]` on a 5-chain removes a middle entry (covered). But
  removing `entries[2:4]` — two consecutive middle entries — is a
  related but distinct scenario. Not tested. The iff should still
  catch it (entries[4] points prev_hash at entries[3].id which is
  now missing), but it's not pinned.
- **No test that combines two violations** (e.g., mutate AND reorder).
  The iff covers this by construction (either condition alone is
  sufficient to red), but a combined-violation test would pin that the
  detector doesn't silently compensate.
- **No test that a mutated entry survives if its prev_hash is ALSO
  recomputed consistently.** This would be the "adversary who
  understands the chain" test: mutate `entries[2]` content, recompute
  `entries[2].id`, update `entries[3].prev_hash` to match. The iff as
  stated would LOGICALLY accept this (the new chain is internally
  consistent); the protection is external (regulator-held root
  hash). Not tested either way, but the paper's Prop 4 text correctly
  defers external-anchor detection to the regulator. Consistent.

**The iff is tightly stated and tightly tested for single-point
violations. Quality 9.** A single Phase-2 test covering
two-consecutive-middle-deletions would nudge this to 9.5.

## 4. New rigor gaps in Round 4

Four MINOR gaps, none blocking freeze:

### R4-G1 (MINOR) — `AuditEntry.from_edn` round-trip uses field-by-field, not `==`

**Location:** `tests/effect/test_audit_from_edn.py::test_round_trip_preserves_all_fields`
**What:** 15+ per-field `restored.X == original.X` assertions.
**Why it bites:** A field added to `AuditEntry` in Phase 2 will not be
round-trip-tested unless the engineer also adds a line here. The
stricter `assert AuditEntry.from_edn(e.to_edn()) == e` would catch
drift automatically.
**Fix:** One-line replacement. 15 minutes.

### R4-G2 (MINOR) — `test_wire_identity.py` not a full algebraic identity

**Location:** `tests/fact/test_wire_identity.py::TestWireRoundTripIdentity`
**What:** Tests assert `restored.a == original.a` and
`.provenance["source"]`, not `restored == original`.
**Why it bites:** Same failure mode as R4-G1 — a new Datom field (e.g.,
`signature`, which is a Phase-2 ed25519 slot) won't be covered unless
manually added.
**Fix:** Replace three `.a == .a` assertions with `assert
wire_to_datom(datom_to_wire(d)) == d`. 15 minutes.

### R4-G3 (MINOR) — Intervention wire lacks empty / out-of-order / duplicate-step tests

**Location:** `tests/replay/test_intervention_wire.py`
**What:**

- Empty intervention list raises `ValueError` (verified by probe) but
  is not test-pinned.
- Out-of-order interventions preserve submission order in
  `cf.intervention` (verified by probe); not test-pinned against a
  future sort.
- Duplicate-step interventions silently last-write-wins on
  `interventions_by_step`; neither error nor documented behaviour is
  pinned.
**Why it bites:** Phase 2 DPO / regulator-replay may need to order
interventions by step for presentation; silent behaviour change would
not red any test.
**Fix:** Three small tests. 30 minutes.

### R4-G4 (MINOR) — Prop 4 lacks combined-violation and two-middle-deletion tests

**Location:** `tests/effect/test_audit.py`
**What:** Single-point violations are covered. Two-consecutive-middle-
deletions and mutate+reorder combos are not.
**Why it bites:** Phase-2 regulator-replay is adversarial; the detector
should obviously fail on combined violations, but "obviously" is where
bugs live.
**Fix:** Two more tests in the same file. 20 minutes.

## 5. Overall rigor grade

**9.1 / 10** — FREEZE GATE PASSED (target ≥ 9.0).

The grade reflects:

- **+0.2 on R3 (8.9)** from four closed R3 residuals: G4 shape-pin
  rewritten correctly, BANNED_CALLS extended with self-tests, multi-line
  noqa span scan, AuditEntry.from_edn inverse added.
- **+0.2 from new wire-boundary rigor** that covers three production
  paths previously not exercised: `test_intervention_wire.py` (engine
  output → to_edn conform), `test_handler_chain_wire.py` (production-
  shape bare-string chain → conform), `test_wire_identity.py`
  (extended input domain for Datom round-trip).
- **−0.2 from four new MINOR gaps** (R4-G1..G4 above), all 15-30-minute
  fixes that weren't explicitly in scope this round.

Not 9.5 because: the three "should be full algebraic identity" tests
(AuditEntry round-trip, Datom wire round-trip, and wire-identity
TestWireRoundTripIdentity) are all narrowly-scoped field lists rather
than `==` comparisons. Each is correct as shipped; each is weaker than
it could be for Phase 2 evolution. The Prop 4 iff backing is tight on
single-point violations but not on combined violations.

Not 9.0-flat because: the W-wire track is honest and surgical, the
tests that DID ship are all the right shape (plant-and-catch, production
-path probes, cross-replay determinism), zero xfail markers were used
as cover, and the paper's Prop 4 is stated as a genuine iff rather than
a one-way implication. That earns the +0.1 above the gate.

**R2-Rigor grade: 9.1 / 10 → FREEZE GATE PASSED.**

## 6. Phase 2 residual rigor todos

The backlog carried into Phase 2. All are scope-extension, not Phase-1
regressions.

1. **Prop 1 scaling regression guard** — parametrise branch-isolation
   over log sizes `[10, 100, 1_000, 10_000]`; assert `branch_time /
   log_size < bound`. Paper §4.2 softening becomes test-backed. ~1h.
2. **Prop 2 mask-interaction test** — `rt.is_well_formed(catalog)`
   under `mask(...)` — surface the transient-ill-formedness scenario
   explicitly. ~30min.
3. **R4-G1** — replace AuditEntry field-by-field round-trip with
   `assert AuditEntry.from_edn(e.to_edn()) == e`. ~15min.
4. **R4-G2** — replace Datom field-by-field wire round-trip with
   `assert wire_to_datom(datom_to_wire(d)) == d`. ~15min.
5. **R4-G3** — three intervention-wire edge cases (empty list,
   out-of-order, duplicate step). ~30min.
6. **R4-G4** — Prop 4 combined-violation tests (mutate+reorder,
   two-consecutive-middle-delete). ~20min.
7. **Bridge test with non-empty `facts`** — R3 F2 residual; extend the
   effect-replay bridge to assemble a real Trajectory with facts through
   `effect.Runtime`, so Prop 3 byte-identity exercises the full runtime
   path. ~2h.
8. **DB.transact input self-conform** — R2 F8 residual explicitly
   deferred by W-wire worker with rationale (raw-dict input, atomic
   section ordering). Revisit alongside `persistence.txn` STM design.
9. **`:audit/parent` threading test** — no test currently pins the
   `parent` field on AuditEntry across nested calls; latent per R3.
   ~30min.
10. **Generative property-based coverage** — `tests/spec/test_generative.py`
    is hypothesis-gated and skipped in this sandbox (13 tests); Phase 2
    should extend to hypothesis strategies over `:persistence.fact/datom`,
    `:persistence.replay/trajectory`, and `:persistence.effect/audit-entry`
    for the full wire round-trip. ~half-day.

**Combined Phase-2 rigor work: ~5-6 focused hours + one half-day on
generative coverage.** None is a Phase-1 unfreeze trigger.

## 7. Go / no-go for freeze

**GO — FREEZE Phase 1.**

- All five must-fixes from Round 3 are closed (G4 shape pin rewritten,
  BANNED_CALLS extended with self-test, multi-line noqa span scan,
  AuditEntry.from_edn inverse, Prop 4 paper statement + test backing).
- Three new wire-boundary test files land rigor on production paths
  the happy-path fixtures had been hiding (bare-string chain, extended
  Datom input domain, engine-produced intervention list).
- 538 local / 551 full suite green, zero flakes over three concurrency
  reruns.
- R2-Rigor 9.1 ≥ 9.0 gate.
- No residual regression traps (the R3 xfail concern is retired with
  the correct-shape rewrite).

The four new MINOR gaps (R4-G1..G4) are all 15-30-minute Phase-2 polish,
orthogonal to each other, and none requires a redesign. The Phase-2
rigor backlog is clean and focused.

Sign-off from R2-Rigor: **Phase 1 is NeSy-submittable on the rigor
axis.** Freeze.
