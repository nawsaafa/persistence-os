# CHANGELOG — `persistence.orchestrate`

## v0.9.0a1 (unreleased) — Phase 7 ship (2026-05-11)

First release. Ships `persistence.orchestrate` as the meta-skill that emits
installable downstream orchestrator skills on top of the curated SDK facade.
This is the substrate's invocation surface for orchestrator-driven agents per
`CLAUDE.md:65-66`.

### What ships

- `persistence.orchestrate._schema` — EDN canonical chain schema parser +
  serializer. `Chain` / `Step` / `StepCapability` dataclasses; args-dict keys
  normalised to bare strings (T1.1 fix to match `s.effect.perform` convention
  per `tests/effect/test_audit_signer_env.py:131`).
- `persistence.orchestrate._emit` — pure-stdlib emit functions that produce a
  4-file orchestrator skill from a `Chain`:
  - `SKILL.md` — marketplace artifact with marker-delimited prereqs region
  - `chain.edn` — canonical EDN serialization of the chain
  - `preflight.toml` — capabilities + ops manifest (mirrors the 2.4c
    `tests/preflight_manifest.toml` schema)
  - `orchestrate.py` — pure-Python runner using `persistence.sdk.Substrate`
    + `persistence.sdk.Capability`. R1-fold B3 rewrite: installs only
    targeted handlers (`make_echo_llm_handler` at bottom for `:llm/call`,
    `:capability/denied` audit/terminator at top). NO wildcard terminator
    masking — arbitrary missing ops raise through the canonical stack.
- `persistence.orchestrate.__main__` — CLI: `python -m persistence.orchestrate
  emit --chain <path> --out <dir>`.
- `persistence.orchestrate.examples/` — 2 canned demo chains:
  `capability-denial-chain.edn` (G3 LD-1 falsifier; step 2 denied at
  capability gate) and `pause-resume-sysnow-chain.edn` (G5 cap-1+3 base; G5
  harness demonstrates cap-4 and cap-5 via direct substrate calls).
- `persistence.tools.mintkey` — thin Ed25519 PEM mint CLI: `python -m
  persistence.tools.mintkey --out <path>`. Wraps
  `persistence.effect._signing.generate_keypair()` (~70 LOC); resolves
  FD-MINTKEY-CLI inline at T1 so the LD-4 prereqs text is honoured by a
  real binary.

### Marketplace artifact

- `skills/persistence-orchestrate/SKILL.md` — Anthropic Skill markdown with
  the same canonical PREREQS_REGION emitted into downstream skills
  (verbatim install sentence from `docs/release-notes/v0.9.0a1-positioning.md:98`).
- `skills/persistence-orchestrate/manifest.json` — Skills metadata
  (substrate dep `persistence-os>=0.9.0a1`).

### Test gates G1-G5 (all PASS at ship)

- **G1** `tests/orchestrate/test_emitter_determinism.py` — byte-identical
  4-file tree across runs (LD-2 D-3 falsifier).
- **G2** `tests/orchestrate/test_chain_schema.py` — EDN roundtrip + YAML
  rejection + missing-field error (LD-3 falsifier, 3 tests).
- **G3** `tests/sdk/test_persistence_orchestrate_emit_smoke.py` — end-to-end
  emit + in-process import via `importlib.util` + run twice in fresh
  `Substrate.open("memory", audit_signer=...)` contexts under pinned clock
  + compare canonical hashes (R1-fold B1: replay byte-identity now genuinely
  falsifiable).
- **G4** `tests/orchestrate/test_skill_md_prereqs_section.py` — marker-delimited
  region extraction + exact string equality against `PREREQS_REGION`
  constant + positioning.md cross-check + negative controls (no
  `pip install git+`, no empty-string env unset).
- **G5** `tests/orchestrate/test_demo_chains_showcase_five_capabilities.py` —
  5-capability showcase: cap-1+2+3 via chain runs (signing + capability
  denial + replay byte-identity); cap-4 via direct
  `_CoderSteeringSession.pause/resume` (`:repl/request` + `:repl/response`
  audit ordering matching positioning.md:78-80); cap-5 via direct
  `substrate.effect.perform(":sys/now", {})` returning `datetime.datetime`.

### Architectural decisions (LDs)

- **LD-1 (codex consensus REJECT-FOR-NEW-OPTION-A-PRIME):** artifact emitter,
  not runtime orchestrator. The emitted `orchestrate.py` IS the "thin
  orchestrator that calls the curated SDK surface"; persistence-os stays
  substrate.
- **LD-2 (codex DISAGREE-STRONG → D-3 Mixed):** pure stdlib Python emitter
  with deterministic byte output. No Jinja2, no LLM-driven codegen.
- **LD-3 (codex DISAGREE-WEAK → EDN canonical):** chain schemas are authored
  in EDN. v0 is EDN-only; YAML W3-rescoped (W3-5).
- **LD-4 (codex 2 passes; D-honest-refined):** emitted SKILL.md has explicit
  prereqs region (marker-delimited). Install sentence verbatim from
  positioning.md:98; mintkey/UNSET clause grounded in CLAUDE.md:32 +
  positioning.md:21-23.

### ARIS trajectory

- Design: R0 FAIL 7.6/7.1 → R0.1 PASS-WITH-FIXES 7.9/7.2 → R0.2 PASS 8.3/8.1.
- Impl R1: FAIL 6.6/5.4 (3 BLOCKING + 3 IMPORTANT + 1 NICE).
- Impl R1-fold: substrate-truth rewrite of emitter (dropped non-canonical
  view-op handler + wildcard terminator masking); G3/G5 redesigned to actually
  replay; `Capability` re-exported from `persistence.sdk`; lazy `WSServer` in
  `persistence.repl/__init__.py` to keep wheel-distribution smoke (2.4c G1)
  passing without aiohttp in fresh-venv installs.
- Impl R1.2 lite: **PASS 8.3/7.8**. All 6 findings CLOSED. Phase 7 FROZEN.

### Side effects on other modules

- `src/persistence/sdk/__init__.py` — added `Capability` and `CapabilitySet`
  re-export (closes I1).
- `src/persistence/repl/__init__.py` — lazy `WSServer` via PEP 562
  `__getattr__` (substrate-side fix enabling I1 without breaking the 2.4c
  wheel-distribution G1 smoke).

### W3 rescopes queued

- **W3-1:** marketplace publishing automation (manual publish in Phase 7
  per skill-systems plan.md:128; Phase 7.1 ships tooling).
- **W3-2:** PyPI-aware prereqs swap (once Phase E ships PyPI release ~Jul 20).
- **W3-3:** NL → EDN chain description parsing (v0.10.x).
- **W3-4:** richer emitter validators (op-existence preflight against
  CANONICAL_AUDIT_WRAPPED_OPS; capability lattice validation).
- **W3-5:** YAML sugar input compiled to canonical EDN.
- **W3-6:** multi-chain composition with named handoffs.
- **W3-7:** Mimir Phase D handoff doc (boundary between emitted skills and
  Mimir supervisor verbs spawn/train/watch/halt/branch/replay/verify/export).
