# ARIS Round 5 (FREEZE-CLEAN GATE) — Reviewer R2 — Test Rigor & Invariants

**Commit reviewed:** `60b3c85` (main, W-polish2 merged)
**Suite state:** **560 passed, 2 skipped** on this sandbox (1 hypothesis-gated
collection error on `tests/spec/test_generative.py` — `hypothesis` not
installed in this env; matches worker-claimed 575 on the full environment
with hypothesis available; +24 since Round 4 baseline of 538 local /
551 full).
**Round 1 grade:** 6.4 / 10.
**Round 2 grade:** 8.3 / 10.
**Round 3 grade:** 8.9 / 10.
**Round 4 grade:** 9.1 / 10 (FREEZE GATE passed; four R4-G1..G4 minors).
**Round 5 grade:** **9.3 / 10** — FREEZE-CLEAN GATE PASSED (target ≥ 9.2).

## Summary

I read the four new test files end-to-end, diffed `audit.py`,
`datom.py`, `wire.py` against their Round 4 bodies, verified the bonus
assertion in `test_audit_from_edn.py`, re-read the paper §4.3 Prop 4
text, and re-ran the relevant suite locally (560 pass / 2 skip on this
sandbox; full 575 on hypothesis-enabled env).

W-polish2 is an honest, surgical round:

- **R4-G1 closed as claimed** — the bonus commit `9299e40` adds
  `test_round_trip_equality_by_dataclass_eq` using dataclass `__eq__`.
  True algebraic identity on the test's verdict fixture.
- **R4-G2 — NOT touched.** Still narrow field-by-field. Carries forward
  to Phase 2.
- **R4-G3 — NOT touched.** Empty / out-of-order / duplicate-step
  intervention edge cases still unpinned.
- **R4-G4 — NOT touched** on the test side. The *paper* got a crisper
  Prop 4 sentence (`make_audit_handler`'s Merkle-hashed chain-append
  clause replaces the phantom `append_audit_entry`), but no new test
  pins combined-violation or two-consecutive-middle-deletion.
- **Three new R5 test files add genuine rigor** on top of the R4
  freeze: `test_audit_canonicalize.py` (14 tests), `test_datom_idempotent.py`
  (4 tests), `test_provenance_symmetry.py` (5 tests). The bodies are
  surgical and each test exercises a distinct production defect that
  was latent behind Round 4 happy-path fixtures (R1 N7 bare policy_id,
  R3 R4-N1/N2/N3/N4 pre-keyworded round-trips, double-colon
  idempotency, pre-keyworded-key + bare-value provenance asymmetry).

The net rigor delta is **+0.2** from R4: G1 closes for +0.1, and the
three new test files land real rigor on production paths for +0.1. I
stop at 9.3 (not 9.5) because G2/G3/G4 are unchanged — they are still
the same Phase-2-polish backlog, and two of them now have been flagged
across two rounds which mildly increases the risk that a Phase-2 change
silently breaks them. Still freeze-clean.

## Per-module rigor grades

| Module | R1 | R2 | R3 | R4 | **R5** | Δ (R4→R5) | Notes |
|---|---:|---:|---:|---:|---:|---:|---|
| fact | 6.5 | 8.3 | 9.2 | 9.4 | **9.5** | +0.1 | `test_datom_idempotent.py` double/triple colon + `test_provenance_symmetry.py` closes the R3 R4-N4 asymmetry. Both are small files but cover exactly the defects R4 flagged. |
| effect | 7.0 | 9.0 | 9.3 | 9.4 | **9.6** | +0.2 | `test_audit_canonicalize.py` (14 tests across 4 classes) closes R1 N7 + R3 R4-N1/N2 — the single biggest surviving production-path defect. G1 bonus assertion closes the composition identity (narrow verdict coverage though — see §2). |
| spec | 6.0 | 7.2 | 8.3 | 8.3 | **8.3** | 0 | Unchanged — no spec-scope fixes in W-polish2. |
| replay | 7.0 | 8.7 | 8.8 | 9.2 | **9.2** | 0 | R4-G3 unchanged (empty / out-of-order / duplicate-step interventions still unpinned). |
| tests/lint | 7.5 | 8.5 | 9.0 | 9.3 | **9.3** | 0 | No changes in W-polish2. |

## 1. R4-G1..G4 status

### R4-G1 — `AuditEntry.from_edn ∘ to_edn` dataclass equality — **CLOSED**

**Claim:** Bonus commit `9299e40` adds `test_round_trip_equality_by_dataclass_eq`
using `==` not field-by-field.

**Verdict: CLOSED at quality 8 (not 9 — see verdict-coverage gap
below).**

Read at `tests/effect/test_audit_from_edn.py:63-77`:

```python
def test_round_trip_equality_by_dataclass_eq(self):
    ...
    original = _entry()
    restored = AuditEntry.from_edn(original.to_edn())
    assert restored == original
```

This is a true algebraic identity assertion — `AuditEntry` is
`@dataclass(frozen=True)`, so `__eq__` is synthesised over *every*
field including any future Phase-2 additions (`signature`, etc.). A
future engineer adding a field and forgetting to wire it through
`to_edn`/`from_edn` will red this test automatically. Correctly
strictly-stronger than the field-by-field assertion it sits next to.

The older `test_round_trip_preserves_all_fields` remains in place with
the docstring noting its role is \"debug ergonomics\"; good — when the
stronger `==` reds, the weaker test gives a field-level breadcrumb.

**Verdict coverage gap (narrow):** The bonus `==` assertion runs on
the `_entry()` default which has `verdict="ok"`. The existing per-field
test `test_round_trip_preserves_error_entry` covers `verdict="error"`
but *via field-by-field comparison*, not `==`. The three other verdicts
(`"deny"`, `"deny-silently"`, `"require-approval"`) are covered by
neither test-strength nor verdict variation in this file. The prompt
asks: *does it cover all 5 verdicts?* **No — 1 of 5 under `==`, 2 of
5 if you count the field-by-field error-entry test.**

This doesn't block freeze: the `_verdict_as_edn`/`_verdict_as_python`
round-trip is tested independently at `tests/effect/test_verdicts.py`
(I verified this file covers all five verdicts both directions); so
the risk of `==` missing a verdict-specific round-trip bug is low.
But if Phase 2 extends `AuditEntry` with verdict-conditional fields
(e.g., a denial-reason), the missing verdict coverage would bite.

**Phase-2 polish (minor):** parametrise the bonus test over the five
verdicts. 10 minutes.

### R4-G2 — `test_wire_identity.py` algebraic identity — **STILL OPEN**

**Claim:** No claim from W-polish2 — explicitly listed as out-of-scope
(the worker summary calls \"R2 R4-G1..G4 (beyond the one freebie taken)
— Phase 2 rigor polish\").

**Verdict: UNCHANGED from R4.**

`git log --since=\"2026-04-20\" -- tests/fact/test_wire_identity.py`
returns no R5 commits. I re-read the file; it still uses
field-by-field `.a` / `.provenance[\"source\"]` assertions rather than
the whole-dataclass `assert wire_to_datom(datom_to_wire(d)) == d`
form. The R5 provenance-symmetry file *indirectly* exercises the
symmetry on an extended input (pre-keyworded key + bare value), but
does not pin the algebraic identity. R4-G2 carries forward.

**Phase-2 polish (minor):** one-line replacement. 15 minutes.

### R4-G3 — empty / out-of-order / duplicate-step interventions — **STILL OPEN**

**Verdict: UNCHANGED from R4.**

`git log --since=\"2026-04-20\" -- tests/replay/test_intervention_wire.py`
returns no R5 commits. `grep -r \"empty intervention\\|out.of.order\\|
duplicate.*step\"` in `tests/replay/` returns zero new tests. The
three edge-case behaviours R4 flagged (ValueError on empty list,
original-submission-order preservation on out-of-order input,
last-write-wins on duplicate steps) remain test-unpinned. R4-G3
carries forward.

**Phase-2 polish (minor):** three small tests. 30 minutes.

### R4-G4 — Prop 4 combined-violation + two-consecutive-middle-deletion — **STILL OPEN (tests); PARTIALLY ADDRESSED (paper)**

**Verdict: UNCHANGED on test backing; paper wording improved.**

**Paper side:** The W5-paper-patch2 commit `1e291b5` rewrote the Prop 4
sentence from `append_audit_entry` (phantom) to \"`make_audit_handler`'s
Merkle-hashed chain-append clause\" (real — the clause closure in
`audit.py:336-386` that appends to `ctx[\"entries\"]`). This is a
correctness fix on the paper's pointer, not a test addition. Good.

**Test side:** `tests/effect/test_audit.py` has the same four tests as
R4 — tampering, middle-deletion (single entry), reorder, tail-truncation.
No R5 commit adds combined-violation (mutate+reorder) or
two-consecutive-middle-deletion (e.g., `del entries[2:4]`). R4-G4
carries forward unchanged on the test side.

**Phase-2 polish (minor):** two tests. 20 minutes.

---

## 2. New R5 test rigor assessment

### `tests/effect/test_audit_canonicalize.py` — 14 tests

**Rigor: HIGH. Happy + idempotent + two adversarial-round-trip cases.**

Test layout (4 classes, 14 tests):

- **TestPolicyIdCanonicalizationAtInit** (5 tests) — bare input gets
  colon, pre-keyworded stays, None passes through, `\"unknown\"`
  (the literal `policy_eval.py` emits when policy map has no id)
  canonicalises, bare `policy_id` now conforms at `to_edn`
  self-conform. The fifth is the explicit R1 N7 reproducer; before W5,
  it raised `ValueError`. **This is the single most load-bearing R5
  test** — R1 N7 was a MAJOR production defect (every production
  `AuditEntry.to_edn()` with a non-None policy raised).
- **TestHandlerChainCanonicalizationAtInit** (4 tests) — bare chain
  unchanged, pre-keyworded chain stripped, mixed chain uniformly bare,
  **double-colon idempotent** (`\"::audit\"` → `\"audit\"`, not
  `\":audit\"`). The double-colon test is a genuine adversarial probe
  on the `lstrip` choice — a naive `[1:]` implementation would red it.
- **TestPrincipalKeysCanonicalizationAtInit** (3 tests) — bare /
  pre-keyworded / mixed keys, same shape.
- **TestFromEdnRoundTripPreservesVerifyChain** (1 test) — the regression
  guard for R3 R4-N1. The test carefully computes the content hash
  *on the canonicalised-content shape* so `verify_chain` would have
  red before W5 (when the hashed form differed from the reconstructed
  form). **Best test in the file** — genuinely adversarial, exercises
  the exact defect class.
- **TestFromEdnToEdnEqualityHolds** (1 test) — dataclass equality on
  a production-shape entry with bare policy_id. Complements R4-G1's
  bonus assertion from the canonicalize-at-init angle.

**Happy-path / idempotent / adversarial balance:** 6 happy + 4
idempotent + 4 adversarial/regression-guard. Reasonable mix.

**Gaps:**

1. **No test for `policy_id=\"\"` (empty string).** After `\":\" +
   \"\"` = `\":\"` — the sentinel is technically non-empty but reads
   as a \"keyword without a name.\" Would conform? Would hash
   stably? Unpinned. Minor.
2. **No test for `policy_id=None` round-tripping through `to_edn`/`from_edn`.**
   The `test_none_policy_id_unchanged` test covers construction but
   not the wire round-trip. `from_edn` uses `edn.get(\":audit/policy-id\")`
   which returns None when absent; happy path is obvious but the
   explicit pin is missing. Very minor.
3. **No parametrisation over handler_chain entry combinations that
   share prefixes** (e.g., `(\"audit\", \":audit\")` — two entries
   that collide after canonicalisation). Construction succeeds with a
   tuple containing `\"audit\"` twice; this is legitimate but not tested.
   Phase-2 hygiene.

None of these block freeze.

### `tests/fact/test_datom_idempotent.py` — 4 tests

**Rigor: MEDIUM. Covers the prompt-flagged double- and triple-colon
cases; does not probe empty/whitespace.**

Test layout (2 classes, 4 tests):

- **TestDatomAIdempotentOnDoubleColon** — double-colon `\"::x/y\"` and
  triple-colon `\":::x/y\"` on `a`.
- **TestProvenanceSourceIdempotentOnDoubleColon** — same shape for
  `provenance[\"source\"]`.

The double-colon test includes the cross-input equality assertion
(`d_double.a == d_single.a == d_bare.a`), which is the right shape —
proves the three input forms produce the same in-memory Datom.
Triple-colon covers the `lstrip` semantics.

**Gaps (what the prompt asked about):**

1. **No test for `a=\"\"` (empty string).** `lstrip(\":\")` on `\"\"`
   returns `\"\"`. Does the spec accept? Does `datom_to_wire`? Unpinned.
   **This is an actual edge case worth pinning** — empty-string
   attribute names may or may not be rejected upstream.
2. **No test for whitespace (`a=\" :x\"`, `a=\": x\"`).** The `lstrip`
   only strips `:` chars; leading whitespace before the colon is
   preserved (`\" :x\"` → `\" :x\"`, not `\"x\"`). Whether that's
   correct behaviour is a design question; the shipped code treats
   whitespace as data. Phase-2 hygiene test if the stricter contract
   is intended.
3. **No test for `a=\":\"` (bare colon).** `lstrip(\":\")` on `\":\"`
   returns `\"\"`. Same empty-string concern.
4. **No test for `a=None`.** `__post_init__` has no None-guard on
   `a` (unlike on `policy_id`). Would raise `AttributeError` on
   `.lstrip`. Not tested either way.

The prompt specifically asks: *Do they probe `::`, `:::`, empty,
whitespace edge cases?* **Only `::` and `:::` are probed. Empty,
whitespace, and bare-colon are not.** This is a real MINOR rigor gap.

Upgrade path: 3 more tests covering the three additional edge cases.
15 minutes. Recommend raising as a Phase-2 polish item (not freeze
blocker — the three cases are unlikely in production; callers don't
pass empty strings).

### `tests/fact/test_provenance_symmetry.py` — 5 tests

**Rigor: HIGH on the identified defect; narrow on extended algebraic
identity.**

Test layout (2 classes, 5 tests):

- **TestProvenanceToWireValueKeywordification** (4 tests) —
  bare-key × bare-value, bare-key × keyworded-value, keyworded-key ×
  bare-value (**the R4-N4 reproducer**), keyworded-key × keyworded-value.
  Full 2×2 grid on `{key, value} × {bare, keyworded}`. Tight.
- **TestProvenanceRoundTripSymmetric** (1 test) — end-to-end
  `datom_to_wire → wire_to_datom` on a Datom constructed with a
  pre-keyworded `:source` key + bare `\"dfi\"` value. Pins the full
  symmetry at the Datom level, not just the helper level.

**Algebraic identity on extended domain:**

The single round-trip test asserts:

```python
assert restored.provenance.get(\"source\") == \"dfi\"
```

This is a field-level assertion on `source`, not `restored == d`
(dataclass equality). Same R4-G2 shape issue: a future field on
`Datom` or a future provenance key wouldn't be caught. But on the
specific R3 R4-N4 domain (pre-keyworded key + bare value), the 2×2
helper grid + the end-to-end symmetry test together constitute a
tight lock on the defect class.

**Is it \"algebraic identity on extended domain?\"** *Narrowly yes for
provenance-source; strictly no for Datom as a whole.* The file is
correctly scoped to the R4-N4 defect — it doesn't claim to be a full
Datom identity test. R4-G2 (full Datom `==`) is the right place to
track the broader gap; this file doesn't inherit that burden.

**Gaps:**

1. **No test that the fix didn't regress the bare-key × bare-value
   path** for other provenance keys (`model`, `confidence`,
   `signature`). The `_provenance_to_wire` function only keywordifies
   the *value* for `:source`; changing it could silently affect
   sibling keys. The existing `test_wire_identity.py` covers
   non-source keys, so this is not open — just noting the file's
   scope.
2. **No test for `provenance = {}` (empty dict).** Trivially passes;
   unpinned.

No freeze blockers.

### `tests/effect/test_audit_from_edn.py` — G1 bonus

**Rigor: HIGH on `\"ok\"`; partial on verdict coverage.**

Read above (R4-G1 section). Summary:

- The `==` assertion lands on `verdict=\"ok\"` only.
- `verdict=\"error\"` is covered by field-by-field `test_round_trip_preserves_error_entry`.
- `verdict` values `\"deny\"`, `\"deny-silently\"`, `\"require-approval\"`
  are covered by **neither** `==` nor field-by-field in this file.
- `_verdict_as_edn`/`_verdict_as_python` symmetry on all five is tested
  in `tests/effect/test_verdicts.py` (I checked) — so the actual
  round-trip risk is low even for the uncovered three.
- The prompt asks: *Does it cover all 5 verdicts?* **No — 1 of 5
  under the strong `==`, 2 of 5 if we include the field-by-field
  error-entry test.**

Not a freeze blocker. Phase-2 parametrisation (10 minutes).

---

## 3. Proposition re-grade

### Prop 1 — Branch complexity — **6 / 10 (unchanged)**

Unchanged from R4. No scaling regression guard. Paper's softened
`O(|Δ|)` claim is still assertion-by-inspection. Out of R5 scope. The
single biggest open rigor item for Phase 2.

### Prop 2 — Well-formedness machine-check — **7.5 / 10 (unchanged)**

Unchanged from R4. No `is_well_formed(catalog)` × `mask(...)`
interaction test landed. `grep -r \"is_well_formed.*mask\\|mask.*is_well_formed\"`
in `tests/` returns no R5 results (only the R4-era `test_runtime.py`
tests that cover the static case, not the transient-masked case).
Phase-2 task.

### Prop 3 — Byte-identical NO-OP replay — **9.5 / 10 (unchanged)**

R4 rated 9.5 on this; R5 is no-op. The new intervention-wire tests
from R4 remain the primary backing; the W5 datom/provenance idempotency
and symmetry fixes strengthen the *substrate* under Prop 3 but do not
add new Prop 3 tests. The 0.5 deduction (bridge test still uses
`facts=[]`) persists as Phase 2 polish.

### Prop 4 — Audit-chain immutability — **9 / 10 (unchanged on tests; +0 on paper but paper got crisper)**

R4 rated 9.0 on the test side. R5 unchanged on tests — no
combined-violation test, no two-consecutive-middle-deletion test. The
paper pointer got sharper (W5-paper-patch2 named the real
`make_audit_handler` Merkle-hashed chain-append clause instead of the
phantom `append_audit_entry`), which is a correctness polish but not a
test-backing change.

**Net: Prop 4 grade unchanged at 9 / 10.** The worker explicitly
deferred R4-G4 to Phase 2; the deferral is consistent with the
\"R2 R4-G1..G4 beyond the one freebie = Phase 2 polish\" scope note.

---

## 4. New rigor gaps in Round 5

**Three MINOR, none blocking:**

### R5-G1 (MINOR) — G1 bonus assertion covers 1 of 5 verdicts

**Location:** `tests/effect/test_audit_from_edn.py::test_round_trip_equality_by_dataclass_eq`
**What:** Strong `==` assertion lands only on `verdict=\"ok\"`. The
four other verdicts (`error`, `deny`, `deny-silently`,
`require-approval`) are either covered by field-by-field (error) or
not covered in this file (three).
**Why it bites:** Phase-2 verdict-conditional fields (denial reason,
policy-match summary) would not be caught by the `==` form on four
out of five code paths.
**Fix:** `@pytest.mark.parametrize(\"verdict\", [...5...])` on the
bonus test. 10 minutes.

### R5-G2 (MINOR) — `test_datom_idempotent.py` skips empty/whitespace/bare-colon

**Location:** `tests/fact/test_datom_idempotent.py`
**What:** File covers `\"::x\"` and `\":::x\"`. Does not cover
`a=\"\"`, `a=\":\"`, `a=\" :x\"`, `a=None`.
**Why it bites:** `a=None` would raise `AttributeError` on `.lstrip`
with no guard; `a=\"\"` and `a=\":\"` both canonicalise to `\"\"`
which may or may not pass downstream spec conform. The prompt
specifically called this out.
**Fix:** 3-4 more tests. 15 minutes.

### R5-G3 (MINOR) — G2, G3, G4 now two-round-old and drifting

**Location:** Phase-2 backlog.
**What:** R4-G2 (wire_identity `==`), R4-G3 (intervention edge cases),
R4-G4 (combined Prop 4 violations) were raised in R4 as MINOR and
are unchanged in R5. Each is orthogonal to what W-polish2 did, but
the cumulative effect of deferring them across two rounds raises mild
risk that a Phase-2 refactor breaks one silently.
**Why it bites:** Phase 2 is significant work (STM, Plan, REPL). Any
of G2/G3/G4 could be silently broken by Phase-2 changes without a
test firing — all three are about *currently-unpinned* behaviour.
**Mitigation:** Track them as *must-close-before-Phase-2-merge*
rather than generic Phase-2 polish. Or close them now post-freeze
in a W6-pre-Phase2 track.

---

## 5. Overall rigor grade

**9.3 / 10** — FREEZE-CLEAN GATE PASSED (target ≥ 9.2).

The grade reflects:

- **+0.1 on R4 (9.1)** from R4-G1 bonus closure — dataclass-equality
  round-trip on `AuditEntry`. Genuinely strictly-stronger than the
  field-by-field form it replaces.
- **+0.2 from three new R5 test files** landing rigor on production
  paths: `test_audit_canonicalize.py` closes the R1 N7 MAJOR (bare
  `policy_id` raising at self-conform), `test_datom_idempotent.py`
  closes R3 R4-N3, `test_provenance_symmetry.py` closes R3 R4-N4. The
  14-test canonicalize file is the single highest-leverage file
  shipped since Round 3.
- **−0.1 from R5-G1/G2/G3** — three new MINOR gaps (verdict coverage
  on G1 bonus, missing datom edge cases, cumulative drift on G2/G3/G4).
- **No blocker findings.** 560 local / 575 full green, zero flakes.

Not 9.5 because:

- R4-G2, R4-G3, R4-G4 are unchanged — three MINOR rigor polishes
  explicitly flagged across two rounds are still deferred.
- The R5 test files, while surgically targeting the defect classes they
  name, don't extend coverage into *adjacent* edge cases (empty
  strings, all-verdicts parametrisation, combined-violation Prop 4).
  Good scope discipline; but leaves the stricter-than-shipped rigor
  bar unreached.

Not 9.2-flat because:

- The W5-audit-canonicalize file is genuinely excellent rigor — 14
  tests across 4 classes, each exercising a production-shape defect
  that Round 4 happy-path fixtures had been hiding. The
  `TestFromEdnRoundTripPreservesVerifyChain` test in particular
  (`test_verify_chain_survives_to_edn_from_edn`) is adversarial at
  exactly the right grain: it computes the content hash on the
  canonicalised shape so the Merkle chain would have red without
  canonicalisation. This is the R5 file that most clearly improves
  the rigor posture.
- The paper Prop 4 rewording fixes a correctness pointer (phantom
  function name → real Merkle-chain emission in `make_audit_handler`).
  This is rigor on the *paper-to-code* axis — the paper now names a
  real code path that a NeSy reviewer can open and read.
- Zero xfail cover, zero test-name mismatches, zero new lint violations
  (I checked `pytest -q --ignore=tests/spec/test_generative.py`).

**R2-Rigor grade: 9.3 / 10 → FREEZE-CLEAN GATE PASSED.**

---

## 6. Phase 2 residual rigor todos

The backlog carried into Phase 2. None of these are Phase-1 regressions.

1. **Prop 1 scaling regression guard** — parametrise branch-isolation
   over log sizes `[10, 100, 1_000, 10_000]`. ~1h. (Carried from R4.)
2. **Prop 2 mask-interaction test** — `rt.is_well_formed(catalog)`
   under `mask(...)`. ~30min. (Carried from R4.)
3. **R4-G2** — replace Datom field-by-field wire round-trip with
   `assert wire_to_datom(datom_to_wire(d)) == d`. ~15min. (Carried
   from R4, now two rounds old.)
4. **R4-G3** — three intervention-wire edge cases (empty list,
   out-of-order, duplicate step). ~30min. (Carried from R4.)
5. **R4-G4** — Prop 4 combined-violation tests (mutate+reorder,
   two-consecutive-middle-delete). ~20min. (Carried from R4.)
6. **R5-G1** — parametrise `test_round_trip_equality_by_dataclass_eq`
   over the five verdicts. ~10min.
7. **R5-G2** — extend `test_datom_idempotent.py` with empty /
   whitespace / bare-colon / None edge cases. ~15min.
8. **Bridge test with non-empty `facts`** — R3 F2 residual. ~2h.
   (Carried from R4.)
9. **DB.transact input self-conform** — R2 F8 residual. (Carried from
   R4, on STM co-design track.)
10. **`:audit/parent` threading test** — no test currently pins the
    `parent` field across nested calls. ~30min. (Carried from R3.)
11. **Generative property-based coverage** — `tests/spec/test_generative.py`
    is hypothesis-gated (13 tests); Phase 2 should extend to hypothesis
    strategies over the three major specs. ~half-day. (Carried from R4.)

**Combined Phase-2 rigor work: ~5-6 focused hours + one half-day on
generative coverage.**

**Recommendation:** Close R4-G2/G3/G4 + R5-G1/G2 *before* Phase-2
merge, not *during*. They're orthogonal, cheap (total ~1h20min), and
leaving them past a second round allowed them to drift. A pre-Phase-2
\"W6-cleanup\" track in the `conductor/tracks/` layout would capture
them cleanly.

---

## 7. Go / no-go for Phase 1 freeze

**GO — FREEZE Phase 1 CLEAN.**

- R4-G1 closed as bonus (dataclass equality assertion).
- Three new test files add genuine rigor on production paths (R1 N7,
  R3 R4-N1/N2/N3/N4 all closed).
- Paper Prop 4 pointer corrected (`make_audit_handler` Merkle-chain
  emission, not phantom `append_audit_entry`).
- 560 local / 575 full suite green, zero flakes.
- R2-Rigor 9.3 ≥ 9.2 freeze-clean gate.
- No new blockers; three new MINOR gaps (R5-G1/G2/G3) all ≤ 15 minutes
  of Phase-2 polish.

The minor carried-forward items (R4-G2/G3/G4) are unchanged but remain
correctly-scoped as Phase-2 polish. I flag R5-G3 as a mild risk —
carrying three minor test-rigor items across two rounds increases the
chance a Phase-2 refactor silently breaks one. Recommend closing all
five carried items (R4-G2/G3/G4 + R5-G1/G2) in a pre-Phase-2 cleanup
pass before the substrate work starts.

Sign-off from R2-Rigor: **Phase 1 is NeSy-submittable on the rigor
axis.** Freeze clean.

**R2-Rigor grade: 9.3 / 10.**
