# ARIS Round 2 — Reviewer R4 — Research Claims vs. Code Reality (v0.2 regrade)

*Repo `/Users/nawfalsaadi/Projects/persistence-os/` @ `a28d8f5`. Paper `paper/persistence-nesy-2026-draft.md` v0.2 (454 lines, 8363 words). 463 tests green (verified local run, 1.98s). Round 1 predecessor: `docs/aris-round-1/R4-research.md` (6.5/10). W-paper summary: `docs/aris-round-2/W-paper-summary.md`.*

## Summary grade: 8.2 / 10

The paper-side fix pass is the real deal. Every one of my Round-1 F-ids either (a) disappeared from Phase-1 claims and moved to Future Work, or (b) was rewritten in a form I can verify against the shipped code. The two load-bearing formal propositions that now anchor the paper — `Runtime.is_well_formed` completeness and `trajectory_hash` byte-identity on NO-OP — are both backed by shipped tests with the exact test name cited inline (I ran `tests/replay/test_determinism.py::test_noop_intervention_produces_byte_identical_trajectory` under the paper's revision: it passes in 0.06s). The paper no longer conflates Phase-1 code with Phase-2 deliverables: every §3 invariant carries an explicit Phase tag, every §5 module is marked shipped or designed, and §6 is fenced as a Reproduction Plan rather than a numeric results section. The title change ("Toward Accountable Neurosymbolic Runtimes: The Persistence OS Substrate") is the single most honest edit in the draft — "Toward" and "Substrate" are the right words for a paper reporting a four-module Phase-1 with three more designed.

I still cannot give 9.0 because (a) the paper is now the opposite failure mode — it is an honest *position paper with one proof* rather than a full evaluation paper (§6 is zero numeric rows, except the pre-existing Adaptive Trader baseline), which a strict NeSy PC may dock regardless of honesty; (b) one claim — §4.3 Universality contract — is soft in a way that could still be tightened with a `Runtime.assert_universal_audit` 20-line landing before June 16; (c) Case A/C/D are now 3–5-sentence vignettes with *no* numbers, which is more honest than v0.1 but raises the question of whether they belong in the paper at all. None of these are correctness bugs; they are strategic choices a PC may second-guess.

**Round 2 grade: 8.2 / 10, up from 6.5.** Target ≥ 8.0 met. Go for Round 3.

---

## 1. R1 finding remediation table

For each Round-1 finding: did paper v0.2 address it, and is the new claim true of the code at HEAD?

| R1 F-id | Paper v0.2 addressed? | New claim true of code at HEAD? | Residual |
|---|---|---|---|
| **F1** — seven capabilities overclaim | YES. Abstract says "four of the seven runtime modules — Fact, Effect, Spec, Replay — and demonstrates four substrate-derived capabilities"; §1 "What this paper reports, honestly" paragraph is explicit; §2.6 Fig.1 split into Phase-1 ● / Phase-2 ○ columns; every §3 invariant carries a Phase tag. | TRUE. `src/persistence/` contains four dirs: `fact/`, `effect/`, `spec/`, `replay/`. No `plan/`, `txn/`, `repl/` — matches paper's "three further modules … scheduled for Phase 2." | None. |
| **F2** — Prop 1 O(log n) HAMT claim | YES. Proposition 1 rewritten: "branch(D, t, Δ) returns a new DB value backed by a fresh in-memory store seeded with asOf(D, t) and extended with Δ; writes to the branched value cannot leak back into the parent store. *Complexity:* on the Phase 1 `InMemoryStore` reference implementation (`src/persistence/fact/db.py`), materialization is O(\|D\|) in the seed snapshot plus O(\|Δ\|) in the hypothetical additions." HAMT moved to "Phase 2 upgrade." | TRUE. `src/persistence/fact/db.py:253–292` — `branch()` does `seed = list(self.as_of(t).datoms)` + `InMemoryStore()` + `branched_store.append([Datom(...copy.deepcopy(provenance)...) for d in seed])`. Store is list-backed (`store.py:81`: `self._log: list[Datom] = []`). Isolation is structural — branched store is a fresh instance, writes cannot reach the parent's `_log`. Paper's claim is a tight fit to the code. | Good. The intermediate drafting in Serena memory said "branch is a constant-time logical operation"; the final paper dropped that phrasing and states complexity precisely (O(\|D\|) + O(\|Δ\|)). This is strictly more rigorous. |
| **F4** — ed25519 in §4.1 / §7.1 / §7.2 + "20–40 ms" | YES. `grep ed25519 paper/persistence-nesy-2026-draft.md` returns six hits; all six are honest Phase-2 references: §4.1 line 120 "Cryptographic per-transaction signatures (ed25519) are Phase 2 work and are discussed as a privacy-posture extension in §7.2"; §4.3 line 161 "the current `signature` slot stores a SHA-256 content hash, and per-transaction ed25519 signing is Phase 2 work"; §7.1 line 369 explicitly states "no overhead figure is claimed for it until the signing path ships and is measured"; §7.2 line 379 "per-transaction ed25519 signing is Phase 2 work"; §8 Future Work item (3). The "20–40 ms" figure is deleted. | TRUE. `grep ed25519 src/` returns zero hits (re-verified). The only Phase-1 integrity mechanism is SHA-256 content hashing and Merkle-chained audit entries via `verify_chain` (`src/persistence/effect/handlers/audit.py:83`). | None. The paper also separates **integrity** (shipped via `verify_chain`) from **authenticity** (requires ed25519, Phase 2) — that distinction is a small but real contribution to how accountable-agent papers should frame crypto. |
| **F6** — latency targets unmeasured | YES. §5.1 labels the {50, 200, 100} ms line explicitly as "Phase-2 measurements over a persistent-trie backing store at 1M-datom scale. Phase 1 reference-implementation numbers … are `[TBD]` in this draft (see §6.6)." §7.1 Limitations also marks write-latency overhead as `[TBD]`. | TRUE. No perf tests in `tests/`. Any Phase-1 number the paper stated would be fabricated; the paper correctly declines to state any. | Minor: an actual 10k-datom `as_of` p95 number — even a bad one — is cheap to produce and would let the paper carry one honest latency datapoint for the abstract. Optional. |
| **F7** — Kuzu + mem0 projection claimed, only DictProjection ships | YES. §5.1: "The projection surface is a `ProjectionAdapter` Protocol (`reset`, `apply`) with a reference in-process `DictProjection` in Phase 1; production Kuzu and mem0 projection adapters are Phase 2 work." §5.8 system diagram shows "DictProjection" in the shipped tier and "[Phase 2: Postgres + Kuzu + mem0]" in a labeled future tier. The paper also correctly labels `mem0_adapter` as a legacy-write interceptor, not a projection. | TRUE. `src/persistence/fact/projection.py` contains `ProjectionAdapter` Protocol (`projection.py:28`) and `DictProjection` reference (`projection.py:45`). `src/persistence/fact/interceptors/` contains only `mem0_adapter.py`. No Kuzu code anywhere. | None. |
| **F8 + F9** — §6 contains bench harnesses that do not exist; regulator-replay has no dataset | YES, dramatically. §6 is retitled **"Evaluation — Reproduction Plan"** with an explicit abstract-vs-camera-ready fence in §6.6 ("At the 2026-06-16 abstract deadline, §6 reports the formal properties … as already-checked on the shipped artifact; all numeric tables carry `[TBD]` honestly"). §6.3 regulator-replay rescoped from 200 production to **50 synthetic project-finance trajectories, CC-BY-4.0 licensed**, generator + dataset shipping with camera-ready. §6.2 separates the already-testable NO-OP corollary (ships in the abstract) from the 1000-trajectory distributional CAMO table (camera-ready, gated on Phase-2 per-step rng recording). §6.4 Plan-optimization **removed entirely** with Phase-2 companion-paper redirect. | Partially. The plan is concrete on licensing (CC-BY-4.0), scope (50 trajectories), and synthesis pipeline (BankabilityAI-shape scoring over synthetic inputs). What it does *not* yet specify: (i) the exact dataset schema — what fields does each trajectory carry? — beyond "datom log + plan AST"; (ii) the generator's determinism envelope — is the synthesis itself deterministic, so a PC reviewer can rebuild the corpus? (iii) the reconstruction script's pseudocode. README confirms `bench/` lands in Phase 2 and is "not yet shipped." | **MAJOR remaining gap.** The paper commits to shipping the regulator-replay harness + 50-trajectory synthetic dataset with camera-ready (2026-07-20). That means a generator script + reconstruction script + 50 canonicalized trajectory files all need to exist by July 20. Given Phase-2 workstream overhead, this is *achievable* but not *free*. See Phase-2 minimum-work list below. |
| **F10** — Datalog + Z3 in shipped substrate list | YES. §7.4 now cleanly lists four shipped symbolic pieces ("Bitemporal datom queries with a Datalog-shaped surface", "EDN AST grammars for plans", "Policy-as-data", "Malli-style specs") and separates two adjacent systems: "**Datalog engine** … the Phase 1 query layer is Python list comprehensions, not a rule engine" and "**Z3-discharged `verify` leaves** … no Z3 code ships in Phase 1." §8 Future Work items (4) and (5). Abstract line also softened: "bitemporal datom model with a Datalog-shaped query surface, EDN-grammar plan-ASTs, policy-as-data, Malli-style specs" — no raw "Datalog" or "Z3" claim. | TRUE. Zero Datalog engine in `src/`; queries are Python generators/comprehensions (e.g. `fact/store.py:100` `since(self, tx_time)` returns a generator). Zero Z3 in `src/` or `pyproject.toml`. | None. The "Datalog-shaped surface" wording is precisely the right amount of hedge — it says the query model is relational over (e, a, v) triples (true) without asserting a rule engine (not true). |

**All six R1 criticals/majors: addressed and verified against HEAD.**

---

## 2. Elevated contribution assessment

One paragraph each, grading whether the rewrite gives each elevated contribution paper-grade treatment.

### Proposition 2 (`Runtime.is_well_formed`) — GRADE: A

Prop 2 now reads: *"A stack H over catalog K is well-formed iff for every κ ∈ K, at least one handler above the raw base handles κ. The shipped `Runtime.is_well_formed(catalog)` (`src/persistence/effect/runtime.py`) decides this property in O(\|H\|·\|K\|) time; `Runtime.uncovered_ops(catalog)` returns the witness set. At runtime, `Runtime.perform(op, …)` raises `Unhandled` when no handler covers κ — the property is not merely asserted but enforced on every call."* (line 147). The claim has (i) a formal statement with an iff, (ii) a complexity bound that matches the code (`runtime.py:139` iterates handlers and unions their clauses — O(\|H\|·\|K\|) is accurate), (iii) a decision procedure referenced by filename + function name, (iv) a runtime-enforcement guarantee via `Unhandled` (verified: `runtime.py:178`, `runtime.py:184`, `runtime.py:213`, `runtime.py:265` all raise `Unhandled`), (v) an explicit labeling as "the paper's strongest formal contribution on the Phase-1 artifact." A NeSy PC will read this as a real decidable completeness property. The only thing missing is a proof sketch — for an `iff` this trivial (union over clauses = catalog) it is defensible to assert without proof, but a single sentence proof skeleton would cost two lines and preempt any objection.

### `spec.explain_for_llm` — GRADE: A−

The paper has a dedicated §4.7 paragraph titled **"Self-healing contract (shipped)"** (line 229) that formalizes the **conform → explain → retry** contract: *"When conform fails, `spec.explain_for_llm(err)` returns a structured message containing the field path, the failure reason, and a Fix-clause-annotated hint."* The test reference (`tests/spec/test_llm_errors.py`) is named inline. Reading `src/persistence/spec/_registry.py:124` + `tests/spec/test_llm_errors.py:17–88` confirms: every test asserts `"Fix:"` appears, field names appear, spec key appears on the top line. The contract is concrete enough to reimplement from the paper. **The A− (not A) is because the paper does not formally state the output grammar** — what *exactly* must a valid `explain_for_llm` return? A single regex or BNF would make this a checkable symbolic/neural interface contract rather than a prose commitment. For NeSy 2026 this is not a blocker, but a single labelled grammar line would push the contribution from "clearly described" to "machine-verifiable."

### `trajectory_hash` byte-identical NO-OP — GRADE: A

Framed exactly right as a strict strengthening of CAMO. The Corollary (line 199) reads: *"For a NO-OP intervention on a toy agent instrumented via the shipped replay engine, trajectory_hash(replay(T, I_noop)) = trajectory_hash(T) — byte-identical on the canonical serialization, not merely statistically close. Verified by `tests/replay/test_determinism.py::test_noop_intervention_produces_byte_identical_trajectory`."* I re-ran that exact test: PASSED in 0.06s. §2.5 Related Work (line 87) also frames this as *"stronger on the precise axis these papers care about. Because all state is immutable bitemporal datoms, 'aligned randomness' is not a simulator hack but a substrate property."* The limitation (toy agent, `_advance_rngs_to_match` heuristic) is honestly acknowledged in the paragraph below the Corollary and in the Abstract. This is the paper's cleanest "verifiable on the artifact" contribution and is weighted appropriately — front-and-center in the Abstract, §2.5 Related Work comparison, §4.5 Corollary, §6.2 first-ships-in-abstract. Excellent treatment.

### `verify_chain` Merkle audit — GRADE: B+

§4.3 is now a dedicated subsection titled **"The Merkle-hashed audit chain"** with two named contracts: an **Integrity contract** ("detects any single-field mutation inside an entry (tested)") and a **Universality contract** (honestly scoped to the deployed stack, not the substrate). `verify_chain` is named as a shipped function — code verified at `src/persistence/effect/handlers/audit.py:83`. The paper also separates integrity (shipped) from authenticity (Phase 2, ed25519) — good framing. **The B+ (not A) is because the Integrity contract is a verbal claim, not a formal one.** A two-line proposition — "For any sequence E = [e_1, …, e_n], `verify_chain(E)` returns True iff for all i, e_i.id = sha256(canonical(e_i.fields) ‖ e_{i−1}.id) and e_i.prev_hash = e_{i−1}.id" — would lift this to the same formal grade as Prop 2. Separately, the paper acknowledges that deletion / reorder tamper coverage is incomplete (W-rigor notes 3 new tests; that hardening should be cross-referenced in the paper's §4.3 Integrity paragraph). For a NeSy submission, a reviewer who sees "tested" without a proposition may weight this lower than it deserves.

### Spec-first `:persistence.plan/node` registration — GRADE: A

The paper names this correctly as a **parse-don't-validate methodology contribution** (line 60: *"a deliberate parse-don't-validate move that freezes the data shape ahead of the code that will consume it"*) and gives it explicit paragraph treatment in §4.7 under "Forward-compatible spec-first commitment" (line 234): *"In Phase 1 we register `:persistence.plan/node` and `:persistence.plan/skill` in the spec registry before the Plan module exists. This is a deliberate parse-don't-validate methodology choice: the data shape is locked before code depends on it, which lets Phase 1 and Phase 2 workers agree on the plan AST's structure without blocking on implementation order. The `:persistence.plan/node` spec is the commitment device …"* This is exactly the methodology framing I asked for. Code verified: `src/persistence/spec/_canonical.py:399` registers `:persistence.plan/node`, `:527` registers `:persistence.plan/skill`. `PLAN_NODE_KINDS` enum present at line 346. This elevates from "background engineering discipline" to "named methodological contribution" — the right move for a conference that values clean design principles.

---

## 3. New overclaims introduced in v0.2

The paper grew from 5272 → 8363 words (+59%). That is a lot of room for new overreach. I audited for it specifically. Verdict: **no critical or major new overclaims**; two minor items to tighten.

**Minor-1: §4.2 claim "every §4 property above the effect layer — audit chain integrity, replay determinism, policy universality — builds" (line 153).** The claim chains five properties to the well-formedness check. Policy *universality* in particular is asserted as derivable from well-formedness, but §4.3's own Universality contract explicitly says it is *not* a substrate invariant ("an invariant of the deployed stack, not the substrate"). So §4.2's rhetorical framing slightly overreaches what §4.3 admits. **Fix:** trim "policy universality" from the §4.2 list or cross-reference §4.3's hardening target. Cost: one sentence.

**Minor-2: Abstract claim "stronger determinism guarantee than CAMO's aspirational seed replay" (line 24).** Defensible, because the paper's NO-OP corollary operates at canonical-serialization granularity and CAMO operates at outcome-delta-distribution granularity — so they are strictly comparable only in the NO-OP regime. The paper's §2.5 treatment is careful about this. The Abstract's single-line version omits the qualifier and could read as an unconditional claim. **Fix:** append "for the NO-OP intervention case" to the Abstract sentence — seven words, no loss. A CAMO author on the PC would appreciate the precision.

**Non-issue I checked: title change.** "Toward Accountable Neurosymbolic Runtimes: The Persistence OS Substrate" uses *Runtime* in the title but *Substrate* in the subtitle, which matches the paper's internal distinction (the runtime is Plan+Txn+REPL together; the substrate is the four Phase-1 modules plus the spec-registered plan-node contract). This is slightly subtle but correct.

**Non-issue I checked: Case B claim "every Persistence invariant — immutable decision trail, effect-captured non-determinism, EDN playbook AST, counterfactual-validated skill promotion — is stress-tested against real-money risk" (line 308).** EDN playbook AST and counterfactual-validated skill promotion involve Plan-module functionality (Phase 2). However the preceding sentence correctly states "preliminary prompt-tuning results … are therefore `[TBD — camera-ready]`" — so the "stress-tested" phrasing reads aspirationally, not as shipped fact. Acceptable framing.

---

## 4. Three biggest PC-reviewer objections a NeSy PC would raise against v0.2

Even with v0.2 in its current honest form, three objections are likely. I list them in decreasing order of severity.

### Objection 1 (headline-novelty risk): "The regulator-replay benchmark is the paper's most novel contribution, and as of the submission deadline it has no measured data."

§6.3 commits to shipping "50-trajectory synthetic project-finance corpus, CC-BY-4.0" with camera-ready, but at the abstract deadline (2026-06-09) and paper deadline (2026-06-16) there will be exactly zero numeric rows for this benchmark. A strict PC may read "We propose and evaluate a new benchmark" vs. "We propose a new benchmark and will evaluate it in the camera-ready" as a difference-in-kind that lowers the contribution ceiling from "novel evaluation" to "protocol proposal." The paper's Reproduction-Plan framing is honest but does not escape this: NeSy submissions are typically judged on what is in the submission, not what is promised for camera-ready. **Mitigation before submission:** ship at minimum a 10-trajectory generator + reconstruction-script walkthrough in `bench/regulator_replay/` before June 16 so the artifact contains *something* a reviewer can run, even if the full 50-trajectory table waits for camera-ready. Alternatively, reframe §6.3 as "Regulator-replay: a benchmark proposal" and accept the lower novelty weighting — this is safer, lower-reward, and preserves honesty.

### Objection 2 (representation-vs-results): "You ship four Phase-1 modules and honestly label three as Phase 2. Is this a systems paper with a formal appendix, a position paper, or a full neurosymbolic-agent paper? NeSy 2026 expects results."

The paper reports two formally-proven properties (Prop 2, NO-OP Corollary) that are genuinely nice, plus 463 tests, plus a Reproduction Plan where every numeric table is `[TBD]` at submission. A PC member who reads the paper as "full evaluation paper" will see empty tables and downgrade. A PC member who reads it as "formal methods paper" will expect more propositions with proofs. A PC member who reads it as "position paper" will wonder why there is a 463-test artifact. **Mitigation:** the paper's §1 already has a "What this paper reports, honestly" paragraph — I would strengthen the first sentence of the Abstract to name the paper's genre explicitly: *"We present Persistence, a cognitive-runtime substrate, and report the Phase-1 reference implementation as a **systems contribution with formal guarantees** (463 tests, two machine-checked propositions on the shipped artifact, with a Reproduction Plan for four benchmarks deferred to the camera-ready)."* Naming the genre up front tells the PC what lens to use.

### Objection 3 (evaluation vacuum): "§6.5 Case A, C, D are 3–5-sentence vignettes with no numbers. What do they contribute?"

Case A (project finance), Case C (insurance), Case D (hospitality) are now anonymized vignettes with zero metrics and zero identity disclosure. They are honest, but they do not currently *pay for their page budget*. A reviewer will ask: if there are no numbers and no names, why are these in the evaluation section at all? They could be moved to §7.3 "Adoption path" as qualitative deployment patterns without losing anything, freeing §6 to focus on Case B (Adaptive Trader v2, named, with real baseline numbers) as the single case study. **Mitigation:** either promote *one* of A/C/D to a numeric case (Case A on a synthetic BankabilityAI-shape corpus would be the easiest — same synthetic pipeline that feeds §6.3 can feed §6.5-A), or demote A/C/D from §6 Evaluation to §7.3 Adoption path as non-numeric examples. Both are honest; both tighten the paper.

---

## 5. Abstract-readiness grade

**Abstract submission 2026-06-09 (49 days away): READY WITH TWO 7-DAY TIGHTENINGS.**

The Abstract as currently written is honest, correctly scoped, and names all the right shipped contributions. I do *not* recommend submitting it as-is — there are two cheap tightenings that materially improve it before the June 9 deadline, both of which are measured in hours not weeks:

1. **Genre declaration (15 min).** Add a phrase to the second sentence of the Abstract naming the paper as a *systems-contribution-with-formal-guarantees* paper (per Objection 2 above). This single phrase tells the PC which evaluation rubric to apply and preempts the "half-empty table" reading.

2. **NO-OP qualifier in Abstract (2 min).** Append "for the NO-OP intervention case" to the trajectory-hash sentence. Seven words, eliminates the one minor overreach in the current Abstract.

Without these two fixes the Abstract is still defensible; with them it is substantially better positioned. Neither requires code changes.

**There is no blocker preventing the June 9 Abstract submission.** The paper passes my honesty bar.

---

## 6. Overall research-alignment grade: 8.2 / 10

Breakdown:
- **Honesty & code-fidelity (40%): 9.5/10.** Every R1 F-id resolved or explicitly Phase-2 deferred. The paper's claims match what `git show HEAD -- src/persistence/` actually contains. No critical or major new overclaims.
- **Formal contribution quality (25%): 8/10.** Two propositions with machine-checkable witnesses (Prop 2 enforcement via `Unhandled`; NO-OP Corollary verified by named test). Prop 1 softened to honest complexity statement (O(\|D\|) + O(\|Δ\|)) with isolation as the load-bearing property. What's missing: formal statement of the Integrity contract, formal output grammar for `explain_for_llm`, and a proof sketch for Prop 2 (even two lines).
- **Evaluation completeness (15%): 6.5/10.** §6 is a Reproduction Plan with zero numeric rows at submission, plus pre-existing Adaptive Trader baseline. This is the weakest dimension and the one most likely to draw PC objections. The `[TBD]` labelling is honest but a strict NeSy PC will note the contribution ceiling this implies.
- **Neurosymbolic positioning (10%): 9/10.** Datalog and Z3 correctly separated from shipped substrate list; the spec-first `:persistence.plan/node` registration now named as a methodology contribution; `explain_for_llm` self-healing contract given dedicated paragraph in §4.7. These are exactly the right emphases for NeSy.
- **Writing quality & reviewer empathy (10%): 7.5/10.** The paper at 8363 words is dense but well-structured. The Phase-1/Phase-2 tags are consistent. The "What this paper reports, honestly" paragraph in §1 is unusual in a good way — a PC reviewer who lands on it will feel they can trust the rest of the paper. One concern: the paper reads long for what it delivers, and the NeSy 10-page limit will force a compression pass that may eat some of the elevated contributions' newly-gained paragraph space.

**Weighted total: 8.23 / 10.** Call it **8.2**.

**Compared to Round 1: +1.7 (6.5 → 8.2).** This is the single biggest improvement of any ARIS round I have graded.

---

## 7. Go / no-go for Round 3

### GO for Round 3.

Target (per MEMORY.md ARIS ladder, 4 → 7 → 7.8 → 9): Round 2 min ≥ 8.0. **R4 Round 2 = 8.2. Pass.** Assuming R1/R2/R3 also cross 8.0 (I have not reviewed them but W-integration / W-boundary / W-rigor summaries suggest they should), Round 2 gate clears.

### Minimum Phase-2 (code-side) work to make §6 non-aspirational by 2026-07-20 camera-ready

To convert the Reproduction Plan from "promised" to "shipped" in 56 days plus a 34-day camera-ready window — and to preempt PC Objection 1 — the minimum code-side work I see is:

1. **`Runtime.assert_universal_audit(catalog)` (1 half-day).** Closes §4.3 Universality contract hardening. Cheap, strictly additive, removes one R4-flagged soft claim and one anticipated PC objection.

2. **Per-step rng-state recording in the replay engine (3–5 days).** Unblocks generalizing the NO-OP corollary to stochastic-LLM trajectories. Replace `_advance_rngs_to_match` with a recorded `rng_state_vector: list[bytes]` per Fact; in replay, restore state before each step. This is the gating dependency for §6.2 camera-ready numbers.

3. **50-trajectory synthetic regulator-replay generator + reconstruction script + dataset release (2 weeks).** This is the paper's novelty flagship. Plan: (a) BankabilityAI-shape scoring agent (WACC / gearing / concession-fee / sector) running deterministically on synthetic inputs; (b) persist each decision trajectory through the Phase-1 Fact module; (c) reconstruction script that reads the datom log + plan AST, re-executes, compares byte-for-byte under `effect.canonical.canonical_dumps`; (d) CC-BY-4.0 licensed corpus shipped in `bench/regulator_replay/`.

4. **One measured p95 latency number for the abstract (2 hours).** Any number — `as_of` over a 10k-datom `InMemoryStore` corpus. Lets the paper swap one `[TBD]` for a real datum, improving credibility meaningfully at near-zero cost.

5. **(Optional but recommended)** A 10-trajectory generator walkthrough landed in the repo before the June 16 paper deadline (not camera-ready). Lets a submission reviewer run the regulator-replay harness end-to-end at tiny scale before voting.

Items 1 and 4 are a day of work. Items 2 and 3 are the serious Phase-2 investment. Item 5 is a hedge against Objection 1.

### Round 3 agenda suggestion

For Round 3, I'd ask the four reviewers to grade against a *revised paper* that incorporates the two Abstract tightenings (genre declaration, NO-OP qualifier), the `Runtime.assert_universal_audit` landing, and a resolved choice on §6.5 Case A/C/D (promote one to numeric, or demote all to §7.3). Those three changes are the "polish pass" that moves R4 from 8.2 toward 9.0. The bigger Phase-2 investments (regulator-replay harness, rng recording) don't need to land before Round 3 grading — they land before the camera-ready.

---

*— R4, 2026-04-21. Graded against `a28d8f5`, paper v0.2 (454 lines, 8363 words), 463 tests green locally. An honest 8.2 beats a generous 9.0.*
