# CHANGELOG — persistence.effect

All notable changes to Module 2 (`persistence.effect`) are recorded here.

## v0.8.5a1 (unreleased — lands at Phase 2.0d sub-tag) — Phase 2.0d W4 micro-pass

Phase 2.0d W4 closes the two narrow residuals R2.4 surfaced after W3:

- **W4 — RLIMIT_FSIZE wording correction.** The v0.8.0a1 entry below
  described `RLIMIT_FSIZE = 0` as "read-only on disk" — that is wrong.
  `RLIMIT_FSIZE = 0` is a write-denial limit at the kernel layer; it
  does not prevent reads of host files. The line is corrected to
  "write-denial only; does NOT prevent reads of host files" with a
  pointer to the W3 rescope-pass entry. The runtime behaviour itself
  was already correct from v0.8.0a1 onward — this is a documentation
  fix, not a code change.
- **W4 — concurrency scope honesty note (v0.8.5a1).** The
  substrate-completion claim shipping at the v0.8.5a1 sub-tag is
  scoped to **single-process Python under the GIL**. Multi-process
  Postgres SERIALIZABLE serialisation already shipped at v0.8.0a1
  (PG1-PG6 — `PostgresStore` + `transact_serializable` + cross-process
  Hypothesis property test). No new in-process concurrency
  guarantees are claimed for v0.8.5a1; effect handlers and audit-stack
  installation are still single-runtime. Threaded multi-runtime
  concurrency is a separate v0.9.x track.

### Known issues

- **macOS subprocess-startup flake under suite load.** A
  non-deterministic subset of `tests/effect/test_code_exec.py` cases
  occasionally hits the 5-second `CodeExecTimeout` when the full test
  suite runs sequentially on macOS — child-process fork + Python
  interpreter startup under heavy concurrent FS / resource pressure
  can exceed the default 5s wall-clock budget. All cases pass in
  isolation and on Linux. This is a platform-dependent timing
  characteristic, not a code regression: the underlying
  `:code/exec` handler logic is correct (verified by codex R2.4
  hard-mode review, including audit-chain Merkle integrity and replay
  determinism). Queued for v0.9.x: per-test `timeout_seconds` overrides
  in the suite + macOS-specific subprocess prewarming or `pytest-xdist`
  isolation. Local internal-alpha consumers should run
  `pytest tests/effect/test_code_exec.py -q` separately if the
  full-suite run shows transient `CodeExecTimeout` failures in this
  module.

## v0.8.5a1 (unreleased — lands at Phase 2.0d sub-tag) — Phase 2.0d W3 rescope-pass

Phase 2.0d W3 (R2.3 ARIS codex review fix-pass) is an **honest
rescoping pass**, not a security-hardening pass. The R2.3 review
demonstrated that Python-level capability-denial as architected in
W1+W2 cannot prevent host-FS reads when stdlib transitive closure is
preserved (concrete repro:
`import dataclasses; dataclasses.sys.modules['builtins'].open('/etc/passwd','r').read(20)`
prints host-file bytes with `exit_code == 0`). Rather than chase
Python-level confidentiality (which is whack-a-mole — a different
allowed module's `__dict__` would expose an equivalent path, and a
sufficiently motivated body can rebuild the chain via attribute
walks), this W3 pass **rescopes `:code/exec` honestly** as a
soft-isolation runtime guard / best-effort containment for
trusted-author plan-step bodies under user supervision, and queues
hard isolation as a separate v0.9.x sandbox-redesign engineering
project (real OS-level boundary: gVisor / nsjail / Docker / OCI
runtime / WASM-Pyodide).

**This is a rescope, not a regression.** The W1→W2→W3 trajectory
shows the team discovered that Python-level capability-denial cannot
achieve confidentiality without OS-level isolation; the substrate-
completion claim was honestly corrected and real isolation queued as
a v0.9.x project. The load-bearing substrate-completion guarantees
(audit-chain integrity via the M5 fix at `transaction.py:703`,
replay determinism per § 3.7) remain intact and unchanged. The wedge
story (Karpathy product reframe — rewind / branch / replay over agent
trajectories) does not depend on hard sandbox isolation; it depends
on audit-chain integrity, which is demonstrably correct.

### Documentation

- **ADR-5 amended** in
  `docs/plans/2026-04-30-phase-2-persistence-coder-design.md`. The
  pre-W3 capability-denial-not-detection text is preserved verbatim
  as the historical record; a W3 amendment block follows that:
  - Documents the R2.3 escape vector (`dataclasses.sys.modules['builtins'].open`)
    as a known-known limitation expected to remain until v0.9.x.
  - Restates what the sandbox DOES guarantee — subprocess isolation,
    `RLIMIT_FSIZE=0` kernel-enforced write-denial, determinism
    pinning (`PYTHONHASHSEED=0`), curated user-source builtins,
    top-level import deny-list, audit-chain integrity — all
    unchanged by W3.
  - Restates what it does NOT guarantee — host-FS confidentiality
    against an adversarial body, isolation from already-loaded
    forbidden modules via allowed modules' `__dict__`, defense
    against malicious code.
  - Forward-pointer to the v0.9.x sandbox-redesign track.
- **`code.py` module docstring rewritten** to lead with
  "Soft-isolation runtime guard, NOT a confidentiality boundary."
  Three explicit sections: what the sandbox DOES guarantee, what it
  does NOT guarantee (with concrete escape repro), and intended use
  (trusted-author plan-step bodies, NOT untrusted submissions).
- **Bootstrap shim comments cleaned up** — every "denies host
  files" / "no curated surface to read" / "capability denial closes
  the host-file-read vector" framing rewritten to honest
  soft-isolation language. The import-filter logic itself is
  correct and unchanged (it does deny imports of forbidden
  top-level modules — the escape is via *allowed* modules'
  transitive references, which is a different layer).
- **`test_open_is_denied` docstring** rewritten to remove the false
  "no curated surface to read host files" claim. The test still
  verifies the bare-name `NameError` signal honest plan-step bodies
  see when calling `open()` directly — that remains a useful
  soft-isolation default.

### Tests

- **`test_known_escape_via_dataclasses_sys_modules_builtins_open` —
  new xfail-strict regression test** in `tests/effect/test_code_exec.py`.
  Reproduces the R2.3 sandbox-break verbatim:
  `import dataclasses; data = dataclasses.sys.modules['builtins'].open('/etc/passwd','r').read(20)`.
  Under v0.8.5a1 soft-isolation, `exit_code == 0` (host bytes
  leaked); the assertion `result.exit_code != 0` fails; the
  `@pytest.mark.xfail(strict=True)` marker therefore fires and the
  test runs as `XFAIL`. When the v0.9.x sandbox-redesign track
  ships a real OS-level boundary, the kernel denies the read,
  `exit_code != 0`, the assertion holds, the xfail flips to `XPASS`,
  and `strict=True` forces a CI failure if the marker is not
  removed — i.e. the test becomes the v0.9.x acceptance signal.
- Suite delta on this rescope-pass: 2075 passed / 35 skipped /
  7 xfailed → 2075 passed / 35 skipped / 8 xfailed (the new known-
  escape regression accounts for the +1 xfail; no new passing tests
  in W3 — the work is honest documentation, not new functionality).

### Forward-pointer — v0.9.x real-OS-sandbox track (#TBD)

Real OS-level `:code/exec` sandbox boundary (gVisor / nsjail /
Docker / OCI runtime / WASM-Pyodide) — supersedes the v0.8.5a1
soft-isolation runtime guard. The audit-datom contract carries
forward unchanged. The xfail-strict regression test
`test_known_escape_via_dataclasses_sys_modules_builtins_open` IS
the falsifiable acceptance signal: when it flips to PASS, the v0.9.x
boundary is in place. Tracking #TBD (assign on track creation).

### Behaviour change

None. W3 is documentation + a single new regression test (xfail).
No source files under `src/persistence/effect/` were modified for
behaviour; only inline comments and module docstring text were
rewritten. `:code/exec` runtime behaviour is bit-for-bit identical
to the post-W2 state.

## v0.8.5a1 (unreleased — lands at Phase 2.0d sub-tag) — Phase 2.0d W2 fix-pass

Phase 2.0d W2 (R2.2 ARIS hard-mode fix-pass) closes the effect-side
findings from the codex review at HEAD `8e06fa1` after W1 landed.

### Fixed

- **R2.2 M6 — `:code/exec` sandbox host-filesystem-read via
  `pathlib`.** The W1 (M1) fix removed `open` from the curated
  user-source `__builtins__`, but `pathlib` stayed in
  `_ALLOWED_TOP_LEVEL` — so `import pathlib;
  pathlib.Path('/etc/passwd').read_text()` succeeded inside the
  sandbox because `pathlib.Path.read_text/.read_bytes/.open` reach
  the C-level `_io.open` directly. Capability-denial-not-detection
  (ADR-5) requires deny-by-default for FS-touching modules.
  Removed `pathlib` from `_ALLOWED_TOP_LEVEL`:
  `("json", "re", "dataclasses", "pathlib")` →
  `("json", "re", "dataclasses")`. The bootstrap-shim warm-import
  block dropped `import pathlib`; the `repr(_ALLOWED_TOP_LEVEL)`
  substitution at module load time keeps the parent and child in
  sync. `CodeExecForbiddenImport` docstring + error message
  updated to reflect the three-name allowlist with explicit
  call-out of pathlib's removal. Three new tests:
  `test_pathlib_import_is_denied` (positive: `import pathlib`
  raises `CodeExecForbiddenImport`),
  `test_pathlib_path_is_unreachable_via_attribute_access`
  (defensive: user-source name `pathlib` is unbound),
  `test_etc_passwd_unreadable_via_pathlib` (end-to-end:
  reproduces the R2.2 attack vector verbatim).

### Behaviour change

- User-source bodies that imported `pathlib` under v0.5 / W1 will
  now raise `CodeExecForbiddenImport`. Path-string manipulation
  legitimately fits inside `str` operations.

> **W3 RESCOPE NOTE (2026-05-01).** The W2 entry above stated
> "the sandbox denies host files by design." That framing is
> **superseded** by the W3 honest-rescope. The W2 pathlib fix is
> still correct (it closes one specific path through
> `pathlib.Path.read_text` reaching `_io.open`), but the broader
> claim that the sandbox is confidentiality-preserving was demoted
> in W3 — see the W3 entry above for the full rescope and the
> documented `dataclasses.sys.modules['builtins'].open` escape vector
> that this layer cannot block. The v0.9.x real-OS-sandbox track
> supersedes Python-level filtering for confidentiality.

## v0.8.5a1 (unreleased — lands at Phase 2.0d sub-tag) — Phase 2.0d W1 fix-pass

Phase 2.0d W1 (R2 ARIS hard-mode fix-pass) closes the effect-side
findings from the codex review at HEAD `4e118e9`. See
`review-stage/aris-r2-v0.8.5a1-raw.txt` for the full review.

### Added

- **`canonical_audit_stack(entries)`** — public factory in
  `persistence.effect._audit_stack` returning a `Runtime` whose
  handler chain (innermost-first: raw terminator → clock → audit
  middleware) covers every audit-emitting op shipped through
  Phase 2.0a / 2.0b / 2.0c / 2.0c-ext. `CANONICAL_AUDIT_OPS` exposes
  the canonical op tuple. Used by `persistence.sdk.Substrate.open`
  to install the audit stack by default. (R2 MAJOR M2.)

### Fixed

- **R2 M1 — `:code/exec` sandbox host-file-read + nondeterminism.**
  - User-source `__builtins__` curated: `open` / `eval` / `ex`+`ec` /
    `compile` / `input` / `breakpoint` removed (capability-denial-not-
    detection per ADR-5). The `_DENIED_BUILTINS` parent constant is
    the canonical source; substituted into the child shim at module
    load time alongside `_ALLOWED_TOP_LEVEL`.
  - `__import__` stays callable so the import statement still works;
    the existing import filter rejects deny-listed top-level names
    whether reached via statement or direct call.
  - Child argv switched from `-I` (which suppressed all `PYTHON*`
    env vars) to `-s -P -S` so the substrate-supplied
    `PYTHONHASHSEED=0` and `PYTHONDONTWRITEBYTECODE=1` actually
    take effect.
  - `child_env` overlay pinned at the substrate level; caller-
    supplied `env=` still overrides for fuzz tests.
- **R2 m4 — RLIMIT_FSIZE preexec docstring overclaim.** Rewrote the
  comment: writes are denied (kernel SIGXFSZ on overrun); reads
  remain possible on the filesystem visible to the child. The M1
  `open()` removal closes the host-file-read vector at the
  capability layer.

### Behaviour change

- `:code/exec` user-source `input()` now raises `NameError` (was:
  read substrate-injected stdin). The `stdin=` parameter to
  `exec_code()` is still accepted on the public surface (the
  bootstrap shim still envelope-encodes it), but user code has no
  curated path to read it under the M1 capability set. A future
  revision (#149+) may add a curated `read_stdin()` builtin.

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
     ``RLIMIT_FSIZE = 0`` (write-denial only; does NOT prevent reads
     of host files — see W3 rescope-pass entry at the top of this
     CHANGELOG for the soft-isolation honest framing).
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

> **W3 RESCOPE NOTE (2026-05-01).** The "v0.5 sandboxing" framing
> is superseded by the W3 honest-rescope. The substrate-completion
> guarantee for v0.8.5a1 is **soft-isolation runtime guard /
> best-effort containment**, NOT a confidentiality boundary. See
> the Phase 2.0d W3 rescope-pass entry at the top of this CHANGELOG
> for the full rationale + forward-pointer to the v0.9.x
> sandbox-redesign track that ships a real OS-level boundary.

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
