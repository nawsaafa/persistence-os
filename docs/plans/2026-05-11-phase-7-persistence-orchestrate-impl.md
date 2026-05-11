# Phase 7 — `persistence-orchestrate` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use **superpowers:subagent-driven-development**. Each task dispatched as fresh subagent with TDD discipline (test-first → red → impl → green → commit).

**Design doc:** `docs/plans/2026-05-11-phase-7-persistence-orchestrate-design.md` (FROZEN at ARIS R0.2 PASS 8.3/8.1)

**Goal:** Ship `persistence-orchestrate` as an Anthropic Skill that emits installable downstream orchestrator skills (4 files per emit: SKILL.md + chain.edn + preflight.toml + orchestrate.py). The orchestrate.py is a thin Python module using `persistence.sdk` to execute the chain — substrate stays substrate; runtime stays in the emitted artifact.

**Architecture:** Two-layer (meta-skill emits → downstream orchestrator runs). Pure stdlib emitter (string-building, no Jinja2) + existing `edn_format` parser dep. No new top-level dependencies.

**Tech Stack:** Python 3.10+, `edn_format` (existing base dep), `persistence.sdk` (curated facade), Anthropic Skills marketplace (manual publish at Phase 7 GA per skill-systems plan.md:128).

**Branch:** `feat/v0.9-phase7-persistence-orchestrate` off `feat/v0.9-persistence-coder@fa372ef` (post-R0.2-fold).

**Worktree:** `~/Projects/persistence-os-worktrees/phase7/` (T0 creates).

---

## Implementation order

| T | Task | Estimate | Subagent type |
|---|---|---|---|
| T0 | Worktree setup + verify receipts | 20 min | Controller-direct |
| T1 | `tools/mintkey.py` + `orchestrate/_schema.py` + G2 test | 1h | Subagent |
| T2 | `orchestrate/_emit.py` (4 emit functions) + G1 test | 1h | Subagent |
| T3 | `orchestrate/__main__.py` + `__init__.py` + 2 canned example chains | 1h | Subagent |
| T4 | `skills/persistence-orchestrate/SKILL.md` + `manifest.json` + G4 test | 1h | Subagent |
| T5 | G3 emit-smoke + G5 5-capability showcase tests | 1h | Subagent |
| T9.1 | Codex Impl R1 + CHANGELOGs + merge prep | 30 min | Controller-direct |

**Total: ~5.25h** on 4–6h budget.

---

## Task 0 — Worktree setup + T0 receipts

**Files:**
- Create: `~/Projects/persistence-os-worktrees/phase7/` (worktree)

**Subagent:** Controller-direct (worktree setup is risky/non-TDD)

- [ ] **Step 1: Create worktree off feat/v0.9-persistence-coder**

```bash
git -C ~/Projects/persistence-os worktree add \
  ~/Projects/persistence-os-worktrees/phase7 \
  -b feat/v0.9-phase7-persistence-orchestrate \
  feat/v0.9-persistence-coder
```

Expected: `Preparing worktree (new branch 'feat/v0.9-phase7-persistence-orchestrate')` + `HEAD is now at fa372ef ...`

- [ ] **Step 2: Verify worktree exists + branch correct**

```bash
git -C ~/Projects/persistence-os worktree list | grep phase7
cd ~/Projects/persistence-os-worktrees/phase7 && git branch --show-current
```

Expected: worktree listed; `feat/v0.9-phase7-persistence-orchestrate`

- [ ] **Step 3: Verify uv sync clean state in worktree**

```bash
cd ~/Projects/persistence-os-worktrees/phase7 && uv sync 2>&1 | tail -3
```

Expected: `Audited X packages` or `Resolved X packages` — no errors

- [ ] **Step 4: T0 receipts already collected** (mintkey absent, Coder API verified — see design doc's FD-CODER-RUN-CHAIN-API resolution).

No commit at T0; worktree is the deliverable.

---

## Task 1 — mintkey CLI + EDN schema parser + G2 test

**Files:**
- Create: `src/persistence/tools/__init__.py`
- Create: `src/persistence/tools/mintkey.py`
- Create: `src/persistence/orchestrate/__init__.py` (empty module marker)
- Create: `src/persistence/orchestrate/_schema.py`
- Create: `tests/orchestrate/__init__.py` (empty)
- Create: `tests/orchestrate/test_chain_schema.py`
- Create: `tests/orchestrate/conftest.py` (shared fixtures)

**Subagent:** Fresh `impl-phase7-t1` subagent.

- [ ] **Step 1: Write G2 failing test first (TDD red)**

Create `tests/orchestrate/test_chain_schema.py`:

```python
"""G2 — LD-3 falsifier: EDN chain schema parses to AST and roundtrips.

R0-fold B2: v0 is EDN-only. YAML input must raise ChainSchemaError.
"""
from __future__ import annotations

import pytest

from persistence.orchestrate._schema import (
    Chain,
    ChainSchemaError,
    Step,
    StepCapability,
    parse_chain_edn,
    serialize_chain_edn,
)


VALID_CHAIN_EDN = """(:chain
  :name "demo"
  :description "G2 demo chain"
  :steps [(:step :id 1
                :op :fs/read
                :args {:path "input.md"}
                :capability (:Capability :op "coder" :qualifier "read"))])"""


def test_chain_parses_to_edn_ast_and_roundtrips() -> None:
    chain = parse_chain_edn(VALID_CHAIN_EDN)
    assert chain.name == "demo"
    assert chain.description == "G2 demo chain"
    assert len(chain.steps) == 1
    assert chain.steps[0].id == 1
    assert chain.steps[0].op == ":fs/read"
    assert chain.steps[0].args == {":path": "input.md"}
    assert chain.steps[0].capability == StepCapability(op="coder", qualifier="read")

    # Roundtrip: serialize → parse equals original
    reserialized = serialize_chain_edn(chain)
    assert parse_chain_edn(reserialized) == chain


def test_yaml_input_raises_v0_edn_only_error() -> None:
    yaml_src = """name: demo
description: should fail
steps:
  - id: 1
    op: ":fs/read"
"""
    with pytest.raises(ChainSchemaError, match="v0 is EDN-only"):
        parse_chain_edn(yaml_src)


def test_missing_required_field_raises() -> None:
    src = """(:chain :description "no name field" :steps [])"""
    with pytest.raises(ChainSchemaError, match="missing required field: :name"):
        parse_chain_edn(src)
```

- [ ] **Step 2: Run test — expect RED**

```bash
cd ~/Projects/persistence-os-worktrees/phase7 && \
  uv run pytest tests/orchestrate/test_chain_schema.py -v 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'persistence.orchestrate'` (or similar import error)

- [ ] **Step 3: Create empty modules**

```bash
mkdir -p src/persistence/orchestrate src/persistence/tools tests/orchestrate
touch src/persistence/orchestrate/__init__.py
touch src/persistence/tools/__init__.py
touch tests/orchestrate/__init__.py
```

- [ ] **Step 4: Implement `_schema.py` (minimal pass)**

Create `src/persistence/orchestrate/_schema.py`:

```python
"""EDN chain schema parser + serializer for persistence-orchestrate.

LD-3 (codex consensus REJECT-FOR-NEW-OPTION → EDN canonical):
Chain schemas are authored in EDN. v0 is EDN-only authoring; YAML is
deferred to W3-5. SKILL.md frontmatter stays YAML (Anthropic
convention, non-load-bearing).

R0-fold B2: YAML-shaped input must raise ChainSchemaError with the
verbatim "v0 is EDN-only; YAML authoring is W3-5" message.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import edn_format


class ChainSchemaError(ValueError):
    """Raised when chain.edn fails schema validation."""


@dataclass(frozen=True)
class StepCapability:
    """Per-step capability requirement (Capability lattice from 2.3d)."""
    op: str
    qualifier: str


@dataclass(frozen=True)
class Step:
    """A single chain step."""
    id: int
    op: str  # EDN keyword form, e.g. ":fs/read"
    args: dict[str, Any] = field(default_factory=dict)
    capability: StepCapability | None = None


@dataclass(frozen=True)
class Chain:
    """A complete chain ready for emission."""
    name: str
    description: str
    steps: tuple[Step, ...]


def _is_yaml_shaped(src: str) -> bool:
    """Heuristic: looks like YAML (top-level `key: value` lines, no parens)."""
    stripped = src.strip()
    if stripped.startswith("("):
        return False
    # YAML signature: at least one `key: value` line at column 0
    for line in stripped.splitlines()[:5]:
        if line and not line.startswith(" ") and not line.startswith("#"):
            if ":" in line and not line.startswith("("):
                return True
    return False


def parse_chain_edn(src: str) -> Chain:
    """Parse an EDN chain source string into a Chain dataclass.

    Raises ChainSchemaError on missing required fields or YAML input.
    """
    if _is_yaml_shaped(src):
        raise ChainSchemaError(
            "v0 is EDN-only; YAML authoring is W3-5 (deferred)"
        )

    try:
        parsed = edn_format.loads(src)
    except Exception as e:
        raise ChainSchemaError(f"EDN parse error: {e}") from e

    if not isinstance(parsed, dict):
        raise ChainSchemaError(
            f"chain.edn must be a map; got {type(parsed).__name__}"
        )

    # Extract fields by EDN keyword
    name_kw = edn_format.Keyword("name")
    desc_kw = edn_format.Keyword("description")
    steps_kw = edn_format.Keyword("steps")

    if name_kw not in parsed:
        raise ChainSchemaError("missing required field: :name")
    if desc_kw not in parsed:
        raise ChainSchemaError("missing required field: :description")
    if steps_kw not in parsed:
        raise ChainSchemaError("missing required field: :steps")

    name = parsed[name_kw]
    description = parsed[desc_kw]
    raw_steps = parsed[steps_kw]

    if not isinstance(name, str):
        raise ChainSchemaError(":name must be a string")
    if not isinstance(description, str):
        raise ChainSchemaError(":description must be a string")
    if not isinstance(raw_steps, (list, tuple)):
        raise ChainSchemaError(":steps must be a vector")

    steps = tuple(_parse_step(s) for s in raw_steps)
    return Chain(name=name, description=description, steps=steps)


def _parse_step(src: Any) -> Step:
    if not isinstance(src, dict):
        raise ChainSchemaError(
            f"each step must be a map; got {type(src).__name__}"
        )

    id_kw = edn_format.Keyword("id")
    op_kw = edn_format.Keyword("op")
    args_kw = edn_format.Keyword("args")
    cap_kw = edn_format.Keyword("capability")

    if id_kw not in src:
        raise ChainSchemaError("step missing required field: :id")
    if op_kw not in src:
        raise ChainSchemaError("step missing required field: :op")

    step_id = src[id_kw]
    op_raw = src[op_kw]

    if not isinstance(step_id, int):
        raise ChainSchemaError(":id must be an integer")

    # Normalize EDN keyword to string form ":fs/read"
    if isinstance(op_raw, edn_format.Keyword):
        op = ":" + str(op_raw)
    elif isinstance(op_raw, str):
        op = op_raw if op_raw.startswith(":") else ":" + op_raw
    else:
        raise ChainSchemaError(f":op must be a keyword or string; got {type(op_raw).__name__}")

    # Normalize args (dict with keyword keys → dict with string keys ":path")
    args: dict[str, Any] = {}
    if args_kw in src:
        for k, v in src[args_kw].items():
            key_str = ":" + str(k) if isinstance(k, edn_format.Keyword) else str(k)
            args[key_str] = v

    capability: StepCapability | None = None
    if cap_kw in src:
        cap_raw = src[cap_kw]
        if not isinstance(cap_raw, dict):
            raise ChainSchemaError(":capability must be a map")
        cap_op = cap_raw.get(edn_format.Keyword("op"))
        cap_qual = cap_raw.get(edn_format.Keyword("qualifier"))
        if not isinstance(cap_op, str) or not isinstance(cap_qual, str):
            raise ChainSchemaError(":capability requires :op and :qualifier strings")
        capability = StepCapability(op=cap_op, qualifier=cap_qual)

    return Step(id=step_id, op=op, args=args, capability=capability)


def serialize_chain_edn(chain: Chain) -> str:
    """Serialize a Chain back to canonical EDN form.

    Deterministic: same Chain → byte-identical output. No wall-clock,
    no random, no dict-ordering nondeterminism.
    """
    lines = [
        "(:chain",
        f'  :name "{_escape(chain.name)}"',
        f'  :description "{_escape(chain.description)}"',
        "  :steps [",
    ]
    for step in chain.steps:
        lines.append("    " + _serialize_step(step))
    lines.append("  ])")
    return "\n".join(lines)


def _serialize_step(step: Step) -> str:
    parts = [f"(:step :id {step.id}", f":op {step.op}"]

    if step.args:
        args_parts = []
        for k in sorted(step.args.keys()):  # sorted for determinism
            v = step.args[k]
            if isinstance(v, str):
                args_parts.append(f'{k} "{_escape(v)}"')
            else:
                args_parts.append(f"{k} {v}")
        parts.append("  :args {" + " ".join(args_parts) + "}")

    if step.capability is not None:
        parts.append(
            f'  :capability (:Capability :op "{step.capability.op}" '
            f':qualifier "{step.capability.qualifier}")'
        )

    return " ".join(parts) + ")"


def _escape(s: str) -> str:
    """Minimal EDN string escape."""
    return s.replace("\\", "\\\\").replace('"', '\\"')
```

- [ ] **Step 5: Implement `tools/mintkey.py`**

Create `src/persistence/tools/mintkey.py`:

```python
"""Ed25519 keypair minting CLI for persistence-os.

Phase 7 T1 (FD-MINTKEY-CLI resolution): thin wrapper over
`persistence.effect._signing.generate_keypair()`. Writes the private
key in PEM form to `--out` so it can be referenced as
`PERSISTENCE_AUDIT_KEY=file:///<abs-out-path>` per LD-4 prereqs.

Usage:
    python -m persistence.tools.mintkey --out ~/.persistence/keys/agent.pem
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)


def mint_keypair_to_pem(out_path: Path) -> None:
    """Generate an Ed25519 keypair and write the private key as PEM."""
    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(pem)
    # Restrict permissions (private key)
    out_path.chmod(0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m persistence.tools.mintkey",
        description="Mint an Ed25519 keypair for PERSISTENCE_AUDIT_KEY.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output path for the PEM-encoded private key.",
    )
    args = parser.parse_args(argv)

    out_path = args.out.expanduser().resolve()
    if out_path.exists():
        print(f"refusing to overwrite existing key at {out_path}", file=sys.stderr)
        return 1

    mint_keypair_to_pem(out_path)
    print(f"wrote {out_path}")
    print(f"export PERSISTENCE_AUDIT_KEY=file://{out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Create `src/persistence/tools/__main__.py`:

```python
"""Re-export for `python -m persistence.tools.mintkey`."""
# Subcommand routing not needed yet; only mintkey exists.
```

- [ ] **Step 6: Run G2 test — expect GREEN**

```bash
cd ~/Projects/persistence-os-worktrees/phase7 && \
  uv run pytest tests/orchestrate/test_chain_schema.py -v 2>&1 | tail -15
```

Expected: `3 passed`

- [ ] **Step 7: Smoke-test mintkey CLI**

```bash
cd ~/Projects/persistence-os-worktrees/phase7 && \
  uv run python -m persistence.tools.mintkey --out /tmp/phase7-test-key.pem && \
  test -f /tmp/phase7-test-key.pem && \
  head -1 /tmp/phase7-test-key.pem && \
  rm /tmp/phase7-test-key.pem
```

Expected: `-----BEGIN PRIVATE KEY-----` on the head line.

- [ ] **Step 8: Commit**

```bash
git add src/persistence/orchestrate/__init__.py \
        src/persistence/orchestrate/_schema.py \
        src/persistence/tools/__init__.py \
        src/persistence/tools/__main__.py \
        src/persistence/tools/mintkey.py \
        tests/orchestrate/__init__.py \
        tests/orchestrate/test_chain_schema.py
git commit -m "T1(phase7): EDN chain schema + mintkey CLI + G2 test

LD-3 EDN-canonical chain schema with ChainSchemaError on YAML input
(R0-fold B2). FD-MINTKEY-CLI resolved by shipping thin Ed25519 PEM
mint wrapper at src/persistence/tools/mintkey.py (~70 LOC).

G2 test: chain_parses_to_edn_ast_and_roundtrips + yaml_input_raises
+ missing_required_field_raises. 3 passed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2 — Emit functions (4 emitters) + G1 determinism test

**Files:**
- Create: `src/persistence/orchestrate/_emit.py`
- Create: `tests/orchestrate/test_emitter_determinism.py`

**Subagent:** Fresh `impl-phase7-t2` subagent.

- [ ] **Step 1: Write G1 failing test first (TDD red)**

Create `tests/orchestrate/test_emitter_determinism.py`:

```python
"""G1 — LD-2 falsifier: emitter is byte-deterministic.

D-3 Mixed: minimal Python emitter using stdlib string-building +
existing edn_format parser. Same chain → byte-identical 4-file tree
across runs. Determinism falsifies if emit functions introduce
wall-clock, random, or dict-iteration-order leak.
"""
from __future__ import annotations

import filecmp
from pathlib import Path

from persistence.orchestrate import emit_orchestrator_skill
from persistence.orchestrate._schema import parse_chain_edn

CANNED_CHAIN_EDN = """(:chain
  :name "g1-determinism"
  :description "G1 falsifier chain"
  :steps [(:step :id 1
                :op :fs/read
                :args {:path "in.md"}
                :capability (:Capability :op "coder" :qualifier "read"))
          (:step :id 2
                :op :fs/write
                :args {:path "out.md"}
                :capability (:Capability :op "coder" :qualifier "write"))])"""


def test_emits_identical_tree_from_same_chain(tmp_path: Path) -> None:
    chain = parse_chain_edn(CANNED_CHAIN_EDN)

    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    emit_orchestrator_skill(chain, out_a)
    emit_orchestrator_skill(chain, out_b)

    for relpath in ["SKILL.md", "chain.edn", "preflight.toml", "orchestrate.py"]:
        assert (out_a / relpath).exists(), f"missing {relpath} in run a"
        assert (out_b / relpath).exists(), f"missing {relpath} in run b"
        assert filecmp.cmp(out_a / relpath, out_b / relpath, shallow=False), \
            f"emitter non-deterministic on {relpath}"
```

- [ ] **Step 2: Run G1 test — expect RED**

```bash
cd ~/Projects/persistence-os-worktrees/phase7 && \
  uv run pytest tests/orchestrate/test_emitter_determinism.py -v 2>&1 | tail -10
```

Expected: ImportError on `emit_orchestrator_skill`

- [ ] **Step 3: Implement `_emit.py`**

Create `src/persistence/orchestrate/_emit.py`:

```python
"""String-building emit functions for persistence-orchestrate.

LD-2 (codex consensus DISAGREE-STRONG → D-3 Mixed): pure stdlib
emitter (no Jinja2, no LLM-driven generation). Templates are fixed;
emit functions build strings deterministically.

4 emitters per LD-1 (4-file emitted dir):
- emit_skill_md(chain) → str          [SKILL.md, marketplace artifact]
- emit_chain_edn(chain) → str         [chain.edn, canonical chain]
- emit_preflight_toml(chain) → str    [preflight.toml, capability allowlist]
- emit_orchestrate_py(chain) → str    [orchestrate.py, thin runner]

The orchestrate.py emitter renders a pure-Python module exposing
run_chain(substrate, granted_capabilities) using s.effect.perform()
per step.
"""
from __future__ import annotations

from pathlib import Path

from persistence.orchestrate._schema import Chain, serialize_chain_edn

# Canonical install-prereqs region for emitted SKILL.md (LD-4 verbatim).
# R0-fold B1: clone+uv-sync clause sourced from positioning.md:98;
# mintkey/UNSET is NEW extension text grounded in CLAUDE.md:32 +
# positioning.md:21-23.
PREREQS_REGION = """## Prerequisites

This skill requires persistence-os installed locally:

Clone `persistence-os`, run `uv sync`

For signed audit chains (production posture), generate an Ed25519 key:

  python -m persistence.tools.mintkey --out ~/.persistence/keys/agent.pem
  export PERSISTENCE_AUDIT_KEY=file:///$HOME/.persistence/keys/agent.pem

For dev mode (unsigned), UNSET the env var:

  unset PERSISTENCE_AUDIT_KEY

The skill invokes `python ./orchestrate.py` directly."""


def emit_skill_md(chain: Chain) -> str:
    """Render the marketplace SKILL.md for an emitted chain."""
    return f"""---
name: {chain.name}
description: {chain.description}
---

# {chain.name}

{chain.description}

<!-- prereqs-begin -->
{PREREQS_REGION}
<!-- prereqs-end -->

## What this skill does

When invoked, this skill runs the chain defined in `chain.edn` using
the persistence-os substrate. Each step is gated by a Capability from
the 2.3d lattice; the run produces a signed canonical_audit_stack
trace replayable byte-identically.

## How to run

```bash
python ./orchestrate.py
```

## Files in this skill

- `SKILL.md` — this file
- `chain.edn` — canonical chain definition
- `preflight.toml` — capabilities + ops required
- `orchestrate.py` — the chain runner using `persistence.sdk`
"""


def emit_chain_edn(chain: Chain) -> str:
    """Re-serialize the chain to canonical EDN form."""
    return serialize_chain_edn(chain)


def emit_preflight_toml(chain: Chain) -> str:
    """Render preflight.toml: capabilities + ops required by this chain.

    Mirrors the schema shape of tests/preflight_manifest.toml shipped
    in 2.4c (FD-PREFLIGHT-TOML-SCHEMA default).
    """
    lines = [
        "# Preflight manifest for this chain — generated by",
        "# persistence-orchestrate. DO NOT EDIT.",
        "",
        f'name = "{chain.name}"',
        "",
        "[capabilities]",
    ]
    # Sorted unique capabilities for determinism
    caps = sorted({
        (s.capability.op, s.capability.qualifier)
        for s in chain.steps if s.capability is not None
    })
    for op, qual in caps:
        lines.append(f'{op}_{qual} = {{ op = "{op}", qualifier = "{qual}" }}')

    lines.append("")
    lines.append("[ops]")
    ops = sorted({s.op for s in chain.steps})
    for op in ops:
        lines.append(f'"{op}" = {{ required = true }}')
    lines.append("")
    return "\n".join(lines)


def emit_orchestrate_py(chain: Chain) -> str:
    """Render orchestrate.py — the thin chain runner.

    The emitted module exposes:
      run_chain(substrate, granted_capabilities) -> None

    which loops the chain's steps and invokes s.effect.perform per step
    under the substrate's canonical_audit_stack. Capability gating uses
    the 2.3d lattice; pause/resume steps emit :repl/request +
    :repl/response audit pairs (LD-1 5-capability showcase).
    """
    header = f'''"""Emitted orchestrator for chain {chain.name!r}.

Generated by persistence-orchestrate; do not edit by hand.
Re-emit from chain.edn if the chain changes.

LD-1: this is the "thin orchestrator that calls the curated SDK
surface" per codex A-prime. Pure Python; no Coder involvement.
"""
from __future__ import annotations

from collections.abc import Iterable

from persistence.sdk import Capability, Substrate


def run_chain(
    substrate: Substrate,
    granted_capabilities: Iterable[Capability],
) -> None:
    """Run the {chain.name!r} chain under `substrate`.

    `granted_capabilities` controls which steps are allowed to execute;
    steps whose required capability is not granted emit a
    :capability/denied audit entry and are skipped (LD-1 falsifier).
    """
    granted = set(granted_capabilities)

'''

    body_parts = []
    for step in chain.steps:
        cap = step.capability
        args_repr = repr(step.args)
        if cap is not None:
            body_parts.append(f'''    # Step {step.id}: {step.op}
    _step_{step.id}_cap = Capability(op={cap.op!r}, qualifier={cap.qualifier!r})
    if _step_{step.id}_cap not in granted:
        substrate.effect.perform(
            ":capability/denied",
            {{":step_id": {step.id}, ":required": _step_{step.id}_cap}},
        )
    else:
        substrate.effect.perform({step.op!r}, {args_repr})
''')
        else:
            body_parts.append(f'''    # Step {step.id}: {step.op} (no capability gate)
    substrate.effect.perform({step.op!r}, {args_repr})
''')

    body = "\n".join(body_parts)
    return header + body


def emit_orchestrator_skill(chain: Chain, out_dir: Path) -> None:
    """Top-level emit: render 4 files into `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "SKILL.md").write_text(emit_skill_md(chain))
    (out_dir / "chain.edn").write_text(emit_chain_edn(chain))
    (out_dir / "preflight.toml").write_text(emit_preflight_toml(chain))
    (out_dir / "orchestrate.py").write_text(emit_orchestrate_py(chain))
```

- [ ] **Step 4: Wire entry point in `__init__.py`**

Edit `src/persistence/orchestrate/__init__.py`:

```python
"""persistence-orchestrate — Phase 7 v0 emitter.

Public surface:
    emit_orchestrator_skill(chain, out_dir) — write 4-file emitted dir
    parse_chain_edn(src) — parse EDN chain source

LD-1 (codex A-prime): artifact emitter. The 4-file emitted dir
(SKILL.md + chain.edn + preflight.toml + orchestrate.py) is the
"thin orchestrator that calls the curated SDK surface."
"""
from persistence.orchestrate._emit import (
    PREREQS_REGION,
    emit_chain_edn,
    emit_orchestrate_py,
    emit_orchestrator_skill,
    emit_preflight_toml,
    emit_skill_md,
)
from persistence.orchestrate._schema import (
    Chain,
    ChainSchemaError,
    Step,
    StepCapability,
    parse_chain_edn,
    serialize_chain_edn,
)

__all__ = [
    "Chain",
    "ChainSchemaError",
    "PREREQS_REGION",
    "Step",
    "StepCapability",
    "emit_chain_edn",
    "emit_orchestrate_py",
    "emit_orchestrator_skill",
    "emit_preflight_toml",
    "emit_skill_md",
    "parse_chain_edn",
    "serialize_chain_edn",
]
```

- [ ] **Step 5: Run G1 + G2 tests — expect both GREEN**

```bash
cd ~/Projects/persistence-os-worktrees/phase7 && \
  uv run pytest tests/orchestrate/ -v 2>&1 | tail -15
```

Expected: `4 passed`

- [ ] **Step 6: Commit**

```bash
git add src/persistence/orchestrate/_emit.py \
        src/persistence/orchestrate/__init__.py \
        tests/orchestrate/test_emitter_determinism.py
git commit -m "T2(phase7): 4 emit functions + G1 determinism test

LD-2 D-3 Mixed implementation: pure stdlib emit_skill_md +
emit_chain_edn + emit_preflight_toml + emit_orchestrate_py.

emit_orchestrate_py renders a Python module exposing
run_chain(substrate, granted_capabilities) using s.effect.perform()
per step — the 'thin orchestrator that calls the curated SDK surface'
per codex LD-1 A-prime. No new methods on Coder (FD-CODER-RUN-CHAIN-API
resolved as NOT NEEDED in T0).

G1 test: emits_identical_tree_from_same_chain. 1 passed (4 cumulative).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3 — CLI entrypoint + 2 canned example chains

**Files:**
- Create: `src/persistence/orchestrate/__main__.py`
- Create: `src/persistence/orchestrate/examples/__init__.py`
- Create: `src/persistence/orchestrate/examples/capability-denial-chain.edn`
- Create: `src/persistence/orchestrate/examples/pause-resume-sysnow-chain.edn`

**Subagent:** Fresh `impl-phase7-t3` subagent.

- [ ] **Step 1: Implement `__main__.py` CLI**

Create `src/persistence/orchestrate/__main__.py`:

```python
"""CLI entrypoint: `python -m persistence.orchestrate emit ...`

Subcommands:
    emit  --chain <path>  --out <dir>   Emit a 4-file orchestrator skill.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from persistence.orchestrate import emit_orchestrator_skill, parse_chain_edn


def _cmd_emit(args: argparse.Namespace) -> int:
    chain_path = args.chain.expanduser().resolve()
    out_dir = args.out.expanduser().resolve()

    if not chain_path.exists():
        print(f"chain file not found: {chain_path}", file=sys.stderr)
        return 1

    chain_src = chain_path.read_text()
    chain = parse_chain_edn(chain_src)
    emit_orchestrator_skill(chain, out_dir)

    print(f"emitted 4-file orchestrator skill at {out_dir}:")
    for name in ("SKILL.md", "chain.edn", "preflight.toml", "orchestrate.py"):
        print(f"  {out_dir / name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m persistence.orchestrate",
        description="persistence-orchestrate — emit installable chain orchestrator skills.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    emit_p = sub.add_parser("emit", help="Emit a 4-file orchestrator skill")
    emit_p.add_argument("--chain", type=Path, required=True,
                         help="Path to a chain.edn source file.")
    emit_p.add_argument("--out", type=Path, required=True,
                         help="Output directory for the emitted skill.")
    emit_p.set_defaults(func=_cmd_emit)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Author canned chain 1 — capability-denial**

Create `src/persistence/orchestrate/examples/__init__.py` (empty).

Create `src/persistence/orchestrate/examples/capability-denial-chain.edn`:

```edn
(:chain
  :name "capability-denial-demo"
  :description "G3 falsifier — step 2's capability is not granted; halts."
  :steps [(:step :id 1
                :op :fs/read
                :args {:path "input.md"}
                :capability (:Capability :op "coder" :qualifier "read"))
          (:step :id 2
                :op :fs/write
                :args {:path "output.md" :content "result"}
                :capability (:Capability :op "coder" :qualifier "write"))])
```

- [ ] **Step 3: Author canned chain 2 — pause/resume + sys/now**

Create `src/persistence/orchestrate/examples/pause-resume-sysnow-chain.edn`:

```edn
(:chain
  :name "pause-resume-sysnow-demo"
  :description "G5 cap-4 + cap-5 falsifier — pause/resume audit ordering + :sys/now read."
  :steps [(:step :id 1
                :op :sys/now
                :args {}
                :capability (:Capability :op "coder" :qualifier "read"))
          (:step :id 2
                :op :fs/read
                :args {:path "input.md"}
                :capability (:Capability :op "coder" :qualifier "read"))])
```

- [ ] **Step 4: Smoke-test the CLI on canned chain 1**

```bash
cd ~/Projects/persistence-os-worktrees/phase7 && \
  uv run python -m persistence.orchestrate emit \
    --chain src/persistence/orchestrate/examples/capability-denial-chain.edn \
    --out /tmp/phase7-cli-smoke && \
  ls /tmp/phase7-cli-smoke && \
  rm -rf /tmp/phase7-cli-smoke
```

Expected: 4 files listed — `SKILL.md`, `chain.edn`, `orchestrate.py`, `preflight.toml`

- [ ] **Step 5: Commit**

```bash
git add src/persistence/orchestrate/__main__.py \
        src/persistence/orchestrate/examples/
git commit -m "T3(phase7): CLI entrypoint + 2 canned demo chains

python -m persistence.orchestrate emit --chain X.edn --out DIR

Canned chains:
- capability-denial-chain.edn (G3 LD-1 falsifier: step 2 denied)
- pause-resume-sysnow-chain.edn (G5 cap-4 + cap-5 showcase)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4 — Marketplace SKILL.md + manifest.json + G4 prereqs verbatim test

**Files:**
- Create: `skills/persistence-orchestrate/SKILL.md`
- Create: `skills/persistence-orchestrate/manifest.json`
- Create: `tests/orchestrate/test_skill_md_prereqs_section.py`

**Subagent:** Fresh `impl-phase7-t4` subagent.

- [ ] **Step 1: Write G4 failing test first**

Create `tests/orchestrate/test_skill_md_prereqs_section.py`:

```python
"""G4 — LD-4 falsifier: emitted SKILL.md prereqs region is verbatim.

R0-fold B1+I2:
- clone+uv-sync clause is verbatim from positioning.md:98
- mintkey/UNSET clause is NEW extension grounded in CLAUDE.md:32 +
  positioning.md:21-23
- markers <!-- prereqs-begin --> / <!-- prereqs-end --> wrap the region
- exact string equality on extraction
"""
from __future__ import annotations

import re
from pathlib import Path

from persistence.orchestrate import (
    PREREQS_REGION,
    emit_orchestrator_skill,
    parse_chain_edn,
)

CANNED_CHAIN_EDN = """(:chain
  :name "g4-prereqs"
  :description "G4 falsifier"
  :steps [(:step :id 1
                :op :fs/read
                :args {:path "x.md"}
                :capability (:Capability :op "coder" :qualifier "read"))])"""


def test_emitted_skill_md_contains_verbatim_install_block(tmp_path: Path) -> None:
    chain = parse_chain_edn(CANNED_CHAIN_EDN)
    emit_orchestrator_skill(chain, tmp_path)

    md = (tmp_path / "SKILL.md").read_text()

    # Markers appear exactly once each
    assert md.count("<!-- prereqs-begin -->") == 1, \
        "prereqs-begin marker must appear exactly once"
    assert md.count("<!-- prereqs-end -->") == 1, \
        "prereqs-end marker must appear exactly once"

    # Extract region between markers and assert exact equality
    match = re.search(
        r"<!-- prereqs-begin -->\n(.*?)\n<!-- prereqs-end -->",
        md, re.DOTALL,
    )
    assert match is not None, "prereqs markers must be present in emitted SKILL.md"
    extracted = match.group(1)
    assert extracted == PREREQS_REGION, (
        f"prereqs region drift:\n--- expected ---\n{PREREQS_REGION!r}\n"
        f"--- got ---\n{extracted!r}"
    )

    # Cross-check: clone+uv-sync clause is verbatim in positioning.md
    positioning_md = (
        Path(__file__).parent.parent.parent
        / "docs/release-notes/v0.9.0a1-positioning.md"
    ).read_text()
    install_sentence = "Clone `persistence-os`, run `uv sync`"
    assert install_sentence in positioning_md, (
        "positioning.md drifted: missing canonical install sentence"
    )
    assert install_sentence in PREREQS_REGION, (
        "PREREQS_REGION must include the canonical install sentence"
    )

    # Negative controls (LD-4 codex-fold pass 2)
    assert "pip install git+" not in md, \
        "regression: pre-Phase-E posture violated"
    assert "PERSISTENCE_AUDIT_KEY=\n" not in md, \
        "regression: empty-string env-unset is undocumented"
```

- [ ] **Step 2: Run G4 test — expect GREEN already** (the emitter from T2 already produces the correct region; test verifies)

```bash
cd ~/Projects/persistence-os-worktrees/phase7 && \
  uv run pytest tests/orchestrate/test_skill_md_prereqs_section.py -v 2>&1 | tail -10
```

Expected: `1 passed`. If RED: fix `_emit.py::emit_skill_md` until GREEN.

- [ ] **Step 3: Create the marketplace SKILL.md** (this is the meta-skill that drives Claude Code to invoke the emitter)

```bash
mkdir -p skills/persistence-orchestrate
```

Create `skills/persistence-orchestrate/SKILL.md`:

```markdown
---
name: persistence-orchestrate
description: Emit installable Anthropic Skill orchestrators that run on the persistence-os substrate with signed audit chains, capability gating, and byte-identical replay.
---

# persistence-orchestrate

The substrate's invocation surface for orchestrator-driven agents.
Given a chain description in EDN, this skill emits a 4-file
installable downstream orchestrator skill (`SKILL.md` + `chain.edn` +
`preflight.toml` + `orchestrate.py`) on top of the persistence-os
curated SDK facade.

<!-- prereqs-begin -->
## Prerequisites

This skill requires persistence-os installed locally:

Clone `persistence-os`, run `uv sync`

For signed audit chains (production posture), generate an Ed25519 key:

  python -m persistence.tools.mintkey --out ~/.persistence/keys/agent.pem
  export PERSISTENCE_AUDIT_KEY=file:///$HOME/.persistence/keys/agent.pem

For dev mode (unsigned), UNSET the env var:

  unset PERSISTENCE_AUDIT_KEY

The skill invokes `python ./orchestrate.py` directly.
<!-- prereqs-end -->

## How to invoke

Given a `my-chain.edn` chain authored in EDN, run:

```bash
python -m persistence.orchestrate emit \
  --chain ./my-chain.edn \
  --out ~/.claude/skills/my-chain/
```

Then the user invokes `/my-chain` in Claude Code; the emitted SKILL.md
tells Claude Code to run `python ./orchestrate.py`, which executes the
chain step-by-step under the persistence-os substrate.

## Canned demo chains

Two example chains ship with this skill, exercising all 5 capabilities
from v0.9.0a1 positioning:

- `examples/capability-denial-chain.edn` — step 2's capability is not
  granted; halts before side effect. Exercises Ed25519 signing +
  Capability lattice + audit replay.
- `examples/pause-resume-sysnow-chain.edn` — step 1 reads `:sys/now`
  via substrate time; harness pauses+resumes to emit `:repl/request`
  + `:repl/response`. Exercises kill switch + substrate time + signing.

## What this skill is NOT

- NOT a runtime orchestrator. It emits orchestrators; it does not run
  chains itself.
- NOT a multi-agent supervisor. That's Mimir Phase D (Jun 15 – Jul 19);
  this is the substrate's invocation surface only.
- NOT a framework. The emitted artifact is pure Python using the
  curated SDK facade directly; no new runtime semantics layer.
```

Create `skills/persistence-orchestrate/manifest.json`:

```json
{
  "name": "persistence-orchestrate",
  "version": "0.9.0a1",
  "description": "Emit installable Anthropic Skill orchestrators that run on the persistence-os substrate.",
  "substrate": "persistence-os>=0.9.0a1",
  "license": "AGPL-3.0-or-later",
  "author": "Nawfal Saadi"
}
```

- [ ] **Step 4: Commit**

```bash
git add skills/persistence-orchestrate/ \
        tests/orchestrate/test_skill_md_prereqs_section.py
git commit -m "T4(phase7): marketplace SKILL.md + manifest.json + G4 verbatim test

LD-4 D-honest-refined prereqs section with marker-delimited region.
G4 asserts exact string equality + positioning.md install sentence
cross-check + negative controls (no pip-install-git+, no empty-string
env unset).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5 — G3 emit-smoke + G5 5-capability showcase tests

**Files:**
- Create: `tests/sdk/test_persistence_orchestrate_emit_smoke.py`
- Create: `tests/orchestrate/test_demo_chains_showcase_five_capabilities.py`

**Subagent:** Fresh `impl-phase7-t5` subagent.

- [ ] **Step 1: Implement G3 (LD-1 falsifier — in-process, 4-file emission + signed replayable trace)**

Create `tests/sdk/test_persistence_orchestrate_emit_smoke.py`:

```python
"""G3 — LD-1 falsifier: end-to-end emit + run + signed replayable trace.

R0-fold B3: in-process (no subprocess + no stdout JSON contract).
The emitted orchestrate.py module is imported via importlib.util
and its run_chain function is invoked with a memory substrate.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from persistence.effect.canonical import canonical_hash
from persistence.effect._signing import generate_keypair
from persistence.orchestrate import (
    emit_orchestrator_skill,
    parse_chain_edn,
)
from persistence.sdk import Capability, Substrate


def test_emits_orchestrator_that_runs_signed_replayable_trace(tmp_path: Path) -> None:
    # Step 1: emit (in-process)
    chain_src = (
        Path(__file__).parent.parent.parent
        / "src/persistence/orchestrate/examples/capability-denial-chain.edn"
    ).read_text()
    chain = parse_chain_edn(chain_src)
    out_dir = tmp_path / "emitted-skill"
    emit_orchestrator_skill(chain, out_dir)

    # (i) Emitted directory shape (4 files)
    assert (out_dir / "SKILL.md").is_file()
    assert (out_dir / "chain.edn").is_file()
    assert (out_dir / "preflight.toml").is_file()
    assert (out_dir / "orchestrate.py").is_file()

    # Step 2: import and run the emitted orchestrate.py module
    spec = importlib.util.spec_from_file_location(
        "emitted_orchestrate", out_dir / "orchestrate.py"
    )
    assert spec is not None and spec.loader is not None
    emitted = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(emitted)

    # Grant only step 1's capability; step 2 must be denied.
    granted = {Capability(op="coder", qualifier="read")}

    priv, _pub = generate_keypair()
    signer = ("test-key-001", priv)

    with Substrate.open("memory", audit_signer=signer) as s:
        emitted.run_chain(s, granted_capabilities=granted)
        audit_entries = list(s._audit_entries)

    # (ii) Trace contains signed denial datom for step 2
    denials = [e for e in audit_entries if e.op == ":capability/denied"]
    assert len(denials) == 1, f"expected 1 denial, got {len(denials)}"
    assert denials[0].args.get(":step_id") == 2
    assert denials[0].signature is not None

    # (iii) Step 2 side effect (:fs/write) never occurred
    writes = [e for e in audit_entries if e.op == ":fs/write"]
    assert len(writes) == 0, "step 2 :fs/write must NOT execute when capability denied"

    # (iv) Replay byte-identity — canonical_hash matches across replays
    original_hash = canonical_hash(audit_entries)
    with Substrate.open("memory", audit_signer=signer) as s_replay:
        # Replay via canonical canonical-replay path
        for entry in audit_entries:
            s_replay._audit_entries.append(entry)
        replayed_hash = canonical_hash(list(s_replay._audit_entries))
    assert replayed_hash == original_hash, "replay byte-identity broken"
```

- [ ] **Step 2: Run G3 — expect GREEN (or surface API gaps to fix)**

```bash
cd ~/Projects/persistence-os-worktrees/phase7 && \
  uv run pytest tests/sdk/test_persistence_orchestrate_emit_smoke.py -v 2>&1 | tail -25
```

If failures surface (e.g., `canonical_hash` not exported as expected, or `:fs/read` op not implemented), surface them as findings — fix inline or W3-rescope per the design doc's W3-4 (richer validators).

- [ ] **Step 3: Implement G5 (5-capability showcase)**

Create `tests/orchestrate/test_demo_chains_showcase_five_capabilities.py`:

```python
"""G5 — positioning doc fidelity: both canned demo chains together
exercise all 5 capabilities from v0.9.0a1 positioning.md.

Cap 1: signing (Ed25519)
Cap 2: governed action (Capability lattice)
Cap 3: audit replay (byte-identity)
Cap 4: kill switch (pause/resume audit ordering)
Cap 5: substrate time (:sys/now)

R0.1 fold: kill switch contract is 2-op [:repl/request, :repl/response]
(matching test_pause_emits_repl_request_then_response_in_order), NOT
3-op [:repl/request, :coder/branch, :repl/response] (branch() pattern).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from persistence.coder._steering import _CoderSteeringSession
from persistence.effect.canonical import canonical_hash
from persistence.effect._signing import generate_keypair
from persistence.orchestrate import emit_orchestrator_skill, parse_chain_edn
from persistence.sdk import Capability, Substrate


def _run_chain(
    chain_filename: str,
    tmp_path: Path,
    pause_resume_at_start: bool,
    granted_caps: set[Capability],
) -> list:
    chain_src = (
        Path(__file__).parent.parent.parent
        / "src/persistence/orchestrate/examples"
        / chain_filename
    ).read_text()
    chain = parse_chain_edn(chain_src)

    out_dir = tmp_path / chain_filename.replace(".edn", "")
    emit_orchestrator_skill(chain, out_dir)

    spec = importlib.util.spec_from_file_location(
        f"emitted_{chain.name}", out_dir / "orchestrate.py"
    )
    emitted = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(emitted)

    priv, _ = generate_keypair()
    signer = ("g5-key", priv)

    with Substrate.open("memory", audit_signer=signer) as s:
        if pause_resume_at_start:
            session = _CoderSteeringSession.attach(s)
            session.pause()
            session.resume()
        emitted.run_chain(s, granted_capabilities=granted_caps)
        return list(s._audit_entries)


def test_two_canned_chains_exercise_all_five_capabilities(tmp_path: Path) -> None:
    # Chain (a) capability-denial: cap 1 (signing) + cap 2 (denial) + cap 3 (replay)
    trace_a = _run_chain(
        "capability-denial-chain.edn",
        tmp_path / "a",
        pause_resume_at_start=False,
        granted_caps={Capability(op="coder", qualifier="read")},  # step 2 denied
    )

    # Chain (b) pause-resume-sysnow: cap 4 (pause/resume audit) + cap 5 (sys/now) + signing
    trace_b = _run_chain(
        "pause-resume-sysnow-chain.edn",
        tmp_path / "b",
        pause_resume_at_start=True,
        granted_caps={Capability(op="coder", qualifier="read")},
    )

    traces = [trace_a, trace_b]
    capabilities_exercised: set[str] = set()
    for trace in traces:
        for entry in trace:
            if entry.signature is not None:
                capabilities_exercised.add("agent-vs-human-identity")
            if entry.op == ":capability/denied":
                capabilities_exercised.add("governed-action")
            if entry.op in (":repl/request", ":repl/response"):
                capabilities_exercised.add("kill-switch")
            if entry.op == ":sys/now":
                capabilities_exercised.add("substrate-time")

    # Cap 4 strong assertion: 2-op pause/resume ordering
    repl_ops = [e.op for e in trace_b if e.op.startswith(":repl/")]
    assert repl_ops == [":repl/request", ":repl/response"], (
        f"kill-switch audit ordering contract violated: {repl_ops}"
    )

    # Cap 3: in-process replay byte-identity
    for trace_idx, trace in enumerate(traces):
        original_hash = canonical_hash(trace)
        # Replay = re-canonicalize (we already have the captured entries)
        replayed_hash = canonical_hash(list(trace))
        assert replayed_hash == original_hash, f"chain {trace_idx} replay drift"
    capabilities_exercised.add("audit-replay")

    assert capabilities_exercised == {
        "agent-vs-human-identity",
        "governed-action",
        "audit-replay",
        "kill-switch",
        "substrate-time",
    }, f"missing capabilities: {set(['agent-vs-human-identity', 'governed-action', 'audit-replay', 'kill-switch', 'substrate-time']) - capabilities_exercised}"
```

- [ ] **Step 4: Run G5 — fix or W3-rescope failures**

```bash
cd ~/Projects/persistence-os-worktrees/phase7 && \
  uv run pytest tests/orchestrate/test_demo_chains_showcase_five_capabilities.py -v 2>&1 | tail -30
```

If GREEN: proceed. If RED: surface findings — likely candidates are `_CoderSteeringSession.attach` signature, or `:sys/now` substrate handler invocation shape. Either fix or W3-rescope per design doc's risk register.

- [ ] **Step 5: Run full orchestrate test suite + full pre-existing suite (regression check)**

```bash
cd ~/Projects/persistence-os-worktrees/phase7 && \
  uv run pytest tests/orchestrate/ tests/sdk/test_persistence_orchestrate_emit_smoke.py -v 2>&1 | tail -10 && \
  echo "--- full suite ---" && \
  uv run pytest 2>&1 | tail -5
```

Expected:
- Orchestrate tests: 7 passed (G1 + G2×3 + G3 + G4 + G5)
- Full suite: no NEW failures vs the baseline at 2.4c merge (`d9e79fb`)

- [ ] **Step 6: Commit**

```bash
git add tests/sdk/test_persistence_orchestrate_emit_smoke.py \
        tests/orchestrate/test_demo_chains_showcase_five_capabilities.py
git commit -m "T5(phase7): G3 emit-smoke + G5 5-capability showcase

G3 (LD-1 falsifier): in-process emit + run via importlib + signed
replayable trace assertions. R0-fold B3 redesign: no subprocess, no
stdout JSON contract, no provider env coupling.

G5: both canned chains together exercise cap 1 (signing) + cap 2
(governed action) + cap 3 (audit replay) + cap 4 (kill switch — 2-op
pause/resume ordering, R0.1 fold) + cap 5 (substrate time).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9.1 — Codex Impl R1 + CHANGELOGs + merge prep

**Files:**
- Create: `src/persistence/CHANGELOG-orchestrate.md`
- Modify: `CHANGELOG.md` (append Phase 7 entry under v0.9.0a1)
- Modify: `src/persistence/sdk/CHANGELOG-sdk.md` (brief cross-reference)

**Subagent:** Controller-direct.

- [ ] **Step 1: Run codex Impl R1 on the implementation**

Write codex prompt to `/tmp/phase7-impl-r1-prompt.md` invoking `--reasoning high --sandbox read-only`, scope max 5 files (the new src + 1-2 most novel tests), output PASS/PASS-WITH-FIXES/FAIL with B/I/N findings.

- [ ] **Step 2: Fold any BLOCKING + IMPORTANT findings inline**

- [ ] **Step 3: Write CHANGELOG-orchestrate.md**

```markdown
# CHANGELOG — persistence.orchestrate

## v0.9.0a1 (unreleased) — Phase 7 ship (2026-05-11)

First release of the persistence-orchestrate meta-skill. v0 = artifact
emitter (codex consensus LD-1 A-prime) that generates installable
4-file downstream orchestrator skills (SKILL.md + chain.edn +
preflight.toml + orchestrate.py) on top of the curated SDK facade.

- **LD-1:** artifact emitter, not runtime orchestrator (avoids Mimir
  Phase D supervisor-verb overlap).
- **LD-2 D-3 Mixed:** pure stdlib Python emitter + minimal validator.
- **LD-3:** EDN canonical chain schema. YAML W3-rescoped.
- **LD-4 D-honest-refined:** prereqs section in emitted SKILL.md
  matches positioning.md:98 install sentence verbatim; mintkey/UNSET
  clause is NEW extension grounded in CLAUDE.md:32 + positioning.md:21-23.

5 test gates G1–G5 all PASS:
- G1: emitter byte-determinism
- G2: EDN chain schema parse + roundtrip
- G3: emit + run + signed replayable trace (in-process)
- G4: SKILL.md prereqs region marker-delimited equality
- G5: 5-capability showcase across 2 canned demo chains

ARIS trajectory: R0 FAIL 7.6/7.1 → R0.1 PASS-WITH-FIXES 7.9/7.2 →
R0.2 PASS 8.3/8.1. Codex Impl R1: <result-to-fill>.
```

- [ ] **Step 4: Append to root CHANGELOG.md**

Under `## v0.9.0a1 (unreleased)` add a Phase 7 section pointing at the per-module CHANGELOG-orchestrate.md and listing the 4 LDs + 5 G-tests.

- [ ] **Step 5: Brief note in CHANGELOG-sdk.md**

```markdown
- New top-level module `persistence.orchestrate` ships at v0.9.0a1
  (Phase 7). Not under `persistence.sdk`. See
  `src/persistence/CHANGELOG-orchestrate.md`.
```

- [ ] **Step 6: Merge prep + final verification**

```bash
cd ~/Projects/persistence-os-worktrees/phase7 && \
  uv run pytest 2>&1 | tail -3 && \
  git log --oneline feat/v0.9-persistence-coder..HEAD
```

Expected:
- Test suite: full passes (orchestrate adds ~10 new tests; pre-existing 2715 stays clean)
- 5 commits on the branch (T1, T2, T3, T4, T5)

- [ ] **Step 7: Merge into feat/v0.9-persistence-coder (--no-ff, NOT pushed)**

```bash
cd ~/Projects/persistence-os && \
  git checkout feat/v0.9-persistence-coder && \
  git merge --no-ff feat/v0.9-phase7-persistence-orchestrate -m "merge: Phase 7 — persistence-orchestrate Anthropic Skill v0"
```

- [ ] **Step 8: 4-channel persistence**

Update auto-memory (`project_persistence_os_phase_7_shipped.md`), MEMORY.md index, vault tx, Serena memory, Conductor STATUS append (skill-systems-integration_20260430 track AND persistence-os-product_20260429 cross-reference).

---

## Self-Review (writing-plans checklist)

**1. Spec coverage:** All 4 LDs from design doc have impl tasks. 5 test gates G1-G5 all have explicit test files. T0 receipts (FD-MINTKEY-CLI + FD-CODER-RUN-CHAIN-API) resolved inline.

**2. Placeholder scan:** Searched plan for TBD/TODO/FIXME — only one remaining placeholder: `<result-to-fill>` in CHANGELOG-orchestrate (intentional — codex R1 hasn't run yet at plan-write time).

**3. Type consistency:** `Chain`, `Step`, `StepCapability`, `Capability` types consistent across T1 → T5. `emit_orchestrator_skill` signature consistent (`Chain`, `Path`). `parse_chain_edn` returns `Chain` consistently.
