# persistence-os — Project Brief

This is Nawfal Saadi's bitemporal, effect-typed, homoiconic cognitive runtime for accountable
neurosymbolic agents. It is a **substrate**, not a framework. The product is the substrate; the
first demo is `persistence-coder` (Phase 2 of the product roadmap).

This file orients fresh Claude Code sessions. It does NOT replace the paper, design docs, or ADRs —
it tells you what to read and what NOT to touch.

## Source of truth — read these in order

1. `README.md` — six invariants, seven modules, current shipping status.
2. `paper/` — the 9-section NeSy paper draft. (NeSy 2026 deadline was DROPPED — paper now stands as the formal spec, no longer time-pressured.)
3. `docs/plans/2026-04-30-phase-2-persistence-coder-design.md` — active Phase 2 design (ARIS R4 PASS).
4. `docs/plans/2026-04-29-adapter-sdk-contract-design.md` — adapter SDK contract + ADRs incl. ADR-17 (MCP confidentiality non-goal, v0.8). **Load-bearing — do not modify landed ADRs.**
5. Changelogs — match this tone in any new docs (measured, factual, no marketing). Top-level: `CHANGELOG.md` + `CHANGELOG-txn.md`. Per-module under `src/persistence/<module>/`: `CHANGELOG-effect.md`, `CHANGELOG-plan.md`, `CHANGELOG-replay.md`, `CHANGELOG-sdk.md`, `CHANGELOG-spec.md`. All carry the substrate-completion `v0.8.5a1` entries.
6. `~/Projects/ai-box/conductor/tracks/persistence-os-product_20260429/` — active product track. **STATUS append only.**
7. `src/persistence/{fact,effect,plan,txn,replay,spec,repl,sdk}/` — seven modules + curated SDK facade.

## What persistence-os IS

- **A substrate.** All agent state is immutable, content-addressed, bitemporal datoms. Every action is an effect. Every plan is an EDN AST. Every skill is a content-addressed AST subtree. Every shared state change is a transaction. Every LLM boundary has a spec. Everything is REPL-live.
- **Six invariants** (README §"The six invariants"). Accountability, counterfactual replay, composable safety, compositional skill learning, multi-agent coordination, and live production steering are **derived properties of one substrate**, not five engineered features.
- **A curated SDK surface** (`persistence.sdk`) layered over the substrate: `s.fact`, `s.txn` (incl. `s.txn.fold` / `s.txn.fork`), `s.plan` (incl. `s.plan.mcts_*`), `s.replay`, `s.spec`, `s.escape` (escape hatch to raw modules). Stability decorators (`@experimental` / `@stable(since=...)`) gate every public name.
- **Pre-alpha, local-only.** Latest substrate-completion bundle = `v0.8.5a1` annotated sub-tag (on `acb237c`, branch `feat/v0.9-2.0d-completion`, **NOT pushed**). Phase 7 GA target = `v0.9.0a1`.

## What persistence-os is NOT

1. **NOT a memory library wrapping mem0/Pinecone with vibes.** The six invariants and the paper are the contract; everything else is an implementation detail.
2. **NOT an agent framework replacing LangChain / CrewAI.** It ships primitives *below* frameworks (effects, plans, txn, replay, spec). A framework can sit on top; the substrate is still the substrate.
3. **NOT a creator-economy product.** Karpathy product reframe holds: this is a **B2B team-knowledge-work substrate** — jaggedness mitigation, mode-collapse defense, cognitive-core distribution. Position accordingly in any external-facing copy.
4. **NOT pip-installable / open-source-ready yet.** No PyPI release. No pushed git tag. Adapter SDK is `@experimental`. Treat all public surface names as movable until `v0.9.0a1`.
5. **NOT a real-OS-sandbox security product.** `persistence.effect.handlers.code` is **capability-denial-not-detection** (deny-list + child interpreter + setrlimit). The v0.9.x real-OS-sandbox track is queued separately with the `tests/effect/test_code_exec.py` F4 xfail-strict marker as the falsifiable acceptance signal. Do not market the current `:code/exec` handler as confidentiality-isolated. ADR-17 makes the same non-goal explicit for first-party MCP in v0.8.

## Founder context — informs every design decision

Nawfal Saadi is a Director of Business Development MENA & Green Hydrogen at **Scatec ASA**, running
a multi-billion-dollar megaproject with 50 direct reports and board-level access. He has been
self-teaching LLM engineering, agent orchestration, and full-stack AI product development for 9
months from 9 PM to 4 AM in Casablanca, between school runs for his 2-year-old daughter and
supporting his pregnant wife — without domestic help. persistence-os is the substrate thesis. The
duality of operator + builder is the moat. **Treat this work with the gravity it deserves.**

## Hard rules

- **DO NOT modify `src/` without an active design plan + ARIS review.** Standard soft-mode threshold is mean ≥ 8.0 / min ≥ 7.5. Hard-mode reviews (used when stakes are high or the design is adversarial) can pass below those thresholds *only* via the W3 honest-rescope pattern: a finding that can't be fixed in scope gets queued as a separate v0.9.x track with a **falsifiable acceptance signal** (e.g. an xfail-strict marker that flips when the fix lands). Phase 2.0d closing at 6.4 is the precedent — `tests/effect/test_code_exec.py` F4 xfail-strict is the v0.9.x real-OS-sandbox acceptance signal. Do not invoke "hard-mode precedent" without queueing the rescope artifact.
- **Impl ARIS — when REQUIRED vs SKIPPABLE.** Design ARIS (above) is mandatory before touching `src/`. A SECOND impl-time ARIS pass on the merge diff is **REQUIRED** when impl introduces architectural decisions not litigated at design ARIS — new public surface names, new error-class hierarchies, new ADRs, or multi-file integration where "did the implementer copy the spec right" doesn't fully describe the work. **SKIPPABLE** when impl is verbatim-from-spec translation gated by pyright-clean + AST-guard greps + suite green — Phase 2.1a is the precedent (design ARIS R1 PASS-WITH-FIXES → TDD subagent dispatch with locked code per task → impl ARIS retroactive at user prompt, mean 8.X / min 7.X). Default when unclear: run it; retroactive impl ARIS on a merge commit via `codex-companion.mjs task --background --fresh --effort xhigh` is cheap (~5–15 min wall, one cache-warm window).
- **DO NOT modify landed ADRs.** ADR-17 (in `docs/plans/2026-04-29-adapter-sdk-contract-design.md`) is load-bearing for v0.8 confidentiality posture; the v0.9 privacy-arch work is the proper venue for any change.
- **PUSH allowed.** `v0.8.5a1` annotated tag + `feat/v0.9-*` branches MAY be pushed to `origin` as of 2026-05-03 (Mimir Phase A, ADR-003 in `~/Projects/conductor/tracks/mimir-os-product_20260503/decisions.md`). Repository visibility flipped public same day. Substrate distribution moves from local-only to open-core under AGPL-3 to back the Mimir commercial wrapper. The `@experimental` stability gates remain authoritative for surface stability — public visibility does NOT imply API stability before `v0.9.0a1`.
- **DO NOT branch off `feat/v0.9-persistence-coder` for docs work.** Branch off `main`. (This file lives on `docs/claude-md-voice` off `main` for that reason.)
- **DO NOT modify the active track file** at `~/Projects/ai-box/conductor/tracks/persistence-os-product_20260429/`. STATUS append only.
- **DO NOT touch `juba-os/CLAUDE.md`.** That file's founder paragraph (lines 46–47) is the canonical source referenced from this file and the other product CLAUDE.md files. Edit upstream there only via dedicated juba-os work.
- **Worktree-CWD discipline.** Substrate work runs in dedicated worktrees under `~/Projects/persistence-os-worktrees/`. Always use absolute paths; never cross worktree boundaries with relative paths. (Lesson held across PG6, 2.0a, 2.0b, 2.0c, 2.0c-ext, 2.0c-prime, 2.0d.)
- **No `Co-Authored-By: Claude` trailer.** Commits authored in any Claude Code session must NOT include the `Co-Authored-By: Claude Opus … <noreply@anthropic.com>` trailer. Establishes "this is Nawfal's work" as the canonical historical record for the public open-source repo. Effective 2026-05-03 going forward; the 283 past trailer-bearing commits stay as-is (force-push to public origin not justified for a one-line trailer).
- **Public-vs-local branch discipline.** `origin/main` is the curated public surface; local `main` is the substrate archive. Forward changes destined for the public surface go through a `publish/<topic>` branch off `origin/main`, NOT a direct merge from local `main`. `scripts/git-hooks/pre-push` enforces a banned-pattern denylist on every push (see § Public-vs-Local Branch Discipline below). Install once per clone: `bash scripts/git-hooks/install.sh`.

## Public-vs-Local Branch Discipline

`origin/main` is the curated public surface. Local `main` is the substrate archive — substantive engineering work lands here first, with internal cross-references (track names, ai-box submodule, working-day cadence) intact. Forward changes destined for the public surface go through `publish/<topic>` branches off `origin/main`, not direct merges from local `main`.

**Banned patterns** in any commit destined for `origin/*` (enforced by `scripts/git-hooks/pre-push`; the regex list in that hook is the source of truth):

- Absolute paths: `/Users/<name>/`
- Tilde-home refs: `~/Projects/`, `~/.claude*`
- Cross-repo refs: `ai-box/conductor/`
- Internal track names: `conductor/tracks/<name>_<YYYYMMDD>/`
- Internal hostnames: `srv870083`, `tail89def3.ts.net`
- Vault env names: `AIOPS_VAULT_API_KEY`, `VAULT_API_KEY`
- Vault tier/bucket refs: `nawfal-{dev,public,self,vault,prod-*,eng-*}/L<N>`

**Bypass** for the rare acknowledged case (e.g. importing pre-existing history whose diffs contain now-banned patterns):

```sh
PERSISTENCE_OS_ALLOW_INTERNAL_REFS=1 git push ...
```

**Acknowledged historical baseline.** The `docs/aris-round-N/*` and `docs/aris-bitemporal-design-round-N/*` directories were published before this discipline existed. Their headers and bodies contain `/Users/nawfalsaadi/Projects/persistence-os/` paths. They are NOT retroactively scrubbed — force-push to public history is not justified for cosmetic path strings. Future ARIS docs use repo-relative paths only.

## Skill systems pointer — Phase 7 / `persistence-orchestrate`

The public face of this substrate as an Anthropic Skill is the `persistence-orchestrate` meta-skill
specified in **Phase 7** of the cross-project skill-systems-integration track:

- Track: `~/Projects/conductor/tracks/skill-systems-integration_20260430/`
- Source plan: `~/Projects/docs/plans/2026-04-30-skill-systems-integration.md`
- GA target: alongside `v0.9.0a1`. **Blocked by Phase 2.4c lockfile snapshot** of the persistence-coder MVP (per ADR-004 in the track decisions doc).

Phase 7 bundles the substrate + the curated SDK facade (`Substrate.fact` / `.txn` / `.plan` /
`.replay` / `.spec` / `.escape`) into a single chainable Skill installable via the Anthropic Skills
distribution channel. It is NOT a tutorial wrapper — the skill IS the substrate's invocation
surface for orchestrator-driven agents.

## Convergent positioning — three independent April-2026 analyses

Three separate research passes in one week converged on the same conclusion (this is why Phase 7
moved from "deferred" to "GA-target alongside v0.9.0a1"):

- **Chase 7-levels (2026-04-29)** — portfolio voice-docs gap analysis. Convergence: B2B team-knowledge-work substrate, NOT creator-economy. Drives the "what it is NOT" anti-patterns above.
- **Howie Liu HyperAgent fit (2026-04-30 AM)** — ship persistence primitives as Anthropic Skills. Convergence: durability + steerability as product axis. Drives the Phase 7 distribution-channel choice.
- **Simon Scrapes "skill systems" (2026-04-30 PM)** — orchestrator-skill compositional architecture. Convergence: chainable component skills + 5-things checklist (architecture / inputs / handoffs / HITL / display) is the right packaging for the curated SDK facade. Drives Phase 7's structural design.

Research artefacts:
- `~/Projects/research-output-2026-04-30-simon-scrapes-skills.md`
- `~/Projects/transcript-FD53kEpLh9c.txt` (Simon Scrapes video transcript)

If a fourth independent analysis lands and contradicts the convergence, re-open positioning before
shipping `v0.9.0a1`. Until then, treat the three above as the locked product narrative.
