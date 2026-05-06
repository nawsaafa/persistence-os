# persistence.coder CHANGELOG

## Phase 2.2b — 2026-05-06 (LD3 latest-action prompt widening + `_act` coverage of `:code/run` + `:git/*`)

Phase 2.2b extends the coder's prompting and dispatch surface to the new
`:code/run` and `:git/{diff,status,log,commit}` ops shipped in
`persistence.effect` this phase. The `build_messages` body grows an LD3
"latest action" block so the LLM is no longer blind on its own outputs,
and the `_act` body from 2.2a now has falsifiable test coverage of every
new op via isolated substrate fixtures.

### Added

- **LD3 `build_messages` widening** (`_prompt.py`). New helper
  `_render_latest_action(action: Mapping[str, Any]) -> list[str]` renders
  `obs.recent_actions[-1]` with explicit field formatting (op, error,
  result_summary keys laid out individually). The rendered block is
  inserted into `build_messages` BEFORE the existing "Recent loop
  history" block. This closes the "blind on its own outputs" failure
  mode where the existing `[:200]` truncation in the older history
  block would eat stdout sentinels and tracebacks. The OLDER history
  block (trailing 3 entries with the `[:200]` cap) is unchanged so
  rolling context still has a hard length budget. Forced spec
  deviation: action dicts in `recent_actions` do NOT carry an
  `iter_count` field (only the top-level `Observation` does), so the
  latest-action header is plain `"Latest action output:"` without
  iter-count context. Iter context continues to be announced once via
  the existing `"Recent loop history (iter N):"` line.

- **Three falsifiable LD3 acceptance signals (G5a)** in
  `tests/coder/test_observe_latest_action.py`:
  - stdout sentinel reaches the prompt verbatim past 200-char depth
    (would have been truncated under 2.2a's `[:200]` cap).
  - stderr sentinel reaches the prompt verbatim past 200-char depth.
  - `result_summary=None` exception path renders the placeholder and
    leaves the `error` field non-null, so the LLM sees both that the
    last action failed and what failed about it.

- **`_act` dispatch coverage of the new ops** via isolated `Substrate.open`
  fixtures. The `_act` body itself (`_session.py:225` from 2.2a) is
  unchanged — it still routes via `s.effect.perform(op, args)` and emits
  a `:act/result` provenance datom on both success and failure paths.
  T4 added:
  - `tests/coder/test_act_git.py` (4 G3 tests). `:git/diff` and
    `:git/commit` via `_act` write a `:act/result` datom AND advance the
    canonical audit chain by EXACTLY 1 entry per op (the LD2 mask
    property holds end-to-end, not just at the handler call site).
    `:git/commit` paths-unchanged returns `exit=1` passthrough; the
    coder records a non-error `:act/result` because the op succeeded
    even if git's exit code is 1. cwd-outside-root surfaces
    `FsCapabilityDenied` via `:act/result.error` (provenance-survives-
    failure invariant from 2.2a).
  - `tests/coder/test_act_code_run.py` (4 G4 tests). `:code/run`
    traceback is captured in `result_summary` (truncated by
    `_summarize_result`'s 512-char cap, same shape as every other
    op's failure-path `:act/result`).

### Notes

- LD3 fixes a regression-class introduced incidentally by 2.2a's
  `[:200]` cap — the cap was applied to the JSON-dump of EVERY recent
  action including the most recent one, which meant any action whose
  result string exceeded 200 bytes would get truncated before the LLM
  saw it. After 2.2b only OLDER history entries (the trailing 3 in
  `recent_actions[:-1]`) get the cap; the most recent action passes
  through verbatim, subject only to `_summarize_result`'s 512-char
  ceiling on string fields. That 512-char ceiling is the new
  effective limit for "what the LLM sees about its last action".
- The G5b 5-iter scripted scenario in `test_loop_e2e.py` asserts
  EXACTLY 9 audit entries across the run (5 `:llm/call` + 1 each of
  `:fs/read`, `:code/run`, `:git/diff`, `:git/commit`) AND that the
  `:shell/exec` count is 0. The `:shell/exec` zero-count IS the LD2
  single-audit-entry-via-mask property proved end-to-end through the
  coder loop, not just at a single handler call site.
- The G6 deterministic-invariants tests in `test_loop_replay.py`
  assert `output_hash` byte-identity for `:code/run` across runs and
  argv-determinism for `:git/*` across handler instances. They do
  NOT assert full `AuditEntry.id` byte-identity — that would fail
  because `wall_clock_ms` is included in both ops' result dicts and
  `result_hash` is non-deterministic by design. The deterministic
  invariants (`output_hash`, argv) are the right G6 surface; the
  full-replay byte-identity property is a separate concern owned by
  the replay module's own tests.
- CLI wiring of `:fs/*`, `:shell/exec`, `:code/run`, and `:git/*` in
  `__main__.py` is DEFERRED. This is a pre-existing gap from 2.2a:
  `__main__.py` only installs the LLM provider handler, and the
  test surface uses isolated `Substrate.open("memory")` fixtures
  that install the new handlers per-test. Production CLI wiring of
  the full effect surface belongs to 2.4a hardening or a separate
  follow-up; carrying that work into 2.2b would have widened scope
  beyond LD2/LD3.

### Tests

- `tests/coder/test_observe_latest_action.py` — 6 G5a tests
  (latest-action verbatim passthrough, stdout/stderr sentinel
  reach, exception-path placeholder + non-null error, header
  ordering, empty-history no-op, `_render_latest_action` field
  formatting).
- `tests/coder/test_act_git.py` — 4 G3 tests (`:git/diff` audit
  entry count, `:git/commit` paths-unchanged exit=1 passthrough,
  cwd-outside-root provenance-survives-failure, single-audit-entry
  per `:git/*` call via `_act`).
- `tests/coder/test_act_code_run.py` — 4 G4 tests (`:code/run`
  success path, traceback captured in `result_summary`,
  forbidden-import sentinel reaches `:act/result.error`,
  `args_hash` distinct from `:code/exec` for the same logical
  input).
- `tests/coder/test_loop_e2e.py` — 3 G5b tests appended (existing 5
  unchanged): 5-iter 9-audit-entry scenario asserting
  `:shell/exec` count == 0 (LD2 e2e proof), `:code/run`
  traceback reaches the next-iter prompt verbatim, cwd-denied
  `:git/diff` surfaces error in `:act/result`.
- `tests/coder/test_loop_replay.py` — 3 G6 tests appended
  (existing 3 unchanged): `:code/run` `output_hash` byte-identity
  across runs, `:git/diff` argv-determinism via spy fixture,
  `:git/log` argv-determinism across handler instances.

### Refs

- Design: `docs/plans/2026-05-06-phase-2.2b-git-code-exec-design.md`
- Suite delta at T7: 2354 → 2404 (+50 across 2.2b T1-T7; +20 in
  the coder module).

---

## v0.9.0a1 (unreleased) — Phase 2.2a `_observe` + `_act` + `run()` loop widening

Phase 2.2a fulfils the LD5 deferral from 2.1b: "loop widening lands in 2.2a
when `_observe`/`_act` have substance to iterate over." All three methods
(`_observe`, `_act`, `run`) now have real bodies. The coder can execute
scripted multi-step runs end-to-end: observe substrate state, decide, act on
the decision, observe updated state, repeat up to `max_iters`.

### Added

- **`Coder.max_iters: int = 20`** (`_session.py`; LD2). Upper bound on the
  `run()` for-loop. `--max-iters N` CLI flag exposed via `_cli.py` and wired
  in `__main__.py`.

- **`Coder.observe_depth: int = 5`** (`_session.py`; LD3). Controls how many
  recent datoms per attribute are kept in each `Observation`. Tighter windows
  keep `build_messages` prompts from growing unbounded across iterations.

- **`Coder._session_start_dt`** (`_session.py`; `init=False`). Set
  unconditionally by `run()` before the loop so `_observe` can filter datoms
  emitted before the current session. Type: `datetime | None` (default None).

- **`Coder._observe()` body** (`_session.py`). Reads via
  `s.fact.since(self._session_start_dt)`, sorts by `d.tx`, filters
  `d.op == "assert"`, partitions datoms into decisions (attribute
  `"llm/decision"`) vs actions (attribute `"act/result"`) by bare `d.a`
  (Datom strips leading colon per `datom.py:175` — see forced deviations),
  slices last `observe_depth` entries per attribute. Returns a frozen
  `Observation`.

- **`Coder._act(decision)` body** (`_session.py`). Validates
  `decision.kind == "act"` and that `decision.payload["op"]` is a
  `:`-prefixed string. Dispatches via `s.effect.perform(op, args)`. Emits a
  `:act/result` provenance datom via the `_record` helper in BOTH the success
  and failure paths (provenance-survives-failure guarantee). `args_hash`
  reuses `canonical_hash` (same helper as the audit middleware, so the hash
  is byte-identical to what the audit chain would record for a wrapped op).
  `_summarize_result` truncates dict-string values longer than 512 chars.
  Inside the `except` clause the bare `raise` re-raises the original
  exception (R0 B1 fix; preserves stack trace).

- **`run()` loop widening** (`_session.py`). Body is now a
  `for self._iter_count in range(self.max_iters)` loop (see forced deviations
  for the assignment-form used). Four exit paths in order:
  1. `_should_escalate_branch` → `_escalate_branch` (stub; `CoderStubNotImplemented`).
  2. `_should_escalate_plan` → `_escalate_plan` (stub; `CoderStubNotImplemented`).
  3. `decision.payload.get("done") is True` → return before `_act`.
  4. Loop exhausted → silent return (max_iters cap).
  `_check_pause` removed from the loop body (LD8 deferral to 2.3d — pausing
  requires hormone-event integration that doesn't exist yet).

- **`_should_escalate_plan` and `_should_escalate_branch` one-liners**
  (`_session.py`). Each returns `decision.kind == "plan"` or
  `decision.kind == "branch"` respectively. No stub raises — these are
  one-liner predicate gates, not stub bodies.

- **`_escalate_plan`, `_escalate_branch`, `_check_pause` stub bodies**
  (`_session.py`). All keep `CoderStubNotImplemented` raises per Phase 2.2a
  scope. `decision` parameter renamed `_decision` per project convention
  (unused-parameter signals intent clearly; prevents accidental use before
  the body is filled in 2.3a/2.3b/2.3d).

- **`Observation` dataclass body** (`_types.py`). Three fields:
  `iter_count: int = 0`, `recent_decisions: tuple = ()`,
  `recent_actions: tuple = ()`. Empty defaults preserve unparametrized
  constructor calls in all 2.1b unit tests — no test breakage.

- **`build_messages` history hook** (`_prompt.py`). Adds an optional
  "Recent loop history" section rendered only when `obs` has non-empty
  `recent_decisions` or `recent_actions`. Per-entry text is truncated to 200
  chars; the list is further trimmed to the last 3 entries of each attribute
  before rendering. Absent history → no section added (prompt identical to
  2.1b for the zero-history case).

### New datom emitted

- **`:act/result`** — one entity per `_act` call (success or failure).
  Value is a canonical-JSON serialized dict:
  `{op, args_hash, summary, latency_ms, error}` where `error` is `None` on
  success and a `str` on failure. Written via `s.fact.transact`; bypasses the
  canonical audit chain (same supporting-provenance shape as `:llm/messages`
  and `:llm/decision` from 2.1b — not a billable wrapped op, just a
  substrate-side fact written for observability).

### Forced spec deviations

1. **`Datom.a` strips leading colons** (`datom.py:175`). `_observe` filters
   by `d.a == "llm/decision"` and `d.a == "act/result"` (bare, no colon)
   throughout. The design doc used `:llm/decision` notation; the runtime
   strips the colon on storage so bare-string comparison is correct.
2. **`make_callable_llm_handler` call_fn signature is `**kwargs`**
   (`model, messages, tools, temperature, max_tokens`), not a single-arg
   callable. `_scripted_decisions` test helper adapted to accept and ignore
   the extra kwargs.
3. **`for self._iter_count in range(...)` assignment form.** Python does not
   support `for self.attr in range(...)` directly in all toolchain versions.
   Loop body uses the equivalent `for i in range(...): self._iter_count = i`
   form to keep `_iter_count` current for any observer that inspects it.
4. **`__main__.py` is the Coder construction site** for
   `max_iters=args.max_iters`. The `_cli.py` module only defines argparse
   args; the wiring between `args.max_iters` and the `Coder` constructor
   happens in `__main__.py` (consistent with `--model` wiring precedent
   from 2.1b).

### Architectural decisions at impl time

1. `_safe_resolve(*allowed_roots)` variadic helper in the FS handler — allows
   `:fs/read` to accept both `project_root` and `scratch_dir` while
   `:fs/write` is restricted to `scratch_dir` only, without duplicating the
   path-escape check.
2. Empty `argv` guard placed before `basename` call in the shell handler →
   `ShellAllowlistDenied("argv is empty")` (T3 contract fix; `basename("")`
   would pass through silently otherwise).
3. TimeoutExpired bytes-branch in the shell handler documented as empirically
   live on macOS (`_check_timeout` joins raw bytes before decode); not dead
   code despite appearances.
4. `ALLOWLIST_VERSION` auto-derived from `frozenset` at module load time via
   `sha256(canonical_dumps(sorted(ALLOWLIST_V1)))[:16]`. Any allowlist edit
   propagates the version automatically — no manual constant to update.

### Test surface

- **G1** — `tests/effect/handlers/test_fs_handler.py` (13 tests). Capability
  denial, binary base64 round-trip, glob/grep canonical sort, symlink escape,
  scratch_dir glob symmetry.
- **G2** — `tests/effect/handlers/test_shell_handler.py` (12 tests). Allowlist
  pass/denial, version pin, timeout, version-mismatch replay, env passthrough,
  full-path basename matching, empty-argv denial.
- **G3** — `tests/coder/test_observe.py` (6 tests). Empty substrate,
  3-datom window, 7-datom window-trim, partition by attribute, iter-count
  propagation, pre-session datom exclusion.
- **G4** — `tests/coder/test_act.py` (8 tests). Dispatch, provenance-survives-
  failure, `kind != "act"` rejection, missing op, non-string op, missing colon
  prefix, args_hash agreement, `latency_ms >= 0`.
- **G5** — `tests/coder/test_loop_e2e.py` (5 tests). 3-iter scripted run,
  max_iters cap, done-flag short-circuit, plan halt, branch halt.
- **G6** — `tests/coder/test_loop_replay.py` (3 tests). Byte-identity positive,
  handler-swap mismatch, clock-skew mismatch.

Suite delta: 2300 → 2354 (+54). Pyright 0 / 0 / 0 on touched files.

### Notes

- The LLM is still not exposed to `:fs/` or `:shell/exec` tool schemas
  directly. In 2.2a the routing is substrate-side: `_act` reads
  `decision.payload["op"]` and dispatches. The LLM emits a structured
  `payload` via `EMIT_DECISION_TOOL_SCHEMA`; the substrate validates and
  routes. No change to the LLM-visible tool surface in 2.2a.
- `_escalate_plan`, `_escalate_branch`, and `_check_pause` remain stubs.
  2.3a fills `_escalate_plan`; 2.3b fills `_escalate_branch`; 2.3d wires
  `_check_pause` to hormone events.

---

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
