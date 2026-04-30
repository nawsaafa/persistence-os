# CHANGELOG — persistence.effect

All notable changes to Module 2 (`persistence.effect`) are recorded here.

## v0.8.5a1 (unreleased — lands at Phase 2.0d sub-tag) — `:code/exec` sandbox handler (#141)

Phase 2.0b of the persistence-coder MVP (Phase 2 of the v1.0 roadmap).
Ships the first-class ``:code/exec`` Plan effect that runs short, pure
Python snippets in a capability-denied subprocess sandbox. Determinism
is achieved by **capability-denial + environment control**, NOT by
static detection of nondeterministic calls (ADR-5). Every successful
(and timeout-failed) call emits a ``:code/exec`` audit datom that
rides the existing Merkle chain at ``effect/handlers/audit.py`` — no
new chain code, mirrors the ``:plan/edit`` pattern from #140 / 2.0a.

### Added

- **`handlers/code.py`** — sandbox handler module.
  - `exec_code(source, *, stdin, timeout_seconds, memory_mb, env, tx,
    replay_mode, expected_output_hash) -> CodeExecResult` — synchronous
    public surface; runs the source in a sandboxed subprocess and
    returns the outcome. MUST be called inside ``db.dosync(...)`` with
    the active Transaction passed via ``tx=`` (mirrors the Plan-Edit
    invariant from #140).
  - `CodeExecResult` frozen dataclass: ``stdout``, ``stderr``,
    ``exit_code``, ``wall_clock_ms``, ``output_hash`` (sha256 of
    canonical-JSON of (stdout, stderr, exit_code) — wall_clock_ms is
    intentionally excluded so byte-identity holds across CPU contention).
  - `make_code_exec_handler()` — no-op terminator handler so the audit
    middleware (``make_audit_handler(wraps=":code/exec")``) has a raw
    handler underneath. The actual subprocess execution happens INSIDE
    ``exec_code()`` BEFORE ``tx.effect()`` is queued; the
    intent-replay-time ``perform`` call exists solely to emit the
    AuditEntry with the captured hashes.
- **Capability-denial layers (ADR-5):**
  1. Subprocess isolation via ``sys.executable -I -S`` (isolated, no
     site.py) — never ``eval`` / ``exec`` in-process.
  2. POSIX ``setrlimit`` preexec hook — ``RLIMIT_CPU = timeout_seconds + 1``
     (kernel backstop), ``RLIMIT_AS = memory_mb * 1024 * 1024``
     (Linux-honored, macOS best-effort), ``RLIMIT_NOFILE = 32`` (no
     fd-flood DoS), ``RLIMIT_NPROC = 1`` (no fork bombs),
     ``RLIMIT_FSIZE = 0`` (read-only on disk).
  3. Wall-clock timeout via ``proc.communicate(timeout=...)`` + kill
     on ``TimeoutExpired``.
  4. Module allowlist enforced inside the child via a bootstrap shim
     that monkey-patches ``builtins.__import__``. Allowed top-level:
     ``json``, ``re``, ``dataclasses``, ``pathlib``. Explicit deny-list
     overrides the cache (else pathlib's transitive warm-import would
     leak ``os`` / ``sys`` / ``time``): blocks ``os``, ``sys``,
     ``subprocess``, ``socket``, ``urllib``, ``http``, ``ctypes``,
     ``threading``, ``multiprocessing``, ``marshal``, ``time``,
     ``random``, ``asyncio``, ``ssl``, ``shutil``, ``tempfile``, ``io``,
     ``fcntl``, ``signal``, ``resource``, ``importlib``, ``hashlib``,
     ``platform``, ``uuid``, ``secrets``, ``requests``, plus
     ``p``+``ickle`` and ``_thread`` / ``posix`` / ``nt`` path siblings.
  5. No network — ``socket`` is blocked at import; we do NOT add a
     network-namespace dance (capability-denial, not detection).
  6. Working dir = fresh ``tempfile.mkdtemp()`` cleaned up on exit.
- **Audit datom (seven keys)** — ``:code/exec/source-hash``,
  ``:code/exec/stdin-hash``, ``:code/exec/output-hash``,
  ``:code/exec/exit-code``, ``:code/exec/wall-clock-ms``,
  ``:code/exec/timeout-seconds``, ``:code/exec/memory-mb``. Stdout /
  stderr full captures are NOT in the datom (potentially huge); only
  the hashes. Audit-replay reads recorded hashes; re-execution-replay
  re-runs and verifies ``output_hash`` matches.
- **Errors (all subclass ``CodeExecError``):** ``CodeExecOutsideDosync``,
  ``CodeExecTimeout(timeout_seconds, partial_stdout)``,
  ``CodeExecMemoryExceeded(memory_mb)``,
  ``CodeExecForbiddenImport(module_name)``,
  ``CodeExecReplayMismatch(expected_hash, actual_hash)``. All exported
  from ``persistence.effect``.
- **Replay semantics (§ 3.7):**
  - Audit-replay default: replay reads the datom, returns recorded
    hashes / exit_code / wall_clock_ms with empty stdout/stderr (we
    don't store them).
  - Re-execution-replay opt-in: caller passes ``replay_mode="re-execute"``
    + ``expected_output_hash=``; source re-runs under same env; mismatch
    raises ``CodeExecReplayMismatch``.

### Tests

- ``tests/effect/test_code_exec.py`` — unit + Hypothesis @
  max_examples=200 over 4 deterministic source patterns
  (``print(constant)``, ``print(json.dumps(constant))``, etc.).
  Covers happy path / timeout / forbidden imports (one test per:
  os, sys, subprocess, socket, urllib, ctypes, threading, ``p``+``ickle``)
  / allowed imports / stdin / outside-dosync rejection / audit datom
  shape / Merkle-chain integration / re-execution match + mismatch.
  Memory-cap test platform-skipped on Darwin per ADR-5 RLIMIT_AS
  caveat.

### Security caveat (per ADR-5)

This is **v0.5 sandboxing** — suitable for trusted code (the agent's
own generations under user supervision), NOT for untrusted user
submissions. Hardening to ``firejail`` / ``bubblewrap`` lands in
Phase 3. Any commercial deploy disables ``:code/exec`` by default.

## [0.4.0a1] — 2026-04-25 — audit handler `parent_provenance_hash` alias

### Changed

- **Audit handler provenance bridge**
  (`persistence.effect.handlers.audit.audit_entry_to_datom`) — the
  function now writes a `parent_provenance_hash` bare-snake_case key
  alongside the existing `:prev-hash` provenance entry. Both keys point
  to the same value. The alias bridges audit chain hashes to the new typed
  `Provenance` schema in `persistence.fact` so `DB.causal_history()` can
  walk the chain transparently using either key. No behavioral change for
  callers that read `:prev-hash`; the extra key is additive only.

## [0.1.0] — 2026-04-20 — Initial cut (Workstream B Phase 1)

First ship of the algebraic effect handler stack described in
`docs/agent3-effect-spec.md` and formalised in `paper/persistence-nesy-2026-draft.md`
§4.2 / §5.2.

### Runtime

- `runtime.py` — `Effect`, `Handler`, `Runtime`, `perform(op, **args)`.
- Outermost→innermost dispatch following paper §4.2.
- **Proposition 2 (well-formedness)** check: `Runtime.is_well_formed(catalog)`
  and `Runtime.uncovered_ops(catalog)`.
- Koka-style **`mask(name)`** context manager — cumulative, nested, scoped
  to the active runtime; hides a named handler so e.g. a policy body can
  perform `:llm/call` without re-triggering `:audit`.
- **Named handler dispatch** — `named(name, op, **args)` addresses a handler
  by name for sinks like `audit-archive`.
- Runtime is per-`ContextVar` — no hidden globals across threads.

### Catalog

- `catalog.py` — the full 15-op catalog from spec §1:
  `llm/call`, `tool/call`, `mem/read`, `mem/write`, `decide`, `ask-user`,
  `emit-artifact`, `sleep`, `random`, `env/read`, `net/fetch`, `secret/use`,
  `cost/charge`, `clock/now`, `audit/emit`.
- Typed args with required/optional markers; extra fields tolerated.
- `validate_args(op, args)` raises `KeyError` (unknown op), `ValueError`
  (missing required), or `TypeError` (wrong type).

### Canonical JSON

- `canonical.py` — `canonical_dumps`, `canonical_hash`.
- Sorted keys, compact separators, `allow_nan=False`; rejects non-JSON
  types (sets, bytes, dataclasses) so the hash never drifts silently.

### Handlers

- `handlers/audit.py` — hash-chained Merkle log. Each entry's `id` is the
  SHA-256 of its content fields; `prev_hash` references the prior entry.
  Routes writes to a *named* sink via `:audit/emit` (spec §9 anti-pattern
  avoidance — no synchronous disk writes). Masks itself on internal
  `:clock/now` to prevent re-entry. Captures both success (`verdict="ok"`)
  and failure (`verdict="error"`) so regulators see attempted-and-denied
  too. `audit_entry_to_datom` / `datom_to_audit_entry` produce the Fact
  spec §1 8-tuple shape with full round-trip fidelity. `verify_chain`
  detects tampering.
- `handlers/retry.py` — exponential backoff via `:sleep` + jitter via
  `:random(kind="jitter")`. Pure-effect routing means replay is bit-for-bit
  deterministic with a recorded jitter seed.
- `handlers/rate_limit.py` — thread-safe token bucket. Reads the clock via
  `:clock/now`; sleeps via `:sleep`. Per-instance `threading.Lock`; no
  hidden globals.
- `handlers/cache.py` — canonical-JSON args key. Per-instance store.
- `handlers/dry_run.py` — short-circuits `:tool/call` / `:emit-artifact`
  (configurable) with mocked returns when `mode="dry-run"`. Supports
  `allow_live` allowlist for read-only ops.
- `handlers/policy.py` — verdicts: `allow | deny | deny-silently |
  require-approval`. `deny` raises `PolicyDenied`; `deny-silently` returns
  a sentinel dict; `require-approval` consults an optional `approval_fn`
  hook (the **single** escape hatch) then raises `ApprovalRequired` if not
  granted. Policy value is never mutated — hot-reload is a pointer swap.
- `handlers/pii_redact.py` — schema `{"fields": {...}, "paths": {...}}`
  where dotted paths address nested dicts. Deep-copies args before
  redacting so the caller's dict is never touched.
- `handlers/raw.py` — echo LLM, flaky LLM (`TransientError` every Nth
  call), scripted tool, deterministic `:random`. Sole authorized caller of
  `random.Random`.
- `handlers/clock.py` — system / fixed / replay clocks. Sole authorized
  caller of `time.time()`.

### Policy evaluator

- `policy_eval.py` — pure function `evaluate(policy, principal, op, args,
  mode=...)` returning `{"verdict", "reasons", "policy_id"}`.
- Operators: `:op=`, `:op-in`, `:contains?`, `:matches?`, `:non-empty?`,
  `:mode=`, `:=`, `:and`, `:or`, `:not`.
- Path forms: `[":args", key, ...]`, `[":principal", key, ...]`, `[":op"]`.
- First fired rule wins; if no rule fires the verdict is `allow`.
- Raises `PolicyError` on unknown operator or malformed node.

### Demo

- `demo.py` — reproduces the BankabilityAI stack from spec §3:
  `audit → policy → dry-run → cache → retry → rate-limit → raw`.
- Runs 9 scripted scenarios showing: success, cache hit, retry recovery,
  policy deny, require-approval (with and without rationale), dry-run
  silent deny on `:tool/call stripe`, full Merkle chain trace, and the
  datom view of entry[0].

### Tests

- 92 tests green; layout:

  | File | Tests |
  |---|---|
  | `test_canonical.py`     | 6  |
  | `test_runtime.py`       | 12 |
  | `test_catalog.py`       | 10 |
  | `test_audit.py`         | 10 |
  | `test_retry.py`         | 5  |
  | `test_rate_limit.py`    | 4  |
  | `test_cache.py`         | 5  |
  | `test_dry_run.py`       | 5  |
  | `test_policy_eval.py`   | 17 |
  | `test_policy_handler.py`| 7  |
  | `test_pii_redact.py`    | 5  |
  | `test_composition.py`   | 6  |

### Verification gates (all green)

1. `pytest tests/effect/ -v` — 92/92 passing in ≈0.4s.
2. `python -m persistence.effect.demo` — prints the nine-scenario trace
   including Merkle chain (`verify_chain → True`) and datom view.
3. **Hash-chain integrity** — `test_audit_prev_hash_chain_intact_across_full_stack`
   and `test_tampering_an_entry_breaks_the_chain` both pass.
4. **Datom round-trip** — `test_datom_roundtrip_preserves_audit_entry`
   and `test_audit_entry_to_datom_has_fact_schema_fields` both pass
   against the 8-tuple from `agent1-fact-spec.md §1`.

### Deviations from spec

- The spec §8 prototype uses a module-level `_stack`; this implementation
  replaces that with a `ContextVar`-scoped `Runtime` so `mask` is safe
  across threads and so two tests can run in parallel. Semantics are
  identical for the single-runtime case.
- `validate_args` exists and is exposed but is **not** called inside
  `perform()` by default; callers opt in. Reason: policy and PII-redact
  handlers deliberately inject/strip fields, and automatic validation
  would reject their output. A future workstream can wire validation at
  the audit boundary if desired.
- Jitter is modelled as an explicit `:random(kind="jitter")` effect with
  `params={"max": jitter_ms}` rather than sampling uniformly in `[0, 1)`.
  This makes replay of retry timings exact when the recorded jitter
  samples are re-played.
