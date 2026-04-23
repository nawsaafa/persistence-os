# ARIS Round 5 — R1 Correctness Review

**Reviewer:** R1 (Correctness)
**Round:** 5 (FREEZE GATE)
**Base:** `main @ 60b3c85`
**Test state:** 575 passed in 2.46s (verified `source .venv/bin/activate && pytest -q`)
**Predecessors:** R1 7.4 → R2 8.3 → R3 8.6 → R4 9.0
**Target:** ≥ 9.2 for `min ≥ 9.2 → Phase 1 FROZEN clean`.

---

## 0. Method

Every claim below is backed by a named file:line, a passing targeted test,
or a reproducible one-off invocation against HEAD `60b3c85`. I ran the
49 tests most directly exercising the R5 polish (all green), then ran
the full suite (575 green), then manually probed the canonicalisation
invariant table, the `lstrip(":")` edge cases, the provenance symmetry,
and the **`make_audit_handler` factory path that the R5 tests do not
exercise**. That last probe surfaced a NEW MAJOR correctness regression
introduced by W5-audit-canonicalize — documented in §3 R5 N1.

Serena LSP returned "No language servers available" mid-session; I
fell back to grep + Read for symbol navigation. All symbolic references
below are to absolute line numbers in HEAD `60b3c85`.

---

## 1. R4 finding remediation table

| R4 ID | Severity | Remediation | Verdict | Evidence |
|---|---|---|---|---|
| **R1 N7** — `AuditEntry.to_edn()` rejects bare-string `policy_id="bankability-v3"` | MAJOR | W5-audit-canonicalize `d12946d` — `__post_init__` prepends `":"` if missing | **CLOSED** *(but see §3 R5 N1 for a NEW MAJOR regression introduced by the fix)* | `src/persistence/effect/handlers/audit.py:126-128`. Reproduction: `AuditEntry(op=":llm/call", policy_id="bankability-v3", …).to_edn()` no longer raises; wire emits `":audit/policy-id": ":bankability-v3"`. Self-conform passes. Test: `tests/effect/test_audit_canonicalize.py::TestPolicyIdCanonicalizationAtInit::test_bare_policy_id_conforms_to_audit_entry_spec` (green). |
| **R1 N8** — paper §6.6 L365 reads `2026-06-16` contradicting L296 `2026-06-09` | MINOR | W5-paper-patch2 `1e291b5` | **CLOSED** | `grep -n "2026-06-16" paper/persistence-nesy-2026-draft.md` returns zero hits. `grep -n "2026-06-09"` returns three honest uses at L296, L317, L365 — all three now read "2026-06-09" and reference the abstract deadline consistently. W-polish2 also fixed L317 (window reference) in the same patch — a scope-respecting extra since leaving that line on `2026-06-16` while fixing L365 would have created a new internal contradiction. |
| **R1 N9** — `AuditEntry.from_edn ∘ to_edn` mixed handler_chain is *canonicalising, not bit-inverse* | MINOR | W5-audit-canonicalize `d12946d` | **CLOSED** | `__post_init__` now drives canonicalisation at the in-memory boundary. Mixed-form construction (`handler_chain=(":audit", "llm", ":tool")`) canonicalises to `("audit", "llm", "tool")` at object creation. The information-theoretic asymmetry I flagged in R4 goes away because the internal form is single-valued: there is no longer a "before" form that `from_edn ∘ to_edn` fails to recover. Verified with mixed-form probe (see §2 below). |
| **R1 N10** — `Datom.__post_init__` crash on non-string `a` | MINOR | **NOT ADDRESSED** — out of scope for W-polish2 | **DEFERRED** | The R4 finding was flagged as Phase-2 spec-hardening work ("no production call-site should hit this"). W-polish2 did not touch this; `src/persistence/fact/datom.py:88` still guards with `isinstance(self.a, str)` so non-string `a` passes through and `datom_to_wire` will still `AttributeError` on non-string inputs. Defensive-lint item, not a blocker. Preserve the R4 verdict: MINOR, Phase-2. |
| **R1 N11** — `Datom(a="::x")` non-idempotent (strip only one colon) | NEGLIGIBLE→CLOSED | W5-datom-idempotent `a62c16f` | **CLOSED** | `src/persistence/fact/datom.py:88-91` now uses `lstrip(":")` so any number of leading colons collapses uniformly. Reproduction: `Datom(a="::x/y").a == "x/y"`; `Datom(a=":::")` yields empty string without crashing. Tests: `tests/fact/test_datom_idempotent.py` (4 tests, all green). Symmetric fix for `provenance["source"]` at `datom.py:92-104`. |
| **R3 R4-N1** (convergent with R1 N9) — pre-keyworded handler_chain breaks `verify_chain` across round-trip | MEDIUM | W5-audit-canonicalize `d12946d` | **CLOSED** *(for the direct-construction path)* | `tests/effect/test_audit_canonicalize.py::TestFromEdnRoundTripPreservesVerifyChain::test_verify_chain_survives_to_edn_from_edn` (green). Note: this test pre-canonicalises the content dict before hashing (audit_canonicalize.py:183-191). That is NOT what `make_audit_handler` does — see §3 R5 N1. |
| **R3 R4-N2** — principal-keyworded keys break `verify_chain` across round-trip | LOW | W5-audit-canonicalize `d12946d` | **CLOSED** *(direct-construction path)* | Same dataclass canonicalisation; `__post_init__` strips colons from principal keys at `audit.py:142-154`. Same caveat as R4-N1: the factory path is not covered. |
| **R3 R4-N3** — `Datom(a="::x")` non-idempotent | LOW | W5-datom-idempotent `a62c16f` | **CLOSED** | Same fix as N11. Covered by `tests/fact/test_datom_idempotent.py`. |
| **R3 R4-N4** — `_provenance_to_wire` asymmetric on bare-value keyworded-key input | LOW | W5-provenance-symmetry `1105811` | **CLOSED** | `src/persistence/fact/wire.py:61-90`. Dropped the `continue` short-circuit so the value-keywordification branch runs unconditionally. Reproduction: `_provenance_to_wire({":source": "dfi-agent"})` now emits `{":source": ":dfi-agent"}` — symmetric with the bare-key input `{"source": "dfi-agent"}`. Tests: `tests/fact/test_provenance_symmetry.py` (5 tests, green). |

Net: **8 of the 9 R4 findings are closed on the code side** (N10 deferred
to Phase-2 as R4 itself recommended). HOWEVER — the N7 fix introduces a
NEW MAJOR regression on the factory path. See §3.

---

## 2. Canonicalisation invariant verification — `AuditEntry.__post_init__`

Verified by live probe against HEAD `60b3c85`:

| Construction input | Expected internal state | Observed | OK? |
|---|---|---|---|
| `AuditEntry(op=":llm/call", policy_id=None, …)` | `policy_id is None` | `None` | ✅ |
| `AuditEntry(op=":llm/call", policy_id="bankability-v3", …)` | `policy_id == ":bankability-v3"` | `":bankability-v3"` | ✅ |
| `AuditEntry(op=":llm/call", policy_id=":bankability-v3", …)` | `policy_id == ":bankability-v3"` (idempotent) | `":bankability-v3"` | ✅ |
| `AuditEntry(handler_chain=("audit", "llm"), …) == AuditEntry(handler_chain=(":audit", ":llm"), …)` (else equal) | `True` (internal state identical) | `True` | ✅ |
| `AuditEntry(principal={"user": "x"}, …) == AuditEntry(principal={":user": "x"}, …)` (else equal) | `True` | `True` | ✅ |
| `AuditEntry.from_edn(e.to_edn()) == e` on mixed-form entry `(handler_chain=(":audit", "llm", ":tool"), principal={":user": "nawfal", "tenant": "egh"}, policy_id="bankability-v3")` | `True` | `True` | ✅ |

The W-polish2 summary's central claim — "`from_edn ∘ to_edn = id` by
dataclass `==`" — holds on every input I threw at it. `lstrip(":")` over-
strip probe on `":::"` yields empty string without crashing
(`Datom(a=":::")` and `AuditEntry(handler_chain=(":::",))` both land as
empty-string internal form and fail loudly at the `to_edn` self-conform
with a spec error, which is the correct loud-failure behaviour).

---

## 3. New correctness findings in Round 5

### R5 N1 (MAJOR, NEW REGRESSION INTRODUCED BY W5-audit-canonicalize) — `make_audit_handler` factory emits chains where `verify_chain` returns `False`

**Where.** `src/persistence/effect/handlers/audit.py:364-381`:

```python
# (line 363 prev_hash lookup elided)
content: dict[str, Any] = {
    "prev_hash": prev_hash,
    "op": op_name,
    …
    "policy_id": ctx.get("policy_id"),          # bare string from policy_eval.py
    "handler_chain": tuple(ctx.get("handler_chain", ())),
    "principal": dict(ctx.get("principal", {})),
    …
}
entry_id = _content_hash(content)                # hashed over BARE form
entry = AuditEntry(id=entry_id, **content)       # __post_init__ MUTATES fields
ctx["entries"].append(entry)
```

**Why it's broken.** W5-audit-canonicalize added three in-place
canonicalisations at `AuditEntry.__post_init__` (`audit.py:126-154`):

- `policy_id`: bare → keyword (prepend `":"`)
- `handler_chain`: keyword → bare (`lstrip(":")` on each)
- `principal`: keyword-keyed → bare-keyed (`lstrip(":")` on each key)

The factory at `audit.py:379-380` computes `_content_hash(content)`
**before** constructing the `AuditEntry`. `content["policy_id"]` is the
bare string `"bankability-v3"` (per `policy_eval.py:161` which emits
`policy.get("policy/id", "unknown")` — always bare). The hash is
computed over bare form. Then `__post_init__` mutates the entry's
`.policy_id` to `":bankability-v3"`. `verify_chain` later calls
`entry.to_dict()` (which reflects the CANONICALISED form) and
recomputes `_content_hash(d)`, which now differs from `entry.id`.
`verify_chain` returns `False`.

**Reproduction against HEAD `60b3c85`:**

```python
from persistence.effect.handlers.audit import make_audit_handler, verify_chain
from persistence.effect.handlers.raw import make_echo_llm_handler
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.runtime import Runtime, with_runtime, perform

entries = []
audit = make_audit_handler(
    entries,
    wraps={":llm/call"},
    policy_id="bankability-v3",       # bare, PRODUCTION SHAPE
)
rt = Runtime([make_echo_llm_handler(), make_fixed_clock_handler(ts=1_712_000_000), audit])
with with_runtime(rt):
    perform(":llm/call", model="m", messages=[{"role":"user","content":"hi"}])

print(verify_chain(entries))  # → False   (REGRESSION)
```

Output on HEAD:

```
entries: 1
e.policy_id: ':bankability-v3'       # canonicalised
e.id: sha256:7aab25d0f97f29578...    # hashed over bare 'bankability-v3'
verify_chain([e]): False             # MISMATCH
```

Extending to a 2-entry chain still returns `False`. The same shape-of-
defect hits `principal={":user": "x"}` (pre-keyworded principal passed
to the factory): `verify_chain` returns `False` on the resulting chain.

**Why the 575-test suite does not catch this.**

- `tests/effect/test_audit.py` never passes `policy_id=` or a non-
  default `principal=` to `make_audit_handler` (verified via grep:
  `grep -rn "make_audit_handler" tests/effect/test_audit.py | grep
  policy_id` → zero matches).
- The 14 new `tests/effect/test_audit_canonicalize.py` tests only
  verify **direct `AuditEntry(…)` construction** and one round-trip test
  (`test_verify_chain_survives_to_edn_from_edn`) that pre-canonicalises
  the content dict at lines 183-191 before computing `_content_hash` —
  i.e., the test hashes the **post-canonical** form, bypassing the bug.
- The factory path at `audit.py:379-380` hashes the **pre-canonical**
  form.

So the R5 test suite exercises `AuditEntry.__post_init__` behaviour
in isolation, but never via `make_audit_handler`. The regression is
invisible to the suite.

**Why this is MAJOR, not MINOR.**

1. **Proposition 4 of the paper (L163) is now false by construction.**
   Prop 4 states: "For any audit-chain $C$ produced by
   `make_audit_handler`'s Merkle-hashed chain-append clause,
   `verify_chain(C) = True` iff …". On HEAD `60b3c85`, `verify_chain(C) =
   False` for every chain produced by `make_audit_handler(policy_id=<non-
   None bare string>)`. The abstract-submission claim that Prop 4 is
   "already-checked on the shipped artifact" (paper L365) is invalidated
   on the factory-path subdomain, which is the only subdomain any
   production agent enters.

2. **Same class of defect as R4 N7** — an invariant that holds on the
   direct-construction path breaks when the production factory path hits
   it. R4 N7 was graded MAJOR. This is the strictly larger sibling: the
   R4 N7 fix closes the `to_edn` boundary but opens a new break at the
   `verify_chain` boundary on the factory path.

3. **Regulator-replay impact.** Regulator-replay (the whole point of the
   persistence.effect module) walks the audit chain and calls
   `verify_chain`. Every production chain will fail this check as long
   as a policy is loaded. The paper's central effect-module claim —
   tamper-evidence via Merkle-hashed append — is strictly false on the
   shipped artifact at the factory boundary.

**Fix shape (out-of-scope for this review, but trivial):** canonicalise
`content` in `make_audit_handler` BEFORE hashing, mirroring
`__post_init__`. Two surgical options:

```python
# Option A — mirror __post_init__ in the factory (before _content_hash)
content["policy_id"] = (
    None if content["policy_id"] is None
    else (":" + content["policy_id"] if not content["policy_id"].startswith(":")
          else content["policy_id"])
)
content["handler_chain"] = tuple(
    h.lstrip(":") if isinstance(h, str) else h for h in content["handler_chain"]
)
content["principal"] = {
    (k.lstrip(":") if isinstance(k, str) else k): v
    for k, v in content["principal"].items()
}
entry_id = _content_hash(content)
entry = AuditEntry(id=entry_id, **content)

# Option B — construct first, then hash from entry.to_dict()
# (cleaner, but requires accepting that the dataclass canonicalises by contract)
entry = AuditEntry.__new__(AuditEntry)
# … but the dataclass is frozen, so this is uglier than Option A.
```

Recommended shape: Option A. Add a regression test that calls
`make_audit_handler(policy_id="bankability-v3")` end-to-end, runs one
`:llm/call` through the stack, and asserts `verify_chain(entries) is
True`. This is the test the W-polish2 commit should have added and
did not.

**Severity:** MAJOR. This is the exact class of defect — a canonicalisation
invariant that holds on the unit-tested path but fails on the factory
path — that Round 5 was convened to eliminate. It MUST be closed before
freeze.

---

### R5 N2 (MINOR, documentation drift) — sibling canonicalisation asymmetry: `policy_id` uses prepend-if-missing, `handler_chain`/`principal` use `lstrip`

**Where.** `src/persistence/effect/handlers/audit.py:126-154`.

**Observation.** The three canonicalisations have different shapes:

```python
if self.policy_id is not None and isinstance(self.policy_id, str):
    if not self.policy_id.startswith(":"):
        object.__setattr__(self, "policy_id", ":" + self.policy_id)
```

vs.

```python
if self.handler_chain is not None:
    object.__setattr__(self, "handler_chain", tuple(
        h.lstrip(":") if isinstance(h, str) else h for h in self.handler_chain
    ))
```

`policy_id` normalises to keyword form via prepend-if-missing.
`handler_chain` normalises to bare form via `lstrip(":")`. The shapes
are asymmetric: `policy_id="::x"` stays as `"::x"` (startswith-gate
short-circuits) and fails at `to_edn` self-conform; `handler_chain=
(":::x",)` collapses to `("x",)` (idempotent).

**Consequence.** Proposition 4 holds for `handler_chain` idempotency
(`Datom`-style) but NOT for `policy_id` idempotency on multi-colon
inputs. Two `AuditEntry` values with `policy_id="::x"` and `policy_id=":x"`
compare unequal even though both serialise to the same spec-rejecting
wire form.

**Why low severity.** Real `policy_eval.py` emits bare strings only
(`src/persistence/effect/policy_eval.py:161`). No production call-site
hits multi-colon `policy_id`. The paper's Prop 4 formal claim does not
mention idempotency on multi-colon inputs, so this is a spec-boundary
hygiene item rather than a correctness claim break.

**Fix shape (Phase-2 or R5 polish3, if there is one):** canonicalise
`policy_id` with `":" + self.policy_id.lstrip(":")` so the invariant is
uniformly "all leading colons → exactly one leading colon". Symmetric
with how `handler_chain` handles "all leading colons → zero leading
colons."

**Severity:** MINOR. Documentation drift, not production correctness
break. Flagged for Phase-2 hygiene pass.

---

### R5 N3 (NEGLIGIBLE) — edge-case probes yielded no crashes

Probed systematically per the deliverable checklist:

- `":::"` → `lstrip(":")` → `""` — empty string, no crash. Both `Datom.a`
  and `AuditEntry.handler_chain[i]` accept this; `to_edn` raises the
  spec error loudly. ✅
- Empty-string `policy_id=""` → becomes `":"` after canonicalisation;
  `to_edn` raises on spec conform. Loud failure. ✅
- Whitespace-only `policy_id="   "` → becomes `":   "`; `to_edn` raises
  on spec conform. Loud. ✅
- Leading-whitespace `policy_id=" bankability-v3"` → becomes
  `": bankability-v3"`; `to_edn` raises. Loud. ✅
- `handler_chain=("",)` (tuple-of-empty-strings) → stays `("",)`; `to_edn`
  raises on spec conform. Loud. ✅
- `_provenance_to_wire` symmetry probe: `{":source": "dfi"}` and
  `{"source": "dfi"}` now both emit `{":source": ":dfi"}`. Symmetric. ✅
- `_provenance_from_wire(_provenance_to_wire(p))` is the identity on
  both key- and value-keywordification inputs. ✅

None of these surface NEW defects. All "suspicious shapes" either
canonicalise to a sentinel empty form or fail loudly at self-conform.

**Severity:** None — reported as positive evidence.

---

## 4. Overall correctness grade

### Deltas relative to R4 (9.0)

| Axis of correctness | R4 state | R5 state | Δ |
|---|---|---|---|
| R1 N7 `policy_id` bare-string `to_edn` rejection (MAJOR) | open | closed via `__post_init__` canonicalisation at construction | +0.3 |
| R1 N8 paper L365 date contradiction (MINOR) | open | closed (all three occurrences consistent) | +0.1 |
| R1 N9 `from_edn ∘ to_edn` canonicalising-not-inverse (MINOR) | open, information-theoretic | closed via single-valued internal form | +0.05 |
| R1 N10 Datom non-string `a` defensive lint (MINOR) | open, deferred | unchanged, still deferred | 0.0 |
| R1 N11 Datom `"::x"` non-idempotent (NEGLIGIBLE) | open | closed via `lstrip(":")` | +0.05 |
| R3 R4-N1 pre-keyworded handler_chain verify_chain break (MEDIUM) | open | closed (direct-construction path) | +0.15 |
| R3 R4-N2 pre-keyworded principal verify_chain break (LOW) | open | closed (direct-construction path) | +0.05 |
| R3 R4-N4 `_provenance_to_wire` asymmetry (LOW) | open | closed | +0.05 |
| Proposition 4 paper phantom `append_audit_entry` (R4 drift) | open | closed — paper names `make_audit_handler`'s Merkle-hashed chain-append clause | +0.05 |
| §4.5 intervention-set definition drift (R4 drift) | open | closed — `I` now defined as a list | +0.03 |
| **NEW R5 N1** — `make_audit_handler` factory break (MAJOR, introduced by the N7 fix) | n/a | OPEN | **−0.5** |
| NEW R5 N2 — sibling canonicalisation asymmetry (MINOR) | n/a | OPEN | −0.05 |

**Net:** 9.0 + 0.3 + 0.1 + 0.05 + 0.05 + 0.15 + 0.05 + 0.05 + 0.05 + 0.03 − 0.5 − 0.05 = **9.23**

Hmm — arithmetically this clears the 9.2 bar. But the **weighted** grade
accounting for the severity of the regression (Prop 4 of the paper is
currently false-by-construction on every production agent path) is lower.
A MAJOR regression that invalidates a paper Proposition on the shipped
artifact is weightier than a pure point-deduction.

**Final grade: R1 = 9.0** (held at R4's level).

Rationale for holding at 9.0 rather than rising:

- 9 of the 10 R4 findings closed cleanly (big improvement).
- But the headline fix (N7 — the MAJOR one that convened R5) creates
  a NEW MAJOR regression in a larger subdomain (`make_audit_handler`
  factory path, which is strictly more touched by production than the
  direct-construction path the R5 tests exercise).
- The new regression invalidates Prop 4 on the paper's artifact-
  reproducibility claim. This is the exact class of defect R5 was
  convened to eliminate.

A freeze at 9.0 with R5 N1 open is not a clean freeze — it is R4's
situation with a different MAJOR.

---

## 5. Go / no-go for Phase 1 freeze

### **NO-GO on Phase 1 freeze.**

R5 N1 must be closed before freeze. The fix is mechanical and small —
canonicalise `content` inside `make_audit_handler` (before
`_content_hash`) so the hash is computed over the same shape the
dataclass will settle into post-construction.

### Recommended path — W5-polish3 (30 minutes, one commit)

One commit on a `W-polish3` branch, targeting exactly:

1. **`src/persistence/effect/handlers/audit.py:364-381`** — before
   `_content_hash(content)`, apply the same canonicalisation
   `__post_init__` applies. Mirror the three fields: `policy_id`
   (prepend-if-missing), `handler_chain` (`lstrip(":")` each),
   `principal` keys (`lstrip(":")` each).

2. **New file `tests/effect/test_audit_factory_verify_chain.py`** — end-
   to-end factory regression test. Three tests:

   - `test_verify_chain_true_on_factory_with_bare_policy_id` —
     `make_audit_handler(policy_id="bankability-v3")` → one
     `:llm/call` → `verify_chain(entries) is True`.
   - `test_verify_chain_true_on_factory_with_keyworded_principal` —
     `make_audit_handler(principal={":user": "x"})` → one
     `:llm/call` → `verify_chain(entries) is True`.
   - `test_verify_chain_true_on_factory_with_bare_chain_and_policy` —
     combined, multi-entry (at least 3 entries to exercise the
     chain-prev_hash walk).

3. **Optional**: tighten `test_audit_canonicalize.py::test_verify_chain
   _survives_to_edn_from_edn` to ALSO hash over the pre-canonical content
   and assert BOTH paths produce the same id. This catches the class of
   defect at the unit-test layer so future regressions on the hash-
   before-canonicalisation boundary are caught locally.

After W5-polish3, R1 grade rises to **9.3-9.4** cleanly, and the freeze
bar is unambiguously met.

### Alternative — freeze with known issue

If there is genuine schedule pressure to freeze today, R5 N1 can be
documented as a known issue in the Phase-1 changelog with an explicit
note that Prop 4 on the shipped artifact is subject to the caveat
"`policy_id` must be pre-canonicalised by the caller before
`make_audit_handler`." This is ugly — it contradicts the paper's factory-
reproducibility claim — and I do not recommend it. The 30-minute fix
closes it cleanly.

---

## 6. Summary

Round 5 W-polish2 closed 8 of 9 R4 findings on the code side and all 3
paper-drift residuals (dates, Prop 4 phantom, §4.5 intervention
definition). 575 tests green. Canonicalisation invariants verified
end-to-end on direct `AuditEntry` construction.

But the headline fix — W5-audit-canonicalize's `__post_init__`
canonicalisation of `policy_id`, `handler_chain`, `principal` — has a
hash-before-canonicalisation regression on the `make_audit_handler`
factory path. Every production audit chain with a set `policy_id` now
fails `verify_chain`. The paper's Prop 4 is false-by-construction on the
factory subdomain. The 575-test suite misses it because the new tests
exercise direct construction, not the factory path.

One 30-minute W-polish3 fix closes it. Strong recommendation: do not
freeze until that commit lands and a factory regression test is green.

## 7. Axis grade: **R1 = 9.0** (held, not risen)

Delta vs R4: 0.0 (9.0 → 9.0). Gate met on arithmetic (9.23 raw), held
back by severity weighting of R5 N1 (paper-Proposition invalidation on
the artifact's production path). Go/no-go: **NO-GO for Phase 1 clean
freeze without W-polish3**; after W-polish3, grade rises to 9.3+ and
freeze is unambiguously safe.

---

*Files inspected (absolute paths): /Users/nawfalsaadi/Projects/persistence-os/{src/persistence/effect/handlers/audit.py, src/persistence/fact/datom.py, src/persistence/fact/wire.py, tests/effect/test_audit_canonicalize.py, tests/effect/test_audit_from_edn.py, tests/effect/test_audit_self_conform.py, tests/effect/test_audit.py, tests/effect/test_handler_chain_wire.py, tests/fact/test_datom_idempotent.py, tests/fact/test_provenance_symmetry.py, tests/fact/test_wire_identity.py, paper/persistence-nesy-2026-draft.md, docs/aris-round-4/R1-correctness.md, docs/aris-round-5/W-polish2-summary.md}*
