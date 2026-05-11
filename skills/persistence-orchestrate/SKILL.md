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
