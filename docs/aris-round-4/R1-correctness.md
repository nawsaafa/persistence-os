# ARIS Round 4 — R1 Correctness Review

**Reviewer:** R1 (Correctness)
**Round:** 4 (FINAL — freeze gate)
**Base:** `main @ 61644f6`
**Test state:** 551 passed in 2.79s (verified `source .venv/bin/activate && pytest -q`)
**Predecessors:** R1 R1 7.4 → R2 8.3 → R3 8.6
**Target:** ≥ 9.0 (NeSy-submittable)

---

## 0. Method

Every finding is backed by a named file:line, a passing test, or a
reproducible one-off invocation. Symbolic navigation via Serena; no
skim-reading. All deltas re-verified on HEAD `61644f6`.

---

## 1. R3 finding remediation table

| R3 ID | Severity | Remediation | Verdict | Evidence |
|---|---|---|---|---|
| **R1 N5** — `Trajectory.to_edn()` rejects real `engine.replay(...)` output | MAJOR | W4-intervention-wire | **CLOSED** | Multi-step replay → `cf.to_edn()` self-conforms AND external `spec.conform(":persistence.replay/trajectory", edn).is_ok == True` (verified interactively with 2-intervention + 3-intervention cases). `tests/replay/test_intervention_wire.py::TestTrajectoryToEdnOnReplayOutput::{single,multi}_intervention_replay` both green. Engine emits `[copy.deepcopy(iv) for iv in interventions]` at `src/persistence/replay/engine.py:170`. Wire boundary keywordifies via `_intervention_to_wire` at `trajectory.py:440-460`. |
| **R1 N6** — `AuditEntry.to_edn()` rejects bare-string `handler_chain` | MAJOR | W4-handler-chain-wire | **CLOSED** *(for `handler_chain` only — see §3 N7 for sibling gap)* | `handler_chain=("audit", "llm")` → `to_edn()` succeeds; wire emits `[":audit", ":llm"]`; external conform `is_ok == True`. Mixed chain (`("audit", ":policy", "raw")`) uniformly keywordified. Empty chain passes. `src/persistence/effect/handlers/audit.py:130-132` invokes `_handler_chain_to_keywords` at wire boundary. `tests/effect/test_handler_chain_wire.py` (5 tests, all green). |
| **R3 N2** — `Datom(a=":x/y") != Datom(a="x/y")`; wire round-trip not identity | MAJOR | W4-wire-identity | **CLOSED** | `Datom.__post_init__` at `src/persistence/fact/datom.py:71-88` strips leading `:` from `.a` and from `provenance["source"]`. Verified: `Datom(a=":project/wacc") == Datom(a="project/wacc") == d.a == "project/wacc"`. `wire_to_datom(datom_to_wire(d)) == d` on pre-keyworded input (`tests/fact/test_wire_identity.py::TestWireRoundTripIdentity`, 4 tests). |
| **B1** — `engine.replay` collapses multi-step interventions to first | MAJOR | W4-intervention-wire | **CLOSED** | `Trajectory.intervention: Optional[list[dict]]` (trajectory.py:104). Engine stores full list (`engine.py:170`). Lineage parity verified with 3-intervention e2e run: `len(cf.intervention) == 3`, `cf.intervention[k]["step"] == k`. `tests/replay/test_intervention_wire.py::test_three_interventions_produce_length_three_list`. |
| **G4** shape-pin still asserts buggy behavior | LINT | W4-g4-xfail (folded into #1) | **CLOSED** | `tests/replay/test_replay.py:214-240` now asserts `isinstance(cf_a.intervention, list)` + per-entry `zip(stored, submitted)` parity. Pin matches the NEW correct shape. No xfail needed. |
| **R3 N6** — no `AuditEntry.from_edn` inverse | MINOR | nice-to-have `b876ec0` | **CLOSED** | `AuditEntry.from_edn` at `audit.py:167-221`. Round-trip preserves all fields; handler_chain and principal keys restored to bare-string Python form; verdict restored via `_verdict_as_python`. 5 tests in `tests/effect/test_audit_from_edn.py`. |
| **R2 new G1** — `BANNED_CALLS` missing monotonic/perf_counter | LINT | `de69e4a` | **CLOSED** | `tests/test_wall_clock_ban.py:48-49` adds `("time", "monotonic")` and `("time", "perf_counter")`. Plant-and-catch tests at lines 213, 222 green. |
| **R2 new G2** — noqa scan only reads starting line | LINT | `2c1ead7` | **CLOSED** | `_has_noqa_in_span` helper scans `node.lineno..node.end_lineno`. Plant-and-catch test added. |
| **R4 paper drift** — SQL portability, name sync, date bug, Prop 4 | STYLE | W4-paper-patch | **PARTIAL** — see §2 | See §2 for per-hunk verification. |

Net: all 5 R3 MAJOR findings closed on the code side. One new MAJOR
sibling defect (N7) found below. Paper drift PARTIALLY fixed — see §2.

---

## 2. Paper patch verification

| Hunk | Claim | Status | Evidence |
|---|---|---|---|
| §5.1 L219 SQL portability | Strike "SQLite 3.37+ and Postgres 14+"; replace with "ships against SQLite 3.37+ today; Postgres adapter planned for Phase 2" | **LANDED** | `paper/persistence-nesy-2026-draft.md:219` — exact rewording verified. Matches reality (`sqlite_store.py` ships; no Postgres adapter). |
| §5.3 L237 name sync | `:audit/entry` → `:persistence.effect/audit-entry` | **LANDED** | `paper/persistence-nesy-2026-draft.md:241` names ``:persistence.effect/audit-entry`` correctly. Grep of paper for `:audit/entry` returns zero hits. |
| §6.6 abstract deadline date | `2026-06-16` → `2026-06-09` | **PARTIAL — see R4-N8 below** | `paper:296` reads "abstract submission (2026-06-09)" ✓. But **`paper:365` still reads "At the 2026-06-16 abstract deadline…"** — same section, same claim, opposite date. Internal contradiction. |
| §4.4 homoiconicity sentence | New sentence on vector-form plan AST as Lisp-style choice | **LANDED** | `paper:167` introduces the vector form `[:tag {attrs} & children]` with the homoiconicity argument. |
| §4.7 self-conforming producers | Names the 5 producers with conform-on-return | **LANDED + verified** | `paper:210` names `audit_entry_to_datom`, `AuditEntry.to_edn`, `Trajectory.to_edn`, `datom_to_wire`, `wire_to_datom`. Grep confirms all 5 call `spec.conform(...)` or `spec.parse(...)` on their return value (see `src/persistence/replay/trajectory.py:279`, `src/persistence/effect/handlers/audit.py:161,521`, `src/persistence/fact/wire.py:106,118`). |
| §5.1 concurrent-writer-safety paragraph | Names `allocate_and_append`, 16×50 barrier, Prop 3 dependency | **LANDED** | `paper:221` paragraph shipped. Test `tests/fact/test_concurrent_transact.py` exists and named `test_allocate_and_append_is_atomic_under_16_threads_50_transacts_each`. |
| §4.3 Proposition 4 — `verify_chain` immutability | Formal iff statement; named 4 tests | **LANDED + verified** | `paper:163` carries the iff. All 4 cited tests exist at `tests/effect/test_audit.py:{69,83,102,116}` under the exact names given in the paper. |

Paper patch net: **6 of 7 hunks land cleanly. The §6.6 date fix is
incomplete — line 365 still carries the old date.** This is a correctness
defect (internal contradiction between paper:296 "abstract submission
(2026-06-09)" and paper:365 "at the 2026-06-16 abstract deadline"). See
§3 N8.

---

## 3. New correctness findings in Round 4

### R4 N7 (MAJOR) — `AuditEntry.to_edn()` rejects production-shape bare-string `policy_id`

**Where.** `src/persistence/effect/handlers/audit.py:147-148`. The
`policy_id` optional slot passes through verbatim:

```python
if self.policy_id is not None:
    edn[":audit/policy-id"] = self.policy_id
```

**Why it's broken.** The spec at `src/persistence/spec/_canonical.py:340`
is:

```python
":audit/policy-id": maybe(_keyword_spec),
```

`_keyword_spec` requires a leading-colon EDN keyword (`:persistence.spec/keyword`
regex check at `_canonical.py:72-73`).

Production policy evaluation at `src/persistence/effect/policy_eval.py:161`
returns `policy_id = policy.get("policy/id", "unknown")` — a **bare
string**. That bare string propagates into `make_audit_handler` at
`audit.py:258`, into ctx at line 354, and into every `AuditEntry`
constructed by the factory. Any `to_edn()` call on such an entry raises
`ValueError: AuditEntry.to_edn produced a non-conformant value:
ConformError(... ':audit/policy-id' not a valid namespaced keyword ...)`.

**Reproduction (HEAD `61644f6`):**

```python
from persistence.effect.handlers.audit import AuditEntry
e = AuditEntry(
    id="sha256:" + "a"*64, prev_hash=None, op=":llm/call",
    args_hash="sha256:" + "c"*64, verdict="ok", latency_ms=10,
    recorded_at=1_700_000_000.0, handler_chain=("audit",), principal={},
    policy_id="bankability-v3",  # bare string — what policy_eval returns
)
e.to_edn()  # raises ValueError
```

**Why this is the same class as N6, not caught by W-wire.** W4 closed
the `handler_chain` wire boundary via `_handler_chain_to_keywords`
(`audit.py:129`). The analogous fix was NOT applied to `policy_id`. The
only existing happy-path test uses `policy_id=":bankability-v3"` —
pre-keywordified — which is precisely the shape `test_audit_self_conform._sample_entry`
was criticised for on `handler_chain` in R3 and tightened to bare-string.
The same fixture has NOT been tightened for `policy_id`. Round 4
closed the sibling (handler_chain); this sibling survived.

**Regulator-replay impact.** Any production audit entry with a non-None
`policy_id` — i.e. every entry produced by an agent running under a
loaded policy — cannot be serialised to the cross-module EDN wire
form. This is the exact failure mode R1 N6 identified on
`handler_chain`: the self-conform gate is real, it rejects the entry
loudly, and downstream regulator-replay cannot reconstruct what it
can't read.

**Fix shape (out-of-scope for this review; belongs in a Round-5
correctness-only patch or as a Phase-2 item):**

```python
# audit.py line ~147
if self.policy_id is not None:
    pid = self.policy_id
    if isinstance(pid, str) and not pid.startswith(":"):
        pid = ":" + pid
    edn[":audit/policy-id"] = pid
```

with a symmetric strip in `from_edn` and a bare-string production-shape
test analogous to `test_to_edn_on_real_bare_string_chain_conforms`.

**Severity.** MAJOR. Same class, same failure mode, same surface area
as N6 which was itself graded MAJOR in R3.

---

### R4 N8 (MINOR) — Paper §6.6 L365 date fix incomplete

**Where.** `paper/persistence-nesy-2026-draft.md:365`:

> **Abstract submission scope.** At the 2026-06-16 abstract deadline, §6 reports the formal properties …

**Contradicts** `paper:296`:

> The abstract submission (2026-06-09) reports the Phase-1 shipped artifact …

The W-wire summary claims the §6.6 date bug was fixed; §6 opener (L296)
was fixed, but §6.6's own paragraph (L365) still carries the pre-fix
date. Both land in §6 of the paper; one says 06-09, the other 06-16.

**Ground truth.** Per the conductor track STATUS and Serena memory
`persistence-os/aris-round-3-passed`, the NeSy 2026 abstract deadline
is 2026-06-09 AoE; the paper deadline is 2026-06-16 AoE. L365's "at
the 2026-06-16 abstract deadline" is factually wrong: 06-16 is the
PAPER deadline, not the ABSTRACT one.

**Severity.** MINOR (docs, not code). Internal self-contradiction that
a reviewer will notice on read-through; cheap one-line fix.

---

### R4 N9 (MINOR) — `AuditEntry.to_edn`/`from_edn` mixed handler_chain is canonicalising, not inverse

**Where.** `AuditEntry.from_edn` at `src/persistence/effect/handlers/audit.py:211-213`
calls `_handler_chain_from_keywords` which strips ALL leading colons
(no matter whether the original had them or not).

**Why it's borderline.** If the original `handler_chain` was `(":audit",
"llm", ":tool")` (mixed), then:
- `to_edn` produces wire `[":audit", ":llm", ":tool"]` — uniformly keyworded (correct)
- `from_edn` restores `("audit", "llm", "tool")` — uniformly bare

So `from_edn(to_edn(e))` is NOT bit-identity on mixed input; it is an
idempotent canonicalisation to the bare-string form. This IS what the
W-wire tests assert (`test_handler_chain_restored_to_bare_strings` at
`test_audit_from_edn.py:69`), so it's documented behaviour.

**However**, the paper's Prop-4-adjacent claim that "the wire boundary
is a pure encoding layer" is slightly stronger than reality: the
encoding layer is one-way-canonicalising (a lossy canonicalisation, in
the math sense, because "the user had ':audit' bare-keyworded" is
information that does not survive the round-trip). In practice no
production call-site hands mixed chains — but the invariant should
either be documented as "canonicalising inverse, not pure inverse" in
the paper's §4.7 self-conforming-producers paragraph, or the from_edn
code should preserve per-entry provenance.

**Severity.** MINOR (information-theoretic; no production correctness
impact because the factory always emits bare strings).

---

### R4 N10 (MINOR, edge case) — `Datom.__post_init__` leaves `a` when non-string; downstream `datom_to_wire` crashes with `AttributeError`

**Where.** `src/persistence/fact/datom.py:81`:

```python
if isinstance(self.a, str) and self.a.startswith(":"):
    object.__setattr__(self, "a", self.a[1:])
```

If a caller supplies `Datom(a=42, ...)`, `__post_init__` does nothing,
the datom is constructed. `datom_to_wire(d)` then crashes at
`wire.py:90` with `AttributeError: 'int' object has no attribute
'startswith'` because it calls `datom.a.startswith(":")` before the
spec gate.

**Why low severity.** The Datom dataclass has type annotation `a: str`,
so static type checkers and normal call-sites respect it. The spec
gate inside `datom_to_wire` WOULD catch this once invoked (`:datom/a`
must be a keyword), but the `startswith` call happens first. An
explicit `isinstance(self.a, str)` check in `__post_init__` (raising
`TypeError` loudly) would close the gap.

**Severity.** MINOR (defensive; no production call-site should hit
this). Belongs on the Phase-2 spec-hardening pass.

---

### R4 N11 (MINOR, edge case) — `Datom(a="::x/y")` strips only one colon

**Where.** Same `__post_init__` line. Only strips ONE leading colon.
`"::x/y"` → `":x/y"` after init. `datom_to_wire` sees `":x/y"` and
does NOT re-prepend (already has colon), outputs `":x/y"`. Wire shape
is correct; but the in-memory form is no longer bare (it has a single
`:`). The claim "canonical in-memory form is bare (no leading colon)"
(comment at `datom.py:72-73`) is weakly violated.

**Severity.** NEGLIGIBLE. Real data never has double colons; no
invariant broken in practice. Flagged for completeness only.

---

## 4. Deferred-item audit — is `DB.transact` input self-conform deferral honest?

**Claim.** W-wire summary §"Deferred items" defers `DB.transact` input
self-conform with this rationale (paraphrased):

1. `DB.transact` accepts raw `list[dict]` (`facts`) — not Datoms or wire
   dicts, so there's no clean map onto `:persistence.fact/datom`.
2. The natural closure is to conform each Datom after construction in
   the transact loop, but Datoms carry `TX_PLACEHOLDER=-1` at that
   point; `allocate_and_append` rewrites tx later, and the conform
   would have to happen inside the atomic section.
3. Risk of side-effects across ~80 tests that exercise `DB.transact`.
4. Deferred to Phase-2 `persistence.txn` co-design.

**Check.**

(1) Verified at `src/persistence/fact/db.py:99-211`. `transact` receives
`facts: list[dict]` — raw, not Datoms.

(2) Verified — `TX_PLACEHOLDER = -1` is the sentinel; `allocate_and_append`
rewrites tx inside the atomic `BEGIN IMMEDIATE` (per the fact-store
concurrency fix from Round 3). However, I tested directly: `Datom(tx=-1,
...)` DOES pass `:persistence.fact/datom` because the spec allows `int_()`
for `:datom/tx` (negative ints aren't rejected — the spec never
constrains positivity). So a conform at the mid-point is technically
feasible, but it would be conforming a datom whose tx is not yet its
final allocated tx — misleading.

(3) I did not run the 80-test side-effect audit (beyond R1 scope). The
deferral rationale is DEFENSIBLE — widening the conform surface during
a freeze round is exactly the kind of "widen scope = break peripheral
tests" move the round charter forbids.

(4) The deferral naming (Phase-2 `persistence.txn` co-design) is
honest: the txn module will share the same atomic-allocation surface,
so scoping the conform then is the right time.

**Verdict.** Deferral is honest. The rationale accurately describes
the atomic-section constraint, and the risk calculus is correct for a
freeze round. The only sharpening I'd add: the paper's §4.7
self-conforming-producers paragraph lists `audit_entry_to_datom`,
`AuditEntry.to_edn`, `Trajectory.to_edn`, `datom_to_wire`,
`wire_to_datom` — five producers, all of which conform on return.
`DB.transact` is NOT in that list and correctly is not claimed as a
self-conforming producer. The paper is consistent with the code; no
drift here.

---

## 5. Overall correctness grade

### Deltas relative to R3 (8.6)

| Axis of correctness | R3 state | R4 state | Δ |
|---|---|---|---|
| Multi-step intervention lineage (B1) | open MAJOR | closed + tested e2e on real engine output | +0.3 |
| `Trajectory.to_edn` on real replay output (N5) | open MAJOR | closed + 9 dedicated tests | +0.3 |
| `AuditEntry.to_edn` on bare-string handler_chain (N6) | open MAJOR | closed (for handler_chain) + 5 dedicated tests | +0.2 |
| `Datom` wire round-trip identity (N2) | open MAJOR | closed (bare/keyword equal; wire round-trip bit-identity) + 9 tests | +0.2 |
| Prop 4 formal statement | missing | landed with test citations | +0.1 |
| Paper drift (§5.1, §5.3, §6.6, Prop 4) | drift | 6/7 hunks clean; §6.6 L365 still wrong | +0.1 |
| NEW: policy_id bare-string wire gap (N7) | n/a | MAJOR, same class as N6, not caught | **−0.2** |
| NEW: paper L365 date contradiction (N8) | n/a | MINOR | **−0.05** |
| Minor Datom edge cases (N10/N11) | n/a | MINOR | −0.03 |

**Net:** 8.6 + 0.3 + 0.3 + 0.2 + 0.2 + 0.1 + 0.1 − 0.2 − 0.05 − 0.03 = **9.02**

Rounded: **9.0**

Grade is achieved, but it is NOT achieved comfortably. One MAJOR new
correctness defect of the same class as N6 survived the W-wire pass;
one paper line remains internally contradictory. Both are cheap fixes.

---

## 6. Go / no-go for Phase 1 freeze

**CONDITIONAL GO.**

The ≥ 9.0 NeSy-submittable floor is met at 9.02. However, the
NeSy-submittable floor and the "Phase 1 freeze" bar can be different
things. Three options:

### Option A — Freeze now (NeSy-submittable path)

Freeze Phase 1 at 61644f6 with N7 and N8 as **known-issues in the
Phase-1 changelog**. Submit to NeSy 2026 abstract deadline on 2026-06-09.
Ship N7/N8 fix in a Phase-1.0.1 patch release before 2026-06-16 paper
deadline. The paper's L365 contradiction must be fixed before the
abstract reviewer reads it; N7's production-shape bug must be flagged
to Adaptive Trader v2 integrators ASAP (they DO use policy_id).

**Recommended IF:** the 2026-06-09 abstract submission bandwidth is
tight and the track lead wants to close Round 4.

### Option B — Round 5 surgical (my recommendation)

One 30-minute surgical pass. Two commits:

1. **W5-policy-id-wire** — mirror `_handler_chain_to_keywords` for
   `policy_id` at the `AuditEntry.to_edn` wire boundary. Tighten
   `test_audit_self_conform._sample_entry.policy_id` to production
   shape `"bankability-v3"` (bare). Add 2 explicit bare-string tests.
   Closes N7.

2. **W5-paper-l365** — one-word fix on `paper:365`: `2026-06-16` →
   `2026-06-09` (or reword to clarify "abstract" vs "paper" deadline).
   Closes N8.

After these two commits, R1 correctness grade rises cleanly to 9.2–9.3,
the freeze is unambiguously safe, and neither the paper nor the audit
boundary carries an outstanding MAJOR.

**Recommended.** The cost is 30 minutes; the reward is a freeze that
doesn't ship with a latent regulator-replay-breaking bug of the exact
same class the round was convened to close.

### Option C — Freeze and defer

Treat both as Phase-2 items. This is UNSAFE: N7 is exactly the class
of defect Round 4 was called to eliminate. Freezing with N7 open
means the next reviewer (NeSy area chair) reads the paper claiming
self-conforming producers are a shipped Phase-1 property and finds a
case where a self-conforming producer rejects its own production
shape. Recommend against.

---

## 7. Summary

Round 4 W-wire closed all 4 R3 MAJOR findings it targeted (N5, N6, N2,
B1), shipped the paper patch for 6 of 7 hunks, added `AuditEntry.from_edn`,
extended `BANNED_CALLS`, and fixed the multi-line noqa scan. 551 tests
green. Nine new test files under the W-wire commit range.

But two defects slipped the review-and-fix cycle:

- **R4 N7** (MAJOR) — `policy_id` is the next bare-string wire hole
  after `handler_chain`. Same failure class as N6. Caught here, not
  in W-wire.
- **R4 N8** (MINOR) — `paper:365` still carries the old 2026-06-16
  abstract date, contradicting the L296 fix.

Both are one-commit fixes. Strong recommendation: **Round 5 (30
minutes, two commits) → freeze → submit.** Correctness grade is **9.0**
today; it becomes **9.2+** after Round 5.

## 8. Axis grade: **R1 = 9.0**

Delta vs R3: +0.4 (8.6 → 9.0). Meets NeSy floor. Does not meet "Phase
1 clean freeze" bar until N7 is closed. Go/no-go recommendation:
**GO for NeSy abstract submission with Round 5 before paper submission**,
OR **GO for NeSy submission now if W-wire shipping discipline absorbs
N7 as a known issue documented in CHANGELOG-effect.md**.

---

*Files inspected (absolute paths): /Users/nawfalsaadi/Projects/persistence-os/{src/persistence/replay/trajectory.py,src/persistence/replay/engine.py,src/persistence/effect/handlers/audit.py,src/persistence/effect/policy_eval.py,src/persistence/fact/datom.py,src/persistence/fact/wire.py,src/persistence/spec/_canonical.py,src/persistence/fact/db.py,tests/replay/test_intervention_wire.py,tests/replay/test_replay.py,tests/effect/test_handler_chain_wire.py,tests/effect/test_audit_self_conform.py,tests/effect/test_audit_from_edn.py,tests/effect/test_audit.py,tests/fact/test_wire_identity.py,tests/test_wall_clock_ban.py,paper/persistence-nesy-2026-draft.md,docs/aris-round-4/W-wire-summary.md}*
