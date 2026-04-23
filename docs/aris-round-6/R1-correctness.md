# ARIS Round 6 — R1 Correctness Re-Verification (Freeze Gate)

**Reviewer:** R1 (Correctness)
**Round:** 6 (final re-verification; freeze gate)
**Base:** `main @ e8347c6` (W-polish3 merged via `e210d80`; code changes in
`f9d3900` + `60d5dde`; docs-only meta in `a4046c2`)
**Test state:** **579 passed in 2.45s** — verified with
`source .venv/bin/activate && pytest -q`.
**Predecessors:** R1 7.4 → R2 8.3 → R3 8.6 → R4 9.0 → **R5 held 9.0** due to
R5 N1 MAJOR regression introduced by W-polish2's `__post_init__`
canonicalisation. Target: **≥ 9.3**.

Scope of this review: narrow re-verification of R5 N1 + R5 N2 closure and
a spot-check for new correctness regressions in W-polish3. Not a full
correctness review.

Serena LSP returned "No language servers available" during this session
(same failure the W-polish3 worker reported); I fell back to Read + Grep
+ Bash probes. All symbolic references below are to absolute line
numbers in HEAD `e8347c6`.

---

## 1. R5 N1 closure verification — MAJOR

### Evidence

**(a) Regression test exists.** `tests/effect/test_audit_factory_verify_chain.py`
is on disk and checked into `f9d3900`. Three tests:

- `test_verify_chain_on_factory_with_bare_policy_id` (L26-L51) — the
  R5 reproducer verbatim: `make_audit_handler(entries,
  policy_id="bankability-v3")` + `Runtime([raw, clock, audit])` + two
  `perform(":llm/call", …)` → asserts `verify_chain(entries) is True`
  AND `e.policy_id == ":bankability-v3"` for every entry.
- `test_verify_chain_on_factory_with_bare_string_handler_chain` (L54-L83)
  — pre-keyworded `(":audit", ":llm", ":tool")` in ctx; asserts
  canonical bare form AND `verify_chain(...) is True`.
- `test_verify_chain_on_factory_with_mixed_principal_keys` (L86-L111) —
  `{":user": "a", "session": "b"}`; asserts both keys stripped AND
  `verify_chain(...) is True`.

**(b) Tests pass on HEAD.**

```
$ pytest tests/effect/test_audit_factory_verify_chain.py -v
tests/effect/test_audit_factory_verify_chain.py::test_verify_chain_on_factory_with_bare_policy_id PASSED
tests/effect/test_audit_factory_verify_chain.py::test_verify_chain_on_factory_with_bare_string_handler_chain PASSED
tests/effect/test_audit_factory_verify_chain.py::test_verify_chain_on_factory_with_mixed_principal_keys PASSED
3 passed in 0.06s
```

**(c) Independent reproducer — all 3 canonicalised fields simultaneously
in production shape, 3-entry chain.** Constructed against HEAD to go one
step further than the committed tests:

```python
entries = []
audit = make_audit_handler(
    entries,
    wraps={":llm/call"},
    policy_id="bankability-v3",                        # bare
    principal={"user": "nawfal", "tenant": "egh"},     # bare
)
audit.ctx["handler_chain"] = ("audit", "llm", "tool")  # bare
rt = Runtime([make_echo_llm_handler(), make_fixed_clock_handler(...), audit])
with with_runtime(rt):
    perform(":llm/call", ...); perform(":llm/call", ...); perform(":llm/call", ...)

# Observed:
# entries: 3
# entry[0].policy_id: ':bankability-v3'
# entry[0].handler_chain: ('audit', 'llm', 'tool')
# entry[0].principal: {'user': 'nawfal', 'tenant': 'egh'}
# verify_chain(entries): True   ← was False on HEAD 60b3c85
```

The 3-entry chain exercises the `prev_hash` walk too (the R5 worry was
that even if entry 0 matched, a later link could still drift). It does
not. `verify_chain` returns `True`.

**(d) Code-path verification.** `src/persistence/effect/handlers/audit.py:432-440`:

```python
# ARIS Round 5 W-polish3 W6-factory-canonicalize (closes R5 N1 MAJOR).
canonical_content = _canonicalise_content(content)
entry_id = _content_hash(canonical_content)
entry = AuditEntry(id=entry_id, **canonical_content)
```

`_canonicalise_content` is called **before** `_content_hash`, and the
canonical dict is passed to `AuditEntry(id=..., **canonical_content)` so
`entry.to_dict()` reflects exactly the shape that was hashed. This is
the correct order: hash(canonicalise(content)) == hash(entry.to_dict()
minus id) — verified numerically in my hash-invariant probe (below).

**(e) Hash invariant — factory hash equals verify_chain rehash.**

```python
cc = _canonicalise_content(content_with_bare_policy_and_keyworded_chain)
factory_hash = _content_hash(cc)
entry = AuditEntry(id=factory_hash, **cc)
d = entry.to_dict(); d.pop("id")
verify_hash = _content_hash(d)
# factory hash: sha256:375bed32d7169d792cd7e90...
# verify hash:  sha256:375bed32d7169d792cd7e90...
# factory_hash == verify_hash → True
```

Byte-identical. This is the precise invariant R5 N1 broke; W-polish3
restores it.

### Verdict

**R5 N1 CLOSED.** Severity MAJOR → resolved. Prop 4 of the paper
(Merkle-chain tamper-evidence on the shipped artifact) now holds on the
factory path — the only production subdomain — and on all three
canonicalised field shapes individually and in combination. Regression
test pinned in `tests/effect/test_audit_factory_verify_chain.py`.

---

## 2. R5 N2 closure verification — MINOR

### Evidence

**(a) Code change.** `src/persistence/effect/handlers/audit.py:126-137`:

```python
if self.policy_id is not None and isinstance(self.policy_id, str):
    # W-polish3 W6-canonicalize-harmonize: ":" + lstrip(":") matches
    # sibling fields handler_chain / principal keys — idempotent on
    # any number of leading colons ("::x" → ":x").
    object.__setattr__(
        self, "policy_id", ":" + self.policy_id.lstrip(":")
    )
```

Harmonised with sibling fields (`handler_chain`, `principal`): all three
now use `lstrip(":")` semantics.

**(b) Idempotency probe against HEAD.**

```
policy_id=":::x" → ':x'
policy_id="::x"  → ':x'
policy_id=":x"   → ':x'
policy_id="x"    → ':x'
```

All variants collapse to `":x"`. Double-init (re-canonicalise an already
canonicalised entry's `policy_id`) also yields `":x"` — idempotent under
repeated construction.

**(c) Test.** `tests/effect/test_audit_canonicalize.py::TestPolicyIdCanonicalizationAtInit::test_policy_id_canonicalisation_is_idempotent_on_double_colon`
(L99-L117) covers `":x"`, `"x"`, `"::x"`, and `None` cases. Passes.

### Verdict

**R5 N2 CLOSED.** Severity MINOR → resolved. `policy_id` now shape-
matches sibling canonicalisations and is idempotent on multi-colon
inputs. Documentation-drift invariant restored.

---

## 3. New correctness findings in Round 6

No MAJOR or MEDIUM defects found in W-polish3. Below are the spot-checks
I ran and one observation.

### 3.1 `_canonicalise_content` edge-case sweep — all PASS

Probed against HEAD `e8347c6` (all green):

| Input | Behaviour | OK? |
|---|---|---|
| `policy_id=None` | passes through as `None` | YES |
| `policy_id` key missing from dict | `"policy_id"` not in output | YES |
| `policy_id="::x"` | canonicalises to `":x"` (idempotent) | YES |
| `handler_chain=None` | passes through as `None` | YES |
| `handler_chain=(":a", 42, ":b")` (mixed type) | non-strings pass through: `("a", 42, "b")` | YES |
| `principal=None` | passes through as `None` | YES |
| `principal={":user": "n", 7: "x"}` (non-string key) | non-string keys pass through: `{"user": "n", 7: "x"}` | YES |
| empty dict `{}` | returns empty dict | YES |
| purity (input dict not mutated) | `orig` unchanged after call | YES |

The helper copies the input (`out = dict(content)`) and only rewrites
the three known keyword-bearing slots. Unknown keys pass through
untouched — if a new field is added to `AuditEntry` that participates in
canonicalisation, `__post_init__` will still handle it but
`_canonicalise_content` will not, so the factory hash will drift from
the dataclass's `to_dict()` on that field. **Not a defect today**
(current fields are fully covered) — but it's a shape the two paths
could diverge along in the future if someone adds a canonicalised field
to `__post_init__` and forgets to update `_canonicalise_content`. Flagged
as Phase-2 hygiene in §3.3 below.

### 3.2 Hash determinism — factory hash == verify_chain rehash

For a realistic production-shape content dict with:

- `policy_id="bankability-v3"` (bare, production shape)
- `handler_chain=(":audit", ":llm")` (keyworded, non-production but
  valid input via `ctx["handler_chain"]` override)
- `principal={":user": "n"}` (keyworded-key)

`_content_hash(_canonicalise_content(content))` equals
`_content_hash(AuditEntry(..., **cc).to_dict() minus id)` — byte-
identical sha256. This is the *exact* invariant R5 N1 broke; it is
restored.

### 3.3 NEW — R6 N1 (NEGLIGIBLE, Phase-2 hygiene) — `_canonicalise_content` and `AuditEntry.__post_init__` are two code paths that must stay in sync

**Where.** `src/persistence/effect/handlers/audit.py:126-163`
(`__post_init__`) and `295-336` (`_canonicalise_content`).

**Observation.** The two paths apply the same three canonicalisation
rules but via different mechanisms — one mutates a dataclass, one
rebuilds a dict. If a future contributor adds a fourth keyword-bearing
field (e.g. `tenant_id`, `capability_token`) and canonicalises it in
`__post_init__` only, the factory path at `audit.py:417-431` will
silently re-open R5 N1's class of defect: hash pre-canonical, construct
post-canonical, mismatch.

**Why NEGLIGIBLE today.** No such field is present today. The invariant
holds on `e8347c6` for every field currently in `AuditEntry`. No
production defect.

**Why flagged anyway.** This is a sibling-of-a-sibling of R4 N7. The
defect class — "canonicalisation rule holds on path A but not path B" —
is exactly what got us here. Long-term fix: refactor so
`_canonicalise_content` is the single source of truth and
`__post_init__` calls it (or the other direction). That change is out
of scope for Phase 1 freeze — the current duplication is safe because
the field set is fixed at freeze time.

**Severity:** NEGLIGIBLE. Phase-2 hygiene. Does not block freeze.

### 3.4 Paper meta — 356 → 579 test-count updates verified

`grep -n "579" paper/persistence-nesy-2026-draft.md` returns six hits at
L12 (revision history), L20 (abstract), L38 (§4.7-adjacent), L297 (§6),
L363 (§6.6), L420 (§8), L461 (closing footer). `grep -n "356"`
returns two remaining hits at L12 and L461 — both in the explicit "test
count 356 → 579" transition sentence, so they are *intentional*
(documenting the progression), not drift. Verdict: CORRECT.

### 3.5 No other sibling defect surfaced

- `AuditEntry.from_edn` does not change in W-polish3; the R5 direct-
  construction round-trip test
  (`TestFromEdnRoundTripPreservesVerifyChain::test_verify_chain_survives_to_edn_from_edn`)
  still passes.
- `_provenance_to_wire` symmetry (R5-verified) untouched.
- `Datom.__post_init__` `lstrip(":")` idempotency (R5-verified)
  untouched.
- `canonical_hash` determinism: 579 tests green including the
  hash-based chain tests in `tests/effect/test_audit.py`.
- `AuditEntry.to_edn` self-conform still raises loudly on malformed
  spec inputs — probed with an explicit bad principal and confirmed.

---

## 4. Overall correctness grade

### Delta relative to R5 (9.0)

| Item | R5 state | R6 state | Δ |
|---|---|---|---|
| R5 N1 MAJOR — factory `verify_chain=False` regression | OPEN | CLOSED (factory canonicalises before hashing, 3 new end-to-end tests) | **+0.4** |
| R5 N2 MINOR — `policy_id` non-idempotent on `"::x"` | OPEN | CLOSED (`":" + lstrip(":")` + idempotency test) | +0.05 |
| Paper meta — test-count 356 → 579, v0.3 bumped | OPEN | CLOSED | +0.02 |
| R6 N1 NEGLIGIBLE — two canonicalisation code paths (Phase-2 hygiene) | n/a | OPEN (non-blocking) | -0.02 |

**Net:** 9.0 + 0.4 + 0.05 + 0.02 − 0.02 = **9.45**.

On axis weighting: the fix closes the exact class of defect
(canonicalisation invariant that holds on the unit-tested path but
fails on the factory path) that R5 was convened to eliminate. Prop 4
of the paper is now true-by-construction on the shipped artifact's
production subdomain. The new regression test ensures it stays that
way. The single negligible R6 finding is Phase-2 hygiene, not a
correctness break.

### **Final grade: R1 = 9.4**

Clears the ≥ 9.3 bar. Delta vs R5: +0.4 (9.0 → 9.4).

---

## 5. Go / no-go for Phase 1 freeze

### **GO on Phase 1 freeze.**

- 579 tests green, verified locally.
- R5 N1 MAJOR closed — the exact reproducer from R5 is now a committed
  regression test that passes on HEAD; the extended 3-field, 3-entry
  production-shape chain verifies end-to-end.
- R5 N2 MINOR closed — `policy_id` canonicalisation shape-matches
  siblings and is idempotent on multi-colon inputs.
- Paper test-count citations consistent; v0.3 meta bumped.
- Hash invariant `_content_hash(_canonicalise_content(c)) ==
  _content_hash(entry.to_dict() − id)` holds by construction and is
  verified numerically.
- Only new finding is NEGLIGIBLE Phase-2 hygiene (two canonicalisation
  code paths that must stay in sync) — does not block freeze.

**No correctness blocker remains.** Tag `v0.1.0a1` can be cut on
`e8347c6`; the bundled artifact's Merkle-audit invariant holds on every
factory-path production chain.

### Recommended post-freeze hygiene (Phase-2, not blocking)

- **R6 N1 follow-up** — consolidate `_canonicalise_content` and
  `AuditEntry.__post_init__` so canonicalisation rules live in one
  place. Either have `__post_init__` call `_canonicalise_content` on
  `dataclasses.asdict(self)` and re-assign fields, or have
  `_canonicalise_content` delegate to an `AuditEntry.__new__`
  construction. The current duplication is safe at freeze but fragile
  under future field additions.
- **R1 N10 carry-forward** — `Datom.__post_init__` non-string `a`
  defensive-lint item, still deferred (Phase-2 as R4 recommended).

---

## 6. Summary

W-polish3 closed the two remaining R5 items cleanly:

- **R5 N1 MAJOR** — `make_audit_handler` factory now canonicalises
  content before hashing via the new `_canonicalise_content` helper
  (`audit.py:295-336`), mirroring the dataclass rules in
  `__post_init__`. Three end-to-end regression tests pin the behaviour.
- **R5 N2 MINOR** — `policy_id` canonicalisation uses `":" +
  lstrip(":")` to match sibling fields and is idempotent on multi-
  colon inputs. One new test covers the idempotency.

579 tests green. Hash invariant verified numerically. No new MAJOR or
MEDIUM defects introduced. One NEGLIGIBLE Phase-2 hygiene note on the
duplication between `_canonicalise_content` and `__post_init__`, but
this is not a correctness break — the two paths are in sync on every
current field.

**Phase 1 freeze: GO.**

## 7. Axis grade: **R1 = 9.4**

Delta vs R5: **+0.4** (9.0 → 9.4). Gate met (≥ 9.3). Go/no-go:
**GO for Phase 1 freeze** on `e8347c6`.

---

*Files inspected (absolute paths):
/Users/nawfalsaadi/Projects/persistence-os/{src/persistence/effect/handlers/audit.py,
tests/effect/test_audit_factory_verify_chain.py,
tests/effect/test_audit_canonicalize.py,
tests/effect/test_audit.py,
paper/persistence-nesy-2026-draft.md,
docs/aris-round-5/R1-correctness.md,
docs/aris-round-6/W-polish3-summary.md}*

*Commands run (evidence):
`source .venv/bin/activate && pytest -q` → 579 passed in 2.45s;
`pytest tests/effect/test_audit_factory_verify_chain.py -v` → 3 passed;
`pytest tests/effect/test_audit_canonicalize.py -v` → 15 passed;
independent 3-entry production-shape factory probe → `verify_chain=True`;
multi-colon `policy_id` idempotency probe → all variants collapse to
`":x"`; `_canonicalise_content` purity + edge-case sweep → no mutations,
all edge cases handled; hash-invariant probe → `factory_hash ==
verify_hash` byte-identical.*
