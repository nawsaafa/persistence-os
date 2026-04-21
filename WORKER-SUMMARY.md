# W-paper — ARIS R4 corrections on NeSy 2026 draft

**Branch:** `W-paper` (worktree at `.claude/worktrees/W-paper/`, forked from `main @ 5f97882`)
**Scope:** paper-only (§1 Discipline #4 — no pytest, no Python edits)
**Date:** 2026-04-21
**Paper file:** `paper/persistence-nesy-2026-draft.md` (v0.1 → v0.2)

## Word count

| Before | After | Δ |
|---|---|---|
| 5272 words (v0.1) | 8363 words (v0.2) | +3091 |

The paper grew because (a) the ARIS-R4 corrections required expanding §4.1/§4.3/§4.5/§4.7 with precise artifact-anchored formal statements (Prop 2 lifted to front-line; Corollary added to Prop 3; Merkle-chain integrity vs authenticity separated; self-healing `conform→explain→retry` contract now a labeled paragraph); (b) §6 was rescoped from six speculative tables to a Reproduction Plan with explicit "abstract vs camera-ready" fencing on every row; (c) per-section Phase-1 / Phase-2 hedging is now explicit rather than implicit. The paper is still well under the NeSy 10-page camera-ready limit at current density — §2–§8 will tighten during copy-edit before 2026-06-16.

## R4 findings addressed — section map

| R4 finding | Severity | Paper sections edited | Resolution |
|---|---|---|---|
| F1 — seven capabilities overclaim | CRITICAL | Abstract, §1 Contribution, §1 "What this paper reports, honestly", §2.6 Fig.1, §3 (invariants tagged Phase-1/Phase-2), §5 (modules split into shipped + designed), §8 Conclusion | Reframed to "four shipped / three designed"; Fig.1 split into Phase-1 ● and Phase-2 ○ columns; every §3 invariant has a Phase tag. |
| F2 — Prop 1 O(log n) HAMT claim false | CRITICAL | §4.1 Prop 1 (rewritten), §7.1 Limitations, §8 Future Work item (1) | Prop 1 now states branch is a logical operation returning a new DB value with parent-store isolation; Phase 1 complexity is `O(\|D\|)` on the list-backed InMemoryStore (honest); HAMT path-copy is a Phase-2 Store-Protocol swap. Isolation — the load-bearing property — holds unconditionally. |
| F4 — ed25519 in §4.1, §7.1, §7.2 + fabricated "20–40 ms" | MAJOR | §4.1 (provenance record), §4.3 (Integrity contract paragraph), §5.1, §7.1 (Write latency bullet), §7.2 (Privacy architecture), §8 Future Work item (3) | Removed ed25519 from all Phase-1 claims. Removed the "20–40 ms" figure. Ed25519 per-transaction signing now appears only as a Phase-2 privacy-posture extension (§7.2, §4.3, §8). Integrity (via sha256 Merkle chain + `verify_chain`) cleanly separated from authenticity (requires ed25519, Phase 2). |
| F6 — latency targets unmeasured | MAJOR | §5.1 Fact (Latency targets — Reproduction Plan paragraph), §6.6 Reproduction posture | The {50, 200, 100} ms p95 line is now flagged as a Phase-2 target over a persistent-trie backend at 1M-datom scale. Phase 1 reference numbers are `[TBD]` to be measured for the camera-ready. |
| F7 — Kuzu + mem0 claimed, only DictProjection ships | MAJOR | §5.1 Fact (projection surface paragraph), §5.8 system diagram (labeled "[Phase 2: Postgres + Kuzu + mem0]"), §2.6 Fig.1, Abstract | Projection surface is now `ProjectionAdapter` Protocol + reference `DictProjection` in Phase 1; Kuzu / mem0 adapters are explicit Phase-2 work per CHANGELOG. `mem0_adapter` correctly labeled as a legacy-write interceptor, not a projection. |
| F8 / F9 — bench/ and Makefile absent; LongMemEval, regulator-replay, CAMO harnesses not shipped | CRITICAL | §6 header (renamed "Evaluation — Reproduction Plan"), §6.1, §6.2, §6.3, §6.6 Reproduction posture; README.md (removed `make bench` + `Makefile` references, added "`bench/` lands in Phase 2" note) | §6 is now an honest Reproduction Plan. §6.3 rescoped to **50 synthetic project-finance trajectories**, CC-BY-4.0 licensed, generator + dataset shipping with camera-ready. §6.2 ships the already-testable NO-OP byte-identity corollary in the abstract and the 1000-trajectory table in camera-ready. §6.6 Reproduction posture adds an explicit "abstract vs camera-ready" fence on every row. |
| F10 — Datalog + Z3 overclaimed in §7.4 + Abstract | MAJOR | Abstract (symbolic-substrate list trimmed), §1 Neurosymbolic positioning, §7.4 (rewritten — shipped list separated from "adjacent systems we draw on"), §8 Future Work items (4), (5) | Datalog and Z3 removed from the shipped-substrate list. §7.4 now lists four shipped symbolic pieces (bitemporal query surface, EDN AST grammars, policy-as-data, Malli-style specs) and explicitly calls out Datalog engine + Z3-discharged `verify` leaves as adjacent systems / Phase-2 future work. |

### Undersold contributions — now elevated (R4 §"What's undersold")

| R4 asked to promote | New front-line location |
|---|---|
| `Runtime.is_well_formed()` + `Unhandled` enforcement (Prop 2) | Abstract ("decidable in linear time by `Runtime.is_well_formed`"); §3 Thesis; §4.2 (labeled "the paper's strongest formal contribution on the Phase-1 artifact"); §8 Conclusion |
| `spec.explain_for_llm` self-healing contract | Abstract ("LLM-self-healing hints"); §1 (in the shipped-capabilities list); §4.7 ("Self-healing contract (shipped)" paragraph — dedicated subsection); §7.4 |
| `trajectory_hash(cf) == trajectory_hash(factual)` on NO-OP (byte-identical) | Abstract (explicit callout: "stronger determinism guarantee than CAMO's aspirational seed replay"); §2.5 Related Work; §4.5 Corollary (labeled "tested"); §6.2 (NO-OP ships in the abstract); §8 |
| `verify_chain` Merkle audit chain | §4.3 promoted from one-line mention to dedicated subsection "The Merkle-hashed audit chain" with Integrity and Universality contracts; §8 |
| `:persistence.plan/node` spec-first registration ahead of Plan module | §1 ("deliberate parse-don't-validate move"); §2.3; §3 invariant 3 tag; §4.4 opening; §4.7 "Forward-compatible spec-first commitment" paragraph; §5.3; §5.5 |

### Also addressed

- **Case B (Adaptive Trader v2)** kept named with real baseline numbers (8 trades, PF 0.43, -$26.87); post-Persistence numbers marked `[TBD — camera-ready]`.
- **Cases A, C, D** compressed from speculative-paragraph-with-metrics to 3–5-sentence anonymized vignettes. No [TBD] metrics; no identities. Cases A, C, D map to BankabilityAI, Insurance Comparator, GuestFlow in MEMORY.md context but those client names do NOT appear in the paper.
- **§6.4 Plan optimization** removed entirely from this paper with an explicit "Phase-2 companion paper" redirect.
- **§2.4 Voyager framing** softened from "both promote skills aggressively on single success" to a factually-accurate "promotes skills on their first successful use (§3.3), without a statistical threshold" (R4 F11 side-fix).
- **§2.1 / §2.4 / §2.5 specific percentages** softened with a meta-note in §2.1 and References reminder that exact numbers will be cross-checked against primary sources in camera-ready (R4 F11).
- **Title** changed from "Persistence: A Bitemporal Effect-Typed Substrate for Accountable Neurosymbolic Agents" to **"Toward Accountable Neurosymbolic Runtimes: The Persistence OS Substrate"** — the "Toward" softens the "unified runtime" framing to match the Phase-1 reality, and "Substrate" is more honest than "Runtime" given Plan/Txn/REPL are not shipped.

## Paper-adjacent doc updates

- `README.md` — replaced shipping-status section with a module table showing Phase-1 shipped / Phase-2 designed split. Removed `make dev` / `make bench` / `make test` references. Added a "Phase 2" note where `bench/` is listed in the repository-layout diagram. Fixed the Privacy posture bullet that claimed "ed25519 per-transaction" as shipped; now correctly lists sha256 + Merkle chain for Phase 1 with ed25519 flagged as Phase 2. Updated module table with Phase-1/Phase-2 annotations on each row.
- `docs/phase-1-milestone-for-vault.md` — reframed the "Research contribution claim (for NeSy 2026)" paragraph from "unified substrate" to "substrate claim, scoped honestly to Phase 1" with explicit list of the two formal propositions that hold on shipped code. Fixed the Privacy & distribution bullet's "ed25519-signed provenance" claim to "SHA-256 content-hashed provenance + Merkle-chained audit (ed25519 per-transaction signing is Phase 2)".

## Deferred / out-of-scope

| Item | Reason |
|---|---|
| Changes to `src/persistence/**` or tests | Paper-only worker; W-boundary / W-integration / W-rigor own code. |
| Naming A/C/D clients | Task §3 ("Preserve Case B (Trader) as named; keep A/C/D anonymized. Never identify A/C/D by client name.") |
| New propositions / theorems | Task §"Out of scope" — only soften existing claims. |
| R1 findings (datom shape triplication, retroactive `valid_to`) | Owned by W-boundary worker; paper does not re-assert the broken shape. |
| R2 findings (audit deletion/reorder tests, ContextVar concurrency, `datetime.now()` lint) | Owned by W-rigor worker. Paper §4.3 Integrity contract notes deletion/reorder coverage is "a hardening target for Round 2" — acknowledges the gap without overclaiming. |
| R3 findings (replay.EffectHandler / effect.Runtime integration) | Owned by W-integration worker. Paper §4.5 states the extension from toy agent to LLM trajectories as Phase-2 prerequisite for §6.2 camera-ready numbers. |
| R4 F3 (every-effect-emits-a-datom universality) | Paper §4.3 now labels this as "an invariant of the deployed stack, not the substrate" and points to `Runtime.assert_universal_audit` as the Round-2 hardening that closes it. |
| R4 F5 (replay determinism proven only for toy agent) | Paper Prop 3 now carries the "per-step rng-consumption recorded" antecedent; §6.2 states per-step rng recording as Phase 2 prerequisite for the distributional CAMO table. Code-side change belongs to W-integration or a future W-replay-rng worker. |

## Honest assessment — before vs after

**Before (v0.1):** The paper asserts seven unified capabilities as shipped, a false HAMT-backed O(log n) branch, a fabricated 20–40 ms ed25519 overhead, Kuzu + mem0 as if implemented, and six evaluation tables whose every Persistence cell is [TBD]. A rigorous NeSy reviewer who opens the artifact would see four shipped modules, a list-backed store, no crypto-signing code, a DictProjection, and a missing `bench/` directory, and would reasonably mark the paper down for systematic overclaiming. R4's 6.5/10.

**After (v0.2):** The paper asserts exactly four shipped capabilities (plus three frozen-at-the-spec-boundary), a list-backed O(|D|) branch with parent-store isolation (true and tested), sha256 content-hashed provenance with Merkle audit chain (true and tested), DictProjection as the reference projection (true), and §6 as a Reproduction Plan with explicit "abstract vs camera-ready" fencing. The two formal propositions the paper does assert as holding on the Phase-1 artifact — `Runtime.is_well_formed` completeness and `trajectory_hash` byte-identity on NO-OP — are both backed by shipped tests. The paper is tighter, more honest, and gives reviewers a clean artifact-to-claim mapping: every numeric TBD is explicitly labeled, every Phase-2 item is flagged. Expected R4 ≥ 8.0 on Round 2.

## Merge order

Per R0 dispatch table: boundary → integration → rigor → paper. W-paper merges last so it can incorporate any shape changes from the earlier three without re-edits. No file overlap between W-paper and W-{boundary, integration, rigor}: this branch touches `paper/persistence-nesy-2026-draft.md`, `README.md`, `docs/phase-1-milestone-for-vault.md`, and adds `WORKER-SUMMARY.md`. Zero files under `src/` or `tests/` changed.
