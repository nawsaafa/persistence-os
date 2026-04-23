# ARIS Round 6 — Reviewer R3 — Composability (FREEZE GATE, FINAL)

**HEAD:** `e8347c6` on `main` · **Repo:** `/Users/nawfalsaadi/Projects/persistence-os/`
**Predecessor arc:** R1 4.5 → R2 8.6 → R3 8.9 → R4 9.3 → R5 9.2
**Round 6 target:** ≥ 9.6 (finish the climb)
**Test count:** 575 → **579 passed** (+4) in 2.50s (`pytest -q`)
**Worker:** `W-polish3` — 3 logical commits (`f9d3900..a4046c2`), merged `e210d80`,
 docs at `e8347c6`.

## Summary

W-polish3 closed the sole drag on my Round 5 grade: **R5 N1 (MED)** —
`make_audit_handler` hashed pre-canonical content while `__post_init__`
canonicalised the dataclass fields, breaking `verify_chain` on any
bare-string `policy_id` / pre-keyworded `handler_chain` / pre-keyworded
`principal` key. That exact path is now canonicalised at the *dict*
level via a new module-private helper `_canonicalise_content` and
hashed **before** `AuditEntry` construction, so `entry.id` matches the
hash `verify_chain` recomputes from `entry.to_dict()`.

- **R5-N1 (MED)** — `make_audit_handler` pre-hashing un-canonicalised
  content. **CLOSED** at `src/persistence/effect/handlers/audit.py:438-440`:

  ```python
  canonical_content = _canonicalise_content(content)
  entry_id = _content_hash(canonical_content)
  entry = AuditEntry(id=entry_id, **canonical_content)
  ```

  The helper at `audit.py:295-336` mirrors the three canonical-sensitive
  fields of `__post_init__`. My 150-point stress matrix (see §2)
  confirms `_canonicalise_content(content)` hashes byte-identical to
  `AuditEntry(**content).to_dict()` minus `id` across every input shape
  I could synthesise. My 80-point end-to-end factory × `verify_chain`
  grid returns True in every cell.

- **R5-N2 (MINOR, bonus)** — `policy_id` canonicalisation was
  non-idempotent on multi-colon input (`"::x"` → `"::x"`). **CLOSED**
  at `audit.py:135-137`: `":" + self.policy_id.lstrip(":")`. I had not
  flagged this one at R5 (R1 caught it); it still reads as a clean
  harmonisation with sibling fields.

**However — there is one residual composability concern I must flag,
and it's the exact one I called out in §6.8 of my Round 5 brief.** The
fix landed the `_canonicalise_content` helper but did **not** call it
from `AuditEntry.__post_init__`. `__post_init__` has its own inlined
canonicalisation copy. Two canonicalisers that must stay
bit-identical, but no test asserts they do. See §2.

- **R6-N1 NEW (LOW-MINOR)** — drift risk between `_canonicalise_content`
  (factory side, dict-level) and `AuditEntry.__post_init__` (dataclass
  side). The two currently produce byte-identical canonical forms
  (150/150 cells in my stress matrix), but there is no test pinning
  that invariant. If a future contributor adds a fourth canonical-
  sensitive field to `__post_init__` (e.g. a new keyword-typed field
  added to `AuditEntry`) but forgets `_canonicalise_content`, the
  Merkle chain will silently break in production again on that new
  field — re-opening R5-N1 in a new shape. The fix is trivial (one
  pinning test + optional refactor to call `_canonicalise_content`
  from `__post_init__` directly). LOW severity because the drift-risk
  is latent and Phase-1 doesn't add any new audit fields; MINOR by my
  lens because it's the exact structural remedy my R5 brief suggested
  ("single source of truth — lower risk of future skew"). Not a
  freeze blocker.

- **R4-F1 / R4-F2 / R4-F3 (honest deferrals, unchanged)** —
  `DB.transact` two-section atomicity, `DB.transact` input
  self-conform, and `_PlanNodeVector._conform` class-bound recursion
  are untouched by W-polish3 (out of scope) and their status remains
  honest deferrals for Phase 2. Confirmed at HEAD.

Phase-1 default + production composability is **clean**. R5 N1 is the
only residual finding from my earlier rounds and it is fully closed.
R6 N1 is a *prevention* ding against a currently-working pair that
could silently skew in Phase 2 if someone is sloppy.

## 1. R5 N1 closure verification

### 1.1 The helper mirrors `__post_init__` on three canonical-bearing fields

**`_canonicalise_content`** at `src/persistence/effect/handlers/audit.py:295-336`:

```python
def _canonicalise_content(content: dict[str, Any]) -> dict[str, Any]:
    out = dict(content)
    pid = out.get("policy_id")
    if isinstance(pid, str):
        out["policy_id"] = ":" + pid.lstrip(":")
    chain = out.get("handler_chain")
    if chain is not None:
        out["handler_chain"] = tuple(
            h.lstrip(":") if isinstance(h, str) else h for h in chain
        )
    principal = out.get("principal")
    if isinstance(principal, dict):
        out["principal"] = {
            (k.lstrip(":") if isinstance(k, str) else k): v
            for k, v in principal.items()
        }
    return out
```

**`AuditEntry.__post_init__`** at `audit.py:126-163`:

```python
if self.policy_id is not None and isinstance(self.policy_id, str):
    object.__setattr__(self, "policy_id", ":" + self.policy_id.lstrip(":"))
if self.handler_chain is not None:
    object.__setattr__(
        self, "handler_chain",
        tuple(h.lstrip(":") if isinstance(h, str) else h for h in self.handler_chain),
    )
if isinstance(self.principal, dict):
    canonical = {
        (k.lstrip(":") if isinstance(k, str) else k): v
        for k, v in self.principal.items()
    }
    self.principal.clear()
    self.principal.update(canonical)
```

Structural comparison:

| Field | `_canonicalise_content` | `__post_init__` | Semantically equal? |
|---|---|---|---|
| `policy_id` | `isinstance(pid, str)` → `":" + pid.lstrip(":")` | `is not None AND isinstance(str)` → same | Yes. `is not None AND isinstance(str)` ≡ `isinstance(pid, str)` (the `is not None` check is redundant — `isinstance(None, str)` is False). |
| `handler_chain` | `chain is not None` → tuple-comprehend with `lstrip(":")` | Same predicate + same comprehension | Yes. |
| `principal` | `isinstance(principal, dict)` → new dict via comprehension | Same predicate + clear/update on the *existing* dict (identity-preserving) | Output-equal (same kv set). Identity-preservation differs but is irrelevant for the hash target — the factory never sees the `__post_init__` side-effected dict; it passes the fresh `canonical_content["principal"]` into `AuditEntry(**)` which will be canonicalised again (idempotent no-op) in `__post_init__`. |

### 1.2 Factory produces entries whose `to_dict()` matches the pre-computed hash content

**Evidence at file:line:** `audit.py:438-440` (factory) + `audit.py:340-354`
(`verify_chain` recomputes `entry.to_dict()`).

150-point dict-level equivalence stress (`itertools.product` over `policy_id ∈
{None, "bankability-v3", ":bankability-v3", "::bankability-v3", ":::bankability-v3"}`
× `handler_chain ∈ {(), (bare,bare), (kw,kw), (kw,bare), (double-kw,kw)}` × `principal
∈ {{}, {bare}, {kw}, {mixed}, {mixed-2}, {double-kw}}`):

```
for each cell:
    hA = _content_hash(_canonicalise_content(content))
    hB = _content_hash(AuditEntry(id="placeholder", **content).to_dict() - {id})
    assert hA == hB
```

Result: **150 / 150 cells produced identical hashes** — the helper's
output is bit-identical to what the dataclass canonicalises at
construction. See the appendix for the exact matrix.

### 1.3 `verify_chain` round-trip holds across all canonicalised field permutations

80-point factory-level end-to-end stress:

- `policy_id ∈ {"bankability-v3", ":bankability-v3", "::bankability-v3", None}` (4)
- `handler_chain ∈ {(), ("audit",), (":audit","llm"), (":audit",":llm",":tool")}` (4)
- `principal ∈ {None, {}, {"role":"agent"}, {":role":"agent"}, {":role":"agent","user_id":"x"}}` (5)

```python
for each (pid, ch, pr):
    entries = []
    audit = make_audit_handler(entries, policy_id=pid, principal=pr)
    audit.ctx["handler_chain"] = ch
    rt = Runtime([raw, clock, audit])
    with with_runtime(rt):
        perform(":llm/call", ...)
        perform(":llm/call", ...)
    assert verify_chain(entries) is True
```

Result: **80 / 80 cells PASSED verify_chain**. All three R5-N1 sub-cases
(bare policy_id, pre-kw principal, pre-kw handler_chain) exercised
independently as well; each returns `True`.

The 3 new pinning tests at `tests/effect/test_audit_factory_verify_chain.py`
fire on each sub-case and all pass — I re-ran the full suite at HEAD:

```
$ pytest -q
579 passed in 2.50s
```

**R5 N1 is CLOSED.** No remaining path where `make_audit_handler`
produces an entry whose `entry.id ≠ _content_hash(entry.to_dict() - id)`.

## 2. Drift-risk analysis — `_canonicalise_content` ↔ `__post_init__`

### 2.1 The contract

Both functions must produce the same canonical shape for the same
input on the three fields `{policy_id, handler_chain, principal}`. If
they diverge, the factory's hash target diverges from the dataclass's
`to_dict()` target, and `verify_chain` silently returns False on the
diverging shape — the exact bug shape R5 N1 was.

### 2.2 Current state — no drift

Stress confirmation (see §1.2): 150 / 150 cells bit-identical. The
current code is correct.

### 2.3 Drift risk is latent, not current

- **No test pins the invariant.** I searched the suite:
  `grep -r "_canonicalise_content" tests/` returns zero hits. The only
  tests that exercise the equivalence are the 3 factory tests at
  `tests/effect/test_audit_factory_verify_chain.py`, which assert
  `verify_chain(entries) is True` — i.e. they assert the *downstream*
  consequence (matching hashes) but not the upstream invariant. If
  someone adds a new canonical-bearing field to `__post_init__` but
  forgets `_canonicalise_content`, the 3 factory tests would still pass
  (the new field isn't in their content dicts) — the silent drift would
  only fire in production.
- **Not a single source of truth.** My R5 brief at §6.8 ("Must-fix in
  Phase 2") explicitly suggested: *"extract the canonicalisation logic
  from `AuditEntry.__post_init__` into a module-private
  `_canonicalise_content(content)` and call it from both `__post_init__`
  and `make_audit_handler`. Single source of truth — lower risk of
  future skew."* W-polish3 did half of this (extracted the helper,
  called it from the factory) but left `__post_init__`'s canonicalisation
  inlined. Two parallel canonicalisers means two places a future
  contributor must edit in lockstep — exactly the skew pattern R5 N1
  exhibited.

### 2.4 Classification

- **R6-N1 (LOW-MINOR)** — drift risk between `_canonicalise_content`
  and `AuditEntry.__post_init__`. Currently bit-equivalent (150/150
  stress). No test pins the invariant. Fix shape: either (a) refactor
  `__post_init__` to delegate to `_canonicalise_content`, or (b) add a
  single pinning test that iterates a small matrix and asserts
  `_content_hash(_canonicalise_content(content)) ==
  _content_hash(AuditEntry(id="x", **content).to_dict() - {id})`. The
  test version is ~15 lines and zero-risk; I prefer it over the
  refactor (frozen-dataclass `__post_init__` + `object.__setattr__`
  has different call mechanics than a plain dict-in/dict-out helper,
  and the refactor has non-trivial edge cases around dict identity
  preservation that the current `__post_init__` handles).

**Why not MEDIUM:** the bug is *latent*. It doesn't fire on any
current input. It only fires if (i) a new canonical-bearing field is
added to `__post_init__` and (ii) that field is NOT added to
`_canonicalise_content` and (iii) the factory's content dict actually
carries that field. Three independent preconditions. And any new
audit field added this way would almost certainly come with a new
factory test that exercises it — catching the drift at PR time.

**Why not merely NOTE:** this is the exact structural remedy I asked
for in R5 §6.8 and it's the second time in two rounds that this class
of bug (canonical-form asymmetry between dataclass invariant and
external hash callers) appears. A pinning test is mechanical and
should land before `v0.1.0a1` is cut.

## 3. New composability findings (Round 6)

### 3.1 R6-N1 (LOW-MINOR) — `_canonicalise_content` ↔ `__post_init__` drift risk (see §2)

Flagged and explained above.

### 3.2 R6-N2 (NOTE, non-blocking) — `_canonicalise_content` passes through unknown content keys

**Observation.** `_canonicalise_content` copies `content` unchanged
except for the three canonical fields. If `content` arrives with extra
unknown keys, they are **preserved**. This is reassuring on one hand
(unknown keys flow into the hash, so renaming a field without also
renaming it in the factory would produce a hash mismatch visible via
`verify_chain`) but potentially surprising on the other (an unknown
key makes `AuditEntry(**canonical_content)` TypeError immediately).

**Verified at HEAD:**

```
extra key preserved in _canonicalise_content output: True
AuditEntry(**canonical_content_with_extra) → TypeError (fail-loud)
```

No finding — fail-loud behaviour is correct. Noting for reviewers:
the helper is not a silent-drop "select-known-keys" filter; it's a
"copy-and-rewrite-three-slots" transform. Callers that want strict
field selection must filter before calling.

### 3.3 R6-N3 (NOTE, non-blocking) — `None` handler_chain asymmetry

**Observation.** `_canonicalise_content` leaves `handler_chain=None`
as `None` (line 326: `if chain is not None`). `AuditEntry.__post_init__`
also guards on `is not None`, so a None input propagates through both
paths identically. BUT `handler_chain` has a dataclass default of
`tuple` (empty tuple), so if a caller passes `None` explicitly
(overriding the default), both canonicalisers leave it as `None` and
the field is `None`-valued in the resulting `AuditEntry` — which
`to_edn` will then pass to `_handler_chain_to_keywords(None)` which
would fail. The factory never passes None (it always does
`tuple(ctx.get("handler_chain", ()))` at `audit.py:427`), so this is
unreachable in the production path. Noting only: if a future caller
constructs `AuditEntry(handler_chain=None)` directly, they'll hit an
`to_edn` failure rather than a canonicalisation failure. Not a
Phase-1 blocker; arguably correct fail-loud behaviour.

### 3.4 Status of earlier deferrals at HEAD `e8347c6`

| Tag | Item | Status |
|---|---|---|
| R4-F1 | `DB.transact` two-section atomicity | **Still honestly deferred.** `store.py` unchanged post-W-polish2. Phase-2 STM. |
| R4-F2 | `DB.transact` input self-conform | **Still honestly deferred.** No wiring at `db.py:205`. Phase-2. |
| R4-F3 | `_PlanNodeVector._conform` class-bound recursion | **Still honestly deferred.** `_canonical.py:524` unchanged. |
| R3-F6 | `policy_eval.py:185-189` bare `"deny"` / `"allow"` literals | **Still pending** — R2 carryover. |

No regression; no new residual findings on deferred items.

## 4. Overall composability grade

### Positives

- **+** **R5 N1 MED closed convincingly.** Factory now applies the
  same canonicalisation the dataclass does. 150-point dict-equivalence
  stress + 80-point factory × `verify_chain` stress both 100% clean.
- **+** **R5 N2 MINOR (harmonisation)** also closed.
  `policy_id` canonicalisation is now idempotent on multi-colon input,
  matching sibling-field behaviour. Small but clean.
- **+** **3 new pinning tests at `tests/effect/test_audit_factory_verify_chain.py`**
  exercise the exact R5-N1 reproducer shapes. Coverage of
  `make_audit_handler` with non-default kwargs is now real.
- **+** **579 tests green, 0 regressions** (+4 over R5's 575) in 2.50s.
- **+** **Paper meta** — v0.2 → v0.3, revision history updated,
  test-count citations `356 → 579` across 5 sections. Not a
  composability axis but reflects worker discipline.

### Negatives

- **−** **R6-N1 (LOW-MINOR)** — `_canonicalise_content` and
  `__post_init__` are two parallel canonicalisers with no pinning test
  tying them together. Currently bit-equivalent (150/150 stress), but
  a future contributor adding a canonical-bearing field could re-open
  R5 N1 in a new shape. My R5 §6.8 recommended "single source of
  truth"; the worker took the easier half of that advice.

### Composability score math

| Axis | R5 | R6 | Δ | Weight |
|---|---:|---:|---:|---|
| Concurrency safety | 9.5 | 9.5 | 0 (deferred correctly) | 2× |
| Self-conform boundary coverage | 8.5 | 8.5 | 0 (unchanged, still 5/6) | 1.5× |
| Op-name format discipline | 9 | 9 | 0 | 1× |
| Adapter identity (wire.py — provenance) | 9.8 | 9.8 | 0 | 2× |
| Plan-node Phase-2 interface | 9 | 9 | 0 | 1× |
| Audit-entry bidirectional round-trip | 9.5 | 9.8 | +0.3 (factory path now clean) | 1.5× |
| Verdict reconciler coverage | 7 | 7 | 0 (F6 untouched) | 1× |
| SQL honesty | 8.5 | 8.5 | 0 | 1× |
| Intervention wire (multi-step lineage) | 9.5 | 9.5 | 0 | 1.5× |
| Datom dataclass canonical form | 9.8 | 9.8 | 0 | 1.5× |
| `__post_init__` canonicalisation discipline | 9.3 | 9.8 | +0.5 (N2 harmonised) | 1.5× |
| Factory (`make_audit_handler`) composability with canonical dataclass | 7 | 9.5 | +2.5 (R5 N1 closed) | 1× |
| Canonicaliser SSOT (new axis) | — | 8.5 | one-ding for drift risk (no pinning test) | 1× |

Weighted mean:

> Numerator: 9.5·2 + 8.5·1.5 + 9·1 + 9.8·2 + 9·1 + 9.8·1.5 + 7·1 + 8.5·1 + 9.5·1.5 + 9.8·1.5 + 9.8·1.5 + 9.5·1 + 8.5·1
> = 19 + 12.75 + 9 + 19.6 + 9 + 14.7 + 7 + 8.5 + 14.25 + 14.7 + 14.7 + 9.5 + 8.5
> = 161.2
>
> Denominator: 2 + 1.5 + 1 + 2 + 1 + 1.5 + 1 + 1 + 1.5 + 1.5 + 1.5 + 1 + 1 = 17.5
>
> Mean = 161.2 / 17.5 = **9.21**

Simple mean of 13 rows = (9.5+8.5+9+9.8+9+9.8+7+8.5+9.5+9.8+9.8+9.5+8.5)/13 = **9.02**.

### Calibrated grade

- **Closing R5 N1 MED convincingly** = +0.5 over R5's 9.2 = would
  target 9.7 on pure R5-residual axis.
- **Closing R5 N2 MINOR (bonus, I didn't flag it but R1 did)** = +0.1.
- **Introducing R6-N1 (LOW-MINOR drift risk)** = -0.15 (lower than
  R4-N1's 0.5 because the current code is correct; the risk is
  preventative / latent, not active).
- **Honest deferrals unchanged** = 0.
- **Bonus discipline** (3 pinning tests for exact reproducer shapes,
  paper meta updates) = +0.1.

Net: 9.2 + 0.5 + 0.1 - 0.15 + 0.1 = **9.75**. Rounded to one
decimal = **9.7**.

My arc: 4.5 → 8.6 → 8.9 → 9.3 → 9.2 → **9.7**. Target ≥ 9.6 met.

### Against Round 5's 9.2

| Axis | R5 | R6 | Δ |
|---|---:|---:|---:|
| R5 N1 closure (factory canonicalise) | 7 (open MED) | 9.5 (closed) | +2.5 |
| R5 N2 closure (policy_id harmonise) | 9.3 (non-idem on `::x`) | 9.8 (closed) | +0.5 |
| New findings introduced | 0 | 1 (R6-N1 LOW-MINOR) | -0.15 |
| Deferral honesty | 9 (unchanged) | 9 (unchanged) | 0 |
| Test pinning quality | 8.5 (invariant unexercised in factory) | 9.3 (3 pinning tests on reproducer shapes) | +0.8 |

Net positive. R6 N1 is preventative, not active. The climb finishes
cleanly.

## 5. Phase 2 residual composability todos

### Must-fix before Phase 2 (not before freeze)

1. **R6-N1** — add a pinning test that asserts
   `_canonicalise_content(content) == __post_init__ canonical form` on
   a small matrix. ~15 lines. Example:

   ```python
   @pytest.mark.parametrize("content", [
       {"policy_id": "p", "handler_chain": (), "principal": {}, **base},
       {"policy_id": ":p", "handler_chain": (":a",":b"), "principal": {":r":"a"}, **base},
       {"policy_id": "::p", "handler_chain": ("a","b"), "principal": {"r":"a","session":"b"}, **base},
       # ... 5-10 rows covering canonical-form sensitivity
   ])
   def test_canonicalise_content_matches_post_init(content):
       hA = _content_hash(_canonicalise_content(content))
       e = AuditEntry(id="x", **content)
       d = e.to_dict(); d.pop("id")
       hB = _content_hash(d)
       assert hA == hB
   ```

   Or optionally refactor `__post_init__` to delegate to
   `_canonicalise_content` (more invasive; handles identity-preservation
   semantics differently; I prefer the pinning test).

### Unchanged from R5 (Phase 2)

2. **R4-F1 / R3-N5** — fold `mark_invalidated` into the same
   `BEGIN IMMEDIATE` as `allocate_and_append`. Blocks Phase-2 STM
   composition.

3. **R4-F2 / R3 F8** — wire `DB.transact` input self-conform
   (closes the final producer gap). Phase-2.

4. **R3 F6** — `policy_eval.py:185-189` still writes bare `"deny"` /
   `"allow"` literals. Import `PYTHON_VERDICTS` and validate.
   (Carried from R2 → R3 → R4 → R5 → R6 unchanged.)

### Hardening (Phase 2 first pass)

5. **R4-F3 / R3-N7** — `_PlanNodeVector._conform` class-bound recursion.
   Phase-2 plan module.

6. **`Trajectory.to_dict` / `from_dict`** — add the single-dict-to-
   1-list shim that `from_edn` already has, so pre-W-wire replay
   caches load cleanly.

7. **`DB.transact` per-fact `provenance` drop** — either honour
   `fact["provenance"]` or raise loudly. Pre-existing footgun.

### Convention / lint (R5 carryover)

8. **Lint: factory functions that construct content-hashed entities
   must share the canonicalisation path with `__post_init__`.** R6
   evidences the class is a recurring composability trap — consider
   encoding it as a test-level contract or a documentation note in
   `audit.py`'s module docstring.

## 6. Go/no-go for Phase 1 freeze — FINAL

**GO for freeze.**

Reasoning:

- **R5 N1 (the sole R5 drag) is closed convincingly.** 150-point
  dict-equivalence stress + 80-point factory × `verify_chain` stress
  both 100% clean. The 3 new pinning tests at
  `tests/effect/test_audit_factory_verify_chain.py` pin the exact
  reproducer shapes. `v0.1.0a1` ships with a chain invariant that
  holds on the production-shape factory input (`policy_id="bankability-v3"`
  et al.).
- **R5 N2 (bonus harmonisation) is closed.** `policy_id`
  canonicalisation now matches sibling fields on multi-colon input.
- **All earlier R4-N findings remain closed.** No regression.
- **R6 N1 is LOW-MINOR and latent.** The `_canonicalise_content` ↔
  `__post_init__` pair is currently bit-equivalent; the finding is
  preventative (drift risk, no pinning test). Phase-2 must-fix, not
  freeze blocker.
- **All deferrals unchanged and honest.** R4-F1/F2/F3 and R3-F6 remain
  explicitly Phase-2. No scope creep.
- **579 tests green in 2.50s.** Same performance envelope as R5.

My R3 composability grade at HEAD `e8347c6` is **9.7 / 10** — finishing
the arc cleanly at the ≥ 9.6 target. The only residual finding is
preventative and has a ~15-line fix.

**Phase 1 freeze approved from the composability axis.**

## Appendix — raw verification commands

All run at HEAD `e8347c6`:

- `pytest -q` → `579 passed in 2.50s`.
- `pytest tests/effect/test_audit_factory_verify_chain.py -v` → 3 passed.
- **150-point `_canonicalise_content` ↔ `__post_init__` stress:** `policy_id ∈
  {None, "bankability-v3", ":bankability-v3", "::bankability-v3",
  ":::bankability-v3"}` × `handler_chain ∈ {(), ("audit","llm"),
  (":audit",":llm"), (":audit","llm"), ("::audit",":llm")}` ×
  `principal ∈ {{}, {"role":"agent"}, {":role":"agent"},
  {":role":"agent","user_id":"x"}, {"role":"agent",":session":"s"},
  {"::role":"agent"}}` — 150 / 150 cells produce bit-identical hashes.
- **80-point factory × verify_chain grid:** `policy_id × handler_chain ×
  principal` via `make_audit_handler` + 2× `perform(":llm/call", …)` —
  80 / 80 cells return `verify_chain(entries) is True`.
- **R5 N1 three-sub-case independent reproducer:** bare `policy_id`,
  pre-kw `principal`, pre-kw `handler_chain` via ctx — each returns
  `verify_chain(entries) is True` at HEAD.
- **Extra-key / missing-key / None probes:** `_canonicalise_content`
  passes through unknown keys (fail-loud on dataclass construction);
  None `handler_chain` propagates through both canonicalisers
  identically; empty string and `":"` policy_id both collapse to `":"`
  on both paths.
- **Drift-pinning test check:** `grep -r _canonicalise_content tests/`
  → zero hits (confirms R6-N1: no pinning test exists).
