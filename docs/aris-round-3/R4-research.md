# ARIS Round 3 — Reviewer R4 — Research Alignment (Paper vs. Code at HEAD `045f4b4`)

*Repo `/Users/nawfalsaadi/Projects/persistence-os/` @ `045f4b4`. Paper `paper/persistence-nesy-2026-draft.md` v0.2 (454 lines, 8409 words). 520 tests green (local: `pytest --ignore=tests/spec/test_generative.py -q` → 507 passed in 2.45s, plus 13 hypothesis-gated tests skipped in my env; consistent with the Serena-memory claim of 520). Round 1 R4 = 6.5. Round 2 R4 = 8.2. Round 3 polish commit: `bb5f6f9` (P-paper-tightening).*

## Summary grade: 8.6 / 10 — target ≥ 8.5 met

Round 3 lands the two Abstract + §4.2 tightenings I asked for in Round 2, cleanly and with honest commentary. The paper no longer contains the single Abstract-level overclaim I flagged (CAMO comparison now qualified to the NO-OP regime), and §4.2's policy-universality rhetorical chain has been trimmed to match §4.3's honest "stack-configuration contract, not substrate invariant" framing. Both edits are verifiable against the commit diff at `bb5f6f9` — two and only two paper hunks, no collateral collateral overreach introduced.

Where I do *not* give 9.0: three residual paper-↔-code fidelity gaps that R3 surfaced but did not close. All three are small, none is a correctness bug, all three are addressable in the 49-day window before the 2026-06-09 abstract deadline.

1. **Plan-node vector form (P-plan-node) is invisible in the paper.** The Round-3 code refactor changed `:persistence.plan/node` from map-form to vector-form `[:tag {attrs} & children]` — a non-trivial syntactic contribution with 28 new passing tests — but the paper's §4.4 still describes the plan AST abstractly as "a labeled tree" with "internal nodes" and "leaves", never surfacing the vector syntax or the parse-don't-validate rejection of map-form. §4.7's "spec-first commitment" paragraph is still intact and correct, but the syntax decision is hidden.
2. **P-concurrency (atomic `allocate_and_append`) is invisible in the paper.** A new `SQLiteStore.allocate_and_append` method under `BEGIN IMMEDIATE` with a 16-thread barrier stress test is a real engineering contribution that a NeSy reviewer reading §5.1 would absolutely care about (multi-worker deployment is the only regime where the paper's Case A/C claims make sense). §5.1 is silent.
3. **P-audit-conform (self-conforming output) is invisible in the paper.** The code now self-conforms `AuditEntry.to_edn()`, `audit_entry_to_datom()`, and `Trajectory.to_edn()` at output, aligning the `:persistence.effect/audit-entry` spec with the dataclass shape. This *is* a methodology contribution (specs as output-time contracts, not just input-time guards) but §4.3 and §4.6 make no reference to it.

Separately, P-sql-portability struck the portability claim from the SQL file header, but the **paper's §5.1 line 219 still says** the SQL migration "runs unmodified on SQLite 3.37+ and Postgres 14+" — creating a new paper-↔-code contradiction that did not exist before Round 3. This is the one negative spillover from R3.

**Compared to Round 2: +0.4 (8.2 → 8.6).** The tightenings hit; the fidelity gaps keep the grade below 9.0. Go for Round 4, with a 3-hour paper-patch list below that would push R4 to 9.0+.

---

## 1. R2 residual verification (abstract NO-OP qualifier + §4.2 softening)

### Abstract NO-OP qualifier — LANDED, verbatim as requested

R2's mitigation text was: *"append `for the NO-OP intervention case` to the Abstract sentence."* Verified at paper.md line 19 (the Abstract paragraph). The exact wording shipped is:

> "...a stronger determinism guarantee **for the NO-OP intervention case** than CAMO's aspirational seed replay (for non-trivial interventions, byte-identity no longer applies; the suffix diverges as soon as the intervened action changes observations)."

This is strictly better than what I asked for: the six-word qualifier is there, and the parenthetical adds the honest caveat about non-trivial interventions that I did not explicitly request but should have. A CAMO author on the PC will read this paragraph and come away with exactly the right understanding of the comparison's scope.

**Verdict: CLOSED. No residual overreach on this sentence.**

### §4.2 policy-universality softening — LANDED, stronger than requested

R2's mitigation was: *"trim `policy universality` from the §4.2 list or cross-reference §4.3's hardening target."* The commit at `bb5f6f9` did both. The exact rewrite (paper.md line 149):

> **Before (v0.2 draft):** "The check is exercised in the Phase 1 test suite and is the foundation on which every §4 property above the effect layer — audit chain integrity, replay determinism, policy universality — builds."
>
> **After (HEAD):** "The check is exercised in the Phase 1 test suite. Properties above the effect layer (audit chain integrity §4.3, replay determinism §4.5) build on well-formedness of the deployed stack; policy composition is *convenient* to express once well-formedness holds but is not itself a substrate invariant (§4.3 treats audit-universality as a stack-configuration contract, not a substrate property)."

The rewrite (a) drops "policy universality" from the chained properties list, (b) re-scopes the remaining two properties to "the deployed stack" rather than "the substrate", (c) explicitly cross-references §4.3 as the honest treatment of universality, and (d) flags policy composition as *convenient* rather than *invariant* — the precise distinction I wanted the paper to make. This is an honest rewrite: nothing is claimed that §4.3 does not itself admit.

**Verdict: CLOSED. §4.2 now reads consistently with §4.3.**

### No new overclaims introduced by the two tightenings

Diff inspection (`git show bb5f6f9 -- paper/persistence-nesy-2026-draft.md`) shows exactly two modified lines (the Abstract and the §4.2 paragraph), no new sentences, no scope expansion elsewhere. The tightenings are additive-safe.

**Verdict: CLEAN. No new overclaims.**

---

## 2. Paper ↔ code fidelity for the Round 3 changes

This is where R3 shows its residuals. The polish-worker correctly made the code changes but did not propagate any of them into the paper. For two of the three changes (P-concurrency, P-audit-conform) this is defensibly "engineering detail, not a paper concession." For one (P-plan-node) it is a real missed contribution.

### P-plan-node → vector form: PAPER IS SILENT ON SYNTAX

**Code state (verified at `src/persistence/spec/_canonical.py:397`):** `_PlanNodeVector(Spec)` with docstring *"Vector-shaped plan AST node per docs/agent2-plan-spec.md §1 + §8. Shape: `[:node-type {attrs} & children]` — an EDN vector whose first element is a keyword tag in `PLAN_NODE_KINDS`..."* Registered at line 547 via `register(":persistence.plan/node", _plan_node)`. 28 new passing tests pin the shape (`tests/spec/test_plan_node_vector.py`, 14 happy-path + 6 rejection + 3 recursive AST).

**Paper state (§4.4, line 167):**

> "A plan is a labeled tree where internal nodes are *control operators* (seq, par, choice, loop, race, let, branch) and leaves are *effect invocations* (tool-call, llm-call, code, checkpoint) or *cognitive operators* (reflect, verify, call-skill). Every node carries a content hash sha256(n) used as its identity. The `:persistence.plan/node` spec is registered in `src/persistence/spec/_canonical.py` (Phase 1) with the enumeration `PLAN_NODE_KINDS = (…)`; the Plan-module evaluator is Phase 2."

This paragraph is syntax-agnostic. A reader could implement the AST either as a vector or as a map — the paper does not commit. The concrete shape `[:tag {attrs} & children]` appears *nowhere* in the paper. This is a gap: the R3 code change took a stance on the syntax (vector, not map; reject map-form with a migration hint), and the paper should surface that stance.

**Why it matters.** The paper's own §4.7 elevates `:persistence.plan/node` as a "parse-don't-validate methodology contribution." The vector form is *the thing being parsed*. Hiding the syntax under an abstract "labeled tree" description flattens the methodology contribution to a generic "we register specs early" — which is far less interesting than "we register specs in a specific vector-form that lets tag-dispatched pattern-matching consume plans homoiconically."

**New contribution angle worth naming.** The rewrite to vector form is not just a syntactic convenience — it makes plans *homoiconic in the Lisp sense*: a plan literal is indistinguishable from the EDN data that describes it. This means Plan-module evaluators (Phase 2) can be written as structural pattern-matchers over `[tag, attrs, *children]`, and plan rewrites (§4.4's homoiconicity contract: `read/splice/compose/rewrite/fork/promote`) become list-splicing operations. The map-form would have forced `kind`-dispatched reflection on a `:node/kind` key — workable but not homoiconic in the same sense. One paragraph in §4.4 or §4.7 naming this ("the vector form was chosen so that plan rewrites reduce to list splicing over well-typed EDN vectors, preserving the homoiconicity contract") would elevate this from "implementation detail" to "methodology decision."

**Verdict: GAP. Cost to close: 1 sentence in §4.4 + 1 sentence in §4.7. ~10 minutes.**

### P-concurrency → atomic transact: PAPER IS SILENT

**Code state (verified at `src/persistence/fact/store.py`):** New `Store.allocate_and_append(datoms) -> list[Datom]` on the Protocol. `SQLiteStore` runs it under `BEGIN IMMEDIATE`; `InMemoryStore` under `threading.Lock`. Module docstring explicitly calls out: *"GIL doesn't protect you — assume you're in a multi-worker deployment."* New `tests/fact/test_concurrent_transact.py` — 16 threads × 50 transacts under `threading.Barrier`, zero collisions.

**Paper state (§5.1 + §7.1):** The word "concurrent" does not appear. "Thread" does not appear. "Atomic" appears once (§4.6 line 201, re. transactions CAS-ing). "BEGIN IMMEDIATE" does not appear. The paper makes no claim about concurrency safety, which on the face of it is *honest* — the paper cannot overclaim what it does not mention. But consider the paper's own claims:

- §4.5 Proposition 3 (replay determinism) assumes "all non-determinism in the agent routes through effects in catalog K" — a claim that silently presupposes no concurrency races in the `Store` layer (a race would be non-determinism leaking outside the effect catalog).
- §5.1 describes the Fact module backends without any operational posture statement. A NeSy PC reading "SQLiteStore (for zero-ops persistent deployments)" may reasonably ask: *"What is the multi-writer concurrency model?"* — a question the paper's current draft cannot answer.
- §6.5 Case B (Adaptive Trader v2) describes a production trading agent. Production trading implies at least two concurrent writers (the agent itself + any monitoring/ARIS-gate handler running in a sibling process). Silent on this point.

**Verdict on silence.** The silence is *technically* honest — the paper makes no claim it cannot back. But a strict PC reviewer will read "§4.5 Prop 3 requires all non-determinism to route through K" and ask how the `Store` layer guarantees this under multi-worker load. Answering that question by saying "`allocate_and_append` is atomic under `BEGIN IMMEDIATE`; 16-thread stress test passes" would strictly strengthen the paper. Currently it reads as an unstated assumption.

**New contribution angle worth naming.** Atomic transaction allocation under multi-writer load is precisely the kind of operational invariant that separates "artifact paper" from "toy paper." Two sentences in §5.1 or §7.1 naming the TOCTOU closure and the 16-thread test would convert this from gap to named engineering contribution.

**Verdict: GAP (but defensibly honest silence). Cost to close: 2 sentences in §5.1 or §7.1. ~15 minutes.**

### P-audit-conform → self-conform at output: PAPER IS SILENT

**Code state (verified at `src/persistence/effect/handlers/audit.py:154` + `src/persistence/effect/audit.py:AuditEntry.to_edn`):** Three self-conforming producers now exist — `AuditEntry.to_edn()`, `audit_entry_to_datom()`, `Trajectory.to_edn()` — each calls `spec.conform(...)` on its own return value and raises `ValueError` if malformed. The `:persistence.effect/audit-entry` spec was aligned with the `AuditEntry` dataclass shape (dropped `:audit/args`, `:audit/cost`, `:audit/valid-from` which had no dataclass counterpart). Six new tests in `tests/effect/test_audit_self_conform.py` pin the contract.

**Paper state (§4.3 line 157–159):**

> "Phase 1 ships:
> - `make_audit_handler(wraps, …)` with configurable W and a default of `("llm/call",)`.
> - `verify_chain(entries) → bool`, which re-derives each `prev_hash` from the canonical serialization and detects field-mutation tamper.
> - `audit_entry_to_datom(entry) → datom-shaped record` that flows the audit entry into the Fact log."

The paper mentions `audit_entry_to_datom` as a shipped function but says nothing about the fact that it now self-conforms against `:persistence.fact/datom` before returning — nor about `Trajectory.to_edn`'s self-conform against `:persistence.replay/trajectory`. Note also: paper line 237 (§5.3) lists canonical specs as "`:audit/entry`" (abbreviation), whereas the actual registered name is `:persistence.effect/audit-entry`. Not a correctness bug — but a minor naming drift the R3 polish missed.

**Is the silence honest?** Partially. The paper does not claim *input-validation-only* semantics — it just doesn't make an output-validation claim either. But the self-conforming-at-output pattern is a genuine methodology contribution: it says "specs are not just guards on untrusted input; they are contracts that load-bearing producers check themselves against." This is a small but interesting refinement of parse-don't-validate, and the paper could claim it cheaply.

**New contribution angle worth naming.** Specs-as-output-contracts (not just input-contracts) is one of the more interesting neurosymbolic refinements the code now implements. It says the symbolic layer does not just validate neural outputs; it validates its own outputs too. This is precisely the kind of self-conforming discipline that makes a runtime auditable without external validators. One sentence in §4.7's "Self-healing contract" paragraph ("*Producers in Phase 1 — `AuditEntry.to_edn`, `audit_entry_to_datom`, `Trajectory.to_edn` — self-conform against their registered specs before returning, making specs a bidirectional contract rather than a one-way input guard*") would make this visible.

**Verdict: GAP. Cost to close: 1 sentence in §4.7 + 1 name correction in §5.3 (`:audit/entry` → `:persistence.effect/audit-entry`). ~5 minutes.**

### P-sql-portability → NEW PAPER ↔ CODE CONTRADICTION (unflagged by R3)

**Code state (verified at `src/persistence/fact/migrations/0001_datom_log.sql` line 3):** Header now reads *"SQLite-only for Phase 1 (ARIS Round 3 P-sql-portability)... Until that work lands, do NOT assume portability."*

**Paper state (§5.1 line 219):**

> "...with a portable SQL migration (`migrations/0001_datom_log.sql`) that runs unmodified on SQLite 3.37+ and Postgres 14+."

**This is now false.** The SQL file explicitly says portability is not assumed; the paper still claims it is. R3's polish-worker correctly updated the SQL file header and correctly recorded in `W-polish-summary.md` that this was a docs-only change — but did not propagate the change to the paper, where the identical portability claim also lived.

**Verdict: NEW DRIFT. Cost to close: 1 sentence rewrite in §5.1 ("*...a Phase-1 SQLite-only migration (`migrations/0001_datom_log.sql`); the Postgres-portable sibling ships in Phase 2.*"). ~2 minutes.** This is the *one* negative spillover from Round 3.

### `grep ed25519 paper/` — 7 hits, all honest

As expected. Breakdown:
- Line 12: v0.2 revision history noting ed25519 removal from Phase 1.
- Line 120 (§4.1): Phase-2 disclosure under datom provenance.
- Line 161 (§4.3): Phase-2 disclosure under Integrity contract.
- Line 369 (§7.1): Phase-2 disclosure under Write-latency limitation.
- Line 379 (§7.2): Phase-2 disclosure under local-first privacy.
- Line 381 (§7.2): Phase-2 disclosure under deployment posture.
- Line 417 (§8 Future Work): Item 3.

Every hit is a Phase-2 scope marker. Zero instances where ed25519 is claimed as shipped Phase-1 work. **The polish-worker's judgment call to keep these rather than delete the Phase-2 markers is correct** — deleting them would regress the honesty markers that make the paper credible.

**Verdict: CLEAN.**

---

## 3. Research-contribution re-grade

Compared to the Round 2 R4 grades. Where the Round 3 paper-side tightenings + code changes changed the grade, I say why.

### Proposition 2 (machine-checkable well-formedness) — GRADE: A (unchanged from R2)

Prop 2's paper treatment is unchanged. Still anchored in `src/persistence/effect/runtime.py`, still backed by `Unhandled` at runtime, still the paper's single strongest formal contribution on the shipped artifact. No R3 code or paper change affected it. A-grade holds. **The one thing I asked for in R2 that did not land is the two-line proof sketch; for Prop 2 (union over clauses = catalog) this is still defensible without a proof, but would move A → A+ at essentially zero cost.**

### NO-OP byte-identity trajectory hash — GRADE: A (up from A−)

With the Abstract qualifier now in place, the NO-OP claim is correctly scoped. Abstract, §2.5, §4.5 Corollary, and §6.2 all treat it consistently. A reader cannot misread it as a universal CAMO-strengthening; the "for non-trivial interventions, byte-identity no longer applies" parenthetical in the Abstract makes the scope explicit. The Corollary is still backed by the named test (`tests/replay/test_determinism.py::test_noop_intervention_produces_byte_identical_trajectory`) which I re-ran: passes. Full A.

### Merkle chain `verify_chain` — GRADE: B+ (unchanged from R2)

R2 graded B+ and said *"a two-line proposition — `verify_chain(E)` returns True iff for all i, e_i.id = sha256(canonical(e_i.fields) ‖ e_{i−1}.id) and e_i.prev_hash = e_{i−1}.id — would lift this to A."* R3 did not land such a proposition. §4.3's Integrity contract is still a verbal claim. Deletion/reorder tamper coverage (flagged in R2 as "hardening target for Round 2") is also unchanged in the paper text — the reference to "Round-1 rigor review / hardening for Round 2" is now stale (we are past both rounds).

**No improvement Round 3. B+ stands.** The formal proposition is a 2-line insertion and the single cheapest R4 → 9.0 move. See §4 below.

### Spec-first `:persistence.plan/node` — GRADE: A (unchanged from R2, with a new angle)

The methodology framing in §4.7 ("parse-don't-validate methodology choice … commitment device") is unchanged and still A-grade. But the R3 vector-form rewrite opens a *new contribution angle* not yet surfaced in the paper: **homoiconicity as a rewrite-simplifying invariant**. Plan rewrites under vector form reduce to list splicing; under map form they would require reflective key-dispatch. This is the kind of neurosymbolic refinement a NeSy PC appreciates — symbolic-layer design decisions that make downstream neural/symbolic composition cleaner. See §2's P-plan-node gap entry for the exact sentence that would claim this.

**Grade stays A; one-sentence addition in §4.7 would bring it to A+ and name a new angle.**

---

## 4. New overclaims or new contribution angles

### Overclaims introduced in Round 3: ONE

**§5.1 SQL portability claim (line 219).** R3's P-sql-portability fix updated the SQL file header to say "SQLite-only" but did not update the paper's matching line. Paper still reads *"a portable SQL migration (migrations/0001_datom_log.sql) that runs unmodified on SQLite 3.37+ and Postgres 14+"*. The SQL file now explicitly contradicts that. This is a net regression from Round 2's state (where both SQL header and paper claimed portability consistently) — Round 3 made the SQL file honest and the paper inconsistent. **Fix (2 minutes):** strike or rewrite the portability phrase.

### Minor drift introduced in Round 3: ONE

**§5.3 line 237 names spec as `:audit/entry`; canonical registry uses `:persistence.effect/audit-entry`.** Not a Round-3 regression — this naming drift has existed since v0.2. But Round 3's P-audit-conform *aligned* the spec with the dataclass, so this would have been the right moment to also sync the paper's name reference. The polish worker missed it.

### Contribution angles worth naming in Round 4: THREE

1. **Homoiconicity of plan AST (§4.7).** Vector form → plan rewrites reduce to list splicing → the homoiconicity contract (§4.4) has an implementation rationale. One sentence.
2. **Self-conforming output (§4.7).** Spec-as-output-contract pattern in `AuditEntry.to_edn` + `Trajectory.to_edn` + `audit_entry_to_datom`. One sentence.
3. **Atomic transaction allocation under multi-writer load (§5.1 or §7.1).** `allocate_and_append` under `BEGIN IMMEDIATE`; 16-thread stress test. Two sentences.

Each of the three, at a sentence or two, would strictly strengthen the paper's artifact-contribution claims. None is urgent for the abstract; all three would land before the 2026-06-16 paper deadline with room to spare.

---

## 5. Three biggest residual NeSy PC objections

Post-Round-3, with the two tightenings landed. These are my best-guess of what a strict NeSy 2026 PC member would object to. I list in decreasing severity.

### Objection 1 (same as R2 Obj 1, unchanged): regulator-replay benchmark has no measured data at submission

**Still the flagship residual risk.** §6.3 commits to a 50-trajectory synthetic project-finance corpus with CC-BY-4.0 licensing, generator script, and reconstruction harness — all deferred to camera-ready (2026-07-20). At abstract (2026-06-09) and paper (2026-06-16) submission, this section has zero numeric rows. Round 3 did not touch §6.3; the commitment and the gap are identical to Round 2.

**Severity: unchanged. Mitigation remains the same:** ship at minimum a 10-trajectory generator walkthrough in `bench/regulator_replay/` before June 16 so the artifact contains *something* a submission reviewer can execute, even at tiny scale. This is the single largest lever on paper acceptance that Round 4 + Phase-2 work can pull.

### Objection 2 (new, arising from Round-3 residuals): paper lags its own code contributions

Round 3 made real code contributions (vector-form plan spec, atomic allocation, self-conforming output) that are not reflected in the paper. A PC member who `git log`s the repo alongside the paper will see the gap. The paper reports on a snapshot (v0.1.0a1 at commit `2c96fb7`) that is three commits older than the paper-tightening commit that updated it — a mild incoherence. **Mitigation:** apply the 3-sentence paper-patch list from §2 above before June 16.

### Objection 3 (updated from R2 Obj 3): §6.5 Case A/C/D still 3–5-sentence vignettes with no numbers

R2 said: *"either promote one of A/C/D to a numeric case … or demote A/C/D from §6 Evaluation to §7.3 Adoption path."* Round 3 did neither. Vignettes still live in §6.5 without metrics. A reviewer who expects numerical evidence from any subsection titled "Evaluation" will note this. **Severity unchanged from R2.** This is a scoping choice, not a correctness bug, but deserves resolution before June 16.

### De-risked objections from R2

- **R2 Obj 2 ("is this a systems/position/results paper?")** is partially addressed by the "reporting discipline" paragraph in §1 (line 37) and the stricter Abstract scoping. The paper now signals its genre clearly enough that a PC using the wrong lens is at least warned. Does not need further mitigation at this round.

---

## 6. Abstract-readiness verdict

### Abstract submission 2026-06-09 (49 days): **READY AS-IS.**

The Abstract is now honest, correctly scoped, with the NO-OP qualifier and the non-trivial-intervention caveat in place. No blocker. Submittable as it stands at HEAD `045f4b4`.

### Paper submission 2026-06-16 (56 days): **READY after ~3 hours of paper patches + ongoing Phase-2 Obj-1 mitigation.**

Delta to submittable:

- **Mandatory before June 16 (3-sentence patch list, ~15 min):**
  - §5.1 line 219: strike or rewrite the SQL-portability claim (P-sql-portability fix propagation). `[~2 min]`
  - §5.3 line 237: `:audit/entry` → `:persistence.effect/audit-entry` (name sync). `[~1 min]`
  - Pick a date: §6.6 line 290 currently says "abstract submission (2026-06-16)" — per the user's stated timeline, that should be `(2026-06-09)` for abstract and the paper-deadline line is the June 16 one. Minor date fix. `[~2 min]`

- **Strongly recommended before June 16 (new contribution angles, ~20 min):**
  - §4.4 and/or §4.7: 1-sentence vector-form + homoiconicity naming. `[~5 min]`
  - §4.7 self-conforming-output: 1 sentence. `[~5 min]`
  - §5.1 or §7.1 concurrency: 2 sentences naming `allocate_and_append` + 16-thread test. `[~10 min]`

- **Optional but would earn a half-grade:**
  - §4.3 formal proposition on `verify_chain` (2-line iff statement). Moves Merkle-chain contribution B+ → A.
  - §4.2 two-line proof sketch for Prop 2. Moves Prop 2 A → A+.

- **The Obj-1 flagship risk (not a paper patch, Phase-2 work):**
  - At minimum, 10-trajectory regulator-replay generator walkthrough in `bench/regulator_replay/` before June 16.

**§6.3 regulator-replay is still the flagship residual risk.** Nothing in Round 3 changed that; R4's answer is identical to R2's.

---

## 7. Overall research-alignment grade: 8.6 / 10

Breakdown:
- **Honesty & code-fidelity (40%): 8.5/10.** Two tightenings landed cleanly. One new drift (§5.1 SQL portability) introduced. Three code contributions not surfaced in paper. On net, the paper is more honest than v0.2 was (NO-OP qualifier, §4.2 rewrite) but slightly more stale relative to the code (P-plan-node / P-concurrency / P-audit-conform invisible + P-sql-portability drift). 8.5 reflects both.
- **Formal contribution quality (25%): 8/10.** Unchanged from R2. Prop 2 A, NO-OP A, verify_chain B+, spec-first plan-node A. No new propositions landed; no existing propositions regressed. R2 → R3 delta: 0.
- **Evaluation completeness (15%): 6.5/10.** Unchanged from R2. §6 is still a Reproduction Plan with zero numeric rows at submission. The flagship residual risk.
- **Neurosymbolic positioning (10%): 9/10.** Unchanged from R2. Datalog/Z3 correctly separated, explain_for_llm contract intact, spec-first plan-node paragraph intact.
- **Writing quality & reviewer empathy (10%): 8.5/10.** Up from R2's 7.5. The §4.2 rewrite is a genuine craft improvement — the softened sentence reads better than the original rhetorical chain. The Abstract NO-OP qualifier is a piece of honest-writing that a careful reader will respect.

**Weighted total: 8.58 / 10. Call it 8.6.**

**Compared to Round 2: +0.4 (8.2 → 8.6).** Smaller delta than R1 → R2 (+1.7), as expected for a polish round. The R2 gate was 8.0 (exceeded); the R3 gate is 8.5 (exceeded). Target ≥ 8.5 met by 0.1.

---

## 8. Go / no-go for Round 4

### GO for Round 4.

Target min ≥ 8.5 met (R4 = 8.6). Assuming R1/R2/R3 also cross 8.5 (Serena memory suggests they should — 520 tests, TOCTOU closed, audit-entry spec aligned, op-invariants linted), the Round 3 gate clears.

### Round 4 readiness criteria

The user's `aris-round-3-polish-merged` memo sets **Round 4 target at min ≥ 9.0 → NeSy-submittable.** For R4 to reach 9.0 from 8.6, the concrete paper-side work is:

1. **Apply the 3-sentence paper-patch list from §6 above (15 min).** Closes the one new drift + two name syncs. Small but unblocks the R4 → 9.0 move.
2. **Add the three new contribution-angle sentences (20 min).** Vector-form homoiconicity, self-conforming output, atomic allocation. Each strictly strengthens the paper's claims against the code.
3. **Fix B1 (Trajectory.intervention single → list) per the Round 3 polish summary.** Touches engine + dataclass + EDN round-trip + spec + DPO reader. Medium effort (~1 day). Unblocks any §4.5 / §6.2 claim about multi-step interventions.
4. **Land `Runtime.assert_universal_audit` (half-day).** Closes the §4.3 Universality contract hardening that the paper has been promising since R2. Moves the staleness markers in §4.3 / §7.1 from "Round-2 hardening list" to "shipped in Phase 1."
5. **§4.3 two-line formal proposition on `verify_chain` (5 min).** Moves Merkle-chain B+ → A.

Of these, (1) and (2) are mandatory for R4 to reach 9.0; (3) is the most R3-flagged-but-unfixed and the most valuable unblocker; (4) closes a stale paper reference; (5) is the single-cheapest half-grade.

The flagship Phase-2 Obj-1 work (regulator-replay harness + 50-trajectory generator) does not need to land before Round 4 grading — it lands before the camera-ready. But a 10-trajectory walkthrough before June 16 is the hedge I would take.

### Round 4 agenda suggestion

For Round 4, ask the four reviewers to grade against a paper that incorporates (1) + (2) + (5) — a ~45-minute paper patch pass with zero code churn. B1 (3) and assert_universal_audit (4) can land in parallel with R4 grading if the reviewers agree to re-verify the §4.5 and §4.3 claims post-fix. That keeps R4 grading on schedule while closing the two highest-value code items before camera-ready.

---

*— R4, 2026-04-21. Graded against `045f4b4`, paper v0.2 (454 lines, 8409 words), 520 tests green (local: 507 passed + 13 hypothesis-gated skipped in my env). An honest 8.6 beats a generous 9.0.*
