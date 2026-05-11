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
| Design R0 (codex high) | PENDING | This document is R0-ready as written |
| Design R0-fold (controller) | PENDING | After R0 |
| Design R0.1 lite | PENDING | After R0-fold |
| Codex Impl R1 | PENDING | After T9.1, before TaskUpdate completed |

**Methodology note:** 5-for-5 codex consensus flips this phase (LD-1 + LD-2 + LD-3 + LD-4 first pass + LD-4 second pass). Pattern continues from 2.4b (2/2), 2.4b.1 (2/2), 2.4c (3/3). The substrate-phase consensus discipline applies here even though Phase 7 is downstream (it touches `src/persistence/orchestrate/` and ships a marketplace artifact whose contract is binding once published).

**Notable codex catches that would have shipped wrong:**
- LD-1: I missed `CLAUDE.md:65-66` ("invocation surface for orchestrator-driven agents") — would have shipped a runtime orchestrator that pre-empted Mimir Phase D.
- LD-4 first pass: framing "dependency-free emitted SKILL.md" was wordplay (positioning.md:98 itself requires clone + uv sync).
- LD-4 second pass: my prereqs draft said `pip install git+...` (conflicts with `CLAUDE.md:32` "NOT pip-installable") + `PERSISTENCE_AUDIT_KEY=` empty (undocumented behavior, prefer UNSET).

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

The emitter is **not** runtime orchestration code — it is a stdlib-only Python codegen that converts a chain.edn into a downstream Anthropic Skill directory. The substrate (`persistence.coder` CLI shipped 2.4a) does all runtime work when the emitted skill is invoked.

**Tech Stack:**
- Python 3.10+ stdlib only (no new dependencies — `edn_format` already in base deps for chain parsing per `src/persistence/plan/_coerce.py:29`)
- EDN as canonical chain schema (homoiconicity)
- Anthropic Skills marketplace = primary distribution channel
- Curated SDK facade (`persistence.sdk.Substrate`) called via `persistence.coder` CLI at downstream-skill invocation time

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

### LD-3 — Chain description schema is **EDN canonical** (codex consensus DISAGREE-WEAK)

**Decision:** Chain schemas are authored in EDN (canonical persistence-os form). Example:

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

YAML is allowed as **optional sugar** (a hand-authoring convenience) that gets compiled into the canonical EDN form during emit-time. SKILL.md frontmatter stays YAML (Anthropic convention; non-load-bearing).

**Rationale (anchored to receipts):**
- `CLAUDE.md:22`: "Every plan is an EDN AST. Every skill is a content-addressed AST subtree." YAML-as-canonical would contradict homoiconic positioning in the marketplace artifact.
- `mimir-orchestrator/SKILL.md:144` uses YAML for scope DECLARATIONS (config), but never as PLAN form. Phase 7 must not blur this distinction.
- `edn_format` already in base deps (`pyproject.toml`). Zero new deps for parsing.

**Rejected competitors:**
- **YAML canonical** — undermines homoiconicity story.
- **Plain language → LLM parsing** — forces LLM-driven emit (conflicts with LD-2 D-3 Mixed determinism).

**Falsifiable acceptance signal (G2):**
`tests/orchestrate/test_chain_schema.py::test_chain_parses_to_edn_ast_and_roundtrips` — parse a known chain.edn, assert the AST shape matches expected `frozenset`/`Symbol` form, then serialize back to EDN and assert byte-identical to input.

---

### LD-4 — Install path is **D-honest-refined** (codex consensus 2 passes; DISAGREE-STRONG then NEW-OPTION-X)

**Decision:** Both the meta-skill (`skills/persistence-orchestrate/SKILL.md`) and the emitted downstream orchestrator skills carry **explicit prerequisites sections** that match `positioning.md:98-108` verbatim:

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

**Rationale (anchored to receipts):**
- `CLAUDE.md:32`: "NOT pip-installable / open-source-ready yet. No PyPI release." Vendoring would create a third distribution channel (marketplace) for a substrate that's explicitly "local-only" at this phase.
- `positioning.md:98-108` (shipped TODAY): the alpha install contract is "Clone persistence-os, run uv sync." Phase 7 SKILL.md inherits this contract verbatim — no separate marketplace install path.
- `mimir-os/.serena/memories/ship_plan_14_weeks.md:11`: PyPI release is Phase E (Jul 20 – Aug 2). Phase 7 GA is at v0.9.0a1 (early June). ~50 days between Phase 7 and Phase E — the audience is alpha evaluators, not casual marketplace browsers. They accept clone + uv sync.
- Codex's two specific corrections folded inline: (1) use clone + uv sync, NOT `pip install git+...`; (2) UNSET env var for unsigned dev, NOT empty string (undocumented behavior).

**Rejected competitors:**
- **D-vendor (vendor slim runtime)** — substrate IS the dependency (coder uses sdk uses fact/effect/plan/txn/replay/spec/repl); "slim" vendoring is impractical. Drift risk codex itself flagged. Posture-violation of `CLAUDE.md:32`.
- **Assume PyPI** — Phase 7 GA pre-dates Phase E PyPI release by ~50 days.

**Falsifiable acceptance signal (G4):**
`tests/orchestrate/test_skill_md_prereqs_section.py::test_emitted_skill_md_contains_verbatim_install_block` — emit any chain, assert the emitted SKILL.md contains the verbatim `git clone https://github.com/nawsaafa/persistence-os.git`+`uv sync`+`PERSISTENCE_AUDIT_KEY=file:///` install block (string equality on a defined prereqs region delimited by `<!-- prereqs-begin -->` / `<!-- prereqs-end -->` HTML comment markers in the template).

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

### G3 — Emit smoke (LD-1 falsifier)

**File:** `tests/sdk/test_persistence_orchestrate_emit_smoke.py::test_emits_orchestrator_that_runs_signed_replayable_trace`

```python
import json
import subprocess
from pathlib import Path

def test_emits_orchestrator_that_runs_signed_replayable_trace(
    tmp_path: Path, monkeypatch
) -> None:
    # Use the canned capability-denial chain.
    chain_path = (Path(__file__).parent.parent.parent
                  / "src/persistence/orchestrate/examples"
                  / "capability-denial-chain.edn")

    out_dir = tmp_path / "emitted-skill"

    # Step 1: emit
    subprocess.run(
        ["uv", "run", "python", "-m", "persistence.orchestrate", "emit",
         "--chain", str(chain_path), "--out", str(out_dir)],
        check=True,
    )

    # Assert emitted directory shape
    assert (out_dir / "SKILL.md").exists()
    assert (out_dir / "chain.edn").exists()
    assert (out_dir / "preflight.toml").exists()

    # Step 2: run the emitted orchestrator via persistence.coder
    # The emitted SKILL.md instructs Claude Code to invoke the
    # coder CLI with the emitted chain.edn. Here we invoke it
    # directly (the SKILL.md is for marketplace UX, not for the test).
    db_path = tmp_path / "substrate.db"
    key_path = tmp_path / "agent.pem"
    # Generate key (test-only; would normally use mintkey CLI)
    from persistence.effect._signing import generate_keypair
    priv, _ = generate_keypair()
    key_path.write_bytes(priv)

    monkeypatch.setenv("PERSISTENCE_AUDIT_KEY", f"file://{key_path}")
    result = subprocess.run(
        ["uv", "run", "python", "-m", "persistence.coder",
         "--task", str(out_dir / "chain.edn"),
         "--db-path", f"sqlite:///{db_path}",
         "--provider", "echo"],
        capture_output=True, text=True,
    )

    # (iii) Trace contains signed denial datom for step 2
    trace = json.loads(result.stdout)
    denials = [e for e in trace if e["op"] == ":capability/denied"]
    assert len(denials) == 1
    assert denials[0]["step_id"] == 2
    assert denials[0].get("signature") is not None

    # (i)+(ii) Step 2 side effect never occurred (no :fs/write entry)
    writes = [e for e in trace if e["op"] == ":fs/write"]
    assert len(writes) == 0

    # (iv) Replay byte-identity
    # ... (uses persistence.replay.canonical replay; same shape as
    # test_coder_loop_audit_replay_byte_identity from positioning doc § 3)
```

**Falsifies if:** any of the LD-1 invariants break — emitted skill misses files, side effect occurs despite denial, trace unsigned, replay drifts.

### G4 — SKILL.md prereqs verbatim (LD-4 falsifier)

**File:** `tests/orchestrate/test_skill_md_prereqs_section.py::test_emitted_skill_md_contains_verbatim_install_block`

```python
from persistence.orchestrate import emit_orchestrator_skill, parse_chain_edn

EXPECTED_PREREQS_BLOCK = """  git clone https://github.com/nawsaafa/persistence-os.git
  cd persistence-os
  uv sync"""

EXPECTED_KEY_BLOCK = """  python -m persistence.tools.mintkey --out ~/.persistence/keys/agent.pem
  export PERSISTENCE_AUDIT_KEY=file:///$HOME/.persistence/keys/agent.pem"""

EXPECTED_UNSET_BLOCK = """  unset PERSISTENCE_AUDIT_KEY"""

def test_emitted_skill_md_contains_verbatim_install_block(tmp_path) -> None:
    src = (Path(__file__).parent.parent.parent
           / "src/persistence/orchestrate/examples"
           / "capability-denial-chain.edn").read_text()
    chain = parse_chain_edn(src)
    emit_orchestrator_skill(chain, tmp_path)

    md = (tmp_path / "SKILL.md").read_text()
    assert EXPECTED_PREREQS_BLOCK in md
    assert EXPECTED_KEY_BLOCK in md
    assert EXPECTED_UNSET_BLOCK in md
    # Verbatim — no paraphrasing allowed (LD-4 corrected from codex pass 2)
    assert "pip install git+" not in md  # Negative — pre-Phase-E posture
    assert "PERSISTENCE_AUDIT_KEY=" + chr(10) not in md  # Negative — empty-string is undocumented
```

**Falsifies if:** emitted SKILL.md drifts from positioning.md install contract, regresses to `pip install git+...`, or uses empty-string env-unset shorthand.

### G5 — 5-capability showcase (positioning doc fidelity)

**File:** `tests/orchestrate/test_demo_chains_showcase_five_capabilities.py::test_two_canned_chains_exercise_all_five_capabilities`

The two example chains together must produce audit traces that exercise all 5 capabilities named in `docs/release-notes/v0.9.0a1-positioning.md` (lines 13, 31, 48, 67, 82).

```python
def test_two_canned_chains_exercise_all_five_capabilities(tmp_path) -> None:
    traces = _run_both_canned_chains(tmp_path)  # helper

    capabilities_exercised = set()
    for trace in traces:
        for entry in trace:
            if entry.get("signature"):
                capabilities_exercised.add("agent-vs-human-identity")  # cap 1
            if entry["op"] == ":capability/denied":
                capabilities_exercised.add("governed-action")  # cap 2
            if entry["op"] in (":repl/request", ":repl/response"):
                capabilities_exercised.add("kill-switch")  # cap 4
            if entry["op"] == ":sys/now":
                capabilities_exercised.add("substrate-time")  # cap 5

    # Audit replay byte-identity (cap 3) exercised in G3 already;
    # here we just confirm both chains replay byte-identical.
    for trace_idx, trace in enumerate(traces):
        replayed = _replay_canonical(trace)
        assert replayed == trace, f"chain {trace_idx} replay drift"
    capabilities_exercised.add("audit-replay")  # cap 3

    assert capabilities_exercised == {
        "agent-vs-human-identity",
        "governed-action",
        "audit-replay",
        "kill-switch",
        "substrate-time",
    }
```

**Falsifies if:** the canned chains underutilize the positioning doc's claims (e.g., kill switch idle, sys/now idle).

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
| The 2 canned chains can't actually exercise all 5 capabilities in a single coder run | Low (verified) | Pause/resume is exposed as **Python API** on `_CoderSteeringSession` (`src/persistence/coder/_steering.py:61, 73`), NOT WS-only. The 2.3d test `tests/coder/test_steering_replay.py::test_pause_emits_repl_request_then_response_in_order` is the harness template — G5 reuses this pattern: start coder in a thread, call `session.pause()` after step 1, assert `:repl/request`, call `session.resume()`, assert `:repl/response`. WS REPL not required. |
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

*Design FROZEN pending ARIS R0 → R0-fold → R0.1 lite. 5/5 codex consensus flips on LDs (continued substrate-phase methodology). Estimated 5h actual on 4–6h budget. Phase 7 GA at v0.9.0a1 GA tag (target 2026-06-14).*
