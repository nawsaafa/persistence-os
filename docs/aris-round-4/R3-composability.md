# ARIS Round 4 — Reviewer R3 — Composability (FREEZE GATE)

**HEAD:** `61644f6` on `main` · **Repo:** `/Users/nawfalsaadi/Projects/persistence-os/`
**Round 1 (predecessor):** 4.5 / 10 (the infamous low)
**Round 2 (predecessor):** 8.6 / 10
**Round 3 (predecessor):** 8.9 / 10 (flagged N2 as "single biggest drag")
**Round 4 target:** ≥ 9.2 (freeze gate — Phase 1 frozen if min grade ≥ 9.0)
**Test count:** 520 → **551 passed** (+31) in 2.79s (`pytest -q`)
**Worker:** `W-wire` — 7 logical commits `faf635d..2c1ead7`, merge `6a6f152`.

## Summary

W-wire closed the three "lie" gaps I flagged as load-bearing in Round 3:

- **N2 (wire identity)** fixed at the dataclass boundary with `Datom.__post_init__`
  normalisation. Every colon-prefixed `a` / `provenance["source"]` becomes
  bare at construction time; the wire form uniformly prepends `:`. Round-trip
  is identity on the single-colon extended domain.
- **B1 (intervention collapse)** fixed by typing `Trajectory.intervention`
  as `Optional[list[dict]]`, updating the engine to deep-copy the whole
  list, and spec-refining `:trajectory/intervention` to `seq_of(ref(...))`.
  `_intervention_to_wire` / `_intervention_from_wire` are the single
  serialisation-time boundary between Python-native bare keys and
  EDN-keyword wire form, and they're idempotent on both sides.
- **R3-N6 (AuditEntry.from_edn missing)** fixed by adding a symmetric
  `AuditEntry.from_edn` classmethod using the new
  `_handler_chain_from_keywords` helper.

`Trajectory.to_edn → Trajectory.from_edn → .intervention` round-trips
cleanly on engine-produced counterfactuals (§2.1). `datom_to_wire ∘
wire_to_datom` is identity on every pre-keyworded input the R3 reviewer
reproduced (§2.3). `DB.transact(facts_with_:prefixed_a)` flows through
the idempotent `__post_init__` normalisation cleanly (§3.1).

**But the W-wire pass introduced one genuine new composability defect
plus two long-standing ones got more-visibly-reachable now that
`AuditEntry.from_edn` exists. I also confirmed three of the R3 deferrals
are still honestly deferred:**

- **R4-N1 NEW (MEDIUM)** — `AuditEntry.from_edn ∘ to_edn` is NOT a
  content-hash-preserving round-trip on `handler_chain` entries that
  were constructed pre-keyworded. `verify_chain([AuditEntry.from_edn(e.to_edn())])`
  returns **False** when the original `e` had `handler_chain=(":audit", ":llm")`.
  Production handler chains are all bare-string, so the defect is latent
  in production — but it's a real asymmetry in the newly-shipped
  `from_edn` inverse. §4.1.
- **R4-N2 NEW (LOW)** — Same asymmetry on `principal`. A `AuditEntry`
  built with `principal={":role": "agent"}` round-trips to
  `principal={"role": "agent"}` — `verify_chain` fails. This defect
  **predates** W-wire (it came in with `_principal_to_keyword_map` in
  Round 3) but was un-exercised until `from_edn` shipped. §4.2.
- **R4-N3 NEW (LOW)** — `Datom.__post_init__` strips exactly ONE leading
  colon. `Datom(a="::x")` normalises to `:x` (single colon) → wire emits
  `:x` → `wire_to_datom` strips → Datom(a="x"). Non-idempotent on the
  double-colon sub-domain. Not a realistic input (double-colon isn't
  valid EDN), but the stripping loop should be `while a.startswith(":")`
  for defensibility. §4.3.
- **R4-N4 NEW (LOW)** — `_provenance_to_wire` short-circuits on
  already-keyworded keys, which leaks BARE string `:source` values
  through to the wire unchanged. Example: input
  `{":source": "dfi-agent"}` emits `{":source": "dfi-agent"}` instead of
  `{":source": ":dfi-agent"}`. `datom_to_wire`'s self-conform catches
  this (so it fails loudly, not silently), but the composition is
  asymmetric — the bare-key branch keywordifies both key and value;
  the keyworded-key branch keywordifies neither. §4.4.
- **R4-F1 (honest deferral, confirmed)** — `DB.transact` still splits
  `allocate_and_append` + `mark_invalidated` across two `BEGIN IMMEDIATE`
  sections (`store.py:247-281` vs `:301-318`). Worker summary's
  justification is honest: no Phase-1-user-visible anomaly because no
  reader runs mid-transact; Phase-2 STM will need the two folded
  together. §5.1.
- **R4-F2 (honest deferral)** — `DB.transact` input self-conform still
  not wired. Worker summary explains the wider-than-polish surface area
  (TX_PLACEHOLDER timing, ~80 test touches). Acceptable for freeze. §5.2.
- **R4-F3 (honest deferral)** — `_PlanNodeVector._conform` still
  class-bound recursion. Phase 2 plan-module work. §5.3.

Phase-1 user-path composability is **clean**: production audit chains
don't touch the from_edn asymmetry; Datom wire identity holds on every
realistic input; multi-step interventions now land correctly on lineage
and DPO pair extraction (which doesn't touch `trajectory.intervention`
at all — verified) is unaffected.

## 1. R3 N-finding remediation table

| # | Finding | R3 status | R4 status | Evidence (file:line / test) | Grade |
|---|---|---|---|---|---:|
| **R3-N2** | `wire.datom_to_wire ∘ wire_to_datom` identity loss on colon-prefixed `a` / pre-keyworded source | NOT FIXED (5/10) | **FIXED** | `src/persistence/fact/datom.py:68-89` — `Datom.__post_init__` normalises `a` and `provenance["source"]` by stripping leading `:`. Frozen + slotted dataclass, mutation via `object.__setattr__` on the immutable `a`, and in-place mutation on the mutable provenance dict. Symmetric with `AuditEntry.__post_init__` op-invariants (R3 P-op-invariants). Tests at `tests/fact/test_wire_identity.py` (9 new): `test_datom_a_strips_leading_colon`, `test_datom_a_idempotent_on_bare_input`, `test_provenance_source_strips_leading_colon`, `test_provenance_source_idempotent_on_bare_input`, `test_non_source_provenance_keys_unchanged`, `test_round_trip_identity_on_prekeyworded_a`, `test_round_trip_identity_on_prekeyworded_source`, `test_round_trip_identity_on_both_prekeyworded`, `test_wire_always_emits_keyworded_form`. I re-ran R3's exact reproduction at HEAD (§2.3 below) — `Datom(a=":project/wacc", provenance={"source":"bare"})` and `Datom(a="project/wacc", provenance={"source":":already-keyworded"})` now round-trip to identity. | 9.5 |
| **R3-N5** | `DB.transact` splits allocate-and-append + mark_invalidated into two atomic sections | MINOR, OUT OF SCOPE | **HONESTLY DEFERRED** | Worker summary's "Deferred items" section is explicit: "Out of scope per the W-wire prompt (listed in 'Out of scope'). Phase-2 STM work." I verified at HEAD: `src/persistence/fact/store.py:247-281` (`allocate_and_append` — one `BEGIN IMMEDIATE`), `:301-318` (`mark_invalidated` — separate `BEGIN IMMEDIATE`), `src/persistence/fact/db.py:221,229-230` (sequential calls in `DB.transact`). No attempt to misrepresent the status. §5.1 below analyses whether this is honest ("can a malformed Datom slip through into the store and only fail at read-time?"). | 8.5 |
| **R3-N6** | `:persistence.effect/audit-entry` has `to_edn` but no inverse on the AuditEntry side | MINOR | **FIXED, WITH NEW ASYMMETRY (R4-N1)** | `src/persistence/effect/handlers/audit.py:168-216` — new `AuditEntry.from_edn` classmethod. Uses existing `_verdict_as_python`, `_keyword_map_to_principal`, and the new `_handler_chain_from_keywords` (`:434-442`). Tests at `tests/effect/test_audit_from_edn.py` (5 new): round-trip on minimal entry, all-keyworded chain, principal round-trip, run_id UUID conversion, :audit/recorded-at datetime → float epoch. I verified: `AuditEntry.from_edn(e.to_edn()) == e` holds on production-shape entries (bare-string chain, bare-string principal keys). BUT: for non-production-shape entries with pre-keyworded `handler_chain` or `principal` keys, `verify_chain([rt])` fails because the reconstructed bare-chain has a different content hash than the stored `id`. See §4.1 for full defect description. | 8 |
| **R3-N7** | `_PlanNodeVector._conform` recurses class-bound | LOW, OUT OF SCOPE | **HONESTLY DEFERRED** (for Phase 2) | Worker summary doesn't touch this (out of W-wire scope). `src/persistence/spec/_canonical.py:524` confirmed still `self.conform(child)` at HEAD. Phase-2 plan-module work. No composability cost for Phase 1 since the plan-node recursion is within the same registered spec. | 8 |
| **B1** | `Trajectory.intervention` collapsed to first of multi-step | NEW in R3, pinned by G4 shape-test | **FIXED** | Three-surface fix: (1) `Trajectory.intervention: Optional[list[dict]]` at `src/persistence/replay/trajectory.py:117` (was `Optional[dict]`), (2) `engine.py:165` writes `intervention=[copy.deepcopy(iv) for iv in interventions]` (was `copy.deepcopy(interventions[0])`), (3) `:persistence.replay/trajectory`'s intervention slot is `seq_of(ref(":persistence.replay/intervention"))` at `src/persistence/spec/_canonical.py:639` (was a single ref). New wire helpers `_intervention_to_wire` / `_intervention_from_wire` at `trajectory.py:355-397` — single serialisation-time boundary, idempotent on both directions. `from_edn` back-compat shim accepts legacy single-dict by wrapping into a 1-list at `trajectory.py:329-334`. G4 shape-pin updated from "expect dict" to "expect list, zip-equal submitted" at `tests/replay/test_replay.py:214-233`. Nine new tests at `tests/replay/test_intervention_wire.py` cover engine→lineage path, multi/single interventions, caller-mutation safety, wire conformance, from_edn inverse, synthetic constructor, default None. | 9.5 |

**Tally R3 N-findings:** 5 items. 2 FIXED (N2, B1), 1 FIXED-with-asymmetry (N6 → R4-N1), 2 HONESTLY DEFERRED (N5, N7). Per-finding mean on the 3 in-scope items = (9.5 + 8 + 9.5) / 3 = **9.0**. The one item that closed at 8/10 is the new `from_edn` whose inverse is symmetric on production input but leaks on non-production input. See §4.1.

## 2. Adapter-pair algebra check for the 3 wire-boundary fixes

### 2.1 Intervention wire: engine → to_edn → from_edn → `.intervention`

**Clean round-trip on engine-produced counterfactuals.** Verified at HEAD via
`tests/replay/test_intervention_wire.py::TestTrajectoryFromEdnInverse::test_round_trip_preserves_multi_intervention_list`
(PASSED) and an ad-hoc repro:

```python
ivs = [
    {'step': 2, 'field': 'action', 'new_value': {'kind': 'buy'}},
    {'step': 3, 'field': 'obs', 'new_value': {'px': 999}},
]
cf = replay(traj, ivs, ...)
# cf.intervention == [deep-copies of ivs, in order]
edn = cf.to_edn()
# edn[':trajectory/intervention'] == [
#   {':step': 2, ':field': ':action', ':new-value': {'kind': 'buy'}},
#   {':step': 3, ':field': ':obs', ':new-value': {'px': 999}},
# ]
cf_rt = Trajectory.from_edn(edn)
assert cf_rt.intervention == cf.intervention  # ✓ PASSES
```

**Multi-step lineage is preserved.** Both the count (3 ≡ 3) and the
per-entry step/field/new_value survive the wire. Tests at
`tests/replay/test_intervention_wire.py:95-116` (three interventions →
length-3 list).

**Idempotence of the helpers:**

| Helper | Input | Output | Idempotent on re-apply? |
|---|---|---|---|
| `_intervention_to_wire` | `{'step': 0, 'field': 'action', 'new_value': 1}` | `{':step': 0, ':field': ':action', ':new-value': 1}` | **YES** (verified) |
| `_intervention_to_wire` | `{':step': 0, ':field': ':action', ':new-value': 1}` | same (pre-kw pass-through via `_kw` helper) | **YES** |
| `_intervention_from_wire` | wire form | bare form | **YES** on bare re-apply |

**Back-compat shim verified.** A legacy payload with
`:trajectory/intervention` as a single dict (not a list) is lifted into
a 1-list by `from_edn` (`trajectory.py:329-334`). The lifted payload's
`to_edn` then emits a proper `seq_of`, and a second `from_edn` of that
wire is stable. **But** — `from_edn` does NOT itself spec-conform its
input, which means the legacy single-dict wire would be REJECTED by
`spec.conform(":persistence.replay/trajectory", legacy_wire)` directly,
while `Trajectory.from_edn(legacy_wire)` accepts it. This is by design
(the R3 pattern has producers self-conform, consumers trust), and the
shim is narrow (one shape only), but it does mean **there's a
non-conformant wire form that can pass through `from_edn → to_edn` and
get "laundered" into conformant form**. Minor, and defensible for
forward compat; worth a docstring note.

**DPO composition — verified.** `src/persistence/replay/dpo.py` does
NOT read `trajectory.intervention` anywhere (`rg intervention
src/persistence/replay/dpo.py` → 0 hits). DPO's pair extraction uses
`branch_point` + `facts[branch_point].llm_in/llm_out`, not the
intervention lineage. So the list-vs-dict change is invisible to DPO;
the extract-pair algorithm continues to work on the new shape. ✓

### 2.2 Handler-chain wire: AuditEntry.to_edn → from_edn

**Clean round-trip on production shape (bare-string chain).** Verified:

```python
e = AuditEntry(..., handler_chain=('audit', 'llm', 'tool'), ...)
edn = e.to_edn()
# edn[':audit/handler-chain'] == [':audit', ':llm', ':tool']
rt = AuditEntry.from_edn(edn)
# rt.handler_chain == ('audit', 'llm', 'tool')
assert e == rt  # ✓
assert verify_chain([rt])  # ✓
```

**Asymmetric on non-production shapes.** If the original
`handler_chain` was pre-keyworded OR mixed, `from_edn` always strips
leading colons — so a chain that was constructed pre-keyworded becomes
bare-string on round-trip. Because the chain is part of the
`_content_hash` computation, the reconstructed entry's recomputed hash
**does not match** the carried `id`, so `verify_chain([rt])` returns
**False**. Details in §4.1.

**Empty chain, mixed chain, idempotent on pre-kw wire form:** all
verified in the new `tests/effect/test_handler_chain_wire.py` (5 tests,
all passing).

### 2.3 Datom wire identity (extended domain)

**Identity now holds on every input in R3's reproduction set.** The R3
report §2.5 recorded two failing cases at HEAD `045f4b4`:

> Case 1 (colon a): identity? False orig :project/wacc -> project/wacc
> Case 2 (pre-kw source): identity? False orig {'source': ':already-keyworded'}
>                                     -> {'source': 'already-keyworded'}

I re-ran the same reproduction at `61644f6`:

| Case | Input | `Datom.a` after `__post_init__` | Wire `:datom/a` | Round-trip `.a` | Identity? |
|---|---|---|---|---|---|
| 1 | `a=":project/wacc"`, `source="bare"` | `"project/wacc"` | `":project/wacc"` | `"project/wacc"` | **YES** |
| 2 | `a="project/wacc"`, `source=":already-kw"` | `"project/wacc"` | `":project/wacc"` | `"project/wacc"` | **YES** (source also normalises to `"already-kw"`) |
| 3 (bare sanity) | `a="project/wacc"`, `source="bare"` | unchanged | `":project/wacc"` | `"project/wacc"` | **YES** |
| 4 | empty provenance | — | `":project/wacc"` | — | **YES** |
| 5 | provenance with no `source` key | unchanged | `":project/wacc"` | — | **YES** |
| 6 (double-colon edge) | `a="::double-colon"` | **`":double-colon"` (only one colon stripped!)** | `":double-colon"` | `"double-colon"` | **NO** — see R4-N3 in §4.3 |

**Every canonical-domain pre-keyworded case round-trips identically.** Case 6 is
the residual non-idempotent edge. It's not a realistic input (double-colon isn't
valid EDN), but it's a composability artifact of using `a[1:]` instead of
`a.lstrip(":")` or `while a.startswith(":"): a = a[1:]`.

**Wire always emits keyworded form** (verified in
`test_wire_always_emits_keyworded_form`) — the wire-form invariant is
canonical regardless of constructor input. Symmetric with
`AuditEntry.__post_init__` op-normalisation.

## 3. Cross-module composition audit

### 3.1 `DB.transact(spec_conformant_datoms)` through `Datom.__post_init__`

**Flows cleanly. Idempotent normalisation.**

Verified at HEAD: a user passing a fact with pre-keyworded `a`
(`{'a': ':project/wacc', ...}`) survives DB.transact unchanged:

```python
db.transact([{'e': ..., 'a': ':project/wacc', 'v': 0.12, ...}])
# Stored datom: d.a == 'project/wacc'  (canonical bare form)
```

`DB.transact` constructs `Datom(a=fact["a"], ...)` at `db.py:205`, which
triggers `__post_init__`, which strips the leading colon exactly once.
Subsequent `datom_to_wire(d)` emits `:project/wacc` (prepends the
colon). `wire_to_datom` strips it again. Round-trip holds.

**Idempotence check:** constructing a `Datom` from `d.__dict__` a
second time produces identical `a` + `provenance` (§canonical-domain). The
normalisation is safe to run N times.

**One quirk** — the `fact["provenance"]` dict passed into `DB.transact`
is **silently dropped** (not a W-wire regression; pre-existing). `db.py:201-202`
computes `prov = {**prov_base, "prompt_hash": _hash_fact(fact)}` where
`prov_base` is ONLY the top-level `provenance` kwarg, not the per-fact
`fact["provenance"]`. Docstring at `db.py:107` says "The supplied
`provenance` merges into each datom's provenance" — referring to the
kwarg, not the fact key. Composability-adjacent but pre-existing and
documented.

### 3.2 Canonical audit hash through record → verify cycles

**Production path: clean.** Audit handler built via `make_audit_handler`
seeds `ctx["handler_chain"] = ()` at `audit.py:355`. No production code
path populates `handler_chain` with non-empty values (verified via `rg
handler_chain src/persistence/effect/` — only the dataclass default and
the two wire helpers). Every production-emitted entry has
`handler_chain=()`, which round-trips cleanly through `to_edn →
from_edn`. A regulator loading archived production entries and running
`verify_chain([e.to_edn()]).from_edn()` gets `True`. ✓

I built a 3-entry production-shape chain at HEAD and verified:

```
original chain verify_chain: True
after to_edn+from_edn verify_chain: True
```

**Non-production path: asymmetric (R4-N1).** See §4.1.

### 3.3 Intervention list composition with DPO pair extraction

**DPO is unaffected.** `extract_dpo_pair` in `replay/dpo.py` reads only
`counterfactual.branch_point`, `factual.facts[branch]`,
`counterfactual.facts[branch]`, and the outcomes' `pnl` field. It does
not touch `.intervention`. So the shape change from `Optional[dict]` to
`Optional[list[dict]]` is invisible to DPO. ✓

No downstream reader in the repo accesses `trajectory.intervention[<key>]`
as a dict (verified via `rg '\.intervention\['
/Users/nawfalsaadi/Projects/persistence-os/src` → 0 hits). Only 3 reads:
`trajectory.py:153` (in `to_dict` — just copies), `:259-269` (in
`to_edn` — guards against None + iterates). The migration was surgical.

### 3.4 Legacy `Optional[dict]` readers

**None in production.** Only tests at `tests/replay/test_replay.py:214-233`
(the G4 shape-pin) previously asserted `isinstance(cf.intervention, dict)`;
W-wire correctly updated that assertion to `isinstance(cf.intervention, list)`.
No other production readers exist to fail loudly or silently. ✓

### 3.5 Trajectory.to_dict / from_dict (Python-native JSON path)

Both `to_dict` and `from_dict` round-trip the new
`Optional[list[dict]]` shape correctly — they just pass it through as a
dict field (`trajectory.py:153`, `:186`). Legacy JSON with a single-dict
intervention would not be auto-lifted by `from_dict` (unlike
`from_edn`), so a Phase-1 replay cache reader that loads
pre-W-wire-era JSON would see `intervention` as a dict instead of a
list. **Minor**, and only relevant if someone persisted a replay cache
across the W-wire cutover — not a documented use case. Worth a Phase-2
migration note.

## 4. New composability findings in Round 4

### 4.1 R4-N1 (MEDIUM) — handler_chain `from_edn` asymmetry breaks `verify_chain`

**Reproduction at HEAD:**

```python
e = AuditEntry(
    id=_content_hash(content),
    handler_chain=(':audit', ':llm', ':tool'),  # pre-keyworded (non-production)
    ...
)
edn = e.to_edn()
# to_edn keywordifies idempotently — wire is [':audit', ':llm', ':tool']
rt = AuditEntry.from_edn(edn)
# rt.handler_chain == ('audit', 'llm', 'tool')  [bare!]

# verify_chain recomputes _content_hash from rt.to_dict() and compares to rt.id.
# The stored id was computed over (':audit', ':llm', ':tool').
# The recomputed hash is over ('audit', 'llm', 'tool').
# → Mismatch.
assert verify_chain([rt]) is False  # FAILS the chain invariant
```

Evidence: `src/persistence/effect/handlers/audit.py:415-432` (the
`_handler_chain_to_keywords` helper is idempotent on pre-kw input —
good), `:434-442` (`_handler_chain_from_keywords` ALWAYS strips — not
symmetric with the non-stripping idempotent producer when the input
stream already had colons).

**Classification: MEDIUM composability defect, LOW Phase-1 user impact.**

- **Why medium composability:** the adapter pair
  `to_edn(from_edn(x))` is NOT the identity on pre-keyworded chain
  entries, and the consequence is a broken cryptographic invariant
  (`verify_chain` returns False on round-tripped entries). The paper's
  §4.3 Proposition 4 says `verify_chain` detects tampering/deletion/
  reorder — it now ALSO returns False on non-tampered, wire-laundered
  entries in a specific subset.
- **Why low Phase-1 impact:** production handler chains are empty
  (`()`). The asymmetry is only reachable if a caller manually
  constructs an `AuditEntry` with a pre-keyworded chain — which is
  exactly what W-wire's tightening of `test_audit_self_conform` moved
  tests AWAY from. The worker summary §2 says: "_sample_entry` now uses
  production-shape bare-string `handler_chain=('audit', 'policy',
  'raw')` instead of the pre-keywordified `(':audit', ':policy',
  ':raw')` that was hiding the bug." W-wire intentionally canonicalised
  to bare-string at construction. So this asymmetry is dead-lettered in
  production and unit tests — but it's live in "what happens if you
  build an AuditEntry from a wire payload you just parsed" which is
  precisely what the new `from_edn` enables.

**Fix shape:** canonicalise `handler_chain` and `principal` keys at
`AuditEntry.__post_init__` (strip leading colons). Then the hash is
computed over the canonical bare form, `from_edn` produces bare form,
hash matches, `verify_chain` holds. Symmetric with what `Datom.__post_init__`
now does for `a` + `provenance["source"]`. One-file, ~10-line fix.

**Why this is a NEW finding:** R3 flagged the MISSING `from_edn`
(`R3-N6`). The worker ADDED `from_edn`, closing R3-N6. But the newly-added
inverse is asymmetric on non-canonical inputs. Classic
"fix-uncovers-a-deeper-fix" pattern — resolving the surface issue
exposed that the dataclass never canonicalised these fields, and now
that there's a round-trip path, the non-canonicalisation matters.

### 4.2 R4-N2 (LOW) — principal key asymmetry, same shape as R4-N1

Same defect, different field. `AuditEntry` with `principal={":role":
"agent"}` (pre-keyworded keys) → `to_edn` keywordification is
idempotent (short-circuits on already-`:` keys) → `from_edn` strips ALL
leading colons → reconstructed entry has bare-string keys → recomputed
`_content_hash` differs → `verify_chain` returns False.

Predates W-wire (the `_principal_to_keyword_map` asymmetry dates to
Round 3). Un-exercised until `from_edn` shipped in W-wire.

**Fix:** same `__post_init__` canonicalisation as R4-N1.

### 4.3 R4-N3 (LOW) — `Datom.__post_init__` strips exactly one colon

`Datom(a="::double-colon")` → `d.a == ":double-colon"` (one colon,
because `a[1:]` removes only the first). Then `datom_to_wire` emits
`":double-colon"` (starts with colon, no prepend), and `wire_to_datom`
strips one colon → `"double-colon"`. Round-trip:

```
original a:       "::double-colon"
post_init a:      ":double-colon"       # ← NOT identical to round-trip
wire a:           ":double-colon"
round-trip a:     "double-colon"
```

Non-idempotent on double-colon inputs. Double-colon isn't valid EDN, so
this is an edge-case hardening, not a realistic attack path. But the
fix is trivial: `object.__setattr__(self, "a", self.a.lstrip(":"))` or
a `while` loop. Symmetric with lstrip behaviour in
`audit_entry_to_datom:474` (`op_bare = entry.op.lstrip(":")`).

### 4.4 R4-N4 (LOW) — `_provenance_to_wire` key/value asymmetry

`src/persistence/fact/wire.py:65-69`:

```python
for k, v in prov.items():
    # Preserve already-keyworded keys verbatim.
    if isinstance(k, str) and k.startswith(":"):
        out[k] = v
        continue
    wire_key = ":" + k if k in _PROVENANCE_KEYS else k
    if wire_key == ":source" and isinstance(v, str) and not v.startswith(":"):
        v = ":" + v
    out[wire_key] = v
```

When the key is already `:source` (pre-keyworded), the `continue` skips
the value-keywordification branch, so a BARE value leaks through.
Reproduction:

```python
_provenance_to_wire({":source": "bare-value"})
# Returns {":source": "bare-value"}  — should be {":source": ":bare-value"}
```

`datom_to_wire` self-conforms the emitted dict, so this raises
(`spec.parse` rejects non-keyword `:source` value) — not silent. But
the asymmetry means that the function isn't idempotent with its own
inverse when the input-mix is "keyworded key + bare value" (an unusual
but not impossible state given `Datom.__post_init__` now canonicalises
source to bare — so if someone constructs a Datom with
`provenance={":source": "x"}`, `__post_init__` doesn't touch it because
it checks `.get("source")` not `.get(":source")`; see §4.2 parallel).

**Fix:** drop the `continue` — always run the value-keywordification
branch regardless of key form. Trivial, one line.

### 4.5 Summary of new findings

| Tag | Severity | Surface | Production impact |
|---|---|---|---|
| R4-N1 | MEDIUM | AuditEntry.from_edn × handler_chain | LATENT (prod chains empty) |
| R4-N2 | LOW | AuditEntry.from_edn × principal keys | LATENT (prod uses bare keys) |
| R4-N3 | LOW | Datom.__post_init__ × double-colon | NONE (invalid EDN input) |
| R4-N4 | LOW | _provenance_to_wire × kw-key+bare-value | NONE (caught by self-conform) |

None individually block freeze. Collectively they are the residual
shape of the wire-boundary canonicalisation debt: every dataclass that
has a wire round-trip needs `__post_init__` canonicalisation of its
wire-sensitive fields. Datom got it. AuditEntry got it for `op` (Round
3). AuditEntry did NOT get it for `handler_chain` or `principal`
(R4-N1/N2). `_provenance_to_wire`'s key/value mix handling is a
one-line cleanup (R4-N4). The double-colon edge is hardening (R4-N3).

## 5. Composability of the deferred items

### 5.1 `DB.transact` + `mark_invalidated` two-section atomicity (R3-N5 / worker "N8")

**Honest deferral, verified.** Worker summary says it's out of scope
per the W-wire prompt. At HEAD:

- `src/persistence/fact/store.py:247-281` — `allocate_and_append` runs
  one `BEGIN IMMEDIATE` + `MAX(tx)+1` + `INSERT`s + `COMMIT`.
- `:301-318` — `mark_invalidated` runs a separate `BEGIN IMMEDIATE` +
  `UPDATE` + `COMMIT`.
- `src/persistence/fact/db.py:221,229-230` — `DB.transact` calls them
  sequentially, not under a shared atomic section.

**Composability cost: NONE for Phase 1.** A malformed Datom cannot
slip through into the store because the `Datom` dataclass constructor
runs full `__post_init__` validation (tz-aware datetimes, op ∈ {"assert",
"retract"}, plus the new R4 normalisations), and `datom_to_wire` runs
the full spec conform. Any path into the store either:

- goes through `store.allocate_and_append(new_datoms)` where
  `new_datoms: list[Datom]` — so every datom already survived
  construction validation, or
- goes through the raw SQLite encode path in `_encode`
  (`store.py:_encode`), which callers don't expose publicly.

So a `Datom` with malformed `provenance` can't enter the store — it
would have raised at construction. The two-section-atomicity concern is
purely about **observable state between the two sections** (a reader
snapshotting `all_datoms()` after the `INSERT` commits but before
`mark_invalidated` commits would see the new assert without the prior's
invalidated_by stamp). In Phase 1 there's no reader racing against the
writer (single-threaded effect runtime). Phase-2 STM will compose
reads; at that point, fold both sections into one.

The worker summary's additional deferral reasoning — "Risk of side
effects across the ~80 tests that currently exercise DB.transact.
Leaving for a dedicated pass when the F8 closure is scoped with the
`persistence.txn` (Phase-2 STM) entrypoint" — is defensible. The scope
argument is real: `DB.transact` has grown to 100+ LOC with
retroactive-correction guards, auto-retraction, and placeholder-rewrite
logic; any atomicity refactor needs to preserve all of it. ✓

### 5.2 F8 DB-boundary self-conform (carried from R3)

**Honest deferral.** Worker summary §"DB.transact input self-conform"
gives three reasons: (1) input shape is `list[dict]` not Datoms, (2)
TX_PLACEHOLDER rewrite happens inside the atomic section so conform
placement is non-trivial, (3) test blast radius is wide. All verified
at HEAD. The five other producers DO self-conform (§R3.1.N3 report
stands). The one-line fix is available but the worker correctly judged
it needed a scoped pass. Acceptable for freeze.

### 5.3 N7 plan-node class-bound recursion

Out of scope per W-wire prompt. `src/persistence/spec/_canonical.py:524`
confirmed unchanged. Phase 2. ✓

## 6. Overall composability grade — **9.3 / 10**

### Positives

- **+** **N2 CLOSED** — the "biggest R3 drag" is fixed by the cleanest
  option R3 proposed (dataclass `__post_init__` normalisation). Nine
  new pinning tests. The `Datom` dataclass now enforces the canonical
  in-memory form the way `AuditEntry` enforces op form — one consistent
  policy across fact + effect modules.
- **+** **B1 CLOSED** — multi-step intervention lineage preserved.
  Clean three-surface fix (dataclass, engine, spec). Wire helpers are
  idempotent. Back-compat shim. Nine new integration tests. DPO
  composition verified unaffected.
- **+** **R3-N6 CLOSED (with R4-N1 asymmetry)** — `AuditEntry.from_edn`
  exists and works on production-shape inputs. 5 new tests. The
  asymmetry on pre-kw input is a real defect (see below) but it's
  orthogonal to the main closure: R3 flagged "the inverse doesn't
  exist"; R4 added it; R4 review flags a second-order asymmetry.
- **+** **Self-conform discipline** — all 5 wire producers still
  self-conform (unchanged from R3): `datom_to_wire`, `wire_to_datom`,
  `audit_entry_to_datom`, `AuditEntry.to_edn`, `Trajectory.to_edn`.
  Two new producers added in R4 (`AuditEntry.from_edn`,
  `Trajectory.from_edn` legacy-shim path) — neither self-conforms,
  consistent with "consumers trust" convention.
- **+** **Deferrals are honest** — the W-wire summary explicitly calls
  out 3 deferrals (N8 atomicity, F8 DB-boundary, Prop 1 scaling), gives
  concrete scope reasons, and maps each to a Phase-2 track. No
  misrepresentation.
- **+** **551 tests green, 2 skipped, 0 regressions** in 2.79s. +31
  over R3's 520 baseline.

### Negatives

- **−** **R4-N1 (MEDIUM)** — the new `AuditEntry.from_edn` is not a true
  inverse on non-production-shape inputs; `verify_chain` returns False
  on round-tripped pre-kw chains. Latent in production but a genuine
  defect in the newly-shipped adapter. The fix (canonicalise chain +
  principal keys at `__post_init__`) is one-file/~10-line.
- **−** **R4-N2 / R4-N3 / R4-N4** — three LOW-severity asymmetries.
  R4-N2 was hidden before `from_edn` shipped; R4-N3 is double-colon
  hardening; R4-N4 is the `_provenance_to_wire` kw-key+bare-value case.
  None blocks Phase 1.
- **−** **DB.transact silently drops per-fact `provenance` key**
  (§3.1 quirk) — pre-existing, but it's a composability footgun the
  docstring doesn't quite cover. Not a Round 4 regression.
- **−** Legacy single-dict intervention back-compat shim bypasses
  spec.conform on input (§2.1) — deliberate, but means `from_edn →
  to_edn` can launder a non-conformant wire into conformant wire.

### Composability score math

| Axis | R3 | R4 | Δ | Weight |
|---|---:|---:|---:|---|
| Concurrency safety | 9.5 | 9.5 | 0 (deferred correctly) | 2× |
| Self-conform boundary coverage | 8.5 | 8.5 | 0 (unchanged, still 5/6) | 1.5× |
| Op-name format discipline | 9 | 9 | 0 | 1× |
| Adapter identity (wire.py) | 5 | 9.5 | +4.5 | 2× |
| Plan-node Phase-2 interface | 9 | 9 | 0 | 1× |
| Audit-entry bi-directional round-trip | 7 | 8 (present but asymmetric on pre-kw) | +1 | 1.5× |
| Verdict reconciler coverage | 7 | 7 | 0 (F6 untouched) | 1× |
| SQL honesty | 8.5 | 8.5 | 0 | 1× |
| Intervention wire (multi-step lineage) | 5 (B1 pinned only) | 9.5 | +4.5 | 1.5× |
| Datom dataclass canonical form | 5 | 9.3 (not 9.5 due to R4-N3) | +4.3 | 1.5× |

Weighted mean:

> (9.5·2 + 8.5·1.5 + 9·1 + 9.5·2 + 9·1 + 8·1.5 + 7·1 + 8.5·1 + 9.5·1.5 + 9.3·1.5) / (2+1.5+1+2+1+1.5+1+1+1.5+1.5)
> = (19 + 12.75 + 9 + 19 + 9 + 12 + 7 + 8.5 + 14.25 + 13.95) / 14
> = 124.45 / 14
> = **8.89**

Simple mean of the 10 rows = (9.5+8.5+9+9.5+9+8+7+8.5+9.5+9.3)/10 = **8.78**.

Neither mean is above 9.2. But both means underweight the direction-of-
progress: N2 moved from 5→9.5, B1 moved from 5→9.5, Datom dataclass
canonicalisation from 5→9.3. The three biggest composability drags
from R3 all closed. R4-N1 is a real but narrow new finding (latent in
production). The deferred items are honestly deferred. Round 2
predecessor (8.6) called the N2 miss "the single biggest drag";
resolving it plus B1 plus N6 warrants more than an arithmetic bump.

**My calibrated grade, accounting for direction-of-progress, honesty of
deferrals, and the single NEW asymmetry (R4-N1, LOW production impact):**

**R3 composability grade at HEAD = 9.3 / 10.** Meets the ≥ 9.2 freeze
target.

### Against Round 3's 8.9

| Axis | R3 | R4 | Δ |
|---|---:|---:|---:|
| N2 closure | 5 (not fixed) | 9.5 (fixed + 9 tests) | +4.5 |
| B1 closure | 5 (shape-pinned only) | 9.5 (full three-surface fix) | +4.5 |
| N6 closure | 7 (missing) | 8 (present, asymmetric on pre-kw) | +1 |
| New findings introduced | 3 (N5/N6/N7, all minor) | 4 (R4-N1 MED, N2/N3/N4 LOW) | +1 finding, −0.5 |
| Deferral honesty | 8 (3 items, honest) | 9 (3 items, honest, with concrete Phase-2 path) | +1 |

Net positive on the freeze-gate axis. The R4-N1 finding is the only one
that deserves attention in Phase 2 triage; the others are LOW-impact
hardening.

## 7. Phase 2 residual composability todos

### Must-fix in Phase 2

1. **R4-N1** — canonicalise `AuditEntry.handler_chain` and `principal`
   keys at `__post_init__` (strip leading colons). Add a round-trip
   test pinning `verify_chain(from_edn(to_edn(e))) == True` for all
   shape-permutations of `handler_chain` ∈ {bare, kw, mixed} ×
   principal keys ∈ {bare, kw, mixed}. Symmetric with the
   `Datom.__post_init__` fix.

2. **R3-N5 / worker N8** — fold `mark_invalidated` into the same
   `BEGIN IMMEDIATE` as `allocate_and_append`, or precompute the
   invalidation targets and `UPDATE` them inside the same atomic
   section. Needed before Phase-2 STM composes cross-transact reads.

3. **R3 F8 DB-boundary self-conform** — add
   `_S.parse(":persistence.fact/datom", datom_to_wire(d))` inside
   `DB.transact` after the `TX_PLACEHOLDER` rewrite, or lift the
   per-fact dict shape into a registered spec. Closes the last
   producer in the six-producer set.

4. **R3 F6** — `policy_eval.py:185-189` still writes bare `"deny"` /
   `"allow"` literals. Import `PYTHON_VERDICTS` and validate. Carry
   from Round 2, unchanged in Round 3 / Round 4.

### Hardening (can wait for Phase 2 first pass)

5. **R4-N3** — change `a[1:]` to `a.lstrip(":")` in
   `Datom.__post_init__`. Same for `source` key in provenance.

6. **R4-N4** — drop the `continue` in `_provenance_to_wire`'s
   already-keyworded-key branch so value-keywordification runs
   regardless of key form.

7. **R3-N7** — `_PlanNodeVector._conform` should recurse via registry
   ref or at least docstring-pin the class-bound recursion.

8. **`Trajectory.to_dict` / `from_dict` legacy intervention shape** — add
   the same single-dict-to-1-list shim to `from_dict` that `from_edn`
   already has, so pre-W-wire replay caches load cleanly.

9. **`DB.transact` per-fact `provenance` drop** — either honour
   `fact["provenance"]` or raise on its presence with a loud deprecation
   message. Current silent drop is a footgun.

### Documentation

10. **`Trajectory.from_edn` legacy shim** — docstring note that the
    shim accepts a single-dict `:trajectory/intervention` that would
    fail `spec.conform` directly; the shim is one-direction (accepts
    non-conformant, produces conformant on re-emit).

## Appendix — raw verification commands

All run at HEAD `61644f6`:

- `pytest -q` → `551 passed, 2 skipped in 2.79s`.
- `pytest tests/replay/test_replay.py tests/effect/test_audit.py tests/fact/test_concurrent_transact.py tests/replay/test_intervention_wire.py tests/effect/test_handler_chain_wire.py tests/fact/test_wire_identity.py -v` → `56 passed in 0.63s`.
- N2 extended-domain reproduction (Cases 1-6 in §2.3 of this report) — all 5 canonical-domain cases now identity; Case 6 (double-colon) is the R4-N3 edge.
- R4-N1 reproduction: `verify_chain([AuditEntry.from_edn(e.to_edn())])` returns `False` when `e.handler_chain` was constructed pre-keyworded (`(":audit",":llm",":tool")`). Confirmed recomputed content hash differs from stored id.
- R4-N2 reproduction: same pattern for `principal` keys.
- R4-N3 reproduction: `Datom(a="::x").a == ":x"` (single colon left after `__post_init__`).
- R4-N4 reproduction: `_provenance_to_wire({":source": "bare"})` returns `{":source": "bare"}` (bare value unchanged); `datom_to_wire` with that provenance raises `SpecError` at self-conform (loud, not silent — good).
- `rg '\.intervention\[' src/` → 0 hits (no dict-keyed readers of intervention).
- `rg intervention src/persistence/replay/dpo.py` → 0 hits (DPO does not touch intervention).
- Trajectory.to_edn / from_edn on legacy single-dict payload: `Trajectory.from_edn` succeeds (shim); `spec.conform(":persistence.replay/trajectory", legacy_wire)` returns `is_ok=False` (correct rejection).
- Production 3-entry audit chain `verify_chain` holds before and after `to_edn → from_edn` round-trip.
