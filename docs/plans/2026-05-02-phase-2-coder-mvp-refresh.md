# Phase 2 — `persistence-coder` MVP — Post-substrate-completion refresh

**Date:** 2026-05-02
**Status:** baseline locked; pending ARIS R1 on the refresh delta only (the base doc passed R4)
**Author:** Nawfal Saadi (with Claude Opus 4.7)
**Audience:** persistence-os engineering — Phase 2 of 16-week commercial-first roadmap

**Base design (canonical for architecture):**
- [`2026-04-30-phase-2-persistence-coder-design.md`](2026-04-30-phase-2-persistence-coder-design.md) — R3-W3 / R4 PASS

**Predecessors:**
- [`2026-04-29-adapter-sdk-contract-design.md`](2026-04-29-adapter-sdk-contract-design.md) — adapter SDK contract + ADR-17
- [`2026-04-27-persistence-os-v1.0-roadmap.md`](2026-04-27-persistence-os-v1.0-roadmap.md) — v1.0 ferrari-first roadmap
- conductor track `persistence-os-product_20260429/STATUS.md` — Phase 2 open

**Substrate-completion bundle shipped at:** `v0.8.5a1` annotated sub-tag on `acb237c`, branch `feat/v0.9-2.0d-completion`. Reachable from `main` and `feat/v0.9-persistence-coder` after the 2026-05-02 PM fast-forward to `2494d9b`. NOT pushed to origin (internal-alpha policy).

**Target tag(s):** `v0.9.0a1` (coder MVP) → `v0.9.0` (post-dogfood polish)
**Target branch:** `feat/v0.9-persistence-coder`
**Window:** 2026-05-04 → 2026-05-22 (compressed; June 5 hard cutoff preserved as buffer)

---

## 1. Status & relationship to 2026-04-30 design

The base design at `2026-04-30-phase-2-persistence-coder-design.md` passed ARIS R4 and remains canonical for: § 3 architecture (3.1–3.6), § 4 substrate-completion narrative, ADR-1 through ADR-10, and the strategic frame (§ 2). Substrate-completion (sub-phases 2.0a, 2.0b, 2.0c, 2.0c-ext, 2.0c-prime, 2.0d) shipped May 1 with ARIS R2 hard-mode codex closing at 6.4 / 4.0 (W3 honest-rescope of `:code/exec` accepted as architecturally correct; F4 xfail-strict marker queued as v0.9.x acceptance signal).

**This refresh supersedes** the base doc's:
- § 3.2 SDK-only consumption table + "Proposed v0.8.5a1 SDK additions" paragraph
- § 3.4 agent-loop diagram escape-hatch fallback comments
- § 3.7 fold-row caveat (now split into `:fold/chosen` foldl-with-marker and `:fork/*` 4-datom rewired-on-fork)
- § 5 G2 negative-test row + G8 dogfood criteria + G10 demo qualitative criteria
- § 6 task breakdown calendar
- § 8 Open Question #3 (closes), Open Question #6 (new)
- § 7.5 timeline-pressure risk re-rating

The base doc's other sections, ADRs, and analysis remain authoritative.

---

## 2. Locked decisions

Five strategic decisions plus two Bhatt-derived items locked through brainstorming on 2026-05-02.

### Q2 — SDK posture (resolves § 3.2 / § 3.4 / ADR-2)

**Decision:** the agent imports `s.plan.execute` and `s.plan.mcts_search` directly from the curated namespace shipped in 2.0c-prime (#147). **No `s.escape.*` callsites in `src/persistence/coder/`.** G1 lockfile (`coder.lockfile.json`) starts with `escape_callsites: []` from the alpha tag onwards.

**Rationale:** #147 shipped as a 24-method curated `s.plan` namespace including `mcts_search`/`mcts_promote`/`apply_action`. #148 (`s.mcts` namespace) was closed as redundant per the May 1 STATUS-decision log — MCTS is reachable through `s.plan.mcts_search`. The "until #147 / #148 ships, fall back to `s.escape.plan`" infrastructure described in the base § 3.2 / § 3.4 is no longer needed; the agent ships against fully-curated SDK from day one.

The escape-hatch infrastructure stays alive in the SDK itself (`s.escape.*` continues to exist for unknown-future-gaps and is a documented out-of-contract surface per Adapter SDK ADR-1) but is exercised only by a fixture-driven unit test, not by a real agent callsite.

### Q3 — Dogfood scope (resolves § 8 OQ #3)

**Decision:** G8 baseline = agent rebuilds `skills/builtin/implement_function.py`. The recorded trajectory deliberately includes a **user-injected wrong path corrected via REPL pause → branch → fold → commit** at a known step boundary. The trajectory is seeded so the wrong-path decision occurs deterministically.

**Rationale:** the wedge differentiator is "the user can debug a live agent and provably steer its trajectory" — not "the agent can rebuild a skill" (Cursor / Cline / Aider have skill-rebuild variants). The deliberate-wrong-path scenario makes the wedge concretely visible in the recorded trajectory. G8 stays falsifiable; G10's storyboard (see § 6 below) upgrades from "agent does the right thing" to "user steers agent off a wrong path." Substrate-helper rebuild stays as a Phase 3 prelude option (not Phase 2 deliverable).

### Q4 — #175 placement (new task row)

**Decision:** `Transaction.completed_step_ids` threading + `delete_step` downstream-check + property test lands as a new task row **2.0e** at calendar Mon 2026-05-04 (~0.5–1d), **before 2.1a scaffolding starts**. Substrate-side change shipped on `feat/v0.9-persistence-coder`; off the agent's critical path.

**Rationale:** `delete_step` cannot enforce its own invariant ("only allowed if no downstream step has executed") without `Transaction.completed_step_ids` threading. The lockfile snapshot at 2.4c pins the agent's allowed `:plan/edit` semantic surface; #175 must land before 2.4c. Co-locating with agent code (folded into 2.1a or 2.3a) blurs worktree boundaries. Dedicated 2.0e row preserves the "every load-bearing change has its own row" discipline that worked across PG6 / 2.0a–2.0d.

### Q5 — Schedule posture

**Decision:** target **2.4c by ~Fri 2026-05-15 / Mon 2026-05-18** (the lockfile snapshot — load-bearing date, unblocks `skill-systems-integration_20260430` Phase 7). Let 2.4d ARIS + dogfood polish + Phase 3 prelude work fill the back half of the original June 5 window.

**Rationale:** substrate-completion shipped 14 task-table-days of work in ~1 calendar day on May 1. With Q3=D scope locked, ~12 task-table-days of remaining work fit easily in ~10 calendar working days for the 2.0e → 2.4c critical path. Buffer (~13 calendar working days, May 25 → June 5) absorbs:
- 2.4d ARIS R2 + tag `v0.9.0a1`
- 2.4b 60-second demo polish
- Chris advisor brief draft (with Bhatt framing baked in — see § 9)
- Phase 3 prelude work (privacy-arch sketch, `:juba/*` spec namespace from Karpathy reframe)

§ 7.5 risk re-rating: timeline pressure drops from "no margin" to **"low — ~2x calendar buffer based on substrate-completion velocity"**.

### Bhatt-1 — `s.plan.judge` curated method (new task row)

**Decision:** ship `_PlanNamespace.judge(plan, criteria, *, evaluator=None) -> ScoreResult` as a thin curated wrapper over the re-exported `LLMJudgeEvaluator` (re-exported in `persistence.sdk.__init__.py:78,124` per 2.0d). Lands as new task row **2.0f** at calendar Mon 2026-05-04 (~0.5d), `@experimental("v0.9.0a1")`. Parametrized stability test mirrors 2.0c-prime pattern. CHANGELOG-sdk.md entry under v0.9.0a1.

**Rationale:** Bhatt's "multi-agent collaboration" principle (different models cross-check each other) and "tests as guardrails" principle (failing test as MCTS terminal-bad signal) both want a standalone judge invocation surface — "score this plan against these criteria, return scalar — no MCTS." The 2.0d re-export expansion landed the *type* (`LLMJudgeEvaluator`) but not a curated *invocation method* on `_PlanNamespace`. v0.8.5a1 substrate-completion is a coherent ARIS-R2'd bundle; patching it with v0.8.5a2 blurs the bundle boundary. A 2.0f row treats `s.plan.judge` as a Phase 2 prerequisite (same shape as 2.0e for #175) — honest because the agent's MCTS scoring path in 2.3b is the consumer.

**Surface signature (locked):**

```python
@experimental("v0.9.0a1", reason="Phase 2.0f curated judge surface — Bhatt principle 5")
def judge(
    self,
    plan: Plan,
    criteria: Mapping[str, Any],
    *,
    evaluator: Evaluator | None = None,
) -> ScoreResult:
    """Score `plan` against `criteria`. If `evaluator` is omitted, defaults
    to `LLMJudgeEvaluator` constructed from substrate's default LLM config."""
```

`ScoreResult` is the existing `persistence.plan` dataclass (already re-exported at v0.8.0a1 through the `persistence.sdk.__init__.py` value-shape subset). G1 lockfile adds `s.plan.judge` to the allowed-set.

### Bhatt-2 — `:test/exec` queued as v0.9.x track (new ADR-11; no task row)

**Decision:** v0.9.0a1 uses `:code/exec` invocation of `pytest --json-report` as the test-runner shim, parsed by a builtin skill (per OQ #6 below). First-class `:test/exec` effect handler is queued as a v0.9.x track. ADR-11 captures the queue.

**Rationale:** Bhatt's principle 3 ("tests as guardrails — harness auto-runs tests, failure stops the loop") wants MCTS to treat failing tests as terminal-bad. The v0.9.0a1 path gets there via `s.plan.judge` calling a builtin test-runner skill that itself uses `:code/exec` — one level of indirection but functionally equivalent. First-class `:test/exec` (deterministic seeds, structured pass/fail/error/skip datom shape, native MCTS terminal-on-fail) is a refinement, not a blocker.

The threat model for `:test/exec` converges with the v0.9.x real-OS-sandbox track (test runner needs the same OS-level isolation as `:code/exec`). Both ship together. **Falsifiable acceptance signal:** new G11 xfail-strict marker (see § 5 below) — flips XPASS when `:test/exec` lands.

---

## 3. Updated § 6 task table (compressed schedule)

Replaces the base doc's § 6 calendar. Working day numbering kept for cross-reference; calendar dates are the load-bearing source of truth. Today is calendar Sat 2026-05-02 (pre-day-2). Substrate-completion ate task-table-days 1–14 in 1 calendar day on May 1.

| # | Task | Calendar target | Deliverable |
|---|---|---|---|
| 2.0e | `Transaction.completed_step_ids` threading + `delete_step` downstream-check + property test (#175) | Mon 2026-05-04 (~0.5–1d) | substrate change shipped on `feat/v0.9-persistence-coder` |
| **2.0f** | `s.plan.judge` curated method on `_PlanNamespace` + parametrized stability test + CHANGELOG-sdk entry, `@experimental("v0.9.0a1")` (Bhatt-1) | Mon 2026-05-04 (~0.5d) | curated SDK invocation surface for `LLMJudgeEvaluator` |
| 2.1a | `persistence.coder` skeleton + CLI entry | Tue 2026-05-05 | `python -m persistence.coder --task "..."` runs no-op loop |
| 2.1b | LLM provider abstraction + Anthropic adapter | Tue-Wed 2026-05-05/06 | first `:llm/*` datoms emitted |
| 2.1c | G1 contract test live (lockfile alpha-mode, `escape_callsites: []`, `s.plan.judge` in allowed-set) | Wed 2026-05-06 | `test_sdk_consumer.py` green on the skeleton |
| 2.2a | `:fs` + `:shell` effect handlers | Thu 2026-05-07 | agent reads/writes/globs/greps; allowlist versioning datomized |
| 2.2b | `:code` + `:git` effect handlers + `pytest`-via-`:code/exec` shim (per ADR-11) | Thu-Fri 2026-05-07/08 | agent runs sandboxed Python (W3 soft-isolation) + commits + runs tests |
| 2.3a | Plan escalation gate + Plan AST builder | Mon-Tue 2026-05-11/12 | `:strategy/plan` decisions execute via `s.plan.execute` |
| 2.3b | MCTS branch + `s.txn.fork` / `s.txn.fold_into` integration + `s.plan.judge` evaluator wiring | Tue-Wed 2026-05-12/13 | `:strategy/branch` rolls out via `s.plan.mcts_search`; `s.plan.judge` exercised |
| 2.3c | Skill library: registry, lookup, builtin set (incl. `skills/builtin/run_tests.py` per OQ #6) | Wed 2026-05-13 | 5 builtin skills + custom-skill registration |
| 2.3d | REPL-steering session class | Thu 2026-05-14 | pause/snapshot/branch/fold/commit ops live; `:repl/request` + `:repl/response` datoms emitted |
| 2.4a | Dogfood: agent rebuilds `implement_function` skill **with deliberate-wrong-path correction** (Q3=D) + tunes confidence threshold from telemetry | Thu-Fri 2026-05-14/15 | golden trajectory recorded |
| **2.4c** | **Coder contract spec doc + lockfile + CI gate** (`coder.lockfile.json` alpha-mode, `escape_callsites: []`) | **Fri 2026-05-15 / Mon 2026-05-18** | `docs/spec/coder-contract-v0.9.md` + `coder-contract.lock` + `coder.lockfile.json` — **unblocks skill-systems-integration Phase 7** |
| 2.4b | 60-second demo video + walkthrough doc (Q3=D storyboard, see § 6) | Mon-Tue 2026-05-18/19 | `docs/demos/coder-rewind.md` + video |
| 2.4d | Phase 2 ARIS R2 + tag `v0.9.0a1` | Wed-Fri 2026-05-20/22 | tag + CHANGELOG |
| **— buffer —** | Phase 3 prelude (privacy-arch sketch, `:juba/*` spec namespace), Chris advisor brief draft (Bhatt framing baked in), README "Failure modes addressed" addition routed to skill-systems-integration | 2026-05-25 → 2026-06-05 | optional carryover; June 5 hard cutoff preserved |

**Critical path:** 2.0e + 2.0f (parallel substrate-side, half-day total) → 2.1a → 2.1b → 2.1c (G1 live) → 2.2a/b → 2.3a/b/c/d → 2.4a → **2.4c (Phase 7 unblock)** → 2.4b → 2.4d (`v0.9.0a1` tag).

---

## 4. Bookkeeping edits to the base doc (catalogued)

These edits patch wording in the 2026-04-30 design to reflect what actually shipped at v0.8.5a1. Applied as a follow-up patch when convenient (not blocking on this refresh's adoption). The refresh doc is the operational source of truth in the meantime.

| § (base doc) | Edit | Source |
|---|---|---|
| § 3.2 — Allowed-Entrypoints table | Add row: `s.plan.* (24 curated methods)`. Add row: `s.txn.fork / .fold_into` (2.0c-ext + 2.0c). Add row: `s.plan.judge` (Phase 2.0f). Remove `s.escape.plan` from agent's allowed-set (move to ADR-2 only as out-of-contract). | Q2 + Bhatt-1 |
| § 3.2 — "Proposed v0.8.5a1 SDK additions" paragraph | Drop entirely. #147 shipped; #148 closed; `s.plan.judge` is a 2.0f task row, not a proposed substrate addition. | Q2 + Bhatt-1 |
| § 3.4 — agent loop diagram | Replace "until #147 ships: s.escape.plan.execute" with direct `s.plan.execute(plan)`. Replace "until #148 ships: s.escape.plan.mcts_search" with direct `s.plan.mcts_search(...)`. | Q2 |
| § 3.7 — fold row | Split into two rows. Row A: `:fold/chosen` (legacy v0.8.0a1 `s.txn.fold` — foldl-with-marker, all-commit). Row B: `:fork/probe` + `:fork/branch` × N + `:fork/score` × N + `:fork/chosen` (rewired `s.txn.fold_into` + new `s.txn.fork` per 2.0c-ext — substrate-true rollback, only-chosen-commit). | 2.0c-ext rewire |
| § 5 G2 negative-test | `ImportError` → `CodeExecForbiddenImport`. Drop `ENETUNREACH`. Replace `PermissionError` for "writes outside scratch" with: "any write from sandbox child → killed by `SIGXFSZ` (RLIMIT_FSIZE=0 W3 mechanic)." Add cross-reference: F4 `/etc/passwd` xfail-strict in `tests/effect/test_code_exec.py:741-848` is **separate from G2** — v0.9.x real-OS-sandbox acceptance signal, not v0.9.0a1 acceptance. | W3 honest-rescope alignment |
| § 5 G8 | Reword to: "recorded trajectory of agent rebuilding `skills/builtin/implement_function.py` **with a user-injected REPL pause-branch-fold-commit correction at step N** replays byte-identical against a fresh substrate. Property: chosen branch's facts committed to `db.history()`, counterfactual branch's facts absent (per `DB.fork` Property 4 verification at @max_examples=200)." | Q3=D + 2.0c-ext rollback discipline |
| § 5 G10 | Storyboard locked (see § 6 below). Qualitative pass remains qualitative. | Q3=D |
| § 7.1 | Cross-reference ADR-5's W3-rescope amendment (already in-doc, lines 482–503). No new text. | bookkeeping |
| § 7.5 | Re-rate from "no margin" to "low — ~2x calendar buffer based on substrate-completion velocity." Mitigation reads "buffer absorbs 2.2 / 2.3 surprises with room left for Phase 3 prelude." | Q5 |
| § 8 OQ #3 | Close. "Resolved: agent rebuilds skill (G8 falsifiable) with deliberate-wrong-path REPL correction (G10 storyboard). Substrate-helper rebuild stays as Phase 3 prelude option." | Q3=D |
| § 8 — new OQ #6 | Add: "v0.9.0a1 ships the `pytest`-via-`:code/exec` shim — does it land as a builtin skill (`skills/builtin/run_tests.py`) consumed by the agent's planner, or as a fixed wrapper inside `_effects/code.py`? Resolves during 2.2b." | Bhatt-2 |
| § 9 — new ADR-11 | Add as drafted in § 7 below. | Bhatt-2 |

---

## 5. Acceptance gates — wording delta + new G11

Replaces the base doc's § 5 entries for G2 / G8 / G10 and adds G11. G1 / G3–G7 / G9 unchanged from base.

| Gate | Surface | Test | Pass criterion |
|---|---|---|---|
| **G2 (refreshed)** | dynamic | per-effect property tests; replay byte-identity at @max_examples=200. **Negative subcase:** non-allowlisted import in sandbox body → `CodeExecForbiddenImport`; any write from sandbox child → killed by `SIGXFSZ` (RLIMIT_FSIZE=0). The negative subcase asserts the bad body cannot complete successfully because the capability is not reachable, NOT that we have detected it via static analysis. | 100% |
| **G8 (refreshed)** | golden | recorded trajectory of agent rebuilding `skills/builtin/implement_function.py` **with a user-injected REPL pause-branch-fold-commit correction at step N** replays byte-identical against a fresh substrate. Property: chosen branch's facts committed to `db.history()`, counterfactual branch's facts absent (per `DB.fork` Property 4 verification at @max_examples=200). | 100% |
| **G10 (refreshed)** | manual | recorded video shows the storyboard at § 6: task → plan → agent picks wrong path at step 4 → user pauses via REPL → branches with corrected directive → fold scores both via `s.plan.judge` → commits corrected branch → counterfactual survives in audit log → replay verifies byte-identical → `s.audit.verify_chain()` OK. | qualitative pass |
| **G11 (NEW, Bhatt)** | xfail-strict | given a recorded trajectory where MCTS scores 3 candidate branches and exactly 1 passes the `pytest`-via-`:code/exec` shim, `s.plan.judge` ranks the passing branch strictly above the failing branches. Marker flips XPASS when `:test/exec` lands as v0.9.x track per ADR-11. | falsifiable acceptance signal for the v0.9.x test-runner-as-effect track |

**Cross-reference for G2:** F4 `/etc/passwd` xfail-strict regression test at `tests/effect/test_code_exec.py:741-848` is the v0.9.x real-OS-sandbox acceptance signal — separate from G2. Do not conflate.

---

## 6. G10 demo storyboard (60 seconds)

Locked storyboard for Q3=D. Pre-baked deterministic seed produces the wrong-path decision at step 4.

| Time | Action | Substrate primitive on display |
|---|---|---|
| 00:00–00:10 | User invokes `persistence-coder --task "implement parse_csv_row + tests"`. Agent enters planning mode. | `s.plan.execute` |
| 00:10–00:25 | Agent runs ReAct loop. **At step 4 it picks a brittle regex parser** (the deliberate wrong path; trajectory seeded so this happens deterministically). | `s.fact.transact`, `:llm/messages` |
| 00:25–00:35 | User opens REPL client. `coder.pause()`. Agent halts mid-step. `coder.snapshot()` shows last 50 datoms incl. the regex decision. | `s.repl.serve`, `:repl/request` |
| 00:35–00:50 | User: `coder.branch(directive="use csv.reader, regex is brittle for quoted fields")`. Agent now has two heads. `coder.fold(probe="run pytest on parse_csv_row")`. `s.plan.judge` scores: regex branch fails 3/5 tests, csv.reader branch passes 5/5. | `s.txn.fork`, `s.plan.judge`, `s.plan.mcts_search` |
| 00:50–01:00 | User: `coder.commit(branch_id=csv_reader)`. Counterfactual (regex) branch survives in audit log. Replay confirms byte-identity. `s.audit.verify_chain()` OK across all ~250 datoms. | `s.replay.replay`, `s.audit.verify_chain` |

The wrong-path framing (concrete bug scenario, user-steers-off, MCTS confirms, commit) is what § 7.4 of the base doc described as the visual mitigation for "REPL steering UX is hard to demo." Now it IS the demo, not the mitigation.

---

## 7. ADR-11 — `:test/exec` queued as v0.9.x effect-handler track

**Decision:** v0.9.0a1 uses `:code/exec` invocation of `pytest --json-report` as the test-runner shim, parsed by a builtin skill (`skills/builtin/run_tests.py`, see OQ #6). First-class `:test/exec` (deterministic seeds, structured pass/fail/error/skip datom shape, native MCTS terminal-on-fail integration via `s.plan.judge`) is queued as a v0.9.x track.

**Rationale:** Bhatt's principle 3 ("tests as guardrails — failure stops the loop") wants MCTS to treat failing tests as terminal-bad. The v0.9.0a1 path gets there via `s.plan.judge` calling a builtin test-runner skill that itself uses `:code/exec` — one level of indirection but functionally equivalent. First-class `:test/exec` is a refinement, not a blocker.

**Sibling-track convergence:** the threat model for `:test/exec` converges with the v0.9.x real-OS-sandbox track. A test runner needs the same OS-level isolation as `:code/exec` (untrusted-author test bodies, network denial, deterministic clocks, structured result extraction). Both ship together when the v0.9.x sandbox-redesign track lands.

**Falsifiable acceptance signal:** new G11 xfail-strict marker (see § 5). The marker flips XPASS when `:test/exec` lands; CI refuses the v0.9.x release tag if the marker is not removed, mirroring the F4 acceptance-signal pattern from W3.

**Reversal cost:** none — `:test/exec` is purely additive. v0.9.0a1's `pytest`-via-`:code/exec` path stays as a fallback / lightweight test-runner; users who don't need the structured datom shape can keep using it.

**Cross-references:**
- `:code/exec` ADR-5 (base doc lines 474–503, with W3 amendment)
- v0.9.x real-OS-sandbox track (forward-pointer in base doc § 15)
- F4 xfail-strict marker `tests/effect/test_code_exec.py:741-848`
- `s.plan.judge` (this refresh § 2 Bhatt-1)
- G11 (this refresh § 5)

---

## 8. Open questions

### Closed

- **OQ #3 (base doc § 8.3) — dogfood scope.** Resolved per Q3 = D. Agent rebuilds skill (G8 falsifiable). Trajectory includes deliberate user-injected wrong path corrected via REPL pause-branch-fold-commit (G10 storyboard). Substrate-helper rebuild stays as Phase 3 prelude option.

### New

- **OQ #6 — `pytest`-via-`:code/exec` shim placement.** v0.9.0a1 ships the test-runner shim required by ADR-11 + G11. Does it land as a builtin skill (`skills/builtin/run_tests.py`) consumed by the agent's planner, or as a fixed wrapper inside `_effects/code.py`? Resolves during 2.2b implementation. Working hypothesis: builtin skill — keeps `_effects/code.py` agnostic and lets users override the test command via skill registration.

### Carrying forward (unchanged from base doc)

- **OQ #1 (base § 8.1) — Skill body Plan AST vs raw Python.** Resolved by base doc ADR-3.
- **OQ #2 (base § 8.2) — MCTS rollout count default.** Stays open; tuned during 2.4a dogfood telemetry per current design text.
- **OQ #4 (base § 8.4) — `Co-Authored-By: persistence-coder` git trailer.** Stays resolved per base.
- **OQ #5 (base § 8.5) — Telemetry from day one.** Stays resolved per base (no telemetry in v0.9.0a1).

---

## 9. Carryovers routed elsewhere (NOT Phase 2 design work)

External-validation work surfaced by the Bhatt brainstorm that does NOT belong in the Phase 2 design refresh:

- **README "Failure modes addressed" addition** (~30 lines mapping Bhatt's compound-error / context-overload / specification-vacuum to substrate primitives, layered on top of the existing skill-systems framing — does NOT replace it). Routed to `skill-systems-integration_20260430` track, branched off `main` per cross-track ADR-004. Not Phase 2 work.

- **Broader CLAUDE.md audit** (sibling product `CLAUDE.md` files + `~/Projects/CLAUDE.md`). `persistence-os/CLAUDE.md` is 82 lines and already passes Bhatt's principle 1 (map-not-manual). Broader audit routed to skill-systems-integration as a Phase-0 / Phase-4 housekeeping pass.

- **Bhatt framing baked into Chris advisor brief draft** ("compound error / context overload / specification vacuum" + the "failures are bugs of the harness, not bugs of the prompt" quote). Phase 2 buffer-time work, ~Mon 2026-05-25 onwards. Tracked under buffer row in § 3 task table.

---

## 10. Persistence channels (cross-references)

- **Conductor track:** `~/Projects/ai-box/conductor/tracks/persistence-os-product_20260429/STATUS.md` (this refresh appended as a 2026-05-02 block)
- **Vault:** memory-id pending ingest
- **Serena memory:** to be written as `persistence-os/v0.9-phase-2-refresh-2026-05-02` after commit
- **Auto-memory:** `MEMORY.md` index entry to be added; topic file at `project_persistence_os_phase_2_refresh.md`
- **Source brainstorm:** the Claude Opus 4.7 session of 2026-05-02 PM (questions Q2–Q5 + Bhatt items 1+2)
- **External validation:** `/Users/nawfalsaadi/Projects/research-output-2026-05-02-bhatt-harness-engineering.md`, vault memory-id `c6a77ac2-5567-4703-8743-46ceba573b5c`, tx=4450

---

## 11. Sign-off

This refresh locks the post-substrate-completion baseline for Phase 2 implementation. ARIS R1 review on the refresh delta only is recommended before 2.0e/2.0f start (the base doc's R4 PASS covers the architecture; ARIS R1 here covers the schedule + new gates + ADR-11 only — fast-pass expected).

Implementation plan to be produced via `superpowers:writing-plans` against this refresh doc, starting with 2.0e + 2.0f (parallel substrate-side) → 2.1a (skeleton).

Hard cutoff: **2026-06-05 (June 5 Friday)**. Load-bearing intermediate gate: **2.4c lockfile snapshot by ~2026-05-15 / 2026-05-18** (unblocks `skill-systems-integration_20260430` Phase 7).
