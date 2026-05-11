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
