# persistence.coder CHANGELOG

## v0.9.0a1 (unreleased) — Phase 2.1b `_decide` body + first `:llm/*` datoms

Phase 2.1b fills the `_decide` method — the first behavioral method
on the persistence-coder skeleton. Three substrate-side handler
factories ship under `persistence.effect.handlers.{anthropic,
claude_code, callable}`. The CLI gains `--provider` + `--model` flags;
provider auto-detection prefers `claude-code` (Max subscription) over
`anthropic` (paid API) over an `echo` floor.

### Added

- **`Coder._decide` body** (`_session.py`). Calls
  `s.effect.perform(":llm/call", ...)` with a single tool exposed —
  `EMIT_DECISION_TOOL_SCHEMA` (LD4 decision/action split). Two-tier
  parsing: tool-use → text-fenced fallback → missing-confidence
  default last (LD3). Transacts `:llm/messages` BEFORE the call (so
  provenance survives even if the call raises) and `:llm/decision`
  AFTER parsing. The decision datom carries an FK `source_call`
  back to the matching `:llm/messages` entity-id; full provenance
  materializable via `s.fact.q` joins.
- **`LLMDecision` dataclass body** (`_types.py`). Three frozen
  fields — `kind: Literal["act","plan","branch"]`, `confidence:
  float`, `payload: Mapping[str, Any]`. Payload shape is loose in
  2.1b; tightens 2.2a/2.3a/2.3b.
- **`Coder.model` field** (`_session.py`). Default `"claude-opus-4-7"`;
  CLI `--model` overrides.
- **`_prompt.py`** — `EMIT_DECISION_TOOL_SCHEMA` (the only tool
  exposed to the LLM in 2.1b), `build_messages(task, obs)`,
  `parse_text_decision(text)` (tier-2 envelope parser, total
  function — never raises on malformed input).
- **`_provider.py`** — `detect_or_explicit(provider)` for CLI
  auto-detection (LD6: `claude-code` → `anthropic` → `echo` ordering)
  and explicit-provider validation. `_claude_code_available()` is
  importability-only; signed-out state surfaces lazily on first call
  per R1 F8.
- **CLI flags `--provider {auto,anthropic,claude-code}` and
  `--model <id>`** (`_cli.py`). `--confidence-threshold` still
  deferred to 2.3b/2.4a per CP2.
- **`__main__.py` provider install**. After `Substrate.open(...)`,
  installs the chosen handler at `position="bottom"` via
  `s.effect.install_handler(...)`. Zero `s.escape.*` callsites in
  `src/persistence/coder/` (LD7 — Q2 preserved literally).

### First datoms emitted

- `:llm/messages` — entity per call. Value is a canonical-JSON
  serialized dict `{messages, tools, model}`. Honors the 2.1a
  CHANGELOG promise verbatim.
- `:llm/decision` — entity per parsed decision. Value is a
  canonical-JSON serialized dict `{kind, confidence, payload,
  parsed_via, source_call}`. The `parsed_via` field is one of
  `"tool_use"` / `"text_fallback"` / `"missing_default"` —
  substrate-side observable signal for "did the LLM behave?"
  without re-parsing logs.

### LD5 — `run()` body deferred to 2.2a

The 2.1a CHANGELOG-coder hint said "2.1b widens one-iter → while-loop".
**Amended in 2.1b.** Loop widening lands in 2.2a when `_observe`/`_act`
have substance to iterate over. Widening in 2.1b would either bypass
`_observe` (architectural drift) or wrap its raise in try/except
(masks the 2.2a stub — defeats the skeleton's audit-friendliness).
`_decide` is exercised via direct unit tests in 2.1b, NOT via
`run()` (which still raises `CoderStubNotImplemented("Phase 2.2a —
substrate read via s.fact.q")` on the first call).

### Test surface

- `tests/coder/test_decide.py` (~13 tests) — comprehensive `_decide`
  coverage: tier 1/2/3 paths, datom emission ordering, FK linkage,
  malformed tool-call fallthrough, provenance-on-failure, AST G6
  decision/action split assertion, Hypothesis G2.1b-a property
  (`_parse_decision` total function over 200 generated catalog
  responses), R5 invariant (`missing_confidence_default <
  confidence_threshold`).
- `tests/coder/test_provider_detection.py` (~7 tests) — G2.1b-c
  auto-detection matrix + explicit-provider error paths.
- `tests/coder/test_prompt_schema.py` (~17 tests) — schema shape,
  text-parser parametric (3 valid + 9 invalid envelope shapes).
- `tests/coder/test_decide_replay.py` (1 test) — G3 byte-identity.
- `tests/coder/test_main_provider_install.py` (4 tests) —
  subprocess-based stderr UX + exit-code coverage.
- `tests/coder/test_types.py` (6 tests) — LLMDecision shape.
- `tests/coder/test_cli_args.py` (6 tests) — `--provider` / `--model`
  argparse coverage.
- `tests/effect/handlers/test_callable_handler.py` (4 tests).
- `tests/effect/handlers/test_anthropic_handler.py` (3-4 tests).
- `tests/effect/handlers/test_claude_code_handler.py` (3 tests).
- `tests/effect/handlers/test_provider_translation_contract.py`
  (6 parametric tests, G5).
- `tests/effect/test_audit_stack_llm_call.py` (6 tests, G4).
- `tests/sdk/test_effect_namespace.py` (5 tests, install_handler).

Suite delta `+57 / 35 skipped / 8 xfailed` (2,108 → ~2,165). Pyright
`0 errors / 0 warnings / 0 info` on touched files.

### Notes

- Substrate prereqs land in this same sub-phase per LD7 (R1 fix-pass):
  curated `s.effect.install_handler` (replaces non-existent
  `s.escape.effect.push`); `CANONICAL_AUDIT_OPS` split into
  `_WRAPPED_OPS` (audit middleware wraps — includes `:llm/call`)
  and `_RAW_OPS` (raw terminator covers — excludes `:llm/call`).
  The split prevents the raw terminator from masking the LLM
  provider handler. See `CHANGELOG-sdk.md` and `CHANGELOG-effect.md`.
- The LLM never sees `:fs/`, `:shell/`, `:code/`, `:git/` tool
  surfaces in 2.1b — only `emit_decision`. Real effect tools land
  in 2.2a; the substrate routes intents in `_act` (2.2a) /
  `_escalate_plan` (2.3a) / `_escalate_branch` (2.3b).
- Mode 3 callable handler (`make_callable_llm_handler`) ensures
  persistence-coder is NOT Claude-specific. Any host (Codex,
  Cursor, juba, Ollama / vLLM / local LLMs) can wire its LLM access
  by passing a `call_fn` that translates its vendor's response into
  the catalog wire shape. ~30 LOC, zero new deps.

---

## v0.9.0a1 (unreleased) — Phase 2.1a `persistence.coder` skeleton

Phase 2.1a lands the persistence-coder skeleton — the FIRST agent
built ON the v0.8.5a1 substrate. Consumer-side module: imports from
`persistence.sdk` only, never from raw substrate modules.

### Added

- **`Coder` class** (`_session.py`). `@dataclass` with substrate
  dependency-injected per design LD2 (callers own substrate
  lifecycle; `repl/_session.py` precedent). Six method ReAct loop
  shape from base design § 3.4: `_observe` → `_decide` →
  `_should_escalate_branch` / `_escalate_branch` /
  `_should_escalate_plan` / `_escalate_plan` → `_act` →
  `_check_pause`. Every method body is a `raise CoderStubNotImplemented(...)`
  tagged with the downstream sub-phase that fills it. Class
  attributes `confidence_threshold = 0.65` and
  `missing_confidence_default = 0.5` from base § 3.4 (CLI flag
  deferred to 2.3b/2.4a per design CP2).
- **`CoderStubNotImplemented`** (`_session.py`). `NotImplementedError`
  subclass — the Phase 2.1a skeleton sentinel. `__main__.py` catches
  this subtype only, so real `NotImplementedError` raised by 2.1b+
  implementation code (e.g. an LLM-provider abstract method that
  isn't overridden) propagates as a genuine failure rather than
  being banner-masked. ARIS R1 fix-1 (codex hard-mode review of
  design doc 2026-05-03; mean 8.0 / min 7.6).
- **`Observation` / `LLMDecision` value-shape dataclasses**
  (`_types.py`). Empty frozen dataclasses in 2.1a so type hints in
  `_session.py` resolve; fields land in 2.1b (LLMDecision) and 2.2a
  (Observation) when wire shapes stabilize.
- **CLI entry** (`__main__.py` + `_cli.py`). `python -m persistence.coder
  --task "..." [--db-path <uri>]`. argparse-based per `repl/_cli.py`
  precedent (no click/typer dep — yagni). `--db-path` defaults to
  `None` → bare-string `"memory"` URI to `Substrate.open()` (per
  design CP1, verified against `_facade.py:1354-1442`) plus a stderr
  warning. On `CoderStubNotImplemented`, prints
  `persistence-coder skeleton: <phase-tag> — <purpose>` to stderr
  and exits 1.

### Notes

- **Zero datom emissions in 2.1a.** Substrate at exit is byte-identical
  to a fresh substrate. 2.1b lands the first datoms (`:llm/messages`,
  `:llm/decision`).
- **Zero `s.escape.*` callsites.** Three AST-guard smoke greps from
  design § 6.1 (G1.A no-raw-substrate-imports / G1.B no-`.escape`
  regardless of alias / G1.C no allowed-set callsites) return zero
  matches. The 2.1c lockfile contract test (Wed 2026-05-06) replaces
  the smoke greps with a load-bearing AST walk.

### Test surface

`tests/coder/test_session_stubs.py` (5 functions / 12 invocations
including 8 parametrized stub-tag checks) + `tests/coder/test_cli_smoke.py`
(3 subprocess-driven CLI invocations). Suite delta `+15 / 33 skipped /
8 xfailed` (2,093 → 2,108). Pyright `0 errors / 0 warnings / 0 info`
on touched files.
