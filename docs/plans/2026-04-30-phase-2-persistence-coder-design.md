# Phase 2 — `persistence-coder` MVP — Design

**Date:** 2026-04-30
**Status:** R3-W3 (post-codex-R3 fix-pass)
**Predecessors (rounds):** R0 → R1 (FIX-FIRST 7.56/6.8) → R1-W1 → R2 (FIX-FIRST 8.0/7.3) → R2-W2 → R3 (FIX-FIRST 8.2/7.2) → **R3-W3** → R4 pending
**Author:** Nawfal Saadi (with Claude Opus 4.7)
**Audience:** persistence-os engineering — Phase 2 of 16-week commercial-first roadmap
**Predecessors:**
- [`2026-04-27-persistence-os-v1.0-roadmap.md`](2026-04-27-persistence-os-v1.0-roadmap.md) — v1.0 ferrari-first roadmap
- [`2026-04-29-adapter-sdk-contract-design.md`](2026-04-29-adapter-sdk-contract-design.md) — `persistence.sdk` (the contract this stream is the first real consumer of)
- [`2026-04-30-v0.8.0-postgres-store-design.md`](2026-04-30-v0.8.0-postgres-store-design.md) — multi-process Postgres SERIALIZABLE backbone
- [`2026-04-28-v0.7.0a1-module-7-repl-design.md`](2026-04-28-v0.7.0a1-module-7-repl-design.md) — REPL steering surface
- [`2026-04-28-v0.6.5-mcts-design.md`](2026-04-28-v0.6.5-mcts-design.md) — MCTS plan-search
- conductor track `persistence-os-product_20260429/STATUS.md` — Phase 2 open
- v0.8.0a1 release at `d0d2847` (ARIS R2 PASS mean 8.81 / min 8.0; suite 1880+32+7)

**Target tag(s):** `v0.8.5a1` (substrate completion sub-tag) → `v0.9.0a1` (coder MVP) → `v0.9.0` (post-dogfood polish)
**Target branch:** `feat/v0.9-persistence-coder` cut from `feat/v0.8.0a1-int` after v0.8.0a1 release lands on `main`
**Window:** 2026-05-01 → 2026-06-05 (**26 working days + 2 days slack** = 28 working-day budget across 5 calendar weeks; weeks 4-8 of 16-week roadmap — see § 6 for the day-by-day breakdown. Original "4-week" framing in the roadmap header was a calendar-month rounding; the honest accounting is 26 + 2 = 28 working days, which is ~5.6 working weeks.)

---

## 1. Summary

Persistence OS v0.8.0a1 is the substrate. Phase 2 ships the **first product on top** — `persistence-coder`, a CLI coding agent whose job is to build, debug, and refactor code, but whose **distinguishing capability** is that every step it takes is a bitemporal datom in the agent's own substrate, every plan it executes is a homoiconic Plan AST, every effect it performs is replayable, and the user can pause it, branch its world, ask "what if you went down path B," let MCTS score both branches, and pick.

This is the **wedge**: not "another coding agent," but "the only coding agent whose entire trajectory is rewindable, branchable, replayable, and provable."

Three load-bearing artifacts ship in this stream:

1. **Substrate completion** (working days 1-11; see § 6 for the day-by-day breakdown) — close the three v0.8.0a1-deferred Phase 1 items: **#140 Plan Edit API** (in-flight Plan mutation under transaction), **#141 `:code` sandbox effect** (sandboxed Python execution as a first-class Plan op), **#145 `fold()` executor surface** (DB.fold over the speculation primitive PG6 shipped). These three unblock specific agent capabilities; without them the agent can run a ReAct loop but cannot self-modify mid-plan, cannot safely execute generated code, and cannot fold counterfactuals into search policies.

2. **`persistence.coder`** (working days 12-23; see § 6) — the in-tree CLI agent. Built **on top of `persistence.sdk.Substrate`** (no reach-through into private module internals; the agent is the first real proof the SDK contract holds under non-trivial use). Ships: a ReAct outer loop with Plan + MCTS escalation, a skill library backed by `:skill/...` datoms, an effect handler stack for `:llm` / `:fs` / `:shell` / `:code` / `:git`, REPL-steering integration so the user can pause/inspect/branch/resume the agent live, and a CLI entrypoint `python -m persistence.coder` (and console-script `persistence-coder`).

3. **Eat-own-dogfood demo + design-partner brief** (working days 24-26; see § 6) — the agent rebuilds part of itself (specifically: the `persistence.coder.skills.builtin` registry from a human-written spec). Trajectory recorded as datoms in a fresh substrate; rewind/branch/replay/MCTS demos run against that recorded trajectory. Output: a 60-second video, a `docs/demos/coder-rewind.md` walkthrough, and a `docs/spec/coder-contract-v0.9.md` defining the agent's externally-stable surface (what design partners can script against).

**Not** in this stream: privacy architecture (#13, Phase 3), multi-tenant policy (Phase 3), web UI (Phase 4), non-Python multi-language client (Phase 4+), public launch / pricing (Phase 4).

**Ships:** ~2400 LOC src + ~520 tests + 3 spec docs + 1 demo video. **26 working days + 2 days slack** across the 2026-05-01 → 2026-06-05 window (one canonical calendar; § 6 task table is the day-by-day breakdown).

---

## 2. Strategic frame

### Why this is the wedge

The coding-agent market is 2026's most commoditized AI surface (Cursor, Cline, Continue, Aider, GitHub Copilot agents, OpenAI Codex agents, Claude Code itself). Cycle time on "another coding agent" is a week — anyone can wire `Anthropic.messages.create()` to a tool-use loop. The substrate-side moat is what we sell:

- **Bitemporal audit** — the agent records `(transaction_time, valid_time)` for every observation and every action. "What did the agent know at 14:32 and what did it claim?" is a SQL query against the `datom_log`. No competitor has this.
- **Counterfactual replay** — every trajectory is *replayable*: by default via **audit-replay** (handlers are NOT re-invoked; recorded request + result datoms are walked in transaction order; byte-identity is computed over the canonicalized datom stream — the v0.6.0a1 + v0.6.5 + v0.7.0a1 invariant), and optionally via **re-execution-replay** against a snapshotted state for counterfactuals (v0.9 supports re-execution for `:code/exec` only — see § 3.7 for the per-effect rule). "Did the agent's behavior actually change between two runs of the same prompt?" is a `replay_check` (audit-replay) away.
- **MCTS plan-search** — when the agent's next action is ambiguous, it can branch the world (`txn.alter` + `db.dosync`), score both with the MCTS module from v0.6.5, and commit the winner. No competitor does multi-trajectory rollouts grounded in a real bitemporal store.
- **REPL steering** — at any point the user can pause via Module 7 REPL, inspect the agent's last 50 datoms, mutate context, and resume from a different head. This is "Cursor with a debugger attached and a time machine."
- **Skill library as bitemporal facts** — every skill the agent uses is a datom; new skills are appended; skill bodies are `:code` Plan ops; hot-patching = transact a new `:skill/...` datom.

These are not "features." They are the substrate. The agent is the **shape** that exposes them.

### Why this stream takes ~5 calendar weeks / 26 working days (not 2, not 8)

A pure ReAct loop wired to `Anthropic.messages.create()` is 2-3 days. We could ship that and call it a Phase 2 MVP. We won't, because:

- Without **#141 :code sandbox** the agent cannot execute generated Python without a separate subprocess hack — and that hack is the leaky-abstraction surface that erodes "every effect is a replayable Plan op."
- Without **#140 Plan Edit API** the agent cannot revise its own plan mid-execution under txn — every revision is a fresh top-level transaction, which loses the "one trajectory, many edits" abstraction.
- Without **#145 fold()** the agent cannot consult counterfactual rollouts when scoring branches — MCTS in v0.6.5 has the search machinery but no public surface to fold a probe over alternate worlds.
- Without **eat-own-dogfood** we have no story for "is this agent actually good." The wedge is not "we built an agent" — it's "we used the agent to build the next slice of the substrate, and you can replay every keystroke to verify."

~5 calendar weeks (26 working days + 2 days slack) is the minimum that lets us ship the agent **and** the proof. The 16-week roadmap header rounds Phase 2 to "weeks 4-8" for narrative cleanliness; § 6 is the load-bearing day-by-day calendar.

### Where this stream sits in the 16-week roadmap

```
Phase 0: strategic prep        (week 0)        ✅ closed 2026-04-29
Phase 1: substrate completion  (weeks 1-4)     ✅ v0.8.0a1 closed 2026-04-30 (Adapter SDK + Postgres)
                                               ⏳ deferred items #140 + #141 + #145 → roll into Phase 2.0
Phase 2: coding agent MVP      (weeks 4-8)     [THIS DESIGN]
  ├─ 2.0 substrate finish      (weeks 4-5)     #140 #141 #145
  ├─ 2.1 coder scaffolding     (week 5)        SDK consumer + CLI entry
  ├─ 2.2 ReAct + effects       (week 6)        :llm + :fs + :shell + :code + :git
  ├─ 2.3 plan + MCTS + skills  (week 7)        Plan escalation + skill library + REPL steering
  └─ 2.4 dogfood + demo        (week 8)        agent rebuilds itself; 60s demo; coder contract v0.9
Phase 3: productization        (weeks 8-12)    privacy v1, design partners, v1.0.0 substrate tag
Phase 4: commercial GA + raise (weeks 12-16)   public launch, pre-seed
Phase 5: paper                 (deferred)      side asset
```

Phase 2 is load-bearing for the entire commercial story. Phase 1 was "the substrate exists." Phase 2 is "we built the wedge product on it, and it works."

---

## 3. Architecture

### 3.1 In-tree, single repo, single suite

`persistence-coder` ships as `src/persistence/coder/` in the persistence-os repo. Same suite (`pytest`), same lockfile, same CI, same license (AGPL-3.0-or-later).

**Why in-tree, not sibling repo:**
- Eat-own-dogfood is the demo. Sibling repo bifurcates "where you are right now" — the agent and the substrate it consumes need to be in the same `git log`.
- Substrate gaps surface as failing agent tests, not cross-repo bug reports. We catch v0.8 leaks while building.
- `persistence.sdk.Substrate` is the only public surface the agent imports. If we discover the SDK is leaky, we patch the SDK, not the agent.
- Sibling repo can be split out at v1.0 if we need it for distribution (`pip install persistence-coder` separate from `pip install persistence`). Pre-v1.0, single repo wins.

**File layout:**
```
src/persistence/coder/
├── __init__.py            # public surface: Coder, run_task, REPL_HOST
├── _agent.py              # ReAct outer loop + plan escalation gate; calls s.txn.dosync + s.fact.transact
├── _planner.py            # Plan AST construction from agent decisions; executes via s.plan.execute (proposed SDK surface, see ADR-2)
├── _mcts_policy.py        # MCTS rollout adapter; calls s.mcts.search (proposed SDK surface, see ADR-2) — until that lands, reaches via s.escape.plan with audit telemetry
├── _llm/                  # LLM provider abstraction
│   ├── __init__.py
│   ├── anthropic.py       # Anthropic SDK direct (default)
│   ├── litellm.py         # optional: LiteLLM adapter for non-Anthropic
│   └── _shape.py          # canonical request/response shape (datom-friendly)
├── _effects/              # effect handlers the agent emits
│   ├── __init__.py
│   ├── llm.py             # :llm/* effects
│   ├── fs.py              # :fs/read :fs/write :fs/glob :fs/grep
│   ├── shell.py           # :shell/exec (allowlisted command stems)
│   ├── code.py            # :code/exec (Plan :code op wrapper, see #141)
│   └── git.py             # :git/* (commit, branch, log, status)
├── skills/
│   ├── __init__.py        # skill registry + lookup
│   ├── builtin/           # ships with the agent
│   │   ├── implement_function.py
│   │   ├── add_test.py
│   │   ├── read_then_edit.py
│   │   ├── search_codebase.py
│   │   └── debug_failing_test.py
│   └── _datom_shape.py    # :skill/name :skill/body :skill/version :skill/checksum
├── _repl_steering.py      # WS hook so REPL clients can pause/branch/resume
├── _cli.py                # python -m persistence.coder entrypoint + arg parsing
└── _provenance.py         # datom-emitting helpers: every action goes through here
tests/coder/
├── test_react_loop.py
├── test_effects_*.py      # one per effect handler
├── test_plan_escalation.py
├── test_mcts_branch.py
├── test_skills_*.py
├── test_repl_steering.py
├── test_cli.py
├── test_dogfood_replay.py # the demo's golden replay test
└── test_sdk_consumer.py   # asserts agent only imports from persistence.sdk.*
```

### 3.2 SDK-only consumption (the contract test)

The agent imports **only** from `persistence.sdk.*`. A test (`test_sdk_consumer.py`) walks `persistence.coder` and asserts no symbol traces back to `persistence.fact._raw`, `persistence.effect._internal`, etc. This is the live contract validation the SDK design doc § 7 promised.

**This subsection is the single canonical (editorial) source of truth for the agent's "closed allowed-set."** ADR-2 defers to this subsection; G1 (§ 5) defers to this subsection. The committed `coder.lockfile.json` is the *operational* source of truth — read by CI in `test_sdk_consumer.py` — and is generated from the table + paragraph below via `scripts/emit_g1_lockfile.py` (Phase 2.4c deliverable). The build rule: **`scripts/emit_g1_lockfile.py` parses the "Allowed SDK entrypoints" table + "Proposed v0.8.5a1 SDK additions" paragraph in this subsection (§ 3.2) and writes `coder.lockfile.json`**. Any divergence between this subsection's enumeration, ADR-2's enumeration, and the lockfile is a doc bug. If the build rule is too operationally heavy at design-time and the script slips into Phase 2.4c, the editorial-source rule still holds: § 3.2 is canonical, ADR-2 mirrors § 3.2 (no independent enumeration), the lockfile mirrors § 3.2.

**Allowed SDK entrypoints** for the agent (verified against `src/persistence/sdk/_facade.py` v0.8.0a1):

| Surface | What the agent uses it for |
|---|---|
| `s.fact.transact / .as_of / .as_of_valid / .history / .since` | bitemporal datom log read/write |
| `s.effect.perform / .is_well_formed` | dispatching `:llm`/`:fs`/`:shell`/`:code`/`:git` ops via the runtime stack |
| `s.txn.dosync / .new_ref / .ref / .fold` | atomic txns, refs, and the speculation primitive (`s.txn.fold` is the v0.8.5a1 home of #145 — `@experimental`) |
| `s.repl.serve / .mint_token / .revoke_token / .list_tokens` | REPL-steering server lifecycle |
| `s.audit.verify_chain / .entries` | audit-chain integrity checks |
| `s.replay.record / .replay / .compare` | trajectory record + replay |
| `s.escape.plan` (audit-emitting) | **gap fallback** for Plan/MCTS until proposed `s.plan` / `s.mcts` namespaces ship — first access per session emits `:sdk/escape-hatch-access` telemetry |

**Proposed v0.8.5a1 SDK additions** (substrate backlog, design-doc-pinned but **NOT** yet on `_facade.py`): `s.plan.execute(plan)` thin curated wrapper for `persistence.plan.execute`, and `s.mcts.search(...)` thin curated wrapper for `persistence.plan.mcts_search`. Each lands as a new bound-method namespace on `Substrate`, marked `@experimental("v0.8.5a1")`. The two surfaces are tracked as substrate backlog items **#147 (`s.plan` namespace)** and **#148 (`s.mcts` namespace)** and ship inside Phase 2.0 (sub-tag `v0.8.5a1`) before Phase 2.1 starts; failure to ship them by day 10 forces the agent down the `s.escape.plan` audit-emitting path until they land.

**SDK-gap protocol** (the rule, not the exception): if Phase 2.x discovers a substrate primitive the SDK does not expose, the gap is filed as a **versioned SDK addition** in the substrate backlog (e.g. #147/#148 above; the precedent is `s.txn.fold` shipped at v0.8.5a1 as the public surface of #145). The agent never imports `persistence.fact`, `persistence.effect`, `persistence.plan`, `persistence.txn`, `persistence.replay`, `persistence.spec`, or `persistence.repl` directly. The only tolerated reach-through is via `s.escape.<module>` — which is documented as out-of-contract per Adapter SDK ADR-1 and emits a `:sdk/escape-hatch-access` audit entry on first access per session, making every escape-hatch usage observable in CI and during dogfood.

**Escape-hatch sunset rule (release gate).** Every `s.escape.*` call site in `src/persistence/coder/` is a **tracked TODO** with a sunset SDK-addition issue (e.g. #147 `s.plan` is the sunset for `s.escape.plan.execute`; #148 `s.mcts` is the sunset for `s.escape.plan.mcts_search`). G1's lockfile (`coder.lockfile.json`, see G1(c) in § 5) records every escape call site with its sunset issue ID; the lockfile is committed and gates CI.

- **`v0.9.0a1` (MVP alpha tag, end of Phase 2)** — escape-hatch usage is **permitted**. The agent ships against `s.escape.plan.execute` / `s.escape.plan.mcts_search` while #147 / #148 are still `@experimental("v0.8.5a1")`. Every escape site is enumerated in the lockfile with a sunset issue ID; the lockfile snapshot cannot grow silently between commits (G1(c)).
- **`v0.9.0` (general release tag, post-Phase-2 dogfood polish)** — the `s.escape.*` call count in `src/persistence/coder/**/*.py` **MUST be 0**. Every escape site closed by an SDK addition (the sunset issue lands a curated namespace) or by code refactor (the agent stops needing the surface). The release-tag CI runs the same G1 against the empty-escape-set lockfile and refuses the tag if any escape site remains. **A lockfile with non-empty escape entries CAN tag `v0.9.0a1` but CANNOT tag `v0.9.0`.**

**Cross-references** (the sunset rule is referenced from each surface that needs to enforce it):
- ADR-2 (allowed-set + enforcement) carries the rule by reference (`see § 3.2 Escape-hatch sunset rule`).
- G1(b) checks the audit-emitting wrapper for every escape site at all release tags; G1(c) checks the lockfile snapshot is byte-identical to the committed expected snapshot at all release tags. Additionally, **at the `v0.9.0` release tag, G1 runs in strict mode**: the expected-lockfile snapshot for `v0.9.0` enumerates `escape_callsites: []` — non-empty escape entries fail G1(c) at the release tag. (`v0.9.0a1` runs G1 in permissive mode against the alpha lockfile.)
- § 6 task table row 2.4c "coder contract spec doc + lockfile + CI gate" produces the alpha-tag lockfile shape; the release-tag-strict lockfile is produced as part of Phase 3 v0.9.0 cleanup.

### 3.3 LLM provider abstraction

Default: **Anthropic SDK direct** (`anthropic>=0.40,<0.50`), Sonnet 4.6 for planning, Haiku 4.5 for fast tool-use cycles. The provider is wrapped in a `_shape.LLMRequest` / `_shape.LLMResponse` dataclass pair so the request and response are structurally datom-shape (every field is canonical-JSON-serializable, no `time.time()`, RNG seed bound from txn context).

LiteLLM is **optional** (`pip install persistence[coder-litellm]`) — for design partners who want OpenAI / Bedrock / Vertex / on-prem. The shape stays canonical; only the transport changes.

**Why this matters for the substrate:** every `:llm/request` and `:llm/response` is a datom. Replaying a trajectory does not call the LLM again — it reads the recorded `:llm/response` datom and yields it deterministically. This is the v0.6.0a1 byte-identity invariant applied to LLM I/O.

### 3.4 Agent loop shape

ReAct as the outer skeleton, escalating to Plan + MCTS as ambiguity rises:

```
┌─ ReAct loop ─────────────────────────────────────────────────────┐
│  observation → :llm/decision → s.effect.perform → :datom emitted │
│  ↑                                                       │       │
│  └───────────────────────────────────────────────────────┘       │
│                                                                  │
│  if :llm/decision.kind == "plan":                                │
│       escalate → build Plan AST → s.plan.execute(plan)           │
│       (until #147 ships: s.escape.plan.execute(plan)             │
│        with first-access audit emission)                         │
│                                                                  │
│  if :llm/decision.kind == "branch":                              │
│       escalate → s.mcts.search(...) over alt branches            │
│       → s.txn.fold(probe, branches) → score → commit             │
│       (until #148 ships: s.escape.plan.mcts_search(...))         │
│                                                                  │
│  if user steers via REPL:                                        │
│       :repl/request datom emitted → pause loop                   │
│       → expose snapshot → accept directive payload               │
│       → :repl/response datom emitted → resume from new head      │
└──────────────────────────────────────────────────────────────────┘
```

Most tool-use cycles stay in ReAct. Plan escalation triggers when the LLM's decision payload includes a `:strategy/plan` field (multi-step composition). MCTS escalation triggers on explicit `:strategy/branch` or when the agent's confidence score on a single decision is below threshold (default `0.65` — **starting value, will be tuned during dogfood (Phase 2.4a) on calibration data; missing-confidence default is `0.5`** so a malformed `:llm/decision` payload does not silently bypass the escalation gate).

### 3.5 Skill library

A skill is a **named, versioned, body-bound datom** the agent can lookup and invoke:

```python
@dataclass(frozen=True)
class Skill:
    name: str            # "implement_function"
    version: int         # monotonic logical version of the skill body (NOT a temporal axis)
    body: PlanAST        # the actual Plan to execute
    checksum: str        # canonical-JSON sha256 of body
    requires: tuple[str, ...]   # capabilities required (:llm/messages, :fs/write, ...)
```

**Bitemporal axis discipline (the canonical rule):** `Skill.version` is a **monotonic logical version of the skill body** and lives on a single attribute (`:skill/version`). It is **not** a temporal axis. Bitemporal queries (transaction-time = "when did the substrate learn this?", valid-time = "when does this skill apply?") are **orthogonal** and addressed via standard `s.fact.as_of(t)` / `s.fact.as_of_valid(t)` + `s.fact.history(?s, :skill/version)`. The two axes compose; you can ask "what was version 3 of skill `implement_function` as of yesterday's transaction time" — that's `s.fact.as_of(yesterday).q(... :skill/version 3 ...)`. The default lookup `db.q '[:find ?s :in $ ?name :where [?s :skill/name ?name]]'` returns the most-recent transacted skill row (i.e. greatest `:skill/version` at the latest transaction time the substrate has seen); the agent may pin a specific logical version via `--skill-version` and a specific transaction time via `--as-of`. G5 tests this composition explicitly: it transacts skill v1 at tx_t1, transacts skill v2 at tx_t2, queries `as_of(tx_t1)` → v1, queries `as_of(tx_t2)` → v2, queries `as_of(tx_t2).where(version=1)` → v1 (history). Three sub-cases, one canonical axis (logical `version`) plus the orthogonal transaction-time lens.

**Builtin skills ship in the suite at the agent's first transaction** — the agent boots, transacts the 5 builtin skills, then enters the loop. Custom skills are added by the user at any point via `persistence-coder skills add <path-to-skill.py>`.

Skill bodies are Plan ASTs, so a skill can compose other skills, branch via MCTS, fold over counterfactuals (#145), and re-stamp its own checksum on edit.

### 3.6 REPL steering integration

Module 7 REPL ships a WebSocket server with capability-gated ops. The coder registers a new operator session class `_CoderSteeringSession` that exposes:

- `coder.pause()` — sets a flag the loop checks before its next decision; loop awaits a `coder.resume()` before continuing
- `coder.snapshot()` — returns the last N datoms the agent emitted (default 50)
- `coder.context_at(t)` — returns the substrate view at txn time t (read-your-own-writes via Module 5's `tx.deref`)
- `coder.branch(directive)` — opens a child txn, the agent's next decision is computed against the parent's view + the directive, both branches survive in the bitemporal log
- `coder.fold(probe)` — runs probe across both branches and returns scores (uses #145 surface `s.txn.fold` per § 4.3)
- `coder.commit(branch_id)` — promotes a branch's head as the new agent head; the other branch stays in the log as a counterfactual

The REPL session is the user's **debugger attached to a live agent**. This is the most differentiated UX surface; it's the demo we lead with.

### 3.7 Replay modes (per-effect, byte-identity discipline)

"Byte-identity replay" is meaningless without a per-effect rule for what replay actually does. v0.9 ships **two named modes**, with **audit-replay** as the default that all G2/G3/G4/G6/G7/G8 gates run under. Re-execution-replay is opt-in for the "rewind to step 5 and change the prompt" demo flow only.

**Mode 1: Audit-replay (default; load-bearing for all gates).**
For a recorded trajectory, the substrate **does not re-invoke effect handlers**. Each effect's recorded request datom + result datom are walked in transaction order; replay reads the recorded result and yields it deterministically. Byte-identity is computed over the canonicalized datom stream (canonical JSON, sorted keys, fixed timestamps recovered from the original transaction time). This is the v0.6.0a1 invariant.

**Mode 2: Re-execution-replay (opt-in, per-effect, manual).**
For "what if I rewind to step 5 and change the prompt" demos, a starting checkpoint (FS snapshot, git ref, REPL directive history) is materialized and effect handlers re-run from that point. **`:code/exec` is the only effect with first-class re-execution support in v0.9** (its sandbox is itself a replayable Plan op via #141 — fixed seed, patched clock, capability-denied environment; see § 4.2). All other effects raise `ReplayMode.RE_EXECUTION` errors until v0.10 hardens checkpointing for them.

**Per-effect replay table.** Every effect lists: the datom shape recorded (request side and result side), the audit-replay action, the byte-identity invariant, and the known caveat. **REPL steering directives are first-class effects on this table** — they are datomized as `:repl/request` / `:repl/response` events so the trajectory replays the same input log; they are not "side calls" outside the audit log.

| Effect | Request datom | Result datom | Audit-replay action | Byte-identity invariant | Re-exec caveat |
|---|---|---|---|---|---|
| `:llm/messages` | `{model, messages, tools, seed, temperature, max_tokens}` (canonical-JSON) | `{content, stop_reason, usage, response_id}` | read recorded result; do **not** call provider | response bytes match across replays | not supported v0.9 (provider non-determinism) |
| `:fs/read` | `{path, encoding}` | `{bytes_or_text, mtime, size, sha256}` | read recorded result | sha256 + bytes match | re-exec requires FS snapshot mount |
| `:fs/write` | `{path, bytes_or_text, mode}` | `{bytes_written, sha256_after}` | read recorded result; do **not** touch FS | recorded sha256 matches recorded write payload | re-exec requires scratch-FS overlay |
| `:fs/glob` / `:fs/grep` | `{pattern, root, flags}` | `{matches: [...]}` | read recorded result | match list (canonical-sorted) matches | re-exec requires FS snapshot |
| `:shell/exec` | `{argv, env_allowlist_subset, cwd, allowlist_version}` | `{exit, stdout, stderr, wall_clock_ms}` | read recorded result; do **not** spawn subprocess | argv + env + cwd + allowlist_version + result bytes match | not supported v0.9 (process non-determinism); the `allowlist_version` is recorded so a stale-allowlist replay raises rather than silently passing |
| `:code/exec` | `{body_sha256, body_text, allowed_imports, seed, scratch_dir, timeout_s}` | `{stdout, stderr, exit, wall_clock_ms}` | read recorded result | recorded result bytes match | **re-exec supported** under #141 sandbox (capability-denied env makes determinism a property of the environment, not a property of the code — see SHOULD-FIX rewrite of § 4.2) |
| `:git/commit` / `:git/branch` / `:git/log` / `:git/status` | `{argv, repo_path, ref}` | `{output, exit, oid_if_any}` | read recorded result; do **not** mutate repo | OID + output match | re-exec requires repo snapshot |
| `:repl/request` (pause/snapshot/branch/fold/commit) | `{op, op_args, session_id, request_id, capability_token_fingerprint}` | `{op_result_payload, request_id}` | read recorded request, **re-inject** at the recorded decision boundary, read recorded response | request payload + response payload match | re-exec supported (the directive payload is the input; the agent's response to a re-injected payload runs against re-injected upstream effects) |
| `:plan/edit` | `{plan_id, step_id, before_op_hash, after_op_hash, txn_id}` | n/a (audit-only) | read recorded edit; reconstruct edit history | hash chain matches | re-exec applies edits from log |
| `:fold/chosen` (#145, foldl-with-marker pattern) | `{chosen_index, chosen_score, all_scores, branch_count}` | n/a (audit-only) | read recorded chosen-marker | chosen-marker matches across replays | re-exec runs `DB.fold` under audit-replayed sub-effects; non-chosen branches' facts persist (fold is foldl/reduce, not speculate-rollback) |
| `:fork/probe` `:fork/branch` `:fork/score` `:fork/chosen` (#145ext, speculate-rollback-pick pattern) | `{seed_hash, items_hash, fn_hash, choose_hash, branch_count}` (probe); `{branch_index, branch_id, item_hash, branch_state_hash}` × N (branch); `{branch_index, score_value, score_hash}` × N (score); `{chosen_index, chosen_branch_id, chosen_state_hash}` (chosen) | n/a (audit-only) | read recorded probe + branch + score + chosen sequence | full 4-datom sequence is byte-identical across replays in canonical order (probe → N×branch → N×score → chosen); only chosen branch's facts persist | re-exec runs `fn`/`choose` under audit-replayed sub-effects (deferred to v0.10 — landing alongside FS-snapshot work; current ship is audit-replay only) |

**REPL directives as first-class input log.** `coder.pause`, `coder.snapshot`, `coder.context_at`, `coder.branch`, `coder.fold`, `coder.commit` (§ 3.6) each emit a `:repl/request` datom with canonical-JSON payload at the moment the directive enters the agent's decision boundary, and a `:repl/response` datom on completion. G6/G7 byte-identity replay re-injects the recorded `:repl/request` payloads at the same decision boundary in the trajectory; the agent's downstream effects are then audit-replayed. Without this datom shape the REPL feature is non-falsifiable; with it, the most differentiated UX surface is provable.

---

## 4. Substrate completion (Phase 2.0, the prelude)

Three deferred Phase 1 items land before the agent build starts. Each ships its own design ADR, impl-plan, ARIS R2.

### 4.1 #140 Plan Edit API (working days 1-3; see § 6 task table row 2.0a)

In-flight Plan mutation under transaction. Today, modifying a Plan mid-execution requires aborting and re-transacting. The Plan Edit API ships:

- `Plan.edit_step(step_id, new_op)` — replace a Plan AST node, valid only inside a `dosync` txn
- `Plan.insert_step_after(step_id, new_step)` / `Plan.insert_step_before(...)`
- `Plan.delete_step(step_id)` — only allowed if no downstream step has executed
- Audit invariant: every edit is a `:plan/edit` datom with `(plan_id, step_id, before_op_hash, after_op_hash, txn_id)` — replay reconstructs the edit history

**Why the agent needs it:** Plan revision mid-execution is the agent's bread and butter. Without this, every revision is a fresh top-level transaction and the trajectory looks discontinuous in the audit log.

**Falsifiable acceptance gate:** property test — for any Plan P with N steps, randomly edit M < N steps under one txn, verify byte-identity replay reconstructs P with the M edits applied at exactly the same Plan-AST positions.

### 4.2 #141 `:code` sandbox effect (working days 3-7; see § 6 task table row 2.0b)

First-class Plan op `:code/exec` that runs sandboxed Python. **Determinism is achieved by capability-denial + environment control, NOT by static detection of nondeterministic calls.** The sandbox does not try to detect `time.time()` / `random.random()` / network calls and reject them — that is whack-a-mole and unreliable in Python. Instead, the sandbox **denies the capabilities** that produce non-determinism, so generated code that would touch them fails at import-time (or at attribute-access against a patched module) within the sandbox subprocess. Capability-denial is verifiable by construction; detection is not.

The capability-denial set:

- Subprocess isolation: default `subprocess` + resource limits via `resource.setrlimit`; upgradeable to `firejail` / `bubblewrap` when shipped.
- **Module allowlist (denial-by-default):** **only** `json`, `re`, `dataclasses`, `pathlib` are importable from the sandbox process. `os`, `sys`, `subprocess`, `socket`, `time`, `random`, `urllib`, `http`, `ctypes`, `multiprocessing`, `threading`, `asyncio` (and every other stdlib + third-party module) are **not on the allowlist**, so `import os` raises `ImportError` at import-time inside the sandbox. Generated code that depends on them fails to start; we do not "scan and reject" — we just don't make them reachable.
- **Patched determinism shims** (off by default; per-call opt-in via `allowed_imports`): a fixed-seed `random` shim and a fixed-clock `time` shim, pre-installed as patched modules in the sandbox boot script when the body opts in. The opt-in is recorded on the `:code/exec` request datom so replay sees the same shimmed environment.
- **Network denied at the OS level:** the sandbox subprocess starts with a network namespace that has no routable interface; sockets fail with `ENETUNREACH`. Even if `socket` were on the allowlist, network access would fail. Belt-and-braces.
- **Filesystem capability:** read-only mount of the project root + a scratch dir for writes; writes outside scratch raise `PermissionError`. The scratch dir is recorded on the `:code/exec` request datom and seeded into the sandbox at a known path so replay reproduces the same filesystem layout.
- **Stdout / stderr** captured as `:code/output` / `:code/error` datoms.
- **Wall-clock timeout** (default 30s, configurable per Plan op); the sandbox process is `SIGKILL`ed past timeout and the trajectory records the partial output.
- **RNG seed bound from txn context** when the sandbox opts in to the patched `random` shim — same seed across replays, deterministic output.

**Why the agent needs it:** the agent will generate code (test stubs, helper functions, refactor migrations) and need to run it without a side-channel `subprocess.run`. `:code/exec` makes generated-code execution a Plan op, which means it's replayable, audit-chained, and bitemporal.

**Falsifiable acceptance gate:** property test — for any code body C compatible with the capability-denial set, byte-identity replay yields the same `:code/output` datoms across replays. The complementary negative test: a body that imports a non-allowlisted module (or attempts to open a socket or write outside scratch) fails inside the sandbox with the documented exception (`ImportError`, `PermissionError`, `ENETUNREACH`). The gate does **not** assert that we have detected a bad body via static analysis; it asserts that the bad body cannot complete successfully because the capability is not reachable. This is the implementable form of the original "rejects non-determinism" claim.

### 4.3 #145 + #145ext `DB.fold` + `DB.fork` executor primitives (working days 7-12.5; see § 6 task table rows 2.0c + 2.0c-extended)

Phase 2.0c ships **two co-existing executor primitives** on the substrate, with curated SDK surfaces on `_TxnNamespace`. The split is intentional and pinned by ADR-7: the two primitives are semantically distinct, so they get distinct namespaces (`:fold/*` vs `:fork/*`) to avoid silent meaning-drift across replays of trajectories upgraded between v0.8.0a1 and v0.8.5a1.

**Primitive 1 — `DB.fold` / `s.txn.fold` (foldl/reduce with chosen-marker).** PG6 substrate ships `DB.fold` as a private speculation primitive; v0.8.0a1 promoted it to `s.txn.fold(seed, items, fn, **kwargs)` (`@experimental("v0.8")`, `_facade.py:_TxnNamespace.fold`). Foldl/reduce semantics: commits every item's facts as it iterates; the legacy `:fold/chosen` audit op marks one branch within an all-committed foldl. Right shape for "accumulate over a sequence with audit-traceable provenance".

**Primitive 2 — `DB.fork` / `s.txn.fork` (speculate-rollback-pick).** Phase 2.0c-extended (#145ext, folds in original 2.0c carryover #201). Substrate-true rollback semantics: `fn(branch_state, item) -> branch_state` operates on opaque Python state; per-branch isolation is structural; only the chosen branch's facts are committed (non-chosen branches' tentative state is just discarded Python objects — rollback is trivial). Datom shape: the canonical 4-datom emission `:fork/probe` + `:fork/branch` × N + `:fork/score` × N + `:fork/chosen`, in that order, all under the enclosing dosync (so they share `txn_commit` and a stable Merkle prev-hash chain of `2 + 2*N` entries). Right shape for "evaluate N candidate branches, pick the best, discard the rest" — the wedge story for persistence-coder (rewind/branch/replay).

- `s.txn.fold(seed, items, fn, **kwargs)` — `@experimental("v0.8")`. Unchanged from v0.8.0a1.
- `s.txn.fold_into(seed, items, fn, choose, *, tx, **kwargs)` — `@experimental` per Phase 2.0c. Phase 2.0c-extended **rewires fold_into on top of `DB.fork`** so it inherits substrate-true rollback semantics (only chosen branch's facts persist) and the canonical 4-datom audit shape. Public signature unchanged; downstream callers unaffected at the API level. The legacy `:fold/chosen` op is no longer emitted by `fold_into`.
- `s.txn.fork(items, fn, choose, *, seed, tx, on_error, provenance)` — `@experimental("v0.8.5a1")` per Phase 2.0c-extended. New curated surface for callers who want explicit speculate-rollback-pick semantics over Python state without the fact-level convenience layer.
- Datom shapes: `:fold/chosen` (foldl-with-marker, single datom per fold) and `:fork/probe` + `:fork/branch` × N + `:fork/score` × N + `:fork/chosen` (speculate-rollback, 4-datom shape). All canonical-JSON.
- Stability: both primitives stay `@experimental("v0.8")` through v0.8.5a1; promote to `@stable("v0.9")` after Phase 2 dogfood survives without API change (per ADR-7).

**Why the agent needs both:** MCTS scoring is the speculate-rollback-pick shape — N candidate branches, a score function, a chosen branch with the others rolled back. That's `DB.fork`. Skill-body folds (accumulate state across a sequence with audit chain) are the foldl shape. That's `DB.fold`. Without the rollback primitive the wedge story (rewind/branch/replay) is just transactional foldl.

**Falsifiable acceptance gate:** property test (Hypothesis @max_examples=200) covering both primitives:

- For N branches with deterministic probe, `s.txn.fold` returns scores in the same order across two replays. (Property 1.)
- For N branches with deterministic `fn` + `choose`, two replays of `s.txn.fold_into` produce **byte-identical 4-datom intent sequences** (probe → branch × N → score × N → chosen) in the same canonical order. (Property 3.)
- For ANY `(seed, items)` input + deterministic `fn` + argmax `choose`, only the chosen branch's eid appears in committed substrate facts. Non-chosen branch eids (modulo collisions with the chosen eid) MUST be absent. (Property 4 — substrate-true rollback verification.)

5 consecutive flake-checks of all four properties at @max_examples=200 must run green.

---

## 5. Acceptance gates

Per project convention, each gate is a **falsifiable test**, not a narrative claim.

| Gate | Surface | Test | Pass criterion |
|---|---|---|---|
| **G1 SDK consumer purity** | static | `test_sdk_consumer.py` walks `persistence.coder` AST + ships a committed lockfile snapshot of the (allowed-callsite, escape-callsite) sets. Three subchecks: **(a)** every **top-level `import` / `from … import …` statement** in `src/persistence/coder/**/*.py` whose top-level package is `persistence` (or `persistence.*`) resolves to a module name in the **canonical allowed-set defined in § 3.2** (the editorial source of truth — see "Allowed SDK entrypoints" table + "Proposed v0.8.5a1 SDK additions" paragraph); stdlib + third-party top-level imports (e.g. `json`, `pathlib`, `anthropic`, `pydantic`) are out of scope for this check. **Transitive runtime imports inside the SDK are NOT checked** — G1(a) is a static, top-level-import predicate against the agent's own files. **(b)** every `s.escape.*` call site is matched by an audit-emitting wrapper in the agent code (escape access is observable, never silent); **(c)** the lockfile snapshot of the (a)+(b) sets is byte-identical to the committed expected snapshot (escape-hatch delta is tracked — count cannot grow silently between releases). The lockfile is the **operational source of truth** for CI; § 3.2 is the **editorial source of truth** for humans; the two MUST agree (see § 3.2 build-rule paragraph) | **PASS = all three subchecks green; FAIL = any one of (a) / (b) / (c) fails.** This row is the single source of truth for the ADR-2 enforcement contract; ADR-2 references this gate, not vice versa. **At the `v0.9.0` release tag, G1 runs in strict mode**: the expected-lockfile `escape_callsites` field MUST be `[]` (per § 3.2 escape-hatch sunset rule). |
| **G2 Effect handler completeness** | dynamic | per-effect property tests; replay byte-identity at max_examples=200 | 100% |
| **G3 Plan escalation correctness** | dynamic | given a 5-step Plan, agent escalates and replays byte-identical at max_examples=100 | 100% |
| **G4 MCTS branch determinism** | dynamic | given seed S and N branches, MCTS rollout produces the same chosen branch across replays | 100% |
| **G5 Skill library bitemporal** | dynamic | three sub-cases on the orthogonal axes (per § 3.5 / ADR-3): (a) transact skill `version=1` at tx_t1, transact `version=2` at tx_t2; (b) `s.fact.as_of(tx_t1)` query returns logical version 1 row, `s.fact.as_of(tx_t2)` query returns logical version 2 row; (c) `s.fact.as_of(tx_t2)` filtered to `:skill/version 1` returns the historical version-1 row (composition of logical-version axis with transaction-time axis) | 100% on all three sub-cases |
| **G6 REPL steering pause/resume** | integration | client connects, calls `coder.pause()`, agent halts, client `coder.resume()`, agent continues, full trajectory replays byte-identical | 100% |
| **G7 REPL branch/fold/commit** | integration | client branches mid-task, folds, commits one branch; the other survives as counterfactual; both replayable | 100% |
| **G8 Dogfood replay** | golden | recorded trajectory of agent rebuilding `skills/builtin/implement_function.py` replays byte-identical against a fresh substrate | 100% |
| **G9 Coder contract stability** | spec | extract coder contract from `persistence.coder.__init__.__all__` + `_cli.py` argparse surface + `_effects/*.py` registered effect schemas → `coder-contract.lock` (canonical-JSON, sorted keys, sha256-hashed); compare against committed `coder-contract.lock`; **AND** for any new symbol in the contract, assert it is annotated `@stable` / `@experimental` / `@deprecated` (via the SDK stability decorators) AND that any new SDK callsite added to the agent is in the documented allowed-set (§ 3.2 table) | **PASS = lockfile bytes match the committed snapshot AND every new contract symbol is annotated AND every new SDK callsite is in the allowed-set; FAIL = any of the three conditions fails** |
| **G10 60-second demo end-to-end** | manual | recorded video shows: task → plan → execute → user pauses at step 4 → branches → fold → commits branch B → replay verifies | qualitative pass |

---

## 6. Task breakdown

**One canonical calendar: working day 1 = 2026-05-01 (Fri), working day 26 = 2026-06-05 (Fri).** The window holds exactly 26 working days (verified by Mon-Fri count across the 5 weeks 2026-05-01 → 2026-06-05) plus 2 days of slack absorbed inside it for the inevitable W-cycle. Header `Window:` line + § 1 Summary + this table all agree on **26 + 2 across this window**. Original "4-week" framing in the roadmap was calendar-month rounding; the honest working-day count is 26 (not 22, not 28). **Phase 2.0c-extended (#145ext, days 10-12.5) consumes the entire 2-day slack budget** by folding original 2.0c carryover #201 forward into the 2.0c phase rather than deferring it to Phase 3; the rest of the schedule slips by 2 working days but the hard cutoff 2026-06-05 (working day 28 = working day 26 + 2 slack) is preserved since the slack always lived within the 28-day envelope.

| # | Task | Working day | Deliverable |
|---|---|---|---|
| **2.0a** | #140 Plan Edit API design + impl + tests | 1-3 | `plan.edit_step` etc. + property test |
| **2.0b** | #141 `:code` sandbox design + impl + tests | 3-7 | `:code/exec` Plan op + property test |
| **2.0c** | #145 `s.txn.fold` hardening + `fold_into` Path-A + tests | 7-9 | `s.txn.fold_into` (foldl-with-marker, Path-A) + property test (the `s.txn.fold` surface is already shipped at v0.8.0a1) |
| **2.0c-ext** | #145ext `DB.fork` substrate primitive + `s.txn.fork` SDK + `fold_into` rewire on top + 4-datom audit + rollback verification (folds in original 2.0c carryover #201) | 10-12.5 | `s.txn.fork` + 4-datom audit shape + property tests (3 byte-identity + rollback verification at @max_examples=200) |
| **2.0c′** | #147 `s.plan` curated namespace (proposed; SDK-gap protocol) | 12-13 | `s.plan.execute(plan)` thin curated wrapper, `@experimental("v0.8.5a1")` |
| **2.0c″** | #148 `s.mcts` curated namespace (proposed; SDK-gap protocol) | 12-13 | `s.mcts.search(...)` thin curated wrapper, `@experimental("v0.8.5a1")` |
| **2.0d** | Substrate completion ARIS R2 + sub-tag `v0.8.5a1` | 13-14 | tag + CHANGELOG |
| **2.1a** | `persistence.coder` skeleton + CLI entry | 14-15 | `python -m persistence.coder --task "..."` runs no-op loop |
| **2.1b** | LLM provider abstraction + Anthropic adapter | 15-16 | first `:llm/*` datoms emitted |
| **2.1c** | G1 contract test live before effects + plan + MCTS land | 16 | `test_sdk_consumer.py` green on the skeleton |
| **2.2a** | `:fs` + `:shell` effect handlers | 17-18 | agent reads / writes / globs / greps the repo; allowlist versioning datomized |
| **2.2b** | `:code` + `:git` effect handlers | 18-20 | agent runs sandboxed Python + commits |
| **2.3a** | Plan escalation gate + Plan AST builder | 20-21 | `:strategy/plan` decisions execute via `s.plan.execute` |
| **2.3b** | MCTS branch + `s.txn.fork` / `s.txn.fold_into` integration | 21-22 | `:strategy/branch` rolls out via `s.mcts.search` + the fork/fold-into pair (MCTS uses `s.txn.fork` for substrate-true rollback) |
| **2.3c** | Skill library: registry, lookup, builtin set | 22-23 | 5 builtin skills + custom-skill registration |
| **2.3d** | REPL-steering session class | 23-25 | pause/snapshot/branch/fold/commit ops live; `:repl/request`+`:repl/response` datoms emitted |
| **2.4a** | Dogfood: agent rebuilds `implement_function` skill + tunes confidence threshold from telemetry | 25-26 | golden trajectory recorded |
| **2.4b** | 60-second demo video + walkthrough doc | 26 | `docs/demos/coder-rewind.md` + video |
| **2.4c** | Coder contract spec doc + lockfile + CI gate (alpha-mode lockfile per § 3.2 escape-hatch sunset rule; release-tag strict-mode lockfile is Phase 3 v0.9.0 cleanup) | 26-27 | `docs/spec/coder-contract-v0.9.md` + `coder-contract.lock` (alpha) + `coder.lockfile.json` (G1 source per § 3.2 / § 5 G1(c)) |
| **2.4d** | Phase 2 ARIS R2 + tag `v0.9.0a1` | 27-28 | tag + CHANGELOG |

**26 working days** of explicit task rows + **2 days slack** consumed inside the same 2026-05-01 → 2026-06-05 window by **Phase 2.0c-extended (#145ext)** which folded the original 2.0c carryover #201 forward into the 2.0c phase rather than deferring it to Phase 3. Net effect: the 2 days of slack that originally lived between sub-phases now sits at the front (consumed by 2.0c-ext); the rest of the schedule slips by 2 working days but **the hard cutoff 2026-06-05 (working day 28) is preserved** since the slack budget always lived within the 28-day total. If we slip BEYOND the now-zero slack budget, 2.4b (demo polish) is narrowed first; substrate completion (2.0a/2.0b/2.0c/2.0c-ext/2.0c′/2.0c″) and core agent (2.1-2.3) are non-negotiable. Notice that 2.0c′ and 2.0c″ ship the `s.plan` and `s.mcts` curated namespaces in parallel with the tail of 2.0c-ext — these are the **SDK-gap-protocol** surfaces the agent's `_planner.py` and `_mcts_policy.py` will bind to (see § 3.2). Until they ship, the agent runs through `s.escape.plan` with first-access audit emission, and G1 stays green because escape-hatch is a documented out-of-contract surface.

---

## 7. Risks

### 7.1 Sandbox escape (`:code/exec` #141)

**Risk:** sandboxed Python is a security promise. Default `resource.setrlimit` + module allowlist is **not strong** against a determined escape (a `__builtins__.__import__("os").system("...")` payload, fork bombs that survive `setrlimit`, /proc reading, etc.).

**Mitigation:** the v0.8.5a1 sandbox is **explicitly documented as v0.5 sandboxing** — suitable for trusted code (the agent's own generations under user supervision), not for untrusted user submissions. Hardening lands in Phase 3 with `firejail` or `bubblewrap` integration. The CHANGELOG entry says so plainly. Any commercial deploy disables `:code/exec` by default and requires explicit opt-in.

### 7.2 LLM cost during dogfood

**Risk:** the dogfood demo (agent rebuilds a skill) could rack up $50-200 of Anthropic API spend if the agent loops poorly.

**Mitigation:** hard token cap per task (default 200K input + 50K output), wall-clock timeout (default 30 min), max-step cap (default 100). All three are configurable; the demo runs with conservative defaults. We can also use Claude Code's Max-20x subscription for the dogfood specifically (no per-token cost) — same pattern as the `claude2` worker on InfraFlow.

### 7.3 SDK leaks discovered late

**Risk:** the agent needs a substrate primitive the SDK doesn't expose, we discover at week 7, and we're forced to either reach-through into private modules (violating G1) or block on a SDK revision.

**Mitigation:** Phase 2.1 (week 5) builds the agent skeleton against the SDK and runs G1 *before* effects + plan + MCTS land. Any leak surfaces in week 5, when we have 3 weeks to revise. We also keep the SDK's `experimental` annotation hot — surfaces can land as `experimental` and stabilize across Phase 2 dogfood.

### 7.4 REPL steering UX is hard to demo

**Risk:** "pause, branch, fold, commit" is conceptually rich but visually subtle in a 60-second video. The audience nods politely and asks for a different demo.

**Mitigation:** the demo script is storyboarded before week 8 starts — it shows a concrete bug scenario (agent tries to fix a failing test, takes wrong path, user pauses, branches, points at the right path, fold scores both, MCTS confirms, commit). The "wrong path → counterfactual" framing makes the abstract substrate-property concrete.

### 7.5 Timeline pressure inside the 26 + 2 working-day window

**Risk:** Phase 1 took 5 calendar weeks (originally framed as 4), absorbed via the 18-day paper-deadline margin which we then dropped. Phase 2 has no such margin: the load-bearing calendar (§ 6) is **26 working days + 2 days slack** across 2026-05-01 → 2026-06-05 (5 calendar weeks). The "4-week" phrasing in the 16-week roadmap header is calendar-month rounding; § 6 is the source of truth.

**Mitigation:** subagent-driven dispatch with mandatory worktree isolation continues — same playbook that landed v0.8.0a1. The 26-working-day plan has 2 days of slack absorbed inside the same 2026-05-01 → 2026-06-05 window (slack lives between sub-phases — typically 0.5 day after each ARIS R2 cycle and 1 day reserve at the end of 2.3 before dogfood; see § 6 final paragraph). If we slip beyond slack, 2.4b (demo polish) is narrowed first; substrate completion (2.0a-2.0d) and core agent (2.1-2.3) are non-negotiable.

---

## 8. Open questions

1. **Skill body Plan AST vs raw Python?** Initial design has skill body as a Plan AST. Counterargument: most skills will be 5-15 lines of Python the agent could just `:code/exec`. ADR-3 below resolves: skill body is a Plan AST whose primary node is `:code/exec` for simple skills, composes into multi-step Plans for advanced skills. Same shape, scaling complexity.

2. **MCTS rollout count default?** v0.6.5 ships configurable rollout count. For the agent we need a default. Proposal: 5 rollouts when agent confidence < 0.65, 0 rollouts when ≥ 0.65 (degrades to ReAct). Tuned via dogfood telemetry in Phase 3.

3. **Dogfood scope: rebuild a skill, or rebuild a substrate module?** Initial framing says skill (smaller, demoable). Stretch: agent rebuilds a small substrate helper (e.g., `persistence.spec.parse_keyword`). We aim for skill in week 8 with substrate-helper as a stretch goal week 9+ (Phase 3 prelude).

4. **Should the agent emit a `Co-Authored-By: persistence-coder` git trailer?** Yes for self-hosted commits in the demo. No for upstream substrate commits during normal Phase 2 dev (those are still human-authored). Documented in the dogfood demo doc.

5. **Telemetry: do we ship usage metrics from day one?** No. v0.9.0a1 emits no telemetry. Privacy v1 (#13, Phase 3) is the right place to design metrics with proper opt-in.

---

## 9. ADRs

### ADR-1 — Coder is in-tree, not sibling repo (resolved Phase 2.0 day 0)

**Decision:** `src/persistence/coder/` ships in the persistence-os repo.
**Rationale:** § 3.1 — eat-own-dogfood, single suite, single license, surfaces SDK gaps as agent test failures.
**Reversal cost:** if v1.0 needs distribution split, we can `pip install persistence-coder` from the same repo via separate setuptools section, no code move required.

### ADR-2 — Coder consumes only `persistence.sdk.*` (G1)

**Decision:** the agent imports nothing from `persistence.fact`, `persistence.effect`, `persistence.plan`, `persistence.txn`, `persistence.replay`, `persistence.spec`, or `persistence.repl` directly. `persistence.sdk.Substrate` (and only the curated subsurfaces it exposes) is the entry point.

**Allowed SDK surfaces:** see **§ 3.2 "SDK-only consumption (the contract test)"** for the canonical enumeration (the "Allowed SDK entrypoints" table + "Proposed v0.8.5a1 SDK additions" paragraph). § 3.2 is the editorial source of truth; this ADR does NOT re-enumerate the surfaces — duplicating the list here is a known contract-leak vector (R3-W3 fix). Any divergence between § 3.2 and any other enumeration is a doc bug. The committed `coder.lockfile.json` is the operational source of truth for CI and is generated from § 3.2.

**Rationale:** the SDK contract design doc § 7 requires a real consumer to validate the contract. The coder is that consumer. By enumerating the closed set in § 3.2 (instead of "anything under `persistence.sdk.*`"), we make G1 a precise invariant: any new agent callsite MUST appear on the § 3.2 list, OR be added to it (and to `_facade.py`) via the SDK-gap protocol (§ 3.2).

**SDK-gap protocol (mandatory):** if Phase 2.x discovers a substrate primitive the SDK does not expose, the gap is filed as a versioned SDK addition in the substrate backlog (e.g. #147 / #148; precedent is `s.txn.fold` shipped at v0.8.0a1 as the public surface of #145). The agent does **not** import private modules. The only tolerated reach-through is `s.escape.<module>` which is documented out-of-contract and audit-emitting.

**Enforcement:** G1 test walks the AST of `persistence.coder.*`, fails CI on any reach-through outside the canonical allowed-set (defined in § 3.2). `s.escape.*` access is permitted but counted: the test asserts the count does not silently grow between releases (delta requires an explicit annotation in the design doc / `coder-contract.lock`).

**Escape-hatch sunset rule.** See **§ 3.2 "Escape-hatch sunset rule"** for the canonical release-gate text. The summary: `v0.9.0a1` permits escape usage with sunset-issue-ID-tagged lockfile entries; `v0.9.0` (general release post-MVP) requires `s.escape.*` call count == 0 in `src/persistence/coder/**/*.py`. The G1 strict-mode rule (lockfile `escape_callsites: []`) is what mechanically refuses the `v0.9.0` tag if any escape remains.

### ADR-3 — Skill body is a Plan AST; `version` is a logical (not temporal) axis

**Decision:** `Skill.body` is a `Plan` — for trivial skills it's a single `:code/exec` node, for compound skills it's a multi-step plan.

**Bitemporal axis discipline (R1-W1 clarification):** `Skill.version` is a **monotonic logical version** of the skill body, NOT a temporal axis. Bitemporal queries (transaction-time, valid-time) are orthogonal and addressed via `s.fact.as_of(t)` / `s.fact.as_of_valid(t)` / `s.fact.history(...)`. Mixing valid-time language with transaction-time queries on `version` is a category error the design doc previously committed; § 3.5 carries the canonical wording.

**Rationale:** unifies skill execution with plan execution, gets replay + MCTS for free, lets skills compose. Logical-version-on-its-own-axis lets us bitemporally compose ("what was version 3 of skill X as of yesterday's transaction time") without conflating axes.

**Alternatives considered:** raw Python callable (rejected: no replay), opaque string (rejected: no audit), version-as-valid-time (rejected: collapses two orthogonal axes into one column, breaks history queries).

### ADR-4 — LLM provider default is Anthropic SDK direct, LiteLLM optional

**Decision:** Anthropic SDK is the in-tree default; LiteLLM ships as `pip install persistence[coder-litellm]`.
**Rationale:** Anthropic SDK is the lowest-overhead path for Claude models and we are an Anthropic-shop for v0.9. LiteLLM as opt-in covers the design-partner-asks-for-OpenAI case.
**Reversal cost:** swap the default in v1.0 if a design partner explicitly requires OpenAI as in-tree default.

### ADR-5 — `:code/exec` sandbox is "v0.5 sandboxing" via capability-denial (Phase 2.0 #141)

**Decision:** v0.8.5a1 ships subprocess + `setrlimit` + **capability-denial module allowlist** + network-namespace denial + read-only project mount + scratch-dir write capability. **Determinism is achieved by capability-denial + environment control, NOT by static detection of nondeterminism in user code.** Hardening (firejail / bubblewrap) is Phase 3.

**Rationale:** § 7.1 + § 4.2 — strong sandboxing is a multi-week design. The v0.5 capability-denial set is sufficient for trusted-author code under user supervision; documented as such; commercial deploy disables `:code/exec` by default. Capability-denial is verifiable by construction (the module isn't reachable, so the call cannot succeed); detection (AST-scan for `time.time()` / `random.random()` / network) is whack-a-mole and unreliable in Python — it would create false confidence and inevitable false-negatives.

**Enforcement:** CHANGELOG explicit security caveat; `:code/exec` raises if `PERSISTENCE_CODER_ALLOW_CODE_EXEC` env var is unset in non-dev mode. The G2 falsifiable gate runs both positive (capability-compatible code → byte-identity replay) and negative (capability-violating code → documented exception, never silently passes) tests.

**AMENDED 2026-05-01 (Phase 2.0d W3) — soft-isolation rescope.** The R2.3 codex review demonstrated that Python-level capability-denial as architected here **cannot prevent host-FS reads** when the warm-imported stdlib transitive closure is preserved. Concrete repro: `import dataclasses; dataclasses.sys.modules['builtins'].open('/etc/passwd','r').read(20)` succeeded inside the sandbox with `exit_code == 0`, leaking `/etc/passwd` bytes via `dataclasses.__dict__["sys"]` → `sys.modules["builtins"]` → `builtins.open`. The `dataclasses` warm-import (and any other allowed module's `__dict__`) gives an attacker a chain to `sys.modules`, which exposes both `builtins` and any forbidden-but-already-loaded module (`os`, `io`, etc.). Removing those references from individual module dicts is whack-a-mole at a different layer and would still leave equivalent escape vectors via other allowed modules' transitive references.

The decision the substrate-completion track is taking, after this discovery, is to **rescope `:code/exec` v0.8.5a1 honestly** rather than chase Python-level confidentiality:

- The sandbox is restated as a **soft-isolation runtime guard / best-effort containment** for plan-step bodies authored by the agent under user supervision. It is NOT a confidentiality boundary against an adversarial sandbox-body author.
- What the v0.8.5a1 sandbox **does** guarantee, by construction:
  - **Subprocess isolation** — never `eval` / `exec` in-process; user code runs in a fresh interpreter.
  - **Wall-clock + CPU + address-space + fd + nproc bounds** via `setrlimit` (Linux honored; macOS RLIMIT_AS best-effort, others honored).
  - **Write-denial via `RLIMIT_FSIZE=0`** — kernel-enforced `SIGXFSZ` on any `write()` from the child. This is a real OS-level write boundary; it is unaffected by the Python-level escape.
  - **Determinism pinning** — `PYTHONHASHSEED=0`, `PYTHONDONTWRITEBYTECODE=1`, no site-packages, no user-base, no script-dir injection.
  - **Curated user-source `__builtins__`** — `open` / `eval` / `ex`+`ec` / `compile` / `input` / `breakpoint` removed. (This is still a useful default — it raises the bar for accidental misuse and surfaces capability-violation as a `NameError` for honest plan-step bodies.)
  - **Top-level import deny-list + closed allow-list** — only `json` / `re` / `dataclasses` are direct-importable; `os` / `sys` / `subprocess` / `socket` / `urllib` / `pathlib` / etc. raise `CodeExecForbiddenImport` at the import statement.
  - **Audit-chain integrity** — every `:code/exec` call emits the canonical 7-key datom under the active txn's Merkle chain (M2/M5 W2 fix). Replay determinism (audit-replay default; opt-in re-execution-replay with hash verification) is intact.
- What the v0.8.5a1 sandbox **does NOT** guarantee, and now explicitly disclaims:
  - **Confidentiality of host filesystem contents** against a sandbox-body author who knows Python's stdlib. The `dataclasses.sys.modules['builtins'].open` escape (and equivalents via other allowed modules) is a known-known limitation.
  - **Isolation from already-loaded forbidden modules** that landed in `sys.modules` during the warm-import phase. Any allowed module's `__dict__` may transitively expose them.
  - **Defense against malicious or adversarial code.** A determined adversarial body can read host files. `:code/exec` is for trusted-author plan-step bodies under user supervision, not for untrusted user submissions.
- The substrate-completion claim for v0.8.5a1 **does not depend on hard sandbox isolation**. The wedge story (Karpathy product reframe — rewind / branch / replay over agent trajectories) is load-bearing on **audit-chain integrity** + **replay determinism**, both of which are demonstrably correct (see M5 resolution at `transaction.py:703` and the W2 audit-stack tests). The sandbox's role in that story is best-effort containment of accidentally-divergent reads, not adversarial containment.
- **Hard isolation is queued as a v0.9.x sandbox-redesign track** (forward-pointer in § 14 changelog and CHANGELOG-effect.md). The redesign uses an **OS-level boundary** (gVisor, nsjail, Docker / OCI runtime, or WASM-Pyodide) where confidentiality is enforced by the kernel / hypervisor, not by a Python-level filter. That track supersedes the v0.8.5a1 soft-isolation runtime guard rather than extending it; the audit-datom contract carries forward unchanged.
- **Test-side documentation:** the R2.3 escape repro is preserved as an `xfail`-strict regression test (`test_known_escape_via_dataclasses_sys_modules_builtins_open` in `tests/effect/test_code_exec.py`) so the moment the v0.9.x real-OS-boundary lands, the xfail flips to PASS and the marker is removed — the regression test becomes the v0.9.x sandbox-redesign acceptance signal.

The pre-W3 ADR-5 text above is preserved verbatim as the historical record of the original capability-denial framing. Where W3 deviates from it (the confidentiality claim, specifically), this amendment supersedes; everything else (write-denial, determinism, audit-replay) is unchanged.

### ADR-6 — Plan Edit API audit invariant

**Decision:** every plan edit is a `:plan/edit` datom with before/after op-hash + step-id. No silent edits.
**Rationale:** preserves replay byte-identity — re-executing a trajectory must reconstruct the edit history exactly.
**Enforcement:** G3 property test at max_examples=100.

### ADR-7 — `DB.fold` AND `DB.fork` are public SDK primitives on `s.txn.*`

**Decision:** Two co-existing executor primitives ship on `_TxnNamespace`:

- `s.txn.fold` / `s.txn.fold_into` — foldl/reduce with chosen-marker (Path-A audit shape: `:fold/chosen`).
- `s.txn.fork` — speculate-rollback-pick with the canonical 4-datom audit shape (`:fork/probe` + `:fork/branch` × N + `:fork/score` × N + `:fork/chosen`).

**Single source of truth for the surface naming is § 4.3** (verified against `src/persistence/sdk/_facade.py:_TxnNamespace.fold:178` and `_TxnNamespace.fork` added in Phase 2.0c-extended); this ADR defers to § 4.3 / § 3.2 / ADR-2 for the canonical surface and only ratifies the *decisions* (a) to expose `fold` and `fork` as curated SDK primitives rather than keeping them on the raw `DB`, and (b) to use distinct namespaces (`:fold/*` vs `:fork/*`) to disambiguate the two semantically distinct primitives.

- `s.txn.fold(seed, items, fn, **kwargs)` — shipped at v0.8.0a1 as `@experimental("v0.8")` (see `_facade.py:_TxnNamespace.fold:178`). Foldl/reduce semantics: commits every item's facts as it iterates. Unchanged in Phase 2.
- `s.txn.fold_into(seed, items, fn, choose, *, tx, **kwargs)` — convenience method added in Phase 2.0c (sub-tag `v0.8.5a1`) that runs the speculate-pick lifecycle and commits the chosen branch in one txn. Phase 2.0c-extended **rewires fold_into on top of `DB.fork`** so it inherits substrate-true rollback semantics (only chosen branch's facts persist) and the canonical 4-datom audit shape. Public signature unchanged; downstream callers unaffected.
- `s.txn.fork(items, fn, choose, *, seed, tx, on_error, provenance)` — added in Phase 2.0c-extended (sub-tag `v0.8.5a1`) as the substrate-true speculate-rollback-pick primitive. For callers who want explicit speculate-rollback semantics over Python state without the fact-level convenience layer.

**Why two primitives, not one:** `fold` is a transactional foldl/reduce; `fork` is speculate-rollback-pick. They are semantically distinct, and giving them distinct audit namespaces (`:fold/*` vs `:fork/*`) is **load-bearing for replay safety**. A trajectory recorded under v0.8.0a1's `fold_into`-emits-`:fold/chosen` shape would silently change meaning if upgraded to a Phase 2.0c-extended trajectory where the same call now emits `:fork/*`. The namespace split lets readers tell at a glance which semantic was in play.

**Why `s.txn` and not `s.fact`:** Both primitives are transactional speculation lifecycles — they open per-branch state, score it, and commit at most one. Per the curated-namespace shape pinned in Adapter SDK ADR-1, that lifecycle belongs on `_TxnNamespace`, not `_FactNamespace` (which is the bitemporal fact-log read/write surface). The R1-W1 cleanup notes this as a side-effect correction; ADR-7 inherits the same placement for both `fold` and `fork`.

**Rationale:** § 4.3 — MCTS scoring needs the speculate-rollback-pick shape (`fork`); skill-body folds need the foldl-with-audit shape (`fold`). The agent is the first consumer of both.

**Stability annotation:** Both primitives are `@experimental` today (`fold`: `@experimental("v0.8")`; `fork`: `@experimental("v0.8.5a1")`); promoted to `@stable("v0.9")` after Phase 2 dogfood survives without API change.

**Cross-references (must stay in sync):** § 3.2 allowed-SDK-surface table row for `s.txn.*`; § 3.7 per-effect replay table rows for both `:fold/chosen` and `:fork/*`; § 4.3 (the load-bearing description, now covering both primitives); ADR-2 closed allowed-set; § 13 R1+R2 changelogs; § 14 Phase 2.0c-extended changelog.

### ADR-8 — REPL-steering session class registers via Module 7's capability system

**Decision:** `_CoderSteeringSession` is a Module 7 operator session with capabilities `coder/pause`, `coder/snapshot`, `coder/context`, `coder/branch`, `coder/fold`, `coder/commit`. Tokens are minted by the existing REPL token system.
**Rationale:** no new auth surface; reuses Module 7's capability gate.
**Enforcement:** test asserts `coder/*` tokens cannot reach non-coder ops on the same WS connection.

### ADR-9 — Dogfood demo uses Anthropic Max-20x subscription (claude2 pattern)

**Decision:** the 60-second demo run uses a Max subscription, not per-token API. Production deploys can use either.
**Rationale:** § 7.2 — protects against token-cost surprises during demo recording, mirrors the InfraFlow `claude2` worker pattern.
**Reversal cost:** none; the LLM provider abstraction (§ 3.3) is transport-agnostic.

### ADR-10 — Coder contract spec lands at v0.9.0a1, not v0.9.0

**Decision:** `docs/spec/coder-contract-v0.9.md` ships at v0.9.0a1 alongside the agent. Stable v0.9.0 (no `a`) follows 1-2 weeks of dogfood.
**Rationale:** mirrors the SDK contract pattern (v0.8.0a1 alpha → v0.8.0 stable). Agent surface needs real use to stabilize.

---

## 10. Persistence channels

- Conductor: `persistence-os-product_20260429/STATUS.md` — append per-phase block (2.0 / 2.1 / 2.2 / 2.3 / 2.4)
- Vault: `nawfal-dev/L1` topic memory at design lock + per sub-phase
- Serena memory: `persistence-os/phase-2-coder-design-locked` (post-ARIS R1) → `persistence-os/v0.9.0a1-coder-mvp-shipped` (Phase 2 close)
- Auto-memory: `MEMORY.md` index entry per major shipment + `project_persistence_os_product_track.md` topic file

---

## 11. Out-of-scope (deliberate)

- **Web UI / IDE plugin** — Phase 4 minimum. The agent ships CLI-only at v0.9.0a1.
- **Multi-tenant policy + privacy** — Phase 3, gated on #13.
- **Non-Python language clients** — Phase 4+; the SDK contract supports MCP, which is enough for cross-language agents in Phase 3 design partners.
- **Commercial pricing / public launch** — Phase 4. v0.9.0a1 is internal + design-partner only.
- **Paper rewrite** — Phase 5, deferred per Karpathy product reframe.
- **Skill marketplace / sharing** — Phase 4+. v0.9.0a1 ships builtin skills + local custom registration only.

---

## 12. Sign-off

This design is R0 DRAFT. Per project convention:

1. User reviews this doc.
2. ARIS R1 codex hard-mode review (8.5 mean / 7.5 min target). Carry-forward reviewer memory from v0.8.0 design rounds where relevant (Phase 1 ADR conventions, the substrate-completion rhythm, the SDK contract precedent).
3. R1 fix-pass if FIX-FIRST.
4. Lock design at R1 PASS.
5. Dispatch Phase 2.0a (#140 Plan Edit API) — first impl task.

The standing rule applies: polish the design, run ARIS, then proceed. Nothing dispatches before R1 PASS.

---

## 13. R1 fix-pass changelog (R0 → R1-W1)

R1 codex hard-mode review (FIX-FIRST 7.56 mean / 6.8 min) flagged 3 BLOCKERs + 4 SHOULD-FIXes + 2 NITs. W1 closes each:

| # | R1 finding | Fix landed in | Section / ADR / Gate |
|---|---|---|---|
| **B1** | SDK-purity contradiction — file layout + loop diagram reach into `persistence.plan.*` directly, contradicting ADR-2 / G1 | Renamed callsites to `s.plan.execute` / `s.mcts.search` (proposed v0.8.5a1 SDK additions #147 / #148); explicit closed-set surface table; SDK-gap protocol pinned; until #147/#148 ship the agent uses `s.escape.plan` (audit-emitting, documented out-of-contract) | § 3.1 file layout; § 3.2 SDK-only consumption (rewritten); § 3.4 loop diagram; ADR-2 (rewritten); § 6 task table 2.0c′ + 2.0c″ |
| **B2** | Replay byte-identity under-specified for non-LLM effects (`:fs`/`:shell`/`:git`/REPL); only `:llm` replay was pinned | New § 3.7 Replay Modes — Audit-replay (default, all gates) vs Re-execution-replay (opt-in, `:code/exec` only in v0.9); per-effect table (`:llm`, `:fs/read`, `:fs/write`, `:fs/glob`, `:fs/grep`, `:shell/exec`, `:code/exec`, `:git/*`, `:repl/request`+`:repl/response`, `:plan/edit`, `:fold/*`); REPL directives datomized as input events — folds in SHOULD-FIX 4 | § 3.7 (new); G2 / G3 / G6 / G7 / G8 (now have a defined replay rule to test against) |
| **B3** | Timeline math — § 6 reached "days 27-28" inside a stated 22-working-day window | Window normalized to **26 working days + 2 days slack**; § 1 Summary, header `Window:` line, and § 6 task table all agree on a single calendar; tasks reflowed to 1-26 with slack absorbed inside the window; added 2.0c′ + 2.0c″ + 2.1c rows | header; § 1 Summary; § 6 (rewritten) |
| **SF1 (G9)** | G9 inverted pass criterion — read like "drift detected" was a pass; lockfile / public-surface / annotations all undefined | G9 rewritten: extract `__init__.__all__` + `_cli.py` argparse + effect-schema registry → `coder-contract.lock` (canonical-JSON, sha256); **PASS = lockfile bytes match committed snapshot AND every new symbol is annotated AND every new SDK callsite is in the § 3.2 allowed-set**; FAIL on any of three | § 5 G9 row |
| **SF2** | Bitemporal axis confusion — `Skill.version: int` "valid_time index" + lookup ordered by valid_time, but G5 tested "query at past txn" | `Skill.version` is logical (not temporal); bitemporal queries are orthogonal via `as_of` / `as_of_valid`; G5 rewritten to test the composition explicitly (transact v1 at tx_t1, v2 at tx_t2, query as_of(tx_t1)→v1, as_of(tx_t2)→v2, as_of(tx_t2).where(version=1)→v1) | § 3.5; ADR-3 (clarified); G5 |
| **SF3** | `:code/exec` "rejects nondeterministic code" via static detection — not feasible in Python | Reframed: capability-denial + environment control. Module allowlist denies non-deterministic stdlib (`os`, `time`, `random`, `socket`, etc.); patched determinism shims when body opts in; network-namespace denial; read-only project mount + scratch dir; G2 negative tests assert `ImportError`/`PermissionError`/`ENETUNREACH` on capability-violating bodies | § 4.2 (rewritten); ADR-5 (rewritten); G2 |
| **SF4** | REPL directives not datomized — G6/G7 byte-identity replay would have been performative | REPL directives emit `:repl/request` (with op, op_args, session_id, request_id, capability_token_fingerprint) + `:repl/response` (with result payload + request_id) datoms at the decision boundary; replay re-injects the recorded payloads at the same boundary. Folded into the § 3.7 per-effect table | § 3.7; § 3.4 loop diagram; G6 / G7 |
| **N1** | Confidence threshold 0.65 ungrounded | Annotated as starting value, will be tuned during dogfood (Phase 2.4a); missing-confidence default 0.5 to prevent silent escalation-bypass on malformed `:llm/decision` payloads | § 3.4 |
| **N2** | `:shell/exec` allowlist not tied to replay/audit | Added `argv / env_allowlist_subset / cwd / allowlist_version` to the `:shell/exec` request datom; `allowlist_version` participates in audit-replay (a stale-allowlist replay raises rather than silently passing) | § 3.7 per-effect table |

**Side-effect cleanups landed in W1:**

- `s.fact.fold` claim corrected to `s.txn.fold` (§ 4.3) — `_facade.py` ships `fold` on the `_TxnNamespace`, not `_FactNamespace`. The Phase 2.0c task is hardening + adding `fold_into`, not inventing the surface (already shipped at v0.8.0a1 as `@experimental`).
- §3.6 `coder.fold` reference updated to point at `s.txn.fold`.
- ADR-2 enforcement clarified: G1 also asserts `s.escape.*` access count does not silently grow between releases.

**No scope shift.** Two new substrate-backlog items (#147 `s.plan` namespace, #148 `s.mcts` namespace) are tracked as proposed SDK additions inside Phase 2.0 (sub-tag `v0.8.5a1`), reusing the same SDK-gap protocol that landed `s.txn.fold` from #145. Until they ship, the agent runs through `s.escape.plan` with first-access audit emission. No code under `src/persistence/coder/` exists yet (Phase 2 is still in design).

---

## R2 fix-pass changelog (R1-W1 → R2-W2)

R2 codex hard-mode review (FIX-FIRST 8.0 mean / 7.3 min, +0.44 mean / +0.5 min vs R1) closed all 9 R1 findings (B1/B2/B3/SF1-partial/SF2/SF3/SF4/N1/N2 per codex's own ruling) and surfaced 1 new BLOCKER + 4 SHOULD-FIXes + 1 NIT introduced or persisting through W1. **W2 closes the BLOCKER + 2 SHOULD-FIXes (the cross-section/contract-leak items where ADR-2/G1/ADR-7/§2 wedge copy must agree).** The two remaining R2 SHOULD-FIXes (schedule-staleness §1+§7.5 residue and `s.escape.*` sunset gate) are deferred to R3-W3 if R3 still flags them — the W2 scope was tightened to the contract-integrity blockers per the R2 reviewer's "ADR/body drift is now the highest-risk doc integrity failure mode" memory update.

| # | R2 finding | Fix landed in | Section / ADR / Gate |
|---|---|---|---|
| **B1 (R2)** | ADR-7 reintroduced wrong SDK surface naming (`s.fact.fold` / `s.fact.fold_into`), contradicting §4.3 + §3.2 + W1 side-effect cleanup which canonically place fold under `s.txn.*` per `_facade.py:_TxnNamespace.fold:178`. Exact "ADR-as-contract-leak vector" pattern. | ADR-7 rewritten end-to-end: title flips from "v0.8.5a1" to "on `s.txn.*`"; decision body uses `s.txn.fold` / `s.txn.fold_into`; §4.3 is pinned as single source of truth (ADR-7 references §4.3, not the reverse); placement rationale ("`fold` is a transactional speculation primitive — belongs on `_TxnNamespace`, not `_FactNamespace`") added; cross-references list (§3.2, §4.3, ADR-2, §13) called out so future W-cycles must grep all four in the same commit | ADR-7 (rewritten) |
| **SF1 (R2)** | §2 wedge copy ("re-execute any past trajectory… verify byte-identity") outran §3.7's two-mode framing where audit-replay is the default and re-execution-replay is opt-in `:code/exec`-only in v0.9. Marketing verb mismatch with the per-effect spec — wedge copy ahead of semantics. | §2 "Counterfactual replay" bullet rewritten to the two-mode shape: "every trajectory is *replayable*: by default via **audit-replay** (handlers are NOT re-invoked; recorded request + result datoms walked in transaction order; byte-identity over canonicalized datom stream), and optionally via **re-execution-replay** against a snapshotted state for counterfactuals (v0.9 supports re-execution for `:code/exec` only — see § 3.7 for the per-effect rule)"; the `replay_check` reference is now explicitly tagged "audit-replay" so the wedge demo and the gate definition cannot drift apart | § 2 (Counterfactual replay bullet rewritten) |
| **SF2 (R2)** | ADR-2 enforcement claims (closed allowed-set checks + escape-hatch delta gating) exceeded what G1 actually specified — G1 said only "imports outside `persistence.sdk.*`". Spec-level mismatch on the most important contract gate; CI would have encoded the weaker interpretation by default. | G1 row rewritten as **three subchecks (a/b/c)** matching ADR-2's enforcement claims: (a) every external import in `src/persistence/coder/` resolves to the explicit allowed-set (closed enumeration in ADR-2 / § 3.2); (b) every `s.escape.*` call site is matched by an audit-emitting wrapper (escape access is observable, never silent); (c) the lockfile snapshot of the (a)+(b) sets is byte-identical to the committed expected snapshot (escape-hatch delta tracked — count cannot grow silently between releases). PASS = all three green; FAIL = any one fails. The G1 row is now **the single source of truth for the ADR-2 enforcement contract** ("ADR-2 references this gate, not vice versa") so the gate stays the project's truth and ADR text cannot drift the contract | § 5 G1 row (rewritten); ADR-2 (already references G1, now the reverse pin is explicit) |

**Ground-truth verification (each fix grounded against `src/persistence/sdk/_facade.py`):**

- `s.txn.fold` lives at `_TxnNamespace.fold` (`_facade.py:178`, `@experimental("v0.8")` with reason "PG6 R3-M1: speculation / rollback / checkpointing primitive…"). NOT on `_FactNamespace` (which lacks a `fold` method). ADR-7 is now consistent with §4.3 / §3.2 / `_facade.py` reality.
- §3.2 allowed-SDK-surface table row for `s.txn.*` already lists `fold / .fold_into` as of R1-W1; W2 leaves it untouched (it was already correct).
- §4.3 already says "v0.8.0a1 has already promoted it to the SDK at `s.txn.fold(seed, items, fn, **kwargs)`" — W1 caught this; W2 leaves it untouched.

**R2 SHOULD-FIXes deferred to R3-W3 (if R3 still flags):**

- **R2-SF3 (schedule consistency in §1 + §7.5):** §1 "Substrate completion (5-7 days)" vs §6 days 1-11 allocation; §7.5 still says "22-day plan inside 4-week window." Not blocker-grade for the contract integrity W2 scope; the §6 calendar (the load-bearing one) is internally consistent post-W1.
- **R2-SF4 (`s.escape.*` sunset gate / exit criterion):** No release rule that forces `s.escape.*` count to zero by stable v0.9.0. Architecturally orthogonal to the BLOCKER; tracking as substrate backlog "escape-hatch sunset gate" (no number assigned yet).
- **R2-NIT:** `_mcts_policy.py` file-table comment + §4.3/§3.2 wording on `s.txn.fold` shipping date — cosmetic, not load-bearing for R3.

**No scope shift.** All three W2 fixes are doc-only edits (one ADR rewrite, one wedge-copy bullet rewrite, one gate-row rewrite). No code under `src/persistence/` was touched. No new substrate backlog items added. No new ADRs added (ADR-7 was rewritten in place).

---

## R3 fix-pass changelog (R2-W2 → R3-W3)

R3 codex hard-mode review (FIX-FIRST 8.2 mean / 7.2 min, +0.2 mean / -0.1 min vs R2) closed all R2-W2-targeted findings (B1 ADR-7 surface naming, SF1 §2 wedge copy, SF2 G1↔ADR-2 enforcement) per codex's own ruling — except SF2 was "Partially Closed" because W2's G1 rewrite introduced new ambiguity ("every external import" could be misread; "ADR-2 / § 3.2" cited two enumerations as canonical when they differed slightly). Mean improved (+0.2) but min dropped (-0.1) because the R2-deferred SF3 schedule-staleness in §1+§7.5 became the primary min-score drag. **W3 closes the 3 R3 SHOULD-FIXes** (no new BLOCKER; W3 is a polish-pass into PASS territory).

| # | R3 finding | Fix landed in | Section / ADR / Gate |
|---|---|---|---|
| **SF1 (R3) — Schedule text drift §1 + §7.5** | Stale "5-7 days" / "3-4 days" remnants in § 1 Summary; "22-day plan inside 4-week window" remnant in § 7.5. R3-deferred carryover from R2-SF3. The reviewer flagged this as the "schedule-arithmetic-as-scope-leading-indicator" pattern they're tracking | § 1 Summary substrate-completion + coder-build + dogfood durations rewritten to defer to § 6 ("working days 1-11; see § 6", "working days 12-23; see § 6", "working days 24-26; see § 6"); § 4.1 / § 4.2 / § 4.3 sub-task duration headers rewritten the same way ("working days 1-3; see § 6 task table row 2.0a" etc.); § 7.5 Risks heading flipped from "4-week timeline pressure" to "Timeline pressure inside the 26 + 2 working-day window"; § 7.5 risk + mitigation prose rewritten to defer to § 6 — "26 working days + 2 days slack" replaces "22-day plan inside 4-week window"; "4-week" remaining mentions are explicitly tagged "calendar-month rounding; § 6 is source of truth" | § 1; § 4.1; § 4.2; § 4.3; § 7.5 (heading + body) |
| **SF2 (R3) — Escape hatch sunset rule** | No explicit release gate forcing `s.escape.*` count to 0 by stable v0.9.0. R3-deferred carryover from R2-SF4. Open-ended escape hatches drift permanent | § 3.2 added "Escape-hatch sunset rule (release gate)" subsection: every `s.escape.*` call site is a tracked TODO with a sunset SDK-addition issue (#147 sunsets `s.escape.plan.execute`; #148 sunsets `s.escape.plan.mcts_search`); G1 lockfile records every escape site with sunset issue ID; **`v0.9.0a1` permits escapes with sunset-tagged lockfile entries; `v0.9.0` (general release) requires `s.escape.*` call count == 0** — release-tag CI runs G1 in strict mode against an empty-escape-set lockfile and refuses the tag if any escape remains. Cross-referenced from ADR-2 (new "Escape-hatch sunset rule" sub-paragraph), G1 row (added "At the v0.9.0 release tag, G1 runs in strict mode" clause), § 6 task table row 2.4c (alpha-mode lockfile is Phase 2 deliverable; release-tag strict-mode lockfile is Phase 3 v0.9.0 cleanup) | § 3.2 (Escape-hatch sunset rule subsection); ADR-2 (cross-ref added); § 5 G1 row (strict-mode clause added); § 6 task table row 2.4c (alpha-vs-strict lockfile clarified) |
| **SF3 (R3) — G1(a) ambiguity + duplicate allowed-set sources** | (a) G1(a) "every external import" was ambiguous about transitive imports / re-exports / `from X import Y` vs `import X.Y`; (b) the allowed-set was enumerated in three places (ADR-2, § 3.2, lockfile snapshot) without a mechanical "these are byte-identical" anchor — the reviewer flagged this as a new contract-leak vector W2 introduced | § 3.2 made the **single canonical (editorial) source of truth** ("Allowed SDK entrypoints" table + "Proposed v0.8.5a1 SDK additions" paragraph). ADR-2 rewritten to defer to § 3.2 (no independent enumeration); the "Allowed SDK surfaces" enumeration in ADR-2 is replaced with `see § 3.2`. The committed `coder.lockfile.json` is the operational source of truth for CI; build-rule paragraph added at top of § 3.2 ("`scripts/emit_g1_lockfile.py` parses § 3.2 and writes `coder.lockfile.json`", with editorial-source fallback if the script slips into Phase 2.4c). G1(a) tightened to "every **top-level `import` / `from … import …` statement** in `src/persistence/coder/**/*.py` whose top-level package is `persistence` (or `persistence.*`) resolves to a module name in the canonical allowed-set defined in § 3.2"; explicit out-of-scope clauses for stdlib + third-party + transitive runtime imports added. G1 row + § 3.2 now both call § 3.2 the **editorial source of truth** and the lockfile the **operational source of truth** | § 3.2 (build-rule paragraph + canonical-source claim); ADR-2 (rewritten to defer to § 3.2); § 5 G1 row (subcheck (a) tightened) |

**Ground-truth verification (each fix grounded against `src/persistence/sdk/_facade.py` v0.8.0a1):**

- `s.escape.<module>` namespace lives at `_facade.py:_EscapeNamespace.__getattr__` (`_facade.py:362`) — the `:sdk/escape-hatch-access` audit emission on first access per session per attribute is real, the sunset rule is enforceable on the existing audit signal.
- `s.txn.fold` at `_TxnNamespace.fold` (`_facade.py:178`) is unchanged. § 3.2 / ADR-7 / § 4.3 cross-references unchanged in W3.
- `s.plan.execute` and `s.mcts.search` are NOT yet on `_facade.py` — confirmed via `grep -nE "fold|plan|mcts|escape" _facade.py` → only `_EscapeNamespace`, `_TxnNamespace.fold`, no `_PlanNamespace` / `_MctsNamespace`. The "proposed v0.8.5a1 SDK additions" framing in § 3.2 holds; W3 leaves it untouched.

**Schedule grep before/after (Fix 1 evidence):**

Before W3: `grep -nE "5-7 days|22.{0,2}day|4.{0,2}week|~14 days|3-4 days|4-5 days|2-3 days"` returned 11 hits (incl. § 1 Summary "5-7 days" / "14 days" / "3-4 days"; § 4.1 "(3-4 days)"; § 4.2 "(4-5 days)"; § 4.3 "(2-3 days)"; § 7.5 heading "4-week timeline pressure" + body "22-day plan inside 4-week window").

After W3: same grep returns 5 hits — all intentional and explicitly anchored to § 6: header `Window:` line (debunks "4-week" against § 6); § 2 "pure ReAct loop is 2-3 days" (illustrative alternative-MVP, not Phase 2 calendar); § 6 first paragraph (debunks "22-day / 4-week" against § 6 itself); § 7.5 mitigation paragraph (explicitly tags "4-week" as calendar-month rounding, defers to § 6); § 13 R2 changelog historical record (immutable). All non-historical hits explicitly cite § 6 as source of truth.

**Allowed-set duplicate-source closure (Fix 3 evidence):**

Before W3: ADR-2 had a 9-line bullet enumeration; § 3.2 had a 7-row table + 1 paragraph for proposed additions; G1 row cited "ADR-2 / § 3.2" as if they were identical (they were not — `fold_into` framing differed slightly).

After W3: § 3.2 is the canonical editorial source (table + proposed-additions paragraph). ADR-2's "Allowed SDK surfaces" line is now `see § 3.2 …` with no independent enumeration. G1 row cites "the canonical allowed-set defined in § 3.2" as the single source. Lockfile is generated from § 3.2 via `scripts/emit_g1_lockfile.py` (Phase 2.4c) — or, if the script slips, the editorial-source rule is sufficient.

**No scope shift.** All three W3 fixes are doc-only edits. No code under `src/persistence/` was touched. No new substrate backlog items added (#147 / #148 carry the sunset issue IDs already in the substrate backlog). No new ADRs added (ADR-2 was edited in place; the "Escape-hatch sunset rule" lives in § 3.2 + cross-references in ADR-2 / G1 / § 6, not as a standalone ADR — its enforcement mechanism is G1's strict mode at the release tag, not a new contract).

---

## 14. Phase 2.0c-extended changelog (post-Phase-2.0c-shipped → 2.0c-ext)

After Phase 2.0c shipped (`s.txn.fold_into` Path-A foldl-with-marker at HEAD `4d4fb26`, suite 1953 passed), it was clear the shipped contract did not match the canonical 4-datom audit shape from § 3.7 + § 4.3 ADR-7 (which calls for `:fold/probe` + `:fold/branch` × N + `:fold/score` × N + `:fold/chosen` with rollback semantics for non-chosen branches). The shipped Path-A emitted only `:fold/chosen` and committed all branches' facts as a foldl — semantically distinct from the design contract.

**Phase 2.0c-extended (#145ext) closes the gap** by introducing `DB.fork` as a sibling substrate primitive to `DB.fold`, rewiring `s.txn.fold_into` on top of it, and adding `s.txn.fork` as a new curated SDK surface for callers who want explicit speculate-rollback-pick semantics. Key decisions:

| # | Decision | Rationale |
|---|---|---|
| 1 | **Two co-existing primitives** (`fold` and `fork`), not one promoted from the other | `fold` (foldl/reduce + chosen-marker) and `fork` (speculate-rollback-pick) are semantically distinct; making `fold_into` rollback-by-default would silently change the shipped v0.8.0a1 `fold` contract for users of `s.txn.fold` directly |
| 2 | **Distinct audit namespaces** (`:fold/chosen` vs `:fork/probe/branch/score/chosen`) | Replay safety: a trajectory recorded under v0.8.0a1's `fold_into`-emits-`:fold/chosen` would silently change meaning if upgraded to a 2.0c-ext trajectory where the same call now emits `:fork/*`. The split makes the semantic explicit at the wire level |
| 3 | **`DB.fork` operates on opaque Python state, not on the substrate** | Per-branch isolation is structural (each branch starts from `seed`); rollback is trivial (non-chosen branches' state is just discarded Python objects, nothing was ever written to the substrate). Adapters that want fact-side commits (`fold_into`) thread facts through the wrapper closure and commit only the chosen branch via `tx.db.transact_batch` after `DB.fork` returns |
| 4 | **`fold_into` rewired without changing public signature** | Downstream callers unaffected at the API level. Internal behavior changes (per-branch isolation; chosen-only commit; `:fork/*` audit shape; original-exception propagation under "abort") are documented as the v0.8.0a1 → v0.8.5a1 delta in CHANGELOG-sdk |
| 5 | **#201 (originally proposed for v0.9.x Phase 3) folded into 2.0c** | The 4-datom shape is load-bearing for the wedge story (rewind/branch/replay needs per-branch rollback to feel real, per Karpathy product reframe). Deferring it would have left v0.8.5a1 shipping a known-incorrect contract relative to the design doc; better to consume the 2-day slack budget now than to ship a misalignment |

**Schedule impact:** Phase 2.0c-extended runs days 10-12.5 (consuming 2 days of slack from the 28-day envelope). The rest of the schedule slips by 2 days (2.0d at days 13-14, 2.4d at days 27-28), but **the hard cutoff 2026-06-05 is preserved** since the slack always lived within the 28-day envelope. Slip-beyond-slack triggers narrowing 2.4b (demo polish) per § 7.5 mitigation rule.

**Files touched in 2.0c-ext:**

- `src/persistence/fact/_fork.py` (NEW, ~480 lines) — `DB.fork` impl + `ForkResult` / `ForkBranchResult` / `ForkOutsideDosync` / `ForkChooseError` + 4 audit-emission helpers + canonicalisation helper.
- `src/persistence/fact/db.py` (+85 lines) — `DB.fork` method delegating to `_fork.fork_impl`. `DB.fold` untouched.
- `src/persistence/fact/__init__.py` (+8 lines) — exports.
- `src/persistence/sdk/_facade.py` (+66 lines) — `_TxnNamespace.fork` curated surface.
- `src/persistence/sdk/_fold_into.py` (full rewrite ~510 lines) — rewired on top of `DB.fork`, replaces Path-A wrapper-state-list hack + `:fold/chosen` emission.
- `src/persistence/sdk/__init__.py` (+9 lines) — `Fork*` re-exports.
- `src/persistence/sdk/CHANGELOG-sdk.md` — v0.8.5a1 entry rewritten to document both primitives + the supersession of Path-A within 2.0c + closure of #201.
- `tests/store/test_fork.py` (NEW, 20 cases) — `DB.fork` unit coverage.
- `tests/store/test_fork_audit.py` (NEW, 9 cases) — 4-datom audit-shape + Merkle-chain integration.
- `tests/store/test_substrate_txn_fork.py` (NEW, 11 cases) — `s.txn.fork` SDK pass-through coverage.
- `tests/store/test_substrate_txn_fold_into.py` (updated) — Path-A → 2.0c-ext semantic supersession honestly called out; new rollback-verification tests.
- `tests/store/test_substrate_txn_fold_into_audit.py` (rewritten) — replaces single-`:fold/chosen` shape with 4-datom `:fork/*` shape; Merkle-chain integration + legacy-op-not-emitted regression guard.
- `tests/store/test_fold_byte_identity.py` (extended) — Property 3 (full 4-datom shape byte-identity at @max_examples=200) + Property 4 (rollback verification at @max_examples=200) added; existing Properties 1-2 retained.
- `docs/plans/2026-05-01-phase-2.0c-ext-fork-primitive-impl.md` (NEW) — scratch impl plan.
- `docs/plans/2026-04-30-phase-2-persistence-coder-design.md` (this file) — § 3.7 fold-row split into `:fold/chosen` + `:fork/*` rows; § 4.3 expanded to cover both primitives; ADR-7 amended; § 6 schedule extended; § 14 added.

**Suite verification:** 1953 → 2000 passed (+47 new) / 33 skipped / 7 xfailed in ~50s. 5 consecutive flake-checks of the 4 Hypothesis byte-identity / rollback properties at @max_examples=200 all green (0.59-0.68s each, no flakes).

**No version bump.** `__version__` stays 0.8.0a1; v0.8.5a1 lands at Phase 2.0d sub-tag per existing convention. CHANGELOG-sdk.md keeps "unreleased — lands at Phase 2.0d sub-tag" framing.

**No new ADRs.** ADR-7 was edited in place to cover both primitives; the namespace-split decision (`:fold/*` vs `:fork/*`) is a refinement of the existing surface-naming ADR, not a new contract.

**No scope shift on G1-G10 acceptance gates.** The MCTS scoring path (G7 REPL branch/fold/commit) still uses the agent-facing surface — now `s.txn.fold_into` (rewired) or directly `s.txn.fork` for explicit speculate-rollback callers. Property tests at `tests/store/test_fold_byte_identity.py` cover both primitives at @max_examples=200.

---

## 15. Forward-pointer roadmap — v0.9.x sandbox-redesign (post-2.0d)

The Phase 2.0d W3 honest-rescope (2026-05-01, see ADR-5 amendment) demoted `:code/exec` v0.8.5a1 to a soft-isolation runtime guard / best-effort containment for trusted-author plan-step bodies, after the R2.3 codex review demonstrated that Python-level capability-denial cannot prevent host-FS reads when stdlib transitive closure is preserved (`dataclasses.sys.modules['builtins'].open(...)` escape). Hard isolation is queued as a separate engineering project tracked outside the v0.8.5a1 substrate-completion window.

**Real OS-level `:code/exec` sandbox boundary (gVisor / nsjail / Docker / OCI runtime / WASM-Pyodide) — supersedes the v0.8.5a1 soft-isolation runtime guard. Tracking #TBD.**

Constraints the v0.9.x track inherits unchanged:

- The audit-datom contract (the 7 keys at § 3.7) carries forward unchanged. The v0.9.x boundary is an isolation-strength upgrade, not an audit-shape change.
- Replay determinism (audit-replay default, opt-in re-execution-replay with hash verification) carries forward unchanged.
- The xfail-strict regression test `test_known_escape_via_dataclasses_sys_modules_builtins_open` in `tests/effect/test_code_exec.py` IS the falsifiable acceptance signal. When it flips to PASS, the v0.9.x boundary is in place; `strict=True` forces removal of the xfail marker at that point.
- The substrate-completion claim for v0.8.5a1 does NOT depend on hard sandbox isolation. The wedge story (rewind / branch / replay over agent trajectories) is load-bearing on audit-chain integrity + replay determinism, both of which are demonstrably correct (M5 fix at `transaction.py:703`, W2 audit-stack tests). v0.9.x sandbox-redesign closes the remaining adversarial-author confidentiality gap; it does not unblock substrate-completion.

Cross-references that must stay in sync if the v0.9.x track lands:

- ADR-5 (the W3 amendment block ends with the forward-pointer; remove the amendment when v0.9.x supersedes it, leave the historical record).
- `src/persistence/effect/handlers/code.py` module docstring "Known limitations" section.
- `src/persistence/effect/CHANGELOG-effect.md` Phase 2.0d W3 forward-pointer block.
- `tests/effect/test_code_exec.py::test_known_escape_via_dataclasses_sys_modules_builtins_open` xfail marker.
