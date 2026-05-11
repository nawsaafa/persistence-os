# Phase 7 — `persistence-orchestrate` Anthropic Skill

**Phase:** 7 (cross-track from `skill-systems-integration_20260430`; first phase touching persistence-os `src/`-adjacent code in that track per ADR-004)
**Branch:** `feat/v0.9-phase7-persistence-orchestrate` off `feat/v0.9-persistence-coder` (parent `f7d234b`, v0.9.0a1-positioning commit)
**Worktree:** `~/Projects/persistence-os-worktrees/phase7/` (to be created)
**Posture:** internal-alpha — merge `--no-ff`, NOT pushed; marketplace publication is a Phase 7.1 W3-rescope (manual publish only at v0.9.0a1 GA per skill-systems-integration plan.md:128)
**Calendar target:** 4–6h actual (per skill-systems-integration plan.md:139), well within v0.9.0a1 GA window (target 2026-06-14, hard cutoff 2026-06-05, ~25 days runway after 2.4c)

---

## ARIS journey (codex high)

| Stage | Status | Notes |
|---|---|---|
| Brainstorming receipts scan | DONE | `CLAUDE.md:65-66` "invocation surface for orchestrator-driven agents"; `CLAUDE.md:22` "every plan is an EDN AST"; `CLAUDE.md:32` "NOT pip-installable yet"; positioning.md 5 capabilities + cited tests; mimir-orchestrator single-SKILL.md pattern; agent-mode-worker-api SKILL.md + manifest.json shape; `python -m persistence.coder` CLI surface; jinja2 NOT in base deps (stdlib only for emitter) |
| LD-1 codex consensus | LOCKED via REJECT-FOR-NEW-OPTION-A-PRIME | Artifact emitter (not runtime orchestrator); avoids Mimir Phase D supervisor-verb overlap |
| LD-2 codex consensus | LOCKED via DISAGREE-STRONG → D-3 Mixed | Pure SKILL.md UX + minimal deterministic Python emitter/validator (regression = test failure, not support ticket) |
| LD-3 codex consensus | LOCKED via DISAGREE-WEAK → EDN canonical | Chain schema is EDN AST (homoiconicity); SKILL.md frontmatter stays YAML (Anthropic convention) |
| LD-4 codex consensus (2 passes) | LOCKED via DISAGREE-STRONG → second pass NEW-OPTION-X (D-honest-refined) | Emitted SKILL.md has explicit prereqs section matching positioning.md:98-108 verbatim; no vendoring; UNSET (not empty) for unsigned dev |
| Design R0 (codex high) | **FAIL 7.6/7.1** | 3B + 2I + 1N; see R0-fold receipt below. Mean 7.6 below 8.0; min 7.1 below 7.5. All findings receipts-anchored. |
| Design R0-fold (controller) | **DONE inline** | All 3 BLOCKING + 2 IMPORTANT + 1 NICE folded. No architectural change required; documentation/citation/test-shape fixes only. |
| Design R0.1 lite (post-fold) | **PASS-WITH-FIXES 7.9/7.2** | 4 CLOSED + 1 REGRESSED (I1 — kill-switch 3-op vs 2-op ordering conflation) + 1 PASS-WITH-FIXES (N1 orphan instance). Both fixes folded inline; final ordering verified against `test_pause_emits_repl_request_then_response_in_order` + positioning.md:78-80. |
| Design R0.2 (re-confirm) | **PASS 8.3/8.1** | I1 + N1 closures verified. Soft-mode bar (8.0/7.5) cleared. **DESIGN FROZEN.** |
| Codex Impl R1 | PENDING | After T9.1, before TaskUpdate completed |

**Methodology note:** 5-for-5 codex consensus flips this phase (LD-1 + LD-2 + LD-3 + LD-4 first pass + LD-4 second pass). Pattern continues from 2.4b (2/2), 2.4b.1 (2/2), 2.4c (3/3). The substrate-phase consensus discipline applies here even though Phase 7 is downstream (it touches `src/persistence/orchestrate/` and ships a marketplace artifact whose contract is binding once published).

**Notable codex catches that would have shipped wrong:**
- LD-1: I missed `CLAUDE.md:65-66` ("invocation surface for orchestrator-driven agents") — would have shipped a runtime orchestrator that pre-empted Mimir Phase D.
- LD-4 first pass: framing "dependency-free emitted SKILL.md" was wordplay (positioning.md:98 itself requires clone + uv sync).
- LD-4 second pass: my prereqs draft said `pip install git+...` (conflicts with `CLAUDE.md:32` "NOT pip-installable") + `PERSISTENCE_AUDIT_KEY=` empty (undocumented behavior, prefer UNSET).
- ARIS R0 B1: I cited `positioning.md:98-108` as "verbatim prereqs source" — but those lines contain the `uv run pytest` verify-locally block, not prereqs. Only line :98 is the install sentence; mintkey/unset is NEW extension text. Drift would have failed G4.
- ARIS R0 B2: LD-3 contradicted FD/W3-5 on YAML sugar — would have created scope ambiguity going into impl.
- ARIS R0 B3: G3 used subprocess + parsed stdout JSON without a stable CLI contract — would have shipped a flaky gate coupled to provider env.
- ARIS R0 I1: Risk register overclaimed "thread coordination" not actually in the cited template — would have under-budgeted T5.

---

## R0-fold receipt (controller, 2026-05-11)

| Finding | Severity | Resolution |
|---|---|---|
| **B1** LD-4 cites `positioning.md:98-108` as "verbatim prereqs source" but those lines are the verify-locally `uv run pytest` block | BLOCKING | **FOLDED** — LD-4 rationale rewrites the verbatim-source attribution: clone+`uv sync` clause is verbatim from `positioning.md:98`; mintkey + UNSET clause is NEW extension text grounded in `CLAUDE.md:32` + `positioning.md:21-23`. G4 enforces marker-delimited region equality + cross-check that the install sentence equals positioning.md:98 verbatim (positioning-doc drift fails G4). |
| **B2** LD-3 says "YAML allowed as optional sugar"; FD-EDN-VS-YAML-SUGAR + W3-5 say YAML deferred | BLOCKING | **FOLDED** — struck YAML-sugar from LD-3. v0 is EDN-only authoring. YAML stays only in SKILL.md frontmatter (Anthropic convention; non-load-bearing). G2 adds negative-control: YAML-shaped input must raise `ChainSchemaError("v0 is EDN-only; YAML authoring is W3-5")`. |
| **B3** G3 uses subprocess + parses stdout JSON (no stable CLI contract) + replay step elided as `...` | BLOCKING | **FOLDED** — G3 redesigned to in-process pattern matching `tests/coder/test_steering_replay.py` + `test_loop_replay.py::test_coder_loop_audit_replay_byte_identity`. Uses `Substrate.open("memory", audit_signer=...)` + Python API + `s._audit_entries` inspection + in-process `canonical_hash` replay. No subprocess, no stdout coupling, no provider env. New `Coder.run_chain_from_edn(...)` + `Coder.replay_audit_entries(...)` thin wrappers noted as FD-CODER-RUN-CHAIN-API (T0 receipts verify, T1 ships). |
| **I1** Risk register overclaims "threaded pause after step 1" — 2.3d test only does pause+resume in-sequence | IMPORTANT | **FOLDED (R0 + R0.1 correction)** — initial fold reworded to "audit-chain ordering with intact Merkle linkage" but cited the 3-op `[:repl/request, :coder/branch, :repl/response]` pattern. R0.1 lite caught this regression: the 3-op pattern is for `branch()` invocations (test_branch_emits_...), while pure pause/resume emits only `[:repl/request, :repl/response]` (test_pause_emits_..., matching positioning.md:78-80). Final fold corrects all 3 sites (risk register + G5 prose + G5 test code) to use the 2-op pause/resume ordering. |
| **I2** G4 "marker-delimited string equality" claim doesn't match test code (substring `in md` only) | IMPORTANT | **FOLDED** — G4 redesigned: emitter wraps prereqs region in `<!-- prereqs-begin -->` / `<!-- prereqs-end -->`; test asserts markers appear exactly once each, extracts region via regex, asserts exact string equality against `EXPECTED_PREREQS_REGION` constant, cross-checks the clone+uv-sync clause against `positioning.md` content (loaded by test, not hardcoded). Negative-control assertions retained (`pip install git+`, empty-string env). |
| **N1** "stdlib-only" sloppy given `edn_format` runtime dep | NICE-TO-HAVE | **FOLDED (R0 + R0.1 second-pass cleanup)** — initial fold corrected the Tech Stack bullet but left a second misleading instance in the Architecture section ("stdlib-only Python codegen"). R0.1 lite caught the orphan. Final fold rephrases the architecture sentence: "Python codegen (stdlib for emit string-building + the existing `edn_format` parser dep for chain reading; no new dependencies)." |

**Procedural note:** codex invoked the web tool 4 times during this R0 review despite the prompt's NO WEB SEARCH instruction; codex explicitly stated it didn't use web results. All substantive findings are receipts-anchored with file:line citations and stand on their own merit. Logged in case the pattern recurs (3rd phase-design where codex hit web tool against instruction: 2.4c R0, 2.4c R0-fold, Phase 7 R0).

---

## Goal

Ship `persistence-orchestrate` as an Anthropic-Skill-shaped marketplace artifact that, when invoked, **emits** a downstream installable orchestrator skill (SKILL.md + chain.edn + preflight.toml) on top of the curated SDK facade. The emitted orchestrator is itself an Anthropic Skill the user installs and runs; running it produces a signed replayable audit trace using the 5 capabilities shipped at v0.9.0a1 (Ed25519 signing + Capability lattice + canonical_audit_stack replay + REPL pause/resume + `:sys/now` substrate time).

**Phase 7 is the substrate's invocation surface for orchestrator-driven agents** (`CLAUDE.md:65-66`). It is NOT the orchestrator itself, NOT a tutorial wrapper, and NOT a multi-agent supervisor (that's Mimir Phase D, Jun 15 – Jul 19).

---

## Architecture

**Two-layer architecture:**

```
┌─────────────────────────────────────────────────────────────────┐
│ Layer 1 (this phase): persistence-orchestrate meta-skill        │
│ ─────────────────────────────────────────────────────────────── │
│  skills/persistence-orchestrate/SKILL.md  (marketplace artifact)│
│       └─ instructs Claude Code to invoke:                       │
│          uv run python -m persistence.orchestrate emit          │
│              --chain <user-chain.edn>                           │
│              --out <emitted-skill-dir>                          │
│                                                                 │
│  src/persistence/orchestrate/  (the emitter; pure stdlib Python)│
│       ├─ __main__.py     — CLI entrypoint                       │
│       ├─ _schema.py      — EDN chain parser + validator         │
│       ├─ _emit.py        — string-building emit functions       │
│       └─ examples/                                              │
│           ├─ capability-denial-chain.edn  (demo chain 1)        │
│           └─ pause-resume-sysnow-chain.edn (demo chain 2)       │
└─────────────────────────────────────────────────────────────────┘
                            │
                            │  emits
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│ Layer 2 (emitted by user invocations): downstream orchestrator  │
│ ─────────────────────────────────────────────────────────────── │
│  <emitted-out-dir>/                                             │
│      ├─ SKILL.md         (markdown; tells Claude Code how to    │
│      │                    run the chain via shell-out to        │
│      │                    `uv run python -m persistence.coder`) │
│      ├─ chain.edn        (the user's chain, canonicalized)      │
│      └─ preflight.toml   (capabilities + ops required,          │
│                           derived from tests/preflight_manifest │
│                           shipped in 2.4c)                      │
└─────────────────────────────────────────────────────────────────┘
```

The emitter is **not** runtime orchestration code — it is a Python codegen (stdlib for emit string-building + the existing `edn_format` parser dep for chain reading; no new dependencies) that converts a chain.edn into a downstream Anthropic Skill directory. The substrate (`persistence.coder` CLI shipped 2.4a) does all runtime work when the emitted skill is invoked.

**Tech Stack:**
- Python 3.10+; **no new dependencies** — stdlib for emit string-building + existing `edn_format` dep for chain parsing (already in base deps per `src/persistence/plan/_coerce.py:29`) (R0-fold N1)
- EDN as canonical chain schema (homoiconicity)
- Anthropic Skills marketplace = primary distribution channel
- Curated SDK facade (`persistence.sdk.Substrate`) called via `persistence.coder` Python API at downstream-skill invocation time (G3 redesign post-R0-fold uses in-process invocation, not subprocess)

---

## Locked Decisions (LDs)

### LD-1 — v0 shape is **artifact emitter** (codex consensus REJECT-FOR-NEW-OPTION-A-PRIME)

**Decision:** Phase 7 v0 = artifact emitter. Given a chain description (EDN canonical), the skill generates a thin installable downstream orchestrator skill directly on top of the curated SDK facade. No new runtime orchestration semantics; no multi-agent supervision.

**Rationale (anchored to receipts):**
- `CLAUDE.md:65-66`: "It is NOT a tutorial wrapper — the skill IS the substrate's invocation surface for orchestrator-driven agents." Emitter respects this directionality (orchestrators drive; persistence-os provides substrate). Runtime alternative would make persistence-os the orchestrator.
- `mimir-os/.serena/memories/ship_plan_14_weeks.md:10`: Mimir Phase D supervisor verbs (spawn/train/watch/halt/branch/replay/verify/export) ship Jun 15. A runtime orchestrator in Phase 7 would duplicate ~70% of this surface and force later wrap-or-break.
- `CLAUDE.md:30`: "NOT an agent framework replacing LangChain / CrewAI." A runtime chain runner with a chain-description parser IS framework gravity. Emitter stays substrate-shaped.

**Rejected competitors:**
- **B (runtime orchestrator)** — pre-empts Mimir Phase D verb surface; injects orchestration semantics into persistence-os.
- **C (substrate wrapper)** — chicken-and-egg (needs existing orchestrator to wrap); weak marketplace demo.

**Falsifiable acceptance signal (G3):**
`tests/sdk/test_persistence_orchestrate_emit_smoke.py::test_emits_orchestrator_that_runs_signed_replayable_trace` — emit a 2-step chain where step 2 requires a missing capability; assert (i) emitted directory contains SKILL.md + chain.edn + preflight.toml; (ii) running the emitted orchestrator via `uv run python -m persistence.coder` produces a trace where step 2 side-effect never occurs; (iii) trace contains signed datoms including the denial; (iv) replaying the trace yields byte-identical canonical form.

---

### LD-2 — Codegen mechanism is **D-3 Mixed** (codex consensus DISAGREE-STRONG)

**Decision:** Pure SKILL.md UX (user experience) + minimal deterministic Python emitter (correctness gate). The marketplace SKILL.md instructs Claude Code to invoke `python -m persistence.orchestrate emit ...`, which runs the emitter. The emitter is **pure stdlib Python** — no Jinja2, no LLM-driven generation. Emit functions build markdown / EDN / TOML strings by concatenation; templates are FIXED (no conditional logic, no loops beyond step iteration).

**Rationale (anchored to receipts):**
- `positioning.md:5,10`: v0.9.0a1 ships "structural guarantee, not runtime hope; tied to falsifiable test." A pure-instruction emitter (Claude Code reads SKILL.md and decides what to write) pushes correctness into implicit LLM behavior — not pinnable, not testable.
- A deterministic Python emitter means a regression is a TEST FAILURE, not a marketplace support ticket. Falsifiable via byte-equal output assertions.
- Stdlib-only (no Jinja2 dep) avoids the FD-version-bump-side-effects audit overhead (precedent: pydantic in 2.4c F2). Templates are simple enough to not need a template engine.

**Rejected competitors:**
- **D-1 Pure SKILL.md emission** — non-deterministic; hand-waves "Claude Code will figure it out."
- **D-2 Python-backed with Jinja2** — adds a top-level dep; template engine overkill for fixed templates.

**Falsifiable acceptance signal (G1):**
`tests/orchestrate/test_emitter_determinism.py::test_emits_identical_tree_from_same_chain` — emit the same chain twice into separate `tmp_path` dirs, assert the byte-content of each emitted file is identical across runs.

---

### LD-3 — Chain description schema is **EDN canonical** (codex consensus DISAGREE-WEAK; R0-fold B2 strikes YAML sugar from v0)

**Decision:** Chain schemas are authored in EDN (canonical persistence-os form). v0 is **EDN-only authoring** — YAML input was struck in R0-fold B2 for budget + scope clarity. SKILL.md frontmatter stays YAML (Anthropic convention; non-load-bearing). YAML chain authoring is W3-5 (future). Example:

```edn
(:chain
  :name "my-chain"
  :description "..."
  :steps [
    (:step :id 1
           :op :fs/read
           :args {:path "input.md"}
           :capability (:Capability :op "coder" :qualifier "read"))
    (:step :id 2
           :op :fs/write
           :args {:path "output.md"}
           :capability (:Capability :op "coder" :qualifier "write"))])
```

**Rationale (anchored to receipts):**
- `CLAUDE.md:22`: "Every plan is an EDN AST. Every skill is a content-addressed AST subtree." YAML-as-canonical would contradict homoiconic positioning in the marketplace artifact.
- `mimir-orchestrator/SKILL.md:144` uses YAML for scope DECLARATIONS (config), but never as PLAN form. Phase 7 must not blur this distinction.
- `edn_format` already in base deps (`pyproject.toml`). Zero new deps for parsing.

**Rejected competitors:**
- **YAML canonical** — undermines homoiconicity story.
- **YAML as v0 sugar** — struck in R0-fold B2 (was contradicting FD/W3); deferred to W3-5.
- **Plain language → LLM parsing** — forces LLM-driven emit (conflicts with LD-2 D-3 Mixed determinism).

**Falsifiable acceptance signal (G2):**
`tests/orchestrate/test_chain_schema.py::test_chain_parses_to_edn_ast_and_roundtrips` — parse a known chain.edn, assert the AST shape matches expected `frozenset`/`Symbol` form, then serialize back to EDN and assert byte-identical to input. **Negative-control test in same module:** YAML-shaped input must raise `ChainSchemaError("v0 is EDN-only; YAML authoring is W3-5")` — so any accidental YAML-acceptance regresses to FAIL.

---

### LD-4 — Install path is **D-honest-refined** (codex consensus 2 passes; DISAGREE-STRONG then NEW-OPTION-X; R0-fold B1 corrects verbatim-source claim)

**Decision:** Both the meta-skill (`skills/persistence-orchestrate/SKILL.md`) and the emitted downstream orchestrator skills carry **explicit prerequisites sections**. R0-fold B1 clarification: the prereqs region is composed of two parts with different verbatim sources:

- **Clone + uv sync clause:** verbatim from `positioning.md:98` ("Clone `persistence-os`, run `uv sync`")
- **Mintkey + UNSET clause:** NEW extension text in this design, grounded in `CLAUDE.md:32` ("NOT pip-installable yet") + `positioning.md:21-23` (signer fails closed on unknown URI / missing key)

Concretely, the emitted SKILL.md contains:

```markdown
## Prerequisites

This skill requires persistence-os installed locally:

  git clone https://github.com/nawsaafa/persistence-os.git
  cd persistence-os
  uv sync

For signed audit chains (production posture), generate an Ed25519 key:

  python -m persistence.tools.mintkey --out ~/.persistence/keys/agent.pem
  export PERSISTENCE_AUDIT_KEY=file:///$HOME/.persistence/keys/agent.pem

For dev mode (unsigned), UNSET the env var:

  unset PERSISTENCE_AUDIT_KEY

The skill invokes `uv run python -m persistence.coder` directly.
```

No vendoring. No alternate distribution channels. Marketplace artifact carries no Python; emitted skill carries no Python. Both depend on the user having persistence-os installed via clone + uv sync (matching the alpha install contract).

**Rationale (anchored to receipts; R0-fold B1 corrects citations):**
- `CLAUDE.md:32`: "NOT pip-installable / open-source-ready yet. No PyPI release." Vendoring would create a third distribution channel (marketplace) for a substrate that's explicitly "local-only" at this phase.
- `positioning.md:98` (shipped TODAY): the alpha install contract sentence is "Clone `persistence-os`, run `uv sync`". This is the verbatim source for the **clone + uv sync clause** of the prereqs region. (R0-fold B1: earlier draft cited `positioning.md:98-108` which is the broader "Verify locally" block including `uv run pytest`; the prereqs region inherits only the install sentence at :98, not the pytest invocation block.)
- `positioning.md:21-23`: signer fails closed on unknown URI / missing key. This is the verbatim source for the **mintkey + UNSET clause** — the mintkey instruction is NEW extension text (no canonical source) describing how to satisfy the closed-fail contract.
- `mimir-os/.serena/memories/ship_plan_14_weeks.md:11`: PyPI release is Phase E (Jul 20 – Aug 2). Phase 7 GA is at v0.9.0a1 (early June). ~50 days between Phase 7 and Phase E — the audience is alpha evaluators, not casual marketplace browsers. They accept clone + uv sync.
- Codex's two specific corrections folded inline: (1) use clone + uv sync, NOT `pip install git+...`; (2) UNSET env var for unsigned dev, NOT empty string (undocumented behavior).

**Rejected competitors:**
- **D-vendor (vendor slim runtime)** — substrate IS the dependency (coder uses sdk uses fact/effect/plan/txn/replay/spec/repl); "slim" vendoring is impractical. Drift risk codex itself flagged. Posture-violation of `CLAUDE.md:32`.
- **Assume PyPI** — Phase 7 GA pre-dates Phase E PyPI release by ~50 days.

**Falsifiable acceptance signal (G4):**
`tests/orchestrate/test_skill_md_prereqs_section.py::test_emitted_skill_md_contains_verbatim_install_block` — emit any chain; the emitter wraps the prereqs region in HTML comment markers `<!-- prereqs-begin -->` / `<!-- prereqs-end -->`. The test (i) asserts each marker appears **exactly once** in the emitted SKILL.md, (ii) extracts the substring between them, and (iii) asserts **exact string equality** against a canonical constant `EXPECTED_PREREQS_REGION` defined in the test file. The clone+uv sync clause within `EXPECTED_PREREQS_REGION` is asserted to **also equal** the literal sentence "Clone `persistence-os`, run `uv sync`" found at `docs/release-notes/v0.9.0a1-positioning.md:98` (loaded by the test, not hardcoded — so positioning-doc drift fails G4). (R0-fold I2: prior G4 sketch used substring `in md` checks; corrected to marker-extraction + exact-equality.)

---

## File Structure

### New files (this phase touches `src/`)

| File | Responsibility |
|---|---|
| `skills/persistence-orchestrate/SKILL.md` | Marketplace artifact — frontmatter + instructions for Claude Code to invoke the emitter CLI. ~80–120 lines markdown. |
| `skills/persistence-orchestrate/manifest.json` | Anthropic Skills metadata (matching `agent-mode-worker-api/manifest.json` shape). ~10–15 lines JSON. |
| `src/persistence/orchestrate/__init__.py` | Module entrypoint; exposes `emit_orchestrator_skill(chain: Chain, out_dir: Path)` as the single public function. |
| `src/persistence/orchestrate/__main__.py` | CLI: `python -m persistence.orchestrate emit --chain <path> --out <dir>`. Argparse-shaped, mirrors `persistence.coder.__main__` conventions. |
| `src/persistence/orchestrate/_schema.py` | EDN chain parser + validator. Uses `edn_format` (already in base deps). Defines `Chain`, `Step`, `Capability` typed-tuple shapes. |
| `src/persistence/orchestrate/_emit.py` | String-building emit functions: `emit_skill_md(chain) -> str`, `emit_chain_edn(chain) -> str`, `emit_preflight_toml(chain) -> str`. Pure stdlib, deterministic. |
| `src/persistence/orchestrate/examples/capability-denial-chain.edn` | Canned demo chain 1: 2 steps; step 2 capability missing; halts. Exercises Ed25519 signing + Capability lattice + audit replay. |
| `src/persistence/orchestrate/examples/pause-resume-sysnow-chain.edn` | Canned demo chain 2: multi-step with pause checkpoint + `:sys/now` read. Exercises kill switch + substrate time + signing. |
| `src/persistence/CHANGELOG-orchestrate.md` | Per-module changelog matching other persistence modules. Initial entry: v0.9.0a1 Phase 7 ship. |
| `tests/orchestrate/__init__.py` | Empty marker. |
| `tests/orchestrate/test_chain_schema.py` | G2 — LD-3 falsifier. EDN roundtrip + schema validation. |
| `tests/orchestrate/test_emitter_determinism.py` | G1 — LD-2 falsifier. Byte-identical emit across runs. |
| `tests/orchestrate/test_skill_md_prereqs_section.py` | G4 — LD-4 falsifier. Verbatim install block in emitted SKILL.md. |
| `tests/sdk/test_persistence_orchestrate_emit_smoke.py` | G3 — LD-1 falsifier. End-to-end: emit → run emitted skill → verify signed replayable trace. |
| `tests/orchestrate/test_demo_chains_showcase_five_capabilities.py` | G5 — 5-capability showcase. Run both canned demo chains, assert all 5 capabilities (signing + Capability + replay + kill switch + sys/now) are exercised across the union. |

### Modified files

| File | Why |
|---|---|
| `CHANGELOG.md` | Append Phase 7 ship entry under `## v0.9.0a1 (unreleased)`. |
| `src/persistence/sdk/CHANGELOG-sdk.md` | Note: `persistence.orchestrate` is a NEW top-level module, not under sdk; brief cross-reference only. |
| `tests/preflight_manifest.toml` | NO change. The 2.4c manifest is read by the emitter (not modified). |

**No** changes to `pyproject.toml` (no new deps), `uv.lock` (no version bump), or any existing `src/persistence/{fact,effect,plan,txn,replay,spec,repl,sdk,coder}/` modules.

---

## Test Gates (G1–G5)

### G1 — Emitter determinism (LD-2 falsifier)

**File:** `tests/orchestrate/test_emitter_determinism.py::test_emits_identical_tree_from_same_chain`

```python
import filecmp
from pathlib import Path
from persistence.orchestrate import emit_orchestrator_skill, parse_chain_edn

def test_emits_identical_tree_from_same_chain(tmp_path: Path) -> None:
    chain_src = (Path(__file__).parent.parent.parent
                 / "src/persistence/orchestrate/examples"
                 / "capability-denial-chain.edn").read_text()
    chain = parse_chain_edn(chain_src)

    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    emit_orchestrator_skill(chain, out_a)
    emit_orchestrator_skill(chain, out_b)

    # Byte-identical across runs (no nondeterminism — no wall-clock,
    # no random, no dict-order leak).
    for relpath in ["SKILL.md", "chain.edn", "preflight.toml"]:
        assert filecmp.cmp(out_a / relpath, out_b / relpath, shallow=False), \
            f"emitter non-deterministic on {relpath}"
```

**Falsifies if:** emitter introduces wall-clock, random seeds, or `os.urandom` calls; dict iteration order leaks into output; emit functions read mutable global state.

### G2 — EDN chain schema roundtrip (LD-3 falsifier)

**File:** `tests/orchestrate/test_chain_schema.py::test_chain_parses_to_edn_ast_and_roundtrips`

```python
from persistence.orchestrate._schema import parse_chain_edn, serialize_chain_edn

def test_chain_parses_to_edn_ast_and_roundtrips() -> None:
    src = """(:chain
                :name "demo"
                :description "G2 demo"
                :steps [(:step :id 1
                              :op :fs/read
                              :args {:path "input.md"}
                              :capability (:Capability :op "coder" :qualifier "read"))])"""
    chain = parse_chain_edn(src)
    assert chain.name == "demo"
    assert len(chain.steps) == 1
    assert chain.steps[0].op == ":fs/read"
    assert chain.steps[0].capability.qualifier == "read"

    # Roundtrip preserves canonical form.
    reserialized = serialize_chain_edn(chain)
    assert parse_chain_edn(reserialized) == chain
```

**Falsifies if:** EDN parser drops fields, normalizes `frozenset`/`Symbol` lossy, or roundtrip introduces drift.

### G3 — Emit smoke (LD-1 falsifier) — REDESIGNED in-process (R0-fold B3)

**File:** `tests/sdk/test_persistence_orchestrate_emit_smoke.py::test_emits_orchestrator_that_runs_signed_replayable_trace`

R0-fold B3 redesign rationale: prior sketch used subprocess + parsed stdout JSON, but `persistence.coder` has no documented stable stdout JSON contract — subprocess coupling adds flake risk + couples G3 to provider env (`--provider echo`). In-process pattern matches `tests/coder/test_steering_replay.py` and `tests/coder/test_loop_replay.py::test_coder_loop_audit_replay_byte_identity` (the positioning doc § 3 falsifier).

```python
from pathlib import Path
from persistence.orchestrate import emit_orchestrator_skill, parse_chain_edn
from persistence.sdk import Substrate
from persistence.effect._signing import generate_keypair
from persistence.effect.canonical import canonical_hash
from persistence.coder import Coder
from persistence.effect.handlers import make_callable_llm_handler


def test_emits_orchestrator_that_runs_signed_replayable_trace(tmp_path) -> None:
    # Step 1: emit (in-process, no subprocess)
    chain_src = (Path(__file__).parent.parent.parent
                 / "src/persistence/orchestrate/examples"
                 / "capability-denial-chain.edn").read_text()
    chain = parse_chain_edn(chain_src)
    out_dir = tmp_path / "emitted-skill"
    emit_orchestrator_skill(chain, out_dir)

    # (i) Emitted directory shape
    assert (out_dir / "SKILL.md").is_file()
    assert (out_dir / "chain.edn").is_file()
    assert (out_dir / "preflight.toml").is_file()

    # Step 2: run the emitted chain in-process under canonical audit stack
    priv, _pub = generate_keypair()
    signer = ("test-key-001", priv)

    with Substrate.open("memory", audit_signer=signer) as s:
        # The emitted chain executes step-by-step under Coder; step 2's
        # capability is not granted, so Capability lattice (2.3d) denies
        # it before side effect. Granted capabilities derived from
        # chain.edn step 1's :capability field only.
        coder = Coder(substrate=s, granted_capabilities=_first_step_caps(chain))
        coder.run_chain_from_edn(out_dir / "chain.edn",
                                  llm_handler=make_callable_llm_handler(_done_call_fn()))

        audit_entries = list(s._audit_entries)

    # (ii) Trace contains signed denial datom for step 2
    denials = [e for e in audit_entries if e.op == ":capability/denied"]
    assert len(denials) == 1, f"expected 1 denial, got {len(denials)}"
    assert denials[0].args.get("step_id") == 2
    assert denials[0].signature is not None  # signing is mandatory under signer

    # (iii) Step 2 side effect never occurred
    writes = [e for e in audit_entries if e.op == ":fs/write"]
    assert len(writes) == 0, "step 2 :fs/write must NOT execute when capability denied"

    # (iv) Replay byte-identity — canonical replay produces equal canonical hash
    original_hash = canonical_hash(audit_entries)
    with Substrate.open("memory", audit_signer=signer) as s_replay:
        replayer = Coder(substrate=s_replay,
                          granted_capabilities=_first_step_caps(chain))
        replayer.replay_audit_entries(audit_entries)
        replayed = list(s_replay._audit_entries)
    assert canonical_hash(replayed) == original_hash, "replay byte-identity broken"
```

**Falsifies if:** any of the LD-1 invariants break — emitted skill misses files, side effect occurs despite denial, denial entry unsigned, replay produces a different canonical hash. **No subprocess coupling, no stdout JSON contract, no provider env dependency.**

**Note (FD-CODER-RUN-CHAIN-API):** `Coder.run_chain_from_edn(...)` and `Coder.replay_audit_entries(...)` are new methods on `persistence.coder.Coder` that this design assumes. Verify in T0 receipts; if absent, T1 adds them as thin wrappers around `Coder.run()` + the existing canonical-replay path. The methods are pure Python (no new deps).

### G4 — SKILL.md prereqs verbatim (LD-4 falsifier) — REDESIGNED marker-extraction (R0-fold I2)

**File:** `tests/orchestrate/test_skill_md_prereqs_section.py::test_emitted_skill_md_contains_verbatim_install_block`

R0-fold I2 redesign rationale: prior sketch used substring `in md` checks, which couldn't distinguish (a) the prereqs region containing the install clause from (b) the install clause appearing anywhere else in SKILL.md (negative-control vulnerability). New design uses HTML comment markers + region extraction + exact equality.

```python
import re
from pathlib import Path
from persistence.orchestrate import emit_orchestrator_skill, parse_chain_edn

# Canonical extension text (NEW in this design; not from positioning.md)
EXPECTED_PREREQS_REGION = """## Prerequisites

This skill requires persistence-os installed locally:

Clone `persistence-os`, run `uv sync`

For signed audit chains (production posture), generate an Ed25519 key:

  python -m persistence.tools.mintkey --out ~/.persistence/keys/agent.pem
  export PERSISTENCE_AUDIT_KEY=file:///$HOME/.persistence/keys/agent.pem

For dev mode (unsigned), UNSET the env var:

  unset PERSISTENCE_AUDIT_KEY

The skill invokes `python -m persistence.coder` directly."""


def test_emitted_skill_md_contains_verbatim_install_block(tmp_path) -> None:
    src = (Path(__file__).parent.parent.parent
           / "src/persistence/orchestrate/examples"
           / "capability-denial-chain.edn").read_text()
    chain = parse_chain_edn(src)
    emit_orchestrator_skill(chain, tmp_path)

    md = (tmp_path / "SKILL.md").read_text()

    # Markers appear exactly once each (anti-duplication invariant)
    assert md.count("<!-- prereqs-begin -->") == 1
    assert md.count("<!-- prereqs-end -->") == 1

    # Extract region and assert exact equality
    match = re.search(
        r"<!-- prereqs-begin -->\n(.*?)\n<!-- prereqs-end -->",
        md, re.DOTALL,
    )
    assert match is not None
    extracted_region = match.group(1)
    assert extracted_region == EXPECTED_PREREQS_REGION, (
        f"prereqs region drift detected:\n--- expected ---\n{EXPECTED_PREREQS_REGION}\n"
        f"--- got ---\n{extracted_region}"
    )

    # Cross-check: the clone+uv-sync clause within EXPECTED_PREREQS_REGION
    # equals the verbatim positioning.md install sentence (positioning-doc
    # drift fails G4 even if the template doesn't change)
    positioning_md = (Path(__file__).parent.parent.parent
                      / "docs/release-notes/v0.9.0a1-positioning.md").read_text()
    assert "Clone `persistence-os`, run `uv sync`" in positioning_md
    assert "Clone `persistence-os`, run `uv sync`" in EXPECTED_PREREQS_REGION

    # Negative controls (LD-4 codex-fold pass 2)
    assert "pip install git+" not in md, "regression: pre-Phase-E posture violated"
    assert "PERSISTENCE_AUDIT_KEY=\n" not in md, "regression: empty-string env undocumented"
    assert "PERSISTENCE_AUDIT_KEY=$" not in md  # nor "= " with trailing space
```

**Falsifies if:** emitted SKILL.md drifts from canonical region (template change or positioning-doc drift), markers missing/duplicated, regresses to `pip install git+...`, or uses empty-string env-unset shorthand.

### G5 — 5-capability showcase (positioning doc fidelity)

**File:** `tests/orchestrate/test_demo_chains_showcase_five_capabilities.py::test_two_canned_chains_exercise_all_five_capabilities`

R0-fold I1 + R0.1 fold clarification: G5 inherits the 2.3d narrower contract — for the kill switch capability, it asserts the AUDIT-CHAIN ORDERING `[:repl/request, :repl/response]` with intact Merkle linkage (matching `positioning.md:78-80` and `test_pause_emits_repl_request_then_response_in_order`), NOT live thread coordination, and NOT the 3-op branch pattern (which only fires when `branch()` is invoked). The pause-resume-sysnow-chain test harness invokes `session.pause()` + `session.resume()` in sequence before running the chain's body — that's sufficient to emit the audit pair the positioning contract names.

The two example chains together must produce audit traces that exercise all 5 capabilities named in `docs/release-notes/v0.9.0a1-positioning.md` (lines 13, 31, 48, 67, 82).

```python
from persistence.effect.canonical import canonical_hash
from persistence.coder._steering import _CoderSteeringSession

def test_two_canned_chains_exercise_all_five_capabilities(tmp_path) -> None:
    # Run chain (a) capability-denial-chain (in-process, same pattern as G3)
    trace_a = _run_chain_in_process(
        "capability-denial-chain.edn", tmp_path,
        steering_actions=[],  # no pause/resume; just denial path
    )

    # Run chain (b) pause-resume-sysnow-chain — harness calls pause+resume
    # before run; chain body reads :sys/now mid-loop
    trace_b = _run_chain_in_process(
        "pause-resume-sysnow-chain.edn", tmp_path,
        steering_actions=["pause_then_resume_at_start"],
    )

    traces = [trace_a, trace_b]
    capabilities_exercised = set()
    for trace in traces:
        for entry in trace:
            if entry.signature is not None:
                capabilities_exercised.add("agent-vs-human-identity")  # cap 1
            if entry.op == ":capability/denied":
                capabilities_exercised.add("governed-action")  # cap 2
            if entry.op in (":repl/request", ":repl/response"):
                capabilities_exercised.add("kill-switch")  # cap 4
            if entry.op == ":sys/now":
                capabilities_exercised.add("substrate-time")  # cap 5

    # cap 4 stronger assertion: pause/resume audit-ordering matches
    # positioning.md:78-80 + 2.3d test_pause_emits_repl_request_then_response_in_order
    # Pure pause+resume emits only [:repl/request, :repl/response]; the
    # 3-op [:repl/request, :coder/branch, :repl/response] pattern is for
    # branch() invocations (test_branch_emits_...), not for kill-switch.
    repl_ops = [e.op for e in trace_b if e.op.startswith(":repl/")]
    assert repl_ops == [":repl/request", ":repl/response"], \
        f"kill-switch pause/resume audit-ordering contract violated: {repl_ops}"

    # cap 3: in-process canonical replay yields byte-equal hash
    for trace_idx, trace in enumerate(traces):
        replayed_hash = _replay_and_hash(trace)
        original_hash = canonical_hash(trace)
        assert replayed_hash == original_hash, f"chain {trace_idx} replay drift"
    capabilities_exercised.add("audit-replay")  # cap 3

    assert capabilities_exercised == {
        "agent-vs-human-identity",
        "governed-action",
        "audit-replay",
        "kill-switch",
        "substrate-time",
    }
```

**Falsifies if:** the canned chains underutilize the positioning doc's claims (kill switch ordering violated, sys/now idle, signing missing, replay drifts).

---

## Future Decisions (FDs)

| FD | Decision deferred to | Rationale |
|---|---|---|
| **FD-EMIT-PATH-DEFAULT** | T2 impl | Where the emitter writes by default if `--out` omitted — `./<chain-name>/` (CWD-relative) vs `~/.claude/skills/<chain-name>/` (user-skill-dir-relative). Impl decision; user will configure via `--out` typically. |
| **FD-CHAIN-DESC-EXTRACTION** | T2 impl | How the `description:` YAML frontmatter field in emitted SKILL.md is derived — from chain.edn's `:description` field directly, or paraphrased. Impl decision; default to verbatim. |
| **FD-PREFLIGHT-TOML-SCHEMA** | T2 impl | Exact schema of emitted `preflight.toml` — should mirror `tests/preflight_manifest.toml` shape (capability-allowlist), OR a richer "expected ops + capabilities" manifest. Impl decision; default to mirror. |
| **FD-EDN-VS-YAML-SUGAR** | Post-T2 (W3-rescope if needed) | When YAML sugar is added: input format detection (file extension `.yaml` vs `.edn`), error-message strategy when both forms present. v0 ships EDN only; YAML sugar deferred. |
| **FD-MINTKEY-CLI** | W3-rescope (separate ship) | The prereqs section references `python -m persistence.tools.mintkey`. This CLI may not exist yet. Verify in T1 receipts; if missing, ship as part of T1 (small) OR W3-rescope to v0.9.x. |

**FD-MINTKEY-CLI is the highest-priority FD** — it's referenced in the locked LD-4 prereqs text. If the CLI doesn't exist, T1 needs to either ship it (small substrate addition) or the prereqs text needs to swap to a documented alternative (e.g., `openssl genpkey`). To be resolved in T0 setup.

---

## W3 Rescopes (queued for future phases)

| W3 # | What | Trigger / acceptance signal |
|---|---|---|
| **W3-1** | Marketplace publishing tooling — automate `gh skill publish` (or whatever Anthropic Skills marketplace publisher is at v0.9.0a1) | Phase 7 ships manually (per skill-systems plan.md:128 "manually publish in Phase 7"). Phase 7.1 automates. |
| **W3-2** | PyPI-aware install path — update emitted SKILL.md prereqs to prefer `pip install persistence-os` once PyPI ships | Phase E (Jul 20–Aug 2) ships PyPI. Then bump Phase 7's prereqs verbatim text. |
| **W3-3** | NL → EDN chain description parsing — accept plain-language chain descriptions and use persistence-coder LLM to compile to EDN | v0.10.x. Currently EDN-only authoring. |
| **W3-4** | Richer emitter validators — op-existence preflight, capability-existence preflight against actual `Capability(op, qualifier)` lattice | v0.9.x. Currently emits SKILL.md without verifying ops are real. |
| **W3-5** | YAML sugar input — alternative chain author form that compiles to canonical EDN | Post-v0 if user demand emerges. |
| **W3-6** | Multi-chain composition — emit an orchestrator that runs multiple chains in sequence with named handoffs | Phase 7.2 or v0.10.x. Currently single-chain per emit. |
| **W3-7** | Mimir Phase D handoff — once Mimir supervisor ships, define exactly how persistence-orchestrate emitted skills are spawned/supervised by Mimir | Phase D (Jun 15–Jul 19). Boundary needs explicit doc when Mimir surfaces ship. |

---

## Implementation order (T0 → T9.1)

| Task | Estimate | Deliverable |
|---|---|---|
| **T0** | ~20 min | Worktree at `~/Projects/persistence-os-worktrees/phase7/`, branch `feat/v0.9-phase7-persistence-orchestrate` off `feat/v0.9-persistence-coder@f7d234b`. Verify FD-MINTKEY-CLI: does `python -m persistence.tools.mintkey` exist? If no, decide T1 inline vs W3-rescope. |
| **T1** | ~1h | `src/persistence/orchestrate/_schema.py` — EDN chain parser + dataclass shapes (`Chain`, `Step`, `Capability`). G2 test (LD-3 falsifier). |
| **T2** | ~1h | `src/persistence/orchestrate/_emit.py` — string-building emit functions. `src/persistence/orchestrate/__init__.py` + `__main__.py` entry points. G1 test (LD-2 falsifier). |
| **T3** | ~45 min | `src/persistence/orchestrate/examples/capability-denial-chain.edn` + `pause-resume-sysnow-chain.edn` — both canned demo chains authored by hand. |
| **T4** | ~1h | `skills/persistence-orchestrate/SKILL.md` + `manifest.json` — marketplace artifact. Includes verbatim prereqs block. G4 test (LD-4 falsifier). |
| **T5** | ~1h | G3 emit smoke test (LD-1 falsifier) + G5 5-capability showcase test (positioning doc fidelity). |
| **T9.1** | ~30 min | Codex Impl R1 + fold + CHANGELOG.md + CHANGELOG-orchestrate.md + merge prep. |

**Total estimate: ~5h actual** (matches the 4–6h budget from skill-systems plan.md:139).

---

## Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| FD-MINTKEY-CLI doesn't exist; T0 needs scope addition or W3-rescope | Medium | T0 first action: `find src -name "mintkey*"` and `grep -r "mintkey" src/`. If missing: prefer 30-min inline ship in T1 over W3-rescope (the LD-4 prereqs text is binding). |
| Emitter inadvertently introduces wall-clock or random — fails G1 | Low | Pure stdlib + no `time.time()` / `os.urandom` in emit functions. G1 directly falsifies. |
| The 2 canned chains can't actually exercise all 5 capabilities in a single coder run | Low (verified, R0-fold I1 + R0.1 fold reworded) | Pause/resume is exposed as **Python API** on `_CoderSteeringSession` (`src/persistence/coder/_steering.py:61, 73`), NOT WS-only. The 2.3d test `tests/coder/test_steering_replay.py::test_pause_emits_repl_request_then_response_in_order` proves a narrow contract: pause+resume emit `:repl/request` followed immediately by `:repl/response` on the audit chain with intact Merkle linkage. **G5 inherits exactly this 2-op contract** — it does NOT claim live thread coordination (loop-blocked-while-paused) and does NOT claim the 3-op `[:repl/request, :coder/branch, :repl/response]` pattern (which only fires when `branch()` is invoked per 2.3d's `test_branch_emits_repl_request_then_coder_branch_then_response`). Positioning doc § 4 (`positioning.md:78-80`) makes the same pause/resume claim. Real threaded pause-while-running is W3-rescoped if ever needed (substrate-level concern, not Phase 7's). |
| Anthropic Skills marketplace publish process unknown / blocked at GA | Medium | Per skill-systems plan.md:128, manual publish in Phase 7. W3-1 (publishing tooling) is post-GA. Phase 7 just SHIPS the artifact + docs; marketplace push is downstream. |
| Cross-track conductor STATUS append discipline | Low | Phase 7 belongs to `skill-systems-integration_20260430` track. Append-only at `~/Projects/conductor/tracks/skill-systems-integration_20260430/STATUS.md` post-ship. persistence-os-product STATUS gets a cross-reference only. |

---

## Cross-references

- **Parent track:** `~/Projects/conductor/tracks/skill-systems-integration_20260430/` (spec.md + plan.md + decisions.md ADR-004)
- **Source plan:** `~/Projects/docs/plans/2026-04-30-skill-systems-integration.md` (lines 51-189 cover Phase 7)
- **Substrate posture:** `~/Projects/persistence-os/CLAUDE.md:54-66` (Phase 7 pointer)
- **5-capability contract:** `~/Projects/persistence-os/docs/release-notes/v0.9.0a1-positioning.md` (shipped 2026-05-11 commit `f7d234b`)
- **Mimir Phase D boundary:** `~/Projects/mimir-os/.serena/memories/ship_plan_14_weeks.md:10` (supervisor verbs Jun 15–Jul 19)
- **Preflight manifest origin:** `tests/preflight_manifest.toml` (shipped 2.4c merge `d9e79fb`)
- **Closest analog skill:** `~/Projects/mimir-bundle-drafts/skill/mimir-orchestrator/SKILL.md` (single-file SKILL pattern)
- **Anthropic Skills shape:** `~/.claude/skills/agent-mode-worker-api/{SKILL.md, manifest.json}` (minimal skill reference)

---

*Design **FROZEN** at R0.2 PASS 8.3/8.1 (trajectory: R0 FAIL 7.6/7.1 → R0.1 PASS-WITH-FIXES 7.9/7.2 → R0.2 PASS 8.3/8.1). 5/5 codex consensus flips on LDs + 2 R0 design folds (kill-switch ordering + stdlib-only orphan) caught by ARIS iteration. Estimated 5h actual on 4–6h budget. Phase 7 GA at v0.9.0a1 GA tag (target 2026-06-14).*
