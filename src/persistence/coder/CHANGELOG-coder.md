# persistence.coder CHANGELOG

## Phase 2.4a — 2026-05-09/10 (Production CLI wiring + env-keyed Ed25519 signer plumbing)

Phase 2.4a is the FIRST harden-track phase. It wires the production CLI
(`python -m persistence.coder`) so an end-user invocation installs the
same handler stack the test fixtures install, and so audit-chain
tamper-evidence (Mimir Phase B AuditEntry signing) reaches CLI users via
an `ANTHROPIC_API_KEY`-style env handle. Closes 4 queued rescopes from
2.3c.1 / 2.3c.2 / 2.3d / vertical-data-corpus LD-9.

**Critical-path sequencing:** 2.4a → 2.4b (`:sys/now` op + observability
+ `--confidence-threshold` CLI surface) → 2.4c lockfile (~Fri
2026-06-12) → `v0.9.0a1` tag (by 2026-06-14). Hard cutoff 2026-06-05;
runway WELL UNDER BUDGET.

### Locked decisions (LD-1 … LD-4) — design ARIS R0.1 lite PASS (mean 8.46 / min 7.8) → Impl R1 PASS (mean 8.48 / min 7.6)

- **LD-1 — Skill handler at CLI bootstrap.** `__main__.main()` extracts a
  `_build_substrate_and_handlers(args) -> Substrate` helper that opens
  the substrate, then installs `make_skill_handler(skill_lib, name="skill")`
  at `position="bottom"` BEFORE the LLM provider handler. `install_handler`
  is idempotent on `name` (`sdk/_facade.py:143-166`) so skill +
  provider co-exist at `handlers[0]`/`handlers[1]` (each routes its own
  ops). Mirrors 2.3c.1 fixture pattern at
  `tests/coder/test_loop_replay.py:137`.
- **LD-2 — Recursion-aware dispatcher (FD-LD2 collapsed in T0).** No
  production change. Recursion-budget enforcement already lives inside
  `canonical_audit_stack` at `effect/_audit_stack.py:250-303`, gated on
  a bound `DispatcherContext` (`_recursion.py:230`). `Coder.run()` binds
  `dispatcher_context(DispatcherContext())` per iteration at
  `_session.py:87`. End-to-end CLI → Coder.run → DispatcherContext →
  enforcement chain is automatic for any `Substrate.open(audit=True)`
  (the default). G2 is an assertion test only.
- **LD-3 — `:sys/now` substrate-time op (FD-LD3 W3-rescoped to 2.4b).**
  T0 receipts confirmed `Transaction.now()` is dosync-frozen and
  `_steering.branch()` is not in dosync, so a one-line default-arg flip
  isn't possible — landing `:sys/now` requires substrate-level scope
  (new op + handler), which doesn't fit 2.4a's "production CLI wiring"
  theme. **Path B locked:** keep wall-clock fork_at default in
  `_steering.py:299`; re-point 4 W3-rescope comments (lines 271, 297,
  299, 305-306) from `2.4a` → `2.4b`; add a strict-xfail test in
  `tests/coder/test_steering_sys_now.py` whose `reason` string
  explicitly requires 2.4b's spine to remove the decorator. The
  XPASS-strict failure mode catches incomplete 2.4b ships (op + wiring
  landed but marker not removed).
- **LD-4 — Env-keyed Ed25519 signer plumbed through Substrate.open.**
  Mimir Phase B already landed `make_audit_handler(signer: tuple[str, bytes] | None = None)`
  at `effect/handlers/audit.py:488-518`. T4 adds the two missing
  passthrough layers: `Substrate.open(uri, *, audit=True, audit_signer=None)`
  threads to `canonical_audit_stack(entries, *, signer=audit_signer)`
  which forwards to `make_audit_handler(entries, wraps=..., signer=signer)`.
  `__main__.py` reads `PERSISTENCE_AUDIT_KEY=file:///<absolute>/<key>.pem`
  (RFC 8089 three-slash form), loads PEM, derives raw 32-byte private
  key bytes via `cryptography.serialization.load_pem_private_key` +
  `private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())` (per
  FD-T4.2), and computes a stable `signer_id = "ed25519:" + sha256(pem_bytes).hexdigest()[:16]`.
  Unknown URI schemes → `SystemExit` at CLI bootstrap. Mirrors
  `ANTHROPIC_API_KEY` precedent at `_provider.py:43` — zero new CLI
  flags. **FD-LD4-signer-shape RESOLVED** — no Signer Protocol needed
  because `tuple[str, bytes]` is already the API contract.

### Test gates (G1 … G4)

- **G1 — `tests/coder/test_cli_wiring.py::test_main_installs_skill_handler`**.
  Builds substrate via `_build_substrate_and_handlers` with synthesized
  argparse `Namespace` + monkeypatched `detect_or_explicit` (FD-T1.1),
  performs `:skill/lookup` for an unknown skill. Asserts `SkillNotFound`
  (handler IS installed). Falsifiability: omit LD-1 wiring →
  `Unhandled("op ':skill/lookup' reached the bottom of the stack")`
  instead of `SkillNotFound` → assertion fails.
- **G2 — `tests/coder/test_cli_wiring.py::test_cli_run_enforces_recursion_budget`**.
  Replaces the bootstrap's raw-echo provider in place via `name="raw-echo"`
  (FD-T2.1, `install_handler` idempotent contract). Recursive `call_fn`
  triggers depth-4 nested `:llm/call`. Asserts
  `LLMRecursionBudgetExceeded(field="depth", limit=3, observed=4)`
  raised through `Coder.run()`. **Class A falsifiability**: drop
  `dispatcher_context(...)` wrap from `_session.py:87` → `ctx is None`
  at perform-time → middleware pass-through → recursion runs to bail-out
  return → `pytest.raises` reports DID NOT RAISE → test fails (verified
  manually at impl time). **Class B falsifiability**: `audit=False`
  regression in `_build_substrate_and_handlers` → ctx still bound by
  Coder.run but `ctx.depth` never increments → call_fn's bail-out check
  never trips → runaway recursion → Python's recursion limit raises
  `RecursionError` → `pytest.raises(LLMRecursionBudgetExceeded)` fails
  to match → test fails. Different exception shape, same regression
  catch (codex Impl R1 IMPORTANT-1 nuance, folded into both module +
  test docstrings).
- **G3 — `tests/coder/test_steering_sys_now.py::test_branch_default_fork_at_is_substrate_now`**.
  `pytest.mark.xfail(strict=True)` with explicit "DECORATOR MUST BE
  REMOVED in 2.4b" reason string. Body asserts
  `:coder/branch` datom's `fork_at` falls within
  `[s.effect.perform(":sys/now", {}) before, after]` sandwich. Today:
  XFAIL because `:sys/now` op doesn't exist + `_steering.branch()` is
  wall-clock. 2.4b spine must (a) land `:sys/now` op, (b) wire
  `_steering.branch()` default to it, (c) REMOVE the xfail decorator,
  (d) drop the W3 comments at `_steering.py:271,297,299,305-306`.
  Falsifiability mechanism = the xfail-strict + the spine action item:
  XPASS-strict triggers if 2.4b lands op+wiring but forgets the
  decorator removal; suite fails. Sandwich assertion is sharp enough
  that a wall-clock fallback wouldn't satisfy it incidentally.
- **G4 — `tests/effect/test_audit_signer_env.py` (5 tests)**.
  - `test_env_keyed_signer_signs_audit_entries` — generate ephemeral
    Ed25519 keypair, write PEM to `tmp_path`, set
    `PERSISTENCE_AUDIT_KEY=file:///...`, build substrate, perform 2-3
    `:llm/call`s, assert each `AuditEntry.signature` is non-None and
    `signer_id == "ed25519:<sha256(pem)[:16]>"`, then verify chain via
    `verify_chain(entries, public_keys=...)` returns True.
  - `test_env_unset_produces_unsigned_entries` — env-unset → entries
    have `signature is None` and `signer_id is None` (pre-2.4a backward
    compat).
  - `test_tamper_breaks_signature_verification` — Class-A falsifiability
    (FD-T4.3): rebuild a frozen `AuditEntry` with a swapped `args_hash`
    that keeps the surface signature/id but contradicts the signed
    content → `verify_chain` returns False at the content-hash check.
  - `test_unknown_uri_scheme_systemexits` — `PERSISTENCE_AUDIT_KEY=pem:abc123`
    → `_load_audit_signer_from_env` raises `SystemExit` naming the
    unsupported scheme.
  - `test_missing_pem_file_systemexits` — `file:///nonexistent/path.pem`
    → `SystemExit` with FileNotFoundError context.
  Falsifiability spot-check at impl time: temporarily replace
  `signer=signer` with `signer=None` in `canonical_audit_stack` →
  G4.1 fails with `AssertionError: entry.signature is None — LD-4
  kwarg-passthrough is broken (signer didn't reach make_audit_handler)`,
  pinpointing the exact `_audit_stack → make_audit_handler` hop. Line
  restored.

### Forced spec deviations (FDs)

- **FD-LD2** (CONFIRMED in T0): recursion-budget enforcement already
  inside `canonical_audit_stack` (`_audit_stack.py:250-303`). LD-2
  collapsed to assertion test only.
- **FD-LD3** (CONFIRMED in T0): `Transaction.now()` is dosync-frozen
  (`txn/transaction.py:95-99`), `_steering.branch()` is not in dosync.
  `:sys/now` is substrate-level scope. Path B (W3-rescope to 2.4b)
  locked.
- **FD-LD4-key-format**: `file:///absolute/path/to/key.pem` (RFC 8089
  three slashes) chosen over raw-PEM-in-env to leave room for a future
  `pem:` form.
- **FD-LD4-signer-shape RESOLVED** in T0: `make_audit_handler(signer:
  tuple[str, bytes] | None = None)` already landed by Mimir Phase B
  (`audit.py:488-518`). No Signer Protocol needed; `tuple[str, bytes]`
  IS the contract.
- **FD-T1.1**: G1 test uses synthesized `argparse.Namespace` +
  monkeypatched `detect_or_explicit` instead of `--provider echo`
  (`_cli.build_parser` rejects `echo`; choices are
  `{auto, anthropic, claude-code}`).
- **FD-T2.1**: G2 test installs replacement provider with
  `name="raw-echo"` to displace bootstrap handler in place. Required
  because `install_handler` resolves multiple `:llm/call`-wrapping
  handlers in outer→innermost order; bootstrap's `raw-echo` would
  intercept before the recursive callable. Idempotent name-replacement
  is the documented contract at `sdk/_facade.py:149-150`.
- **FD-T4.1**: spec mentioned `verify_audit_chain`; actual helper at
  `effect/handlers/audit.py:429` is `verify_chain` (re-exported as
  `persistence.effect.verify_chain`). Accepts
  `public_keys: dict[str, bytes]`. No new helper added.
- **FD-T4.2**: `_signing.sign` (`effect/_signing.py:60-74`) requires
  RAW 32-byte Ed25519 private key bytes, not PEM. CLI extracts raw via
  `cryptography.serialization` at the boundary; substrate-internal
  contract stays raw-only. PEM is the on-disk operator surface; raw is
  the internal wire shape.
- **FD-T4.3**: tamper test reconstructs `AuditEntry` with a
  contradictory `args_hash` rather than mutating in place
  (`@dataclass(frozen=True)` blocks mutation). The reconstructed entry
  keeps the original `id` + `signature` so the failure is detected at
  content-binding via `verify_chain`, not at raw signature
  verification.

### W3 rescopes queued

- **2.4b** — `:sys/now` substrate-time op + `_steering.branch()`
  wiring. Falsifiable acceptance signal: G3 xfail-strict in
  `tests/coder/test_steering_sys_now.py` flips PASS when 2.4b lands the
  op + wiring + removes the decorator. (FD-LD3 W3 marker.)
- **2.4b** — observability for `_dry_run_apply_action_safely` broad-Exception
  catch (queued from 2.3c.2 rescope #8).
- **2.4b** — `--confidence-threshold` CLI surface (deferred from 2.2a
  CP2 + 2.3b).
- **v0.9.x** — RSA / HSM-backed signer schemes (Ed25519 only in 2.4a).
- **v0.9.x** — multi-key signer rotation.
- **v0.9.x** — `pem:` URI scheme for raw-PEM-in-env (deferred until a
  real use case).
- **v0.9.x** — percent-decode `urlparse(...).path` if operator paths
  with spaces / escaped chars become a real use case (codex Impl R1
  NICE-TO-HAVE-1).

### Subagent pattern (9th use, hybrid)

Persistent implementer `impl-2.4a` for T1-T4 via SendMessage; controller-direct
T0/T9.1. Mirrors the 2.3c.1 / 2.3d hybrid pattern. Clean across all 4
hand-offs (no session-restart incidents). T0 receipts surfaced
FD-LD2 collapse + FD-LD3 W3-rescope before R0; codex R0 surfaced 5
BLOCKING + 3 IMPORTANT + 1 NICE which were folded inline before R0.1
lite PASS 8.46/7.8.

### Files changed

- **MODIFIED** `src/persistence/coder/__main__.py` — `_build_substrate_and_handlers`
  helper extraction, LD-1 skill handler install, LD-4 env-parse +
  Substrate.open audit_signer kwarg threading. (~108 LOC net.)
- **MODIFIED** `src/persistence/sdk/_facade.py` — `Substrate.open(audit_signer=...)`
  kwarg + thread to canonical_audit_stack. (~30 LOC.)
- **MODIFIED** `src/persistence/effect/_audit_stack.py` —
  `canonical_audit_stack(*, signer=...)` kwarg + forward to
  make_audit_handler. (~18 LOC.)
- **MODIFIED** `src/persistence/coder/_steering.py` — 4 W3-rescope
  comment edits (lines 271, 297, 299, 305-306) re-pointing `2.4a` →
  `2.4b`. No semantic change.
- **NEW** `tests/coder/test_cli_wiring.py` — G1 + G2 (2 tests, ~270 LOC
  including comprehensive falsifiability docstrings).
- **NEW** `tests/coder/test_steering_sys_now.py` — G3 xfail-strict W3
  marker (1 test, 140 LOC).
- **NEW** `tests/effect/test_audit_signer_env.py` — G4 round-trip +
  tamper + URI-scheme + missing-PEM (5 tests, 297 LOC).

### ARIS journey

- **R0** (codex, 2026-05-09 evening): PASS-WITH-FIXES, mean 7.19 / min 6.0.
  5 BLOCKING + 3 IMPORTANT + 1 NICE.
- **R0-fold** (controller, inline): all 9 findings closed. FD-LD2
  collapse documented, FD-LD3 W3-rescope to 2.4b documented, LD-4
  surface re-anchored to receipts (Mimir Phase B's
  `make_audit_handler(signer=...)` already exists), G2/G3 falsifiability
  rewrites with explicit XPASS-strict mechanism, signer URI form
  normalized to RFC 8089 three-slash, handler-ordering ambiguity
  resolved.
- **R0.1 lite** (codex re-review): **PASS, mean 8.46 / min 7.8** —
  cleanest first-pass-after-fold on the track since 2.3a (8.46/7.8).
  All 9 findings closed cleanly.
- **Impl R1** (codex, post-T4): **PASS, mean 8.48 / min 7.6** — 0
  BLOCKING, 2 IMPORTANT (doc-only) + 1 NICE-TO-HAVE (deferred to
  v0.9.x). The 2 IMPORTANT findings were folded inline at T9.1 (G2
  Class B failure-mode clarification + G4 module docstring alignment).
  Codex verdict: "Merge-ready into `feat/v0.9-persistence-coder`."

### Calendar

| Task | Budget | Actual |
|---|---|---|
| T0 receipts + ARIS R0 + R0-fold + R0.1 lite | 2.0h | ~2.0h |
| T1 G1 skill handler wiring | 0.5h | ~0.5h |
| T2 G2 recursion-budget assertion test | 0.5h | ~0.4h |
| T3 G3 `:sys/now` xfail + comment edits | 0.25h | ~0.35h |
| T4 G4 signer plumbing (3 prod files + 5 tests) | 1.0h | ~1.0h |
| T9.1 codex Impl R1 + I1+I2 fold + CHANGELOG + merge | 1.0h | ~0.7h |
| **Total** | **~5.25h** | **~5.0h** |

Sub-1 calendar day. Hard cutoff 2026-06-05; ~26 days runway after
2.4a, ~5× the 2.4a budget remaining for 2.4b + 2.4c.

---

## Phase 2.3d — 2026-05-09 (REPL Steering Integration — `_CoderSteeringSession` 7-op live debugger)

Phase 2.3d ships the operator-facing surface that turns the
persistence-coder ReAct loop into a **live debugger**. A new
`_CoderSteeringSession` class composes a `Coder` reference and exposes
seven ops over the existing v0.7.0a1 Module 7 REPL surface:
**pause / resume / snapshot / context_at / branch / fold / commit**.
Every op is audit-anchored — `:repl/request` opens the boundary, the
action body executes (with `:coder/branch` agent-side commit datom
emitted from `branch()` between the request/response pair), and
`:repl/response` closes the boundary. Branches are per-session-durable
in-memory via the existing `db.branch(fork_at, [Δ])` substrate
primitive (LD-1 codex consensus, 1-pass clean fold); `commit(branch_id)`
is a session-level pointer swap, not a substrate merge.

This is the LAST coder-side scope item before harden phases.
Completing 2.3d closes the Phase 2 coder-side maturity arc per the
critical-path sequencing (2.3c.2 recursion+composition → **2.3d REPL
steering** → 2.4a-d harden → 2.4c lockfile → `v0.9.0a1` tag by
2026-06-14, hard cutoff 2026-06-05; ~27 days runway, WELL UNDER
BUDGET).

### Locked decisions (LD0–LD5) — design ARIS R1 PASS (mean 8.31 / min 8.0)

- **LD-0 — SCOPE**: 7 ops (pause/resume/snapshot/context_at/branch/fold/
  commit). NO substrate touches. Concurrency invariants documented in
  the dataclass docstring (LD-2 set=go/clear=block; multi-pause/multi-
  resume idempotent; single-thread coder loop assumption).
- **LD-1 — Branch durability via `db.branch()` (codex consensus-locked,
  1-pass clean fold)**: substrate-true counterfactual branching using
  the existing `db.branch(fork_at, [directive_assertion])` primitive at
  `src/persistence/fact/db.py:543-582`. Branched DBs registered in
  `_CoderSteeringSession.branches: dict[str, DB]` keyed by deterministic
  `branch_id = sha256(parent_branch_id, fork_at.isoformat(),
  canonical_dumps(directive))[:16]`. NO new substrate primitive. NO
  in-memory dict, no new bitemporal `:branch/*` shape, no `s.txn.fork`
  reuse. Codex adversarial review converged single-pass — `db.branch()`
  IS the right primitive.
- **LD-2 — Pause attach point at iter head; threading.Event(set=go,
  clear=block)** (R0-fold B1): `Coder.run()` calls
  `self._steering_session._check_pause()` at the FIRST line of each
  `for i in range(self.max_iters)` iteration, BEFORE the
  `dispatcher_context(...)` block. `_pause_event` default state is SET
  (NOT paused — `wait()` returns immediately); `pause()` calls
  `clear()`, `resume()` calls `set()`. The `test_pause_blocks_then_
  resume_unblocks` G1 test MUST FAIL if the semantics are inverted —
  this is the load-bearing falsifiability for B1.
- **LD-3 — Capability tokens via `Capability(op="coder",
  qualifier="read|write|any")`** (R0-fold B5): extends ADR-3's closed
  Capability primitive. Mapping table:
  - `pause`/`resume`/`snapshot`/`context_at` → `(coder, read)`
  - `branch`/`fold`/`commit`                  → `(coder, write)`
  - any of the above grant via                 → `(coder, any)` union
  Schema-level representability test FIRST in G6 (R0-fold B5),
  then per-op denial parametrize. `cap_set=None` is the trusted
  in-process caller default — no check (production REPL adapters wire
  a real `CapabilitySet` at session construction).
- **LD-4 — Dual-datom audit (`:repl/request` + `:repl/response`) per
  op; `:coder/branch` agent-side commit datom on parent for
  `branch()`**: each public op emits `:repl/request` BEFORE the action
  body executes and `:repl/response` AFTER, bracketing the action so
  the pair forms a clean replay boundary. R0-fold I3: `:repl/request`
  payload for `branch()` records `fork_at` explicitly. R0-fold I4: G5
  filters to `:repl/* + :coder/branch` only. T9.1-fold B3: the
  `branch()` filtered delta is exactly `[":repl/request",
  ":coder/branch", ":repl/response"]` in that order.
- **LD-5 (FD-LD5) — `coder.fold(probe)` is session-side dict iteration,
  NOT `s.txn.fold`**: deviates from base Phase 2 design § 3.6 line 241
  ("uses #145 surface `s.txn.fold`"). Rationale: `s.txn.fold` emits
  facts on every iteration (foldl-with-emission semantic);
  `coder.fold(probe)` is read-only scoring and cannot use the
  substrate fold primitive without polluting the audit chain. Parent
  is registered first under reserved key `"parent"` (R0-fold B4);
  children iterate in `branches` dict insertion order. v0.10.x
  rescope: substrate primitive change to allow `s.txn.fold` with
  NULL fact-list path.

### Forced spec deviations

- **FD-LD5 (planned)** — session-side fold deviation; v0.10.x rescope.
- **FD-T2.1 (impl-time)** — `canonical_dumps` rejects dataclasses
  per `src/persistence/effect/canonical.py:5` ("rejects non-JSON
  types so the hash is never silently lossy") + `Datom` has no
  `to_dict()` method. Tests use direct `list(s._db.log())` equality
  for active-DB-invariance assertions. Structural — `Datom` is
  `@dataclass(frozen=True, slots=True)` so list-equality is correct.
- **FD-T2.2 (impl-time)** — test `call_fn` signatures match
  `make_callable_llm_handler`'s actual kwarg shape: `def call_fn(*,
  model, messages, tools=None, temperature=None, max_tokens=None)`.
  The plan's draft used required `tools` which `make_callable_llm_
  handler` always passes as `args.get("tools")` (potentially None).
- **FD-T6.1 (impl-time, pathway choice)** — Option A: add
  `:repl/request`, `:repl/response`, `:coder/branch` to BOTH
  `CANONICAL_AUDIT_WRAPPED_OPS` AND `CANONICAL_AUDIT_RAW_OPS` in
  `src/persistence/effect/_audit_stack.py`. The audit middleware
  automatically wraps these ops on `substrate.effect.perform`; the
  raw terminator (audit-only no-op clauses built from `RAW_OPS`)
  provides the bottom-of-stack handler so `perform` doesn't raise
  `Unhandled`. Mirrors the established 2.1c.6 `:claim/emit` /
  `:blob/put` pattern (audit-only ops where the request datom IS
  the audit signal). Drift-pin test in
  `tests/effect/test_canonical_audit_stack.py` updated to include
  the 3 new ops in `expected_wrapped` and the 2.3d-RAW assertion.
- **FD-T6.2 (impl-time, test concurrency)** — T1's
  `test_pause_blocks_then_resume_unblocks` uses `threading.Timer(0.15,
  session.resume)` to fire `resume()` after a delay. With T6's audit
  emission, `resume()` performs `:repl/request` + `:repl/response`
  via the audit ring, which uses the `_active` ContextVar via
  `mask()`. CPython threads spawned by `threading.Timer` start with a
  fresh empty context and don't inherit ContextVars. Fix: wrap the
  Timer target in `contextvars.copy_context().run(session.resume)`,
  mirroring the existing pattern on the worker thread.
  TEST-SIDE update only — production code paths don't have this
  issue because `_CoderSteeringSession` operations are called on
  the main thread in normal usage.
- **FD-T7.1 (impl-time, _caps.py shape)** — `src/persistence/repl/
  _caps.py` uses `OP_NAMES: tuple[str, ...]` + `QUALIFIERS_BY_OP:
  dict[str, frozenset[str]]` + `ALL_CAPS: frozenset[tuple[str,
  str]]` derived auto-from `QUALIFIERS_BY_OP`. The plan's draft
  expected a `Literal` type on `op`/`qualifier`. Adapter: just add
  `"coder"` to `OP_NAMES` AND `"coder": frozenset({"read", "write",
  "any"})` to `QUALIFIERS_BY_OP`. `__post_init__` already validates
  via `(op, qualifier) in ALL_CAPS`. Also: `CapabilitySet` takes
  `caps: frozenset[Capability]` (NOT `list`); test uses
  `CapabilitySet(caps=frozenset())` for empty-cap-set scenarios.
- **FD-T7.2 (impl-time, lazy import)** — `persistence.repl._ws`
  imports `aiohttp` at module load. Eager import of `_OpError` from
  `_ws.py` in `_steering.py` would pull aiohttp into the coder
  package's import-graph. Solution: lazy-import `Capability`,
  `ERR_CAPABILITY_DENIED`, and `_OpError` inside `_check_cap()` body.
  The `CapabilitySet` annotation on the `cap_set` field uses a
  `TYPE_CHECKING` string forward-ref so PEP 563 deferred evaluation
  handles type hints without runtime import.

### W3 rescopes queued

- **v0.9.x — SQLite-backed branch persistence** so branches survive
  REPL session restart (acceptance: xfail-strict marker on a test
  that round-trips a branched DB across `Substrate.open` calls and
  asserts `as_of(fork_at).datoms` equality post-restart).
- **v0.9.x — Concurrent-operator-thread support** beyond the current
  single-connection assumption (multi-WS-client cap_set isolation +
  mutual-exclusion on `commit()`).
- **v0.9.x — Audit-driven replay driver** consuming captured stream,
  re-performing recorded ops via the recorded payloads, asserting
  result-hash parity end-to-end. Current G5 covers determinism +
  tamper-evidence + Merkle-chain integrity, which together close the
  BLOCKING regression classes codex Impl R1 surfaced; the full
  driver is a separate workstream.
- **v0.10.x — `db.merge(branch_db, into=main_db)`** substrate-level
  merge primitive that would let `commit(branch_id)` actually
  retarget the running `Coder`'s substrate DB (codex Impl R1 I2:
  current `commit()` is a session pointer swap only).
- **2.4a — `:sys/now` routing for `fork_at`** joins the existing
  `:sys/now` bundle from 2.3b/c.2. Required for full byte-identity
  replay (currently `fork_at` is sourced from agent-side wall-clock).

### Test gates (G1–G6) — all green at the load-bearing falsifiability

- **G1 — pause/resume threading correctness** (3 tests in
  `tests/coder/test_steering_pause_resume.py`):
  pause-blocks-then-resume-unblocks (load-bearing falsifies B1
  inversion); no-pause-no-block; idempotent pause/resume (concurrency
  invariant N2).
- **G2 — `db.branch()` isolation + fork_at determinism** (4 tests
  in `tests/coder/test_steering_branch.py`, codex-revised version
  from consensus skill R1 verdict): branch_id returned + registered
  + non-parent; parent isolation (writes on branch never leak —
  `db.branch()` builds a fresh `InMemoryStore` with deepcopied
  datoms per `db.py:554-571`); fork_at determinism (two branches
  from same fork_at have byte-identical seeds);
  branch_ids unique across calls.
- **G3 — snapshot/context_at active_db invariance** (6 tests in
  `tests/coder/test_steering_snapshot.py`, R0-fold B3): both ops
  read-only — `db.log()` returns an iterator over the immutable
  store; `db.as_of(t)` builds a fresh `DBView` from a filter over
  the store. R0-fold I2: `branch_id` kwarg defaults to `"active"`,
  resolves to `"parent"` until first `commit()`.
- **G4 — fold parent inclusion + commit pointer swap** (4 + 4 tests
  in `test_steering_fold.py` + `test_steering_commit.py`, R0-fold
  B4): fold returns `len(scores) == len(self.branches) + 1` with
  reserved `"parent"` key first; FD-LD5 session-side iteration;
  commit is a pointer swap (`KeyError` on unknown branch_id, parent
  DB unmodified through any number of branch+commit cycles).
- **G5 — audit-chain integration with replay-meaningful
  falsifiability** (6 tests in `test_steering_replay.py`,
  T9.1-fold-strengthened):
  - `test_branch_emits_repl_request_then_coder_branch_then_response`
    (B3): exact filtered delta = `[":repl/request", ":coder/branch",
    ":repl/response"]`.
  - `test_pause_emits_repl_request_then_response_in_order`: same
    ordering for the read-only ops.
  - `test_branch_args_hash_changes_when_fork_at_changes` (B2 tamper
    falsifiability): same directive + DIFFERENT pinned fork_at MUST
    produce different `:repl/request.args_hash`. If impl drops
    fork_at from payload, both hashes collapse → test fails.
  - `test_branch_args_hash_matches_canonical_expected` (B2 exact
    shape): precomputed `canonical_hash` of expected payload
    `{"op": "branch", "payload": {"directive": directive, "fork_at":
    pinned.isoformat()}}` matches runtime `:repl/request.args_hash`.
  - `test_audit_stream_determinism_under_pinned_fork_at` (B1): with
    pinned `fork_at`, two runs of branch+fold+commit produce
    byte-identical `(op, args_hash)` sequences over the filtered
    `:repl/* + :coder/branch` stream — falsifies any payload drift
    or emission reorder.
  - `test_audit_stream_merkle_chain_is_intact` (B1): each entry's
    `prev_hash` references the previous entry's `id` over the FULL
    captured log; tampering breaks the chain.
- **G6 — capability schema + per-op denial** (8 tests in
  `tests/coder/test_steering_capability.py`, R0-fold B5): schema
  representability + unknown-qualifier raises (FIRST in test
  ordering); 6 per-op denial parametrize; cap denial fires BEFORE
  audit emission (denied ops never happened on the audit chain).

### Suite delta

`tests/coder/` 373 → 407 passed (+34 net: 33 new across 6 G-gates +
2 new in T9.1-fold replacing 4 old, minus 1 stub-converted-to-skip
in T1). `tests/effect/test_canonical_audit_stack.py` drift-pin
updated for `:repl/request`/`:repl/response`/`:coder/branch`. Full
repo: 2676 passed unchanged. Pre-existing 2 banner-text failures
(2.3b leakage) and 18 pre-existing repl-stack failures (aiohttp/
asyncio environment) verified unchanged via stash-test.

### ARIS history

- **Codex consensus on LD-1 branch durability**: PASS 1-pass clean
  fold (2026-05-09 — confirmed `db.branch()` is the right primitive;
  in-memory dict and new `:branch/*` shape both rejected).
- **Design ARIS R0**: PASS-WITH-FIXES mean 7.50 / min 6.5; 5
  BLOCKING + 4 IMPORTANT + 2 NICE folded inline at `8619154`.
- **Design ARIS R1**: PASS mean 8.31 / min 8.0 — DESIGN FROZEN at
  `edcb61d`.
- **Codex Impl R1**: PASS-WITH-FIXES mean 7.96 / min 7.0; 3 BLOCKING
  (B1 replay-isn't-replay + B2 fork_at-not-actually-asserted + B3
  branch-emission-ordering) + 3 IMPORTANT (deferred to v0.9.x).
- **Codex Impl R1.1 lite (post-fold)**: PASS mean 8.21 / min 7.8
  (clears soft-mode threshold 8.0 / 7.5). All 3 BLOCKING closed; no
  new findings. T9.1-fold lifted falsifiability across B1+B2+B3 +
  IMPORTANT I1 boundary semantics (split request-before /
  response-after across all 7 ops).

### Subagent-driven-development pattern (8th use, hybrid)

Persistent implementer (`impl-2.3d`) for T1-T7 via SendMessage
across 7 task hand-offs; T1.1 fixup commit for early Pyright
diagnostics; controller-direct T9.1 fold + CHANGELOG. NO
session-restart incident this phase (clean across all 7 hand-offs;
contrast with 2.3b T6 RECOVERY incident).

### Files in scope (NEW + MODIFIED)

- **NEW** `src/persistence/coder/_steering.py` (~410 LOC) — single
  module holding `_CoderSteeringSession`. 7 ops + helpers
  (`_check_pause` / `_check_cap` / `_resolve_db` /
  `_derive_branch_id` / `_emit_repl_request` /
  `_emit_repl_response`).
- **NEW** 6 test files in `tests/coder/`: `test_steering_pause_
  resume.py` (G1) + `test_steering_snapshot.py` (G3) +
  `test_steering_branch.py` (G2) + `test_steering_fold.py` (G4) +
  `test_steering_commit.py` (G4-extended) + `test_steering_
  replay.py` (G5) + `test_steering_capability.py` (G6).
- **MODIFIED** `src/persistence/coder/_session.py` — added
  `_steering_session` field + `_check_pause()` call at iter head
  in `Coder.run()`.
- **MODIFIED** `src/persistence/coder/__init__.py` — exports
  `_CoderSteeringSession`.
- **MODIFIED** `src/persistence/repl/_caps.py` — extended ADR-3
  closed Capability primitive (`OP_NAMES` + `QUALIFIERS_BY_OP`
  with `"coder"` entry).
- **MODIFIED** `src/persistence/effect/_audit_stack.py` — added
  `:repl/request` / `:repl/response` / `:coder/branch` to
  `CANONICAL_AUDIT_WRAPPED_OPS` + `CANONICAL_AUDIT_RAW_OPS`.
- **MODIFIED** `tests/coder/test_session_stubs.py` — retired
  `_check_pause` stub entry (filled in T1).
- **MODIFIED** `tests/effect/test_canonical_audit_stack.py` —
  drift-pin test extended for the 3 new audit ops.

### Branch + commits

Worktree branch `feat/v0.9-2.3d-repl-steering` off
`feat/v0.9-persistence-coder` parent at `54dca57` (2.3c.2 merge):

- `bfe5a0a` T0: design doc — REPL Steering Integration (LD0-LD5)
- `8619154` R0-fold: close 5 BLOCKING + 4 IMPORTANT + 2 NICE
- `edcb61d` R1-fix: DESIGN FROZEN (ARIS R1 8.31/8.0)
- `c877594` plan: T1-T7 + T9.1 implementation plan
- `8f24519` T1: `_CoderSteeringSession` skeleton + pause/resume
- `5af6d50` T1.1: fix Pyright unused-import diagnostics
- `9f1730b` T2: snapshot + context_at (read-side ops)
- `97c8b3e` T3: branch via `db.branch()` (LD-1 codex-consensus)
- `460aad3` T4: fold(probe) — session-side parent + children
- `d9f5346` T5: commit(branch_id) — session pointer swap
- `88fd623` T6: `:repl/request` + `:repl/response` audit emission
- `6105898` T7: capability gating — `Capability(op="coder", ...)`
- `02abc88` T9.1-fold: close codex Impl R1 BLOCKING B1+B2+B3

Critical-path next: 2.4a-d harden → 2.4c lockfile (~Fri 2026-06-12)
→ `v0.9.0a1` tag (by 2026-06-14). Hard cutoff Phase 2: 2026-06-05.

---

## Phase 2.3c.2 — 2026-05-08 (`:llm/call` recursion + `ComposeWithSkillAction` proposal acceptance)

Phase 2.3c.2 ships the SECOND half of the 2.3c skill-system rollout,
completing the trio: registry (2.3c.1) + recursion + composition. Two
intertwined lifts:

1. **`:llm/call` recursion.** The substrate-side `:llm/call` op is
   audit-wrapped at the canonical stack (Phase 2.1b precedent); when a
   registered skill's Plan AST contains a `:llm/call` leaf, the planner
   walks it and dispatches via `substrate.effect.perform(":llm/call",
   ...)`. Phase 2.3c.2 specifies + audits the recursion semantics
   explicitly: a per-iteration `DispatcherContext` ContextVar threads
   depth + cumulative request_count + cumulative token_count + cycle
   detection through every nested call. Hard `MAX_LLM_CALL_DEPTH=3`
   floor + `MAX_RECURSIVE_TOKENS=20_000` soft cap +
   `MAX_RECURSIVE_REQUESTS=10` soft cap. Budget exhaustion raises
   `LLMRecursionBudgetExceeded(field="depth"|"tokens"|"requests")`.
2. **`ComposeWithSkillAction` proposal acceptance.** 2.3b's MCTS
   expander wrapper REJECTED `ComposeWithSkillAction` proposals at TWO
   layers (kind-string drop at `_searcher.py:361` + isinstance
   belt-and-braces at `_searcher.py:369` — FD7 from 2.3b). 2.3c.2 lifts
   both rejection sites. The MCTS engine's `_apply_compose_with_skill`
   (`_mcts.py:187-209`) grafts the looked-up skill into the Plan AST
   during search; winner unparses through 2.3a's `_escalate_plan_body`
   unchanged. The expander wrapper threads the active SkillLibrary
   (from `coder.skill_library` field) into the search context, and the
   `:mcts/iteration` provenance datom records the looked-up skill's
   content hash in a NEW `composed_skill_content_hash` field for
   replay-explainability.

This is the LAST major coder-side scope item before harden phases.
Completing it lands ALL of Phase 2's coder-side maturity per the
critical-path sequencing (2.3c.2 → 2.3d REPL pause → 2.4a-d harden →
2.4c lockfile → `v0.9.0a1` tag).

### Locked decisions (LD0–LD7) — design ARIS R1.4 lite PASS (mean 7.68 / min 7.2, hard-mode + W3 honest-rescope per CLAUDE.md 2.0d-at-6.4 precedent)

- **LD0 — SCOPE**: 4 cross-cutting changes — (1) FD7 lift in
  `_searcher.py` (delete kind-string drop at line 361 + isinstance
  drop at line 369); (2) thread `skill_library: SkillLibrary` into
  search context via `_make_branch_expander` /
  `_make_branch_evaluator` signatures + bridge invocation of
  `mcts_search(... skill_library=...)`; (3) audit-anchor `:llm/call`
  recursion via per-dispatch `DispatcherContext` carrying depth +
  token-counter + cycle set (content-hash-keyed); (4) wire global
  resource budget at dispatcher boundary — both `:llm/call` recursion
  AND `ComposeWithSkillAction`-grafted skill body execution feed the
  same `DispatcherContext` counter (LD4 unified budget).
- **LD1 — `:llm/call` recursion at dispatcher; per-call DispatcherContext**:
  new `DispatcherContext` mutable dataclass with fields
  `depth: int = 0`, `token_count: int = 0`, `request_count: int = 0`,
  `cycle_path: list[str]` (content-hash-keyed active-path stack;
  R0-fold B3), `parent_audit_entry_id: str | None = None`, `budget:
  RecursionBudget`. ContextVar binding via `dispatcher_context(ctx)`
  context manager mirrors `persistence.effect.runtime.with_runtime`
  (Token-based set/reset). Lifetime spans full coder iteration cycle
  (one `coder.run()` step); persists across non-`:llm/call` ops
  (`:fs/read` / `:shell/exec` / `:git/diff` / etc.) interleaved
  between calls; reset between coder iterations (T4 wraps each
  iteration body with `dispatcher_context(DispatcherContext())`).
  **Depth semantics (R1.1-fold IMPORTANT)**: `ctx.depth` is the count
  of CURRENTLY-ACTIVE `:llm/call`s on the stack INCLUDING the call
  about to start. Initial state `0` (no active calls). On entry:
  increment FIRST, then check `ctx.depth > ctx.budget.max_depth` (`>`
  strict; equality allowed — with `MAX=3` allowed depths are
  `{1,2,3}`). On exit: decrement depth; `request_count` +
  `token_count` are CUMULATIVE across the recursion tree — NOT
  decremented on exit (LD1 cumulative-counter rule).
  **4-layer token enforcement (R1-fold B1+I1 corrected)**:
  - **Layer 1 (best-effort early reject)**: pre-call,
    `ctx.token_count >= max_tokens` → raise
    `LLMRecursionBudgetExceeded(field="tokens")` BEFORE provider call.
  - **Layer 2 (best-effort input estimation)**: pre-call,
    `len(json.dumps(args.get("messages", []))) // 4` rough estimate;
    if `ctx.token_count + estimated_input > max_tokens` → raise.
    Tokenizers undercount for some encodings/role/tool blocks
    (R1-fold I1); load-bearing safety is Layer 4.
  - **Layer 3 (output cap injection — provider-honest)**:
    `args["max_tokens"] = min(args.get("max_tokens", remaining),
    remaining)`. Demo providers Anthropic + OpenAI both honor
    `max_tokens` per public APIs. Streaming + arbitrary-provider
    safety OUT of 2.3c.2 scope (queued v0.9.x).
  - **Layer 4 (post-call hard accounting — load-bearing)**: after
    each call, `ctx.token_count += result["usage"]["total_tokens"]`
    when present; otherwise fall back to
    `estimated_input + injected_max_tokens` as conservative-overcount
    substitute (R1.1-fold NICE; runaway-protection becomes
    "best-effort overcount" for non-usage-reporting providers,
    queued v0.9.x for tokenizer-aware integration).
  **Honest claim statement**: any single `:llm/call`'s output is
  bounded by Layer 3's `max_tokens` cap if and only if the provider
  honors that field. The cumulative recursion-tree budget is enforced
  by Layer 4's post-call accounting which catches per-call overshoot
  before the next nested call. This is "best-effort safety" with a
  load-bearing recursion-tree cap — NOT "single-call provider
  runaway is mathematically impossible".
- **LD2 — Dual-layer cycle detection (R1-fold I2 split)**:
  - **Layer A — search-time STATIC subtree check** (already shipped
    at `_mcts.py:213-217`): `_apply_compose_with_skill` checks
    `if plan.id in skill_ids_in_subtree` and raises
    `_PlanCycleDetected` if the candidate plan's content-hash already
    appears as a subtree of the skill being grafted. 2.3c.2 KEEPS
    this layer unchanged. Detects STRUCTURAL cycles.
  - **Layer B — execution-time DYNAMIC active-path check** (NEW):
    `DispatcherContext.cycle_path` tracks **content hashes** as an
    ACTIVE-PATH STACK (LIST, not global set). T3 ships
    `push_cycle(ctx, content_hash)` (raises
    `SkillCycleDetected(_PlanCycleDetected)` on active duplicate)
    and `pop_cycle(ctx, content_hash)` (LIFO discipline; raises
    `RuntimeError` on top-of-stack mismatch). The hash key is the
    FULL `plan.id` (32-hex chars), NOT the 16-hex-char `skill_id`
    slice — content-addressed registry can have aliases (R0-fold B3
    callout). Sequential reuse after unwind IS allowed (skill A →
    completes → skill A again is OK; skill A → skill B → skill A is
    NOT). The middleware itself does NOT push/pop in 2.3c.2 — T3
    establishes the API surface; T4 / coder-side call sites own the
    actual push/pop (currently nowhere because skill-body recursion
    in 2.3c.2 is sequential at perform layer, not Python-stack-
    overlapping). The push/pop helpers are reserved for v0.9.x
    Python-stack reentrancy paths.
- **LD3 — Composition expansion: search-time RESOLUTION + content-hash
  PROVENANCE for replay-explainability (R0-fold B4 narrowed claim)**:
  when MCTS engine applies `ComposeWithSkillAction(skill_id=X)` via
  `_apply_compose_with_skill`, the looked-up skill plan's content
  hash (`plan.id`) is recorded in `:mcts/iteration` provenance
  alongside the existing `skill_id` field, via NEW
  `composed_skill_content_hash: str` field at
  `_mcts_datoms.py:_expand_proposal_record`. Replay byte-identity is
  provided by the WINNER PLAN AST being content-addressed (winner
  Plan is fully resolved post-graft; no remaining
  `[:compose-with-skill ...]` references). The pinned hash is
  REPLAY-EXPLAINABILITY (audit trail of which skill_id resolved to
  what content_hash + which Plan AST was grafted) — NOT replay
  byte-identity mechanism. Future-proofs v0.9.x lazy-graft option
  where the pin would become load-bearing for replay (execution must
  use pin, not fresh lookup, to avoid skill drift).
- **LD4 — UNIFIED dispatcher budget (codex DISAGREE-INDEPENDENT on c)**:
  `:llm/call` recursion AND `ComposeWithSkillAction`-grafted skill
  execution feed the SAME `DispatcherContext` budget counter.
  Independent limits would create bypass loopholes (N composed skills
  each with depth-1 recursion all locally legal but globally exceed
  depth=N). Cycle detection (LD2) applies to BOTH composition-driven
  and recursion-driven skill activation.
- **LD5 — Audit chain: linear append + parent pointer (W3 RESCOPE at
  T3-PREP, commit `98df051`)**: AuditEntries are appended LINEARLY to
  the canonical audit chain (existing semantics — prev_hash links
  each entry to its predecessor regardless of nesting). T2 ships the
  NEW field `parent_audit_entry_id: str | None = None` at the
  AuditEntry dataclass + canonical content hash + wire form
  (`:audit/parent-audit-entry-id` keyword key) + datom round-trip
  (`:parent-audit-entry-id` provenance slot) + spec
  (`audit/parent-audit-entry-id` qualified-name). Default `None`
  preserves field-level backward compatibility with all 2.0a-2.3c.1
  audit entries; chain-hash drift expected (R0-fold I3 — re-pinned
  in lockstep at T2). **W3 RESCOPE rationale**: during T3
  implementation analysis, a four-way constraint set was discovered:
  (i) G4's "peak depth=2" assertion + (ii) audit middleware's LIFO
  `try`/`finally` append ordering + (iii) AuditEntry's frozen +
  content-hashed invariant + (iv) linear prev_hash chain integrity.
  These four cannot be simultaneously satisfied for nested-Python-
  stack `:llm/call` cases without either (a) a two-layer audit-chain
  redesign (~150-200 LOC; pre-compute "header id" at entry, augment
  with verdict/result at finally) or (b) outer-finally back-patching
  with cascading hash recomputation. Both exceed phase scope.
  **Resolution: T2 ships the field; T3 wires DispatcherContext
  through the middleware for cycle detection (LD2) + budget
  enforcement (LD1) but leaves `parent_audit_entry_id` always None
  at the audit middleware layer. Active wiring of non-None values is
  rescoped to v0.9.x.** G3 reformulated as STRUCTURAL-ONLY (5
  assertion classes a-e; no middleware-population assertion). G4
  revised to assert SEQUENTIAL-recursion semantics (depth peaks at 1
  per call; request_count cumulative=2; depth bound MAX=3 retained
  as safety cap for hypothetical Python-stack reentrancy via tool-
  handlers). The depth-2-peak assertion was the original load-bearing
  G4(c) but is incompatible with sequential recursion at the perform
  layer; v0.9.x track gets the depth-2-peak Python-stack-reentrancy
  acceptance signal alongside the active wiring of
  `parent_audit_entry_id`.
- **LD6 — FD7 lift: 2 lines deleted in `_searcher.py`**:
  `_searcher.py:361` (kind-string drop of `ComposeWithSkillAction`
  pre-decode) + `_searcher.py:369` (isinstance belt-and-braces drop
  post-decode) BOTH REMOVED. `_dry_run_apply_action_safely` gains
  `skill_library: SkillLibrary | None = None` parameter forwarding
  to `apply_action`; without this, `_apply_compose_with_skill` would
  raise `_SkillNotRegistered` at the engine layer for every compose
  proposal, defeating the lift's purpose (FD-T5.1).
  `_make_branch_expander(coder, skill_library)` +
  `_make_branch_evaluator(coder, skill_library)` gain explicit
  parameter (falls back to `coder.skill_library` via `getattr`).
  `_escalate_branch_body` threads `coder.skill_library` into both
  factories AND into `mcts_search(... skill_library=...)`. The
  existing 2.3b reject test
  `test_expander_drops_compose_with_skill_action` is RECAST per LD6
  option (b) as a NEGATIVE coverage test: with `coder.skill_library
  = None`, ComposeWithSkillAction proposals still reject, but now via
  the dry-run layer rather than the wrapper-layer pre-decode bans.
- **LD7 — SUBSTRATE PREREQS: NONE.** `SkillLibrary` +
  `_apply_compose_with_skill` + `Action` ADT (incl.
  `ComposeWithSkillAction`) all already shipped (Phase 2.0c-prime
  for substrate; 2.3c.1 for coder-side bridge). Audit middleware
  extension for `parent_audit_entry_id` is INSIDE `audit.py` — not
  a new substrate primitive, just a new field on AuditEntry.
  `DispatcherContext` is INSIDE the coder boundary —
  substrate-side `s.effect.perform` is unchanged.

### Forced spec deviations

1. **FD-T1.1** (T1) — `RecursionBudget` field naming chose short-form
   lowercase (`max_depth` / `max_tokens` / `max_requests`) per
   dataclass conventions over uppercase mirroring the
   `MAX_LLM_CALL_DEPTH` / `MAX_RECURSIVE_TOKENS` /
   `MAX_RECURSIVE_REQUESTS` module constants. The short→long mapping
   is documented in the dataclass docstring. Spec-permitted choice
   per T1 task spec; no design conflict.
2. **FD-T2.1** (T2) — `repl/_audit.py` symmetric update. Discovery
   at T2 that `persistence.repl._audit.py` is a THIRD AuditEntry
   producer (alongside `effect/handlers/audit.py` + the 2.3c.1
   skill handler emit path). The `parent_audit_entry_id` field add
   required mirroring there for symmetry; otherwise `:repl/op`
   AuditEntries would have a different shape than canonical-chain
   entries. Documented inline at `repl/_audit.py:116`.
3. **FD-T2.2** (T2) — `parent_audit_entry_id` has NO bare-snake_case
   dual-namespace alias (the linear-chain pattern reserved this slot).
   Wire form is `:audit/parent-audit-entry-id` keyword key (matches
   2.3c.1 `:audit/skill-id` etc. convention). The dataclass field is
   already snake_case; canonical content hash includes it under the
   bare key. No additional aliases needed.
4. **FD-T3.1** (T3) — `_make_dispatcher_handler` reads
   `dctx.budget.max_tokens` (etc.) via the bound `RecursionBudget`
   instance, NOT via the module-level `MAX_*` constants. Necessary
   so test fixtures can exercise tight budgets via
   `DispatcherContext(budget=RecursionBudget(max_tokens=N, ...))`.
   The design's intent already had `RecursionBudget` as the override
   seam, but explicit confirmation in the impl path was required.
5. **FD-T3.2** (T3) — Layer 4 fallback charges
   `estimated_input + injected_max_tokens` (NOT just
   `estimated_input`). Discovered when G2.6's "subsequent-call sees
   overcount" test exposed that without the injected-cap component,
   two consecutive no-usage calls under a generous budget would not
   show cumulative growth (Layer 3 caps output but the fallback
   without the cap component would under-account). Spec text in
   design § LD1 R1.1-fold matches the fix exactly.
6. **FD-T3.3** (T3) — Datom shape returned by `audit_entry_to_datom`
   is a `dict` keyed via `":datom/provenance"`, NOT a named-tuple
   with a `.p` attribute as the initial G3.d test sketch assumed.
   G3.d tests adjusted to access
   `datom[":datom/provenance"][":parent-audit-entry-id"]`, which is
   the actual wire form per the existing impl.
7. **FD-T5.1** (T5) — `_dry_run_apply_action_safely` signature MUST
   gain a `skill_library` parameter for the FD7 lift to actually
   function. Without forwarding it to `apply_action`, every
   `ComposeWithSkillAction` proposal would still reject at the
   engine layer with `_SkillNotRegistered`, defeating the lift.
   Design § LD6 estimated `+10/-2` for `_searcher.py` but the actual
   change is broader (~+92/-34) because of this signature cascade
   into `_make_branch_expander` / `_make_branch_evaluator` /
   `_escalate_branch_body` plumbing.
8. **FD-T5.2** (T5) — `_make_branch_evaluator` gained a
   `skill_library` parameter for symmetry with
   `_make_branch_expander` even though the evaluator does NOT
   currently consume it. Marked `del skill_library` with a comment
   reserving it for future evaluator-side composition discipline
   (queued for 2.4a confidence-tied skill quality scoring).
9. **FD-T5.3** (T5; = design's anticipated FD-A3) —
   `_apply_compose_with_skill` itself didn't need changes, but
   `_expand_proposal_record` + `_reject_record` in
   `_mcts_datoms.py` did (both gain `skill_library: SkillLibrary |
   None = None` kwarg; emit `composed_skill_content_hash` only when
   lookup succeeds — emit-only-when-set pattern mirroring
   `txn_commit`). Three callsites in `_mcts.py` updated to forward
   `skill_library`: 2 in `_populate_children` (reject path, where
   `skill_library` was already in scope) and 1 in `mcts_search`
   (expand-phase loop, where `skill_library` is the function
   parameter).
10. **FD-T5.4** (T5) — G5.5 assertion revised under actual graft
    semantics. The design's literal R1-fold N1 wording asserts
    `looked_up_plan.id in collected_ids` post-graft, but
    `_apply_compose_with_skill` at `_mcts.py:218-224` (existing
    2.3c.1 code, NOT modified by 2.3c.2) constructs a NEW root with
    `tag=skill_plan.tag`, `attrs=skill_plan.attrs`,
    `children=(subtree, *skill_plan.children)` — so `skill_plan.id`
    is NOT preserved (different children → different content hash).
    Replaced with two equivalent falsifiability assertions: (A)
    every `skill_body.children` content hash appears in the winner
    AST, (B) the compose-root carries `skill_body.tag` and
    `skill_body.attrs` byte-identically with skill children present.
    Same sophisticated-bug catch ("pin correct, graft wrong" still
    fails) under the actual graft semantics.
11. **FD-T6.1** (T6) — G4(b)+(c)'s LITERAL `request_count == 2`
    only holds on the SIMPLE path (no MCTS). The full MCTS path
    interleaves multiple expander+evaluator `:llm/call` invocations
    under the SAME bound `DispatcherContext` (LD4 unified budget
    claim), pushing request_count to ~6+. Splitting G4 into separate
    `(a)(b)(c)-simple` and `(e)-mcts` sub-tests honors both design
    intents cleanly: the simple path gives sharp falsifiability for
    LD4 unified-counter; the mcts path gives sharp falsifiability
    for LD3 content-hash provenance pinning.
12. **FD-T6.2** (T6) — G4(f) replay byte-identity claim narrowed
    to winner Plan AST content-hash equality across runs, NOT audit
    chain byte-identity. Coder's `_decide` + `_act` emit wall-clock
    provenance facts (`:llm/messages`, `:llm/decision`,
    `:act/result`) BEFORE the audit handler emits, and those facts
    carry `dt.datetime.now` timestamps not routed through the
    pinned clock. Full audit-chain byte-identity would require
    pinning those provenance timestamps too (queued for 2.4a
    `:sys/now` substrate op). LD3 R0-fold B4 explicitly narrowed
    the replay-byte-identity claim to the winner Plan AST
    content-addressing, which IS what G4(f) verifies.

### Test surface

Six new test files under `tests/coder/`, all green:

- `test_recursion_dispatcher.py` — G1 `DispatcherContext` lifecycle
  + push/pop API surface (T1 + T3 extension): 37 G1 cases (counters
  / ContextVar binding / fresh-on-outermost / depth + request bound
  parametrized / `LLMRecursionBudgetExceeded` field validation /
  `SkillCycleDetected` is `_PlanCycleDetected` subclass) + 8
  push/pop cases (append / active-duplicate-raises / LIFO discipline
  / sequential-reuse-after-unwind allowed / content-hash keying not
  skill_id slice).
- `test_recursion_budget.py` — G2 budget enforcement at the
  audit-stack middleware: 26 cases covering all 7 sub-cases (G2.1
  depth bound, G2.2 token Layer 1+2, G2.3 request bound, G2.4 LD4
  unified budget, G2.5 Layer 4 post-call accounting, G2.6 Layer 4
  fallback, G2.7 Layer 3 output-cap injection) + 4 invariant tests
  (layer-ordering: bounds raise BEFORE provider invoked;
  pass-through invariant: no max_tokens injection without bound
  DispatcherContext).
- `test_recursion_audit_chain.py` — G3 STRUCTURAL-ONLY (per LD5 W3
  rescope at commit `98df051`): 23 cases across 5 assertion classes
  a-e (canonical-content-hash participation, field validation,
  `to_edn` / `from_edn` round-trip, datom round-trip via
  `:parent-audit-entry-id` provenance slot, middleware-layer None
  invariant under 2.3c.2 scope). G3.e tests will FAIL when v0.9.x
  activates `parent_audit_entry_id` wiring (intentional flip — the
  v0.9.x track's falsifiable acceptance signal).
- `test_composition_proposal_acceptance.py` — G5 FD7 lift verified:
  8 cases (G5.1 wrapper accepts proposal when skill_library
  provided, drops when None, drops when skill_id unregistered;
  G5.2 bridge passes coder.skill_library to mcts_search; G5.3
  expand-phase provenance carries `composed_skill_content_hash`
  equal to `looked_up_plan.id`; G5.4 LOAD-BEARING falsifiability:
  pinning is 32-hex content_hash NOT 22-char skill_id slice
  (length-discrimination); G5.5 LOAD-BEARING: post-graft AST
  contains skill_body's children + carries skill's tag/attrs at
  compose-root level).
- `test_recursion_composition_g4.py` — **G4 LOAD-BEARING** end-to-end
  recursion + composition: 5 tests across 6 falsifiability assertion
  classes (a-f). Split into (a)(b)(c)-simple-path (single test
  asserting chain integrity + cumulative request_count==2 + peak
  depth=1 per call), (d) cycle detection Layer A static + Layer B
  push/pop, (e) provenance content_hash via full MCTS path, (f)
  replay byte-identity via winner Plan AST plan.id equality across
  two pinned-clock runs.
- `tests/coder/test_searcher_expander.py` — UPDATED per LD6 option
  (b): `test_expander_drops_compose_with_skill_action` recast as
  `test_expander_drops_compose_with_skill_action_when_skill_library_absent`
  — wrapper-layer drops are gone; rejection now happens at the
  dry-run layer when `skill_library is None` or `skill_id` is
  unregistered. Negative coverage preserved.

Suite delta: 2562 → roughly 2562 + 122 net new tests across 5 new
files + 1 modified test file + 4 drift-pin re-pinnings (T2 audit
shape cascade) + 1 modified expander test (LD6 option (b) recast).
**122 recursion + composition tests pass** (37 G1 + 8 push/pop + 26
G2 + 23 G3 + 8 G5 + 15 expander + 5 G4). Full coder + effect + plan
suite: **1293 passed, 7 skipped, 8 xfailed** (excluding 2 pre-existing
CLI banner-text failures + 1 pre-existing flaky byte-identity test
in `test_loop_replay.py` — flake rate unchanged from pre-2.3c.2
baseline at ~3/5 runs both before and after).

**G4 is the load-bearing gate** per design § 4 codex consensus.
Falsifiability assertion classes split across 5 sub-tests:

- (a) Audit chain integrity over both `:llm/call` AuditEntries —
  prev_hash chain walked backward inner→outer matches; broken
  middleware would fail.
- (b) `request_count == 2` cumulative under bound DispatcherContext
  — captured spy snapshots; broken DispatcherContext binding would
  fail. LITERAL-2 holds only on simple path per FD-T6.1.
- (c) Peak depth = 1 per call (sequential perform-level recursion)
  — broken depth decrement would fail.
- (d) Cycle detection rejects A→A — Layer A direct
  `_apply_compose_with_skill` raises `_PlanCycleDetected`; Layer B
  `push_cycle` raises `SkillCycleDetected` on active duplicate.
- (e) `composed_skill_content_hash == looked_up_plan.id` survives
  end-to-end through `_escalate_branch_body` — full MCTS path;
  length-discrimination check (32-hex content hash, NOT 22-char
  skill_id) catches "fake" pinning.
- (f) Replay byte-identity via winner Plan AST plan.id equal across
  two pinned-clock runs (LD3 R0-fold B4: replay byte-identity is
  provided by content-addressing, NOT by audit-chain byte-identity).

### v0.9.x rescopes queued (per design § 8 W3 honest-rescope)

The R1.4 lite PASS at mean **7.68 / min 7.2** is below the standard
ARIS soft-mode threshold (mean ≥ 8.0 / min ≥ 7.5). This is a
hard-mode PASS via the W3 honest-rescope pattern (CLAUDE.md
2.0d-at-6.4 precedent + 2.3c.1 R1.2 precedent which also froze at
7.80/7.2). All in-scope findings are closed; the following residuals
are queued with falsifiable acceptance signals:

1. **LD5 `parent_audit_entry_id` ACTIVE WIRING (T3-PREP rescope at
   commit `98df051`)** — T2 already shipped the field at the
   dataclass + chain-hash + wire form + datom round-trip + spec
   layers; 2.3c.2 leaves the field always `None` at the audit
   middleware layer per the four-way constraint set surfaced during
   T3 implementation analysis. **Falsifiable acceptance signal for
   v0.9.x track**: integration test where a `:llm/call` audit entry
   produced from a Python-stack-nested dispatch carries
   `parent_audit_entry_id` referencing the outer dispatch's audit
   entry id (currently always None at middleware layer). Resolution
   requires either a two-layer audit-chain redesign (~150-200 LOC;
   pre-compute "header id" at entry, augment with verdict/result at
   finally) or outer-finally back-patching with cascading hash
   recomputation. Both exceed phase scope.
2. **LD2 Layer B execution-time RUNTIME WIRING (T9.1-fold rescope at
   commit on this phase's tail)** — T1 + T3 shipped the API surface
   (`DispatcherContext.cycle_path` + `push_cycle` + `pop_cycle` +
   `SkillCycleDetected`) with comprehensive unit-test coverage
   (T3 push/pop tests, 8 cases incl. content-hash keying + alias case
   + sequential-reuse-after-unwind). 2.3c.2 leaves the runtime call
   sites unwired — `_apply_compose_with_skill` does not mark grafted
   subtrees with skill content hash; planner does not push/pop
   `cycle_path` on grafted-subtree entry/exit. Layer A
   (search-time static subtree check at `_mcts.py:213-217`) is FULLY
   ACTIVE in 2.3c.2 and covers STRUCTURAL cycles — the demo's actual
   cycle exposure surface, since sequential perform-level recursion
   does not exhibit Python-stack reentrancy. **Falsifiable acceptance
   signal for v0.9.x track**: integration test where
   `_escalate_plan_body`'s grafted-skill-body walk pushes/pops
   `cycle_path` correctly and runtime cycles raise
   `SkillCycleDetected` end-to-end (currently API-tested only).
   Resolution requires marking grafted subtrees in
   `_apply_compose_with_skill` with the skill's content hash +
   planner-side push/pop on entry/exit of grafted nodes —
   substrate-side substantive work that exceeds 2.3c.2's stated
   scope. Surfaced via codex Impl R1 BLOCKING finding (mean
   7.84/min 7.4); resolved via T9.1-fold W3 rescope mirroring the
   LD5 pattern.
3. **Tokenizer-aware token counting for arbitrary providers** (LD1
   Layer 2 + Layer 4 best-effort gap) — current Layer 2 estimation
   uses `len(json.dumps(messages)) // 4` which under-counts for
   role/tool blocks per R1-fold I1; current Layer 4 fallback when
   `usage.total_tokens` is missing uses `estimated_input +
   injected_max_tokens` conservative-overcount substitute. v0.9.x
   will need provider-specific tokenizer integration for arbitrary
   providers.
4. **Streaming + arbitrary-provider safety beyond Anthropic +
   OpenAI** (LD1 demo-scope) — 2.3c.2 demo uses Anthropic + OpenAI
   which BOTH honor `max_tokens` per public APIs; streaming is OUT
   of demo scope. v0.9.x will need provider-specific honor-checks +
   streaming safety hooks.
5. **Replay tooling consumes `parent_audit_entry_id`** — depends on
   the LD5 v0.9.x activation above. Replay tooling can't consume
   what middleware doesn't populate.
6. **Production CLI wiring of recursion-aware dispatcher** — mirrors
   2.2b deferral pattern (and 2.3c.1's `make_skill_handler`
   deferral). The dispatcher handler from
   `_audit_stack._make_dispatcher_handler` is wired by default into
   `canonical_audit_stack(...)` so any `Substrate.open()` already
   has it; production CLI wiring of `Coder(skill_library=...)` (so
   `_escalate_branch_body` threads the live SkillLibrary into MCTS)
   is the gap. Queued for 2.4a.
7. **Full audit-chain byte-identity replay (FD-T6.2)** — Coder's
   `_decide` + `_act` emit wall-clock provenance facts before the
   audit handler emits; G4(f) only verifies winner Plan AST
   content-hash equality. Full audit-chain byte-identity requires
   pinning those provenance timestamps via a `:sys/now` substrate
   op. Queued for 2.4a.
8. **Observability for `_dry_run_apply_action_safely` broad
   `Exception` catch (T9.1-fold NICE)** — codex Impl R1 NICE
   finding: `_searcher.py:309`'s broad `except Exception` swallows
   unexpected engine bugs alongside the intended drop-malformed-
   proposals contract. **Queued 2.4a** (NOT v0.9.x — observability
   plumbing belongs in the dogfood/harden phase track alongside
   production CLI wiring at item #6): ensure failures are observable
   somewhere (metrics/log/telemetry) before silent-drop semantics
   become production-load-bearing. (Frame note: items #1+#2+#3+#4+#5+#7
   are v0.9.x-track; items #6 + #8 are 2.4a-track. Section header
   "v0.9.x rescopes queued" is shorthand — the queued track is per
   item.)

(All 5 carried v0.9.x rescopes from 2.3c.1 — Case E recovery, A7
PromotionRecord, multi-process define-races, store-identity-keyed
SkillLibrary registry, production CLI wiring of `make_skill_handler`
— remain queued unchanged.)

### Module layout

NEW:

- `src/persistence/coder/_recursion.py` (~376 LOC including
  comprehensive docstrings) — `DispatcherContext`,
  `RecursionBudget`, `LLMRecursionBudgetExceeded`,
  `SkillCycleDetected(_PlanCycleDetected)`, 3 module constants
  (`MAX_LLM_CALL_DEPTH=3`, `MAX_RECURSIVE_TOKENS=20_000`,
  `MAX_RECURSIVE_REQUESTS=10`), `_DISPATCHER_CONTEXT` ContextVar +
  `current_dispatcher_context()` + `dispatcher_context(ctx)`
  context manager + `enter_call` / `exit_call` bounds-check helpers
  + `push_cycle` / `pop_cycle` cycle-path helpers (T3 add). T1
  ships the type plumbing + ContextVar binding + bounds-check
  helpers; T3 adds push/pop API surface for LD2 Layer B execution-
  time dynamic active-path check.
- 5 new test files: `test_recursion_dispatcher.py` (491 LOC),
  `test_recursion_budget.py` (539 LOC),
  `test_recursion_audit_chain.py` (318 LOC),
  `test_composition_proposal_acceptance.py` (659 LOC — T5 G5),
  `test_recursion_composition_g4.py` (657 LOC — T6 G4
  LOAD-BEARING).

MODIFIED:

- `src/persistence/effect/handlers/audit.py` (+77 — T2: add
  `parent_audit_entry_id: str | None = None` field to AuditEntry;
  validation in `__post_init__`; include in canonical content hash
  + `to_edn` / `from_edn` round-trip + `audit_entry_to_datom` /
  `datom_to_audit_entry` round-trip via `:parent-audit-entry-id`
  provenance slot; ctx-dict read at finally-time always returns
  `None` per LD5 W3 rescope).
- `src/persistence/effect/_audit_stack.py` (+199 — T3: add
  `_make_dispatcher_handler` private factory wrapping `:llm/call`
  with 4-layer token enforcement + `enter_call`/`exit_call` bounds
  + ContextVar pass-through when no DispatcherContext bound;
  insert at OUTERMOST position in `canonical_audit_stack` so budget
  rejections raise BEFORE AuditEntry would emit).
- `src/persistence/spec/_canonical.py` (+8 — T2:
  `audit/parent-audit-entry-id` qualified-name + canonical
  ordering).
- `src/persistence/repl/_audit.py` (+9 — T2: symmetric update for
  the `:repl/op` AuditEntry producer; FD-T2.1).
- `src/persistence/coder/__init__.py` (+11 — T1: re-export
  `LLMRecursionBudgetExceeded` + `SkillCycleDetected` as public
  error surface; private value types stay package-internal).
- `src/persistence/coder/_session.py` (+30-50 — T4:
  `dispatcher_context(DispatcherContext())` wrapper around each
  iteration body in `Coder.run()` so per-iteration fresh ctx + LD3
  unified budget cycle reset; +6 — T5: `skill_library:
  SkillLibrary | None = None` field on Coder dataclass).
- `src/persistence/coder/_planner.py` (+~14 / -2 — T4:
  `REGISTERED_LEAF_TAGS` extended 12 → 13 with `:llm/call` so the
  planner can dispatch skill-body `:llm/call` leaves; docstring
  update).
- `src/persistence/coder/_searcher.py` (+92 / -34 — T5: FD7 lift +
  SkillLibrary threading through `_make_branch_expander` /
  `_make_branch_evaluator` / `_dry_run_apply_action_safely` /
  `_escalate_branch_body` / `mcts_search` invocation).
- `src/persistence/plan/_mcts.py` (+13 / -5 — T5 FD-T5.3: 3
  callsites updated to forward `skill_library` to
  `_expand_proposal_record` / `_reject_record`).
- `src/persistence/plan/_mcts_datoms.py` (+42 / -6 — T5: add
  `composed_skill_content_hash` field via `skill_library`
  parameter on `_expand_proposal_record` + `_reject_record`;
  emit-only-when-set pattern mirroring `txn_commit`).
- `tests/effect/test_audit_*.py` (+N — T2 drift-pin re-pinning
  across 4 files for the AuditEntry shape change).
- `tests/coder/test_planner_validate.py` (+/-N — T4 drift-pin from
  12 → 13 ops for `:llm/call` addition).
- `tests/coder/test_searcher_expander.py` (+26 — T5 LD6 option
  (b): negative-case recast for compose proposal drop when
  `skill_library` is None).

### Implementer pattern (7th use; hybrid + W3 rescope mid-phase)

Persistent semantic owner across the phase (T1, T3, T5, T6, T8) +
per-task fresh sessions for isolated tickets (T2) + controller-direct
(T4 coder-side bridge, T3-PREP W3 rescope, T7 SKIPPED). Total
implementer hand-offs: 5 (T1 → T3 → T5 → T6 → T8) on the persistent
semantic owner; T2 + T4 + T3-PREP rescope handled directly by
controller. **T7 SKIPPED** because G6 (cycle detection) is fully
covered by T3 push/pop unit tests (8 cases incl. content-hash
keying) + T5 G5.4 (alias case at unit level) + T6 G4(d) (Layer A
& Layer B end-to-end); G7 (boundary parametrized) is fully covered
by T1 G1.4 depth boundary + T3 G2 parametrized boundary tests. No
remaining T7 scope.

Pattern validated: NO 2.3b T6-style death incident across 5 hand-offs
(matches 2.3c.1 clean-pattern repeat). FD-T3.1 / FD-T5.1 / FD-T5.4
/ FD-T6.1 / FD-T6.2 all surfaced + closed in-task without
re-spec-review. FD-T2.1 (`repl/_audit.py` third-producer) was a
mid-T2 discovery; closed in-task. T3-PREP W3 rescope was a
mid-phase scope adjustment driven by T3 implementation analysis
surfacing the four-way constraint set; controller-direct rescope
of LD5 active-wiring + G3/G4 reformulation kept the rest of T3-T8
unblocked.

**Subagent-driven-development pattern (7th use, hybrid + rescope):**
- Persistent implementer: T1, T3, T5, T6, T8
- Per-task fresh: T2
- Controller-direct: T4, T3-PREP rescope, T7-skip decision

Calendar: ~5-7 hours controller time across 1 calendar day (T0
design + T1-T8 ship). 17-20 files in scope (~3000 LOC test
additions + ~600 LOC src additions including comprehensive
docstrings).

### Critical-path next

**2.3d** (REPL pause hook) → **2.4a-d harden** (dogfood + production
CLI + `:sys/now` substrate op for full audit-chain byte-identity per
FD-T6.2 + production CLI wiring of recursion-aware dispatcher and
SkillLibrary thread-through to Coder constructor) → **2.4c lockfile**
(~Fri 2026-06-12) → **`v0.9.0a1` tag** (by 2026-06-14). Phase 2 hard
cutoff: 2026-06-05; ~28 days runway. Status: **WELL UNDER BUDGET**
— 2.3c.2 was the LAST major coder-side scope item before harden
phases.

## Phase 2.3c.1 — 2026-05-07 (Skill library coder integration — `:skill/define` + `:skill/lookup` audit-anchored ops)

Phase 2.3c.1 ships TWO new audit-wrapped substrate effect ops
(`:skill/define`, `:skill/lookup`), wires them into the coder's plan
escalation path as registered leaf tags, and proves the full
define → lookup → inline-execute round-trip under the canonical audit
chain via a load-bearing G4 end-to-end test. The substrate-side
`SkillLibrary` (357 LOC at `src/persistence/plan/_skill_library.py`)
and the `s.plan.skill_library(db)` factory ALREADY shipped in Phase
2.0c-prime; 2.3c.1 is the bridge layer that lets the coder demo
register Plan AST subtrees as skills under audit accountability and
recall them procedurally as `:plan-edn` strings to splice into
subsequent plans.

This is the FIRST half of the 2.3c skill-system rollout. Per the
2.3c kickoff codex consensus pass, 2.3c.1 ships **define + procedural
recall** only — `:llm/call` recursion, `ComposeWithSkillAction`
proposal acceptance in the MCTS expander (lifting 2.3b's FD7
rejection), and any RUNTIME skill resolution at the dispatcher level
are all deferred to 2.3c.2. The 2.3c.1 model is a "data-flow
demonstration" that registered skill content survives a round-trip
through fact-store + canonical audit chain + plan execution under
coder control.

### Locked decisions (LD0–LD7) — design ARIS R1.2 lite PASS (mean 7.80 / min 7.2, hard-mode + W3 honest-rescope per CLAUDE.md 2.0d-at-6.4 precedent)

- **LD0 — SCOPE**: define + procedural recall, NO runtime resolution,
  NO recursion, NO MCTS interaction. The coder uses the new ops via
  standard Plan AST leaves emitted in `kind="plan"` decisions. Skill
  RESOLUTION is procedural — `:skill/lookup` returns the skill's Plan
  EDN as a substantive-return result; the coder inlines the EDN
  content into a subsequent `plan_edn` payload. NO dispatcher-level
  magic substitution, NO `:skill/compose` Plan AST primitive.
- **LD1 — `:skill/define` op shape (R0-fold B1)**: new audit-wrapped
  substantive-return op. The handler factory takes an INJECTED
  `SkillLibrary` instance (singleton-scoped to the Substrate), NOT
  one constructed per call. Per-call construction would yield empty
  `_plans` / `_records` caches on every fresh instance; cross-call
  `lookup` would always return `None`. The factory pattern matches
  `make_fs_handler` and `make_callable_llm_handler` precedents.
  Public return is `{":skill-id", ":plan-id"}`.
- **LD2 — `:skill/lookup` op shape (R0-fold B1)**: same pattern as
  LD1 — handler closes over the SAME injected `SkillLibrary`
  singleton. Public return is `{":plan-edn", ":promotion-id",
  ":plan-id"}`. The Plan AST `Node` itself is NOT returned — the
  coder receives a STRING it can splice byte-identically into
  subsequent `plan_edn` payloads. Splice-verbatim discipline is
  enforced by content-addressing (G4(h) parametrized falsifiability).
- **LD3 — `_PromotionRecordStub`**: minimal in-coder fabrication
  satisfying `_PromotionRecordLike` structurally
  (`@dataclass(frozen=True, slots=True)` with `promotion_id: str`).
  Invariant boundary (R0-fold I1): `promotion_id` is OPAQUE
  PROVENANCE only. 2.3c.1 makes NO correctness claim about
  promotion. A7's `PromotionRecord` integration is queued for
  v0.9.x. The `_PromotionRecordStub` is intentionally NOT
  re-exported (private; leading-underscore symbol).
- **LD4 — Failure modes mirror 2.3a LD4**: 3 new error classes in
  `handlers/skill.py`:
  - `SkillNotFound(ValueError)` — `:skill/lookup` on unregistered id
  - `SkillDefineValidation(ValueError)` — `:skill/define` arg-shape
    failure (missing/wrong-type fields, unparseable `:plan-edn`)
  - `SkillLookupValidation(ValueError)` — `:skill/lookup` arg-shape
    failure (missing/wrong-type `:skill-id`)
  Native traceback inheritance is queued v0.9.x per 2.3a LD4 W3
  rescope (`__cause__ is None` G6 acceptance signal flips when fix
  lands).
- **LD5 — Audit chain shape (R0-fold B3)**: both ops appended to
  `CANONICAL_AUDIT_WRAPPED_OPS`; NEITHER appended to
  `CANONICAL_AUDIT_RAW_OPS` (both are substantive-return; the
  bottom-of-stack handler is the actual side-effect site, mirrors
  2.2a `:fs/read` pattern). Threading invariant verified at
  design-time: `DB.transact` returns a NEW DB bound to the SAME
  underlying store; the underlying store mutation propagates
  automatically without rebinding `ctx.substrate._db`. The
  injected SkillLibrary singleton (LD1) ensures the library's own
  `_db` view stays current across calls. 5-case failure-mode
  taxonomy (A handler arg validation, B parse failure, C register
  raise, D happy path, E AuditEntry emission failure post-handler-
  return — Case E is THEORETICALLY POSSIBLE but no concrete
  in-scope path; recovery contract is ASPIRATIONAL, queued v0.9.x).
  Idempotent re-define ordering invariant: ZERO additional fact
  datoms, ONE fresh AuditEntry (call event IS the audit signal,
  not the fact-state delta — mirrors 2.2a `:fs/read` semantics).
- **LD6 — `REGISTERED_LEAF_TAGS` extension (R0-fold I2)**: 2 new
  tags appended to the closed set in `_planner.py`, taking it from
  10 → 12 ops. `_register_substrate_handlers` is a loop over the
  constant so the new ops auto-wire without further edits to the
  function body. Single-source-of-truth verified at design-time:
  only 3 reference points (definition, `__all__` export, runtime
  check inside `_check_nodes_recursive`) — no duplicate tag list
  introduced anywhere.
- **LD7 — SUBSTRATE-PREREQS: NONE.** All four SDK touchpoints
  (`s.plan.skill_library`, `s.plan.execute`, `s.fact.transact`,
  `s.effect.perform`) ship today. Zero substrate-prereq tasks.

### Forced spec deviations

1. **FD1** (T1, anticipated in design § 5; CONFIRMED at impl) —
   handler arg keys are BARE strings (no leading colon).
   `args["plan-edn"]`, `args["promotion-id"]`, `args["registered-at-ms"]`,
   `args["skill-id"]`. The EDN parser at `_parse.py:67-73` converts
   `{:plan-edn "..."}` map-keys to plain strings BEFORE the
   dispatcher adapter at `_planner.py:303` calls
   `substrate.effect.perform(tag, dict(node.attrs))`. Same convention
   as `fs.py:33` (`args["path"]`). Public RETURN map preserves
   keyword-form keys (`":skill-id"` etc.) per LD1 / LD2 spec.
2. **FD3** (T1, anticipated in design § 5; CONFIRMED at impl) —
   `parse(plan_edn, strict=False)` per LD1; required because skills
   can have arbitrary leaf tags outside 2.3a's closed 10-op set.
   `ParseError` is caught and wrapped in
   `SkillDefineValidation(field="plan-edn")` so the failure
   surfaces as a typed validation error in the plan-execution
   failure path. Bool numerics are rejected before the
   `registered-at-ms` int validation (Python's `bool` is an `int`
   subclass; allowing them would let `True`/`False` slip into the
   `skill/registered-at` fact datom).
3. **FD-T5.1** (T5) — `AuditEntry.op` keeps the leading colon
   (`":skill/define"`, not `"skill/define"`). `AuditEntry.__post_init__`
   at `audit.py:103-107` rejects non-leading-colon ops. Filter
   `e.op == ":skill/define"` exactly.
4. **FD-T5.2** (T5) — `s.effect.perform(op, args)` requires `args`
   as a positional dict, NOT `**kwargs`. The bare arg keys
   (`"plan-edn"`, `"promotion-id"`, `"registered-at-ms"`,
   `"skill-id"`) contain hyphens — invalid Python identifiers — so
   kwarg expansion fails with `TypeError: Runtime.perform() got an
   unexpected keyword argument 'plan-edn'`. The Plan AST
   dispatcher path at `_planner.py:303` already uses positional
   dicts (`substrate.effect.perform(tag, dict(node.attrs))`), so
   this matches production routing.
5. **FD-T6.1** (T6) — LD0 terminal mode-switch means a single
   `coder.run()` processes ONE plan decision then returns
   immediately (per `_session.py:77-79` early-return contract).
   The G4 "3-iter scripted scenario" is realized via THREE
   sequential `coder.run()` calls on the SAME Substrate — the
   fact-store + closed-over `SkillLibrary` singleton persist
   across calls. Each `run()` consumes ONE scripted LLM decision
   before the plan-escalation exit.
6. **FD-T7.G6.4** (T7) — CPython 3.12 frozen+slots dataclass
   fresh-attribute assignment raises `TypeError` BEFORE the
   `FrozenInstanceError` check fires. The G6 negative test for
   `_PromotionRecordStub` immutability accepts
   `(FrozenInstanceError, AttributeError, TypeError)` rather than
   pinning to `FrozenInstanceError` alone.
7. **FD-T8.1** (T8, NEWLY DISCOVERED at re-export wiring) —
   `persistence.plan` cannot be imported at module load time from
   `handlers/skill.py` without breaking the import graph.
   `persistence.effect.handlers/__init__.py` is loaded eagerly by
   `persistence.effect._audit_stack` (which imports
   `handlers.audit`); a top-level
   `from persistence.plan import SkillLibrary, parse, unparse` here
   triggers `persistence.plan._promotion` which itself imports
   `persistence.effect.datom_to_audit_entry` — circular.
   Resolution: lazy-import `persistence.plan` symbols (`parse`,
   `unparse`, `ParseError`) inside the `make_skill_handler`
   factory body. The `SkillLibrary` parameter annotation uses a
   string forward-reference under `from __future__ import
   annotations` (PEP 563 deferred evaluation) so no runtime import
   is needed for the type hint. Mirrors the 2.3a `_planner.py`
   lazy-import of `_session._summarize_result` and 2.3b
   `Coder._escalate_branch` lazy-import of
   `_searcher._escalate_branch_body`.

### Test surface

Six new test files under `tests/coder/`, all green:

- `test_skill_define_op.py` — G1 `:skill/define` op semantics
  (happy / idempotent / arg validation): 8 cases.
- `test_skill_lookup_op.py` — G2 `:skill/lookup` op semantics
  (happy / not-found / arg validation): 5 cases.
- `test_skill_audit_chain.py` — G3 audit chain integration
  (handler.wraps shape, prev-hash linkage, define→lookup chain
  spans both new ops, idempotent re-define emits FRESH AuditEntry
  with ZERO new fact datoms, negative-path SkillNotFound
  propagation): 6 cases.
- `test_skill_end_to_end_g4.py` — **G4 LOAD-BEARING** end-to-end
  define → lookup → inline-execute round-trip; parametrized
  verbatim/perturbed iter-3 splice; dedicated G4(g)
  idempotent-re-define-in-coder-loop case: 3 parametrized cases
  total. Per design § 4 codex consensus — without G4 passing, the
  registry-CRUD tests can pass while semantic invariants (audit
  anchoring, splice determinism, store identity) ship broken.
- `test_skill_planner_integration.py` — G5 planner integration
  (`REGISTERED_LEAF_TAGS == 12-tag set`, `_register_substrate_handlers`
  call-count spy at exactly 12, single-source-of-truth no
  duplicate hardcoded list outside the constant): 6 cases.
- `test_skill_negative_g6.py` — G6 negative tests (each error
  class raises in expected scenario; `LeafResult.error_repr`
  populated correctly; `PlanExecutionFailed` propagates): 6 cases.

Plus 1 modified test file: `tests/coder/test_planner_validate.py`
— the 2.3a-era `REGISTERED_LEAF_TAGS` closed-set drift-pin
extended in lockstep from 10 → 12 (T3).

**G4 is the load-bearing gate** per codex consensus. Falsifiability
assertion classes:
- (f) Full-payload datom match: 3 `skill/*` fact datoms with EXACT
  `(a, v, op)` triples — `("skill/plan", iter_1_plan_id, "assert")`,
  `("skill/promotion-record", input_promotion_id, "assert")`,
  `("skill/registered-at", input_registered_at_ms, "assert")`.
  Blocks the "writes 3 junk datoms while serving correct lookups
  from a separate cache" sophisticated-bug class.
- (g) Idempotent re-define mutation test: re-emit `:skill/define`
  with byte-identical args; assert `skill/*` fact-datom count
  unchanged AND AuditEntry count +1 AND same `skill_id` returned.
- (h) Splice byte-determinism (parametrized): verbatim case asserts
  `parse(iter_3).id == iter_1_plan_id`; perturbed case (1-byte path
  flip) asserts `parse(iter_3).id != iter_1_plan_id`. Empirically
  confirmed: `9b9e7b09... != a59a58bb...`.
- (i) Store-identity invariant: `id(s._db.store) ==
  id(skill_lib._db.store)` checked after EACH iter (R1-fold I1 —
  catches accidental fork/branch leak).

Suite delta: 2528 → 2562 (+34 net new tests across 6 new files +
1 modified drift-pin). All 262 coder tests pass; full effect
suite (384 passed) regression-free post lazy-import fix.

### v0.9.x rescopes queued (per design § 8 W3 honest-rescope)

The R1.2 lite PASS at mean **7.80 / min 7.2** is below the standard
ARIS soft-mode threshold (mean ≥ 8.0 / min ≥ 7.5). This is a
hard-mode PASS via the W3 honest-rescope pattern (CLAUDE.md
2.0d-at-6.4 precedent). All in-scope findings are closed; the
following residuals are queued with falsifiable acceptance signals:

1. **Case E recovery** (LD5) — AuditEntry emission failure AFTER
   successful handler return is THEORETICALLY POSSIBLE but no
   concrete in-scope path. The "reconstruct missing AuditEntry
   from durable datoms" recovery contract is ASPIRATIONAL.
   Queued for v0.9.x failure-injection track alongside concrete
   recovery mechanism.
2. **A7 `PromotionRecord` integration** (LD3) — `_PromotionRecordStub`
   is OPAQUE PROVENANCE only; A7's full `PromotionRecord` (when
   shipped) MAY reject or ignore skills registered via the stub.
   Queued for v0.9.x.
3. **Multi-process define-races** (LD1 R0-fold I3) —
   `SkillLibrary.register`'s check-then-transact has no
   uniqueness constraint at the fact-store level. Single-process
   coder execution serializes calls; multi-process registration
   needs a uniqueness query primitive. Queued for v0.9.x.
4. **Store-identity-keyed SkillLibrary registry** (LD1 R1-fold I1)
   — multi-store coder operations (`s.txn.fork`, `DB.branch`,
   isolated-branch fold) would silently bind SkillLibrary to a
   stale store. G4(i) static-invariant assertion catches the leak
   today; future fork/branch use needs handler-side detection +
   re-binding via store-identity-keyed registry. Queued for v0.9.x.
5. **Production CLI wiring of `make_skill_handler`** — mirrors
   2.2b's deferral pattern for fs/shell/code/git CLI wiring.
   `make_skill_handler(skill_lib)` installed via
   `s.effect.install_handler(handler, position="bottom")` at
   coder boot. Tests use isolated `Substrate.open("memory")`
   fixtures matching 2.2a precedent (`test_loop_replay.py:137`).
   Queued for 2.4a.

### Module layout

NEW:
- `src/persistence/effect/handlers/skill.py` (~300 LOC including
  T8 lazy-import comments)
- 6 test files (~1300 LOC total)

MODIFIED:
- `src/persistence/effect/_audit_stack.py` (+5 — `:skill/define` +
  `:skill/lookup` appended to `CANONICAL_AUDIT_WRAPPED_OPS`; T2)
- `src/persistence/effect/handlers/__init__.py` (+10 —
  `make_skill_handler`, `SkillNotFound`, `SkillDefineValidation`,
  `SkillLookupValidation` re-exports; T8)
- `src/persistence/coder/_planner.py` (+14 / -2 —
  `REGISTERED_LEAF_TAGS` extended 10 → 12, docstring update from
  "10 ops" to "12 ops"; T3)
- `src/persistence/coder/_prompt.py` (+~50 — `_SKILL_GUIDANCE`
  prompt section + `_PLAN_EDN_GUIDANCE` 12-tag listing; T4)
- `tests/coder/test_planner_validate.py` (+5 / -3 — drift-pin
  rename + 2 new tags in expected frozenset; T3)
- `src/persistence/effect/handlers/audit.py` — UNTOUCHED at
  module level; the `audit/<keyword>` datom encoding handles the
  new `:skill/define` and `:skill/lookup` ops via the existing
  `/ → .` transformation.

### Implementer pattern (hybrid per 2.3c kickoff codex consensus)

Persistent semantic owner across the phase (T1, T3, T5, T6, T8)
+ per-task fresh sessions for isolated tickets (T2, T4, T7).
Total subagent dispatches: ~9-12 (1 persistent-implementer reused
across T1/T3/T5/T6/T8 + per-task-fresh dispatches for T2/T4/T7
+ 6 codex passes for design ARIS R0/R0-fold/R1/R1-fold/R1.1
lite/R1.1-fold/R1.2 lite). FD-T8.1 (lazy-import circular fix)
discovered LATE at T8 re-export wiring; closed in-task without
re-spec-review.

### Critical-path next

**2.3c.2** (`:llm/call` recursion + `ComposeWithSkillAction`
proposal acceptance in the MCTS expander, lift 2.3b FD7 rejection;
depth limit + cycle detection) → 2.3d (REPL pause) → 2.4a-d
harden → 2.4c lockfile (~Fri 2026-06-12) → `v0.9.0a1` tag (by
2026-06-14). Phase 2 hard cutoff: 2026-06-05; ~30 days runway.
Status: WELL UNDER BUDGET.

## Phase 2.3b — 2026-05-07 (MCTS branch escalation — `_escalate_branch` wired to `_searcher._escalate_branch_body`)

Phase 2.3b fills the `_escalate_branch` stub that 2.3a left with
`CoderStubNotImplemented`. When the LLM emits `kind="branch"` with a
`payload["seed_plan_edn"]` field, the new bridge validates the seed plan
through a four-stage ingestion pipeline (byte budget →
`parse(strict=False)` → 2.3a's strict semantic validator
re-used verbatim → optional `mcts_config` resolution with bool-numeric
rejection + LD6 bounds), constructs LLM-driven expander + evaluator
provider closures whose audit datoms route through
`substrate.effect.perform(":llm/call", ...)`, runs `s.plan.mcts_search`,
and executes the WINNER once via 2.3a's `_escalate_plan_body`. Losing
branches are explored as Plan-AST structures only — they NEVER dispatch
their leaves through the substrate handler stack.

Branch escalation is a TERMINAL mode-switch — `Coder.run()` returns
immediately after `_escalate_branch_body` via the same early-return
shape that 2.3a's `_escalate_plan` uses.

### Locked decisions (LD0–LD7) — design ARIS R1.1 PASS (mean 8.16 / min 8.0)

- **LD0 — SCOPE**: MCTS-search drives over Plan-AST structures; the
  winner executes ONCE via 2.3a's `_escalate_plan_body`. NO execution-
  time handler dispatch happens during the search itself. Falsifiability
  pin: the disk-marker test in `tests/coder/test_searcher_search.py::
  test_only_winner_writes_marker_to_disk` constructs a winner whose
  plan writes one file and a seed whose plan would write a different
  file; after `_escalate_branch_body` returns, the file-system contains
  exactly the winner's file and zero loser files.
- **LD1 — TRIGGER**: `_should_escalate_branch` returns
  `decision.kind == "branch"` only. The previous confidence-based half
  (`or decision.confidence < self.confidence_threshold`) was removed
  per the LD1 R0 codex finding: a confidence-below-threshold trigger
  could route a `kind="act"` payload (which carries `{op, args}`) into
  the branch escalator that expects branch-specific payload contract,
  creating a type/shape mismatch. Confidence-based escalation is
  deferred to Phase 2.4a once dogfood calibration data exists.
- **LD2 — PAYLOAD**: `payload["seed_plan_edn"]` is the canonical EDN
  string. Stage 3 semantic validation re-uses 2.3a's strict
  `validate_plan_for_2_3a` verbatim — no looser sibling validator was
  introduced (R0-fold N1: a looser validator would waste search budget
  on plans the winner-execution path can't accept). Stage 1 byte budget
  is `MAX_BRANCH_EDN_BYTES = MAX_PLAN_EDN_BYTES = 8192`. The expander's
  dry-run wrapper rejects proposals whose `apply_action` result would
  fail the same validator.
- **LD3 — EXPANDER + EVALUATOR**: `LLMExpander.provider` and
  `LLMJudgeEvaluator.provider` closures route through
  `substrate.effect.perform(":llm/call", ...)` with the actual
  `_session.py:134-141` request shape `{model, messages, tools}`. NO
  `system` or `response_format` keys (they are not part of the
  registered `:llm/call` dispatch). The system message is embedded as
  `messages[0]` with `role="system"`. JSON-mode is enforced via the
  `tools` arg. Two new tool-use schemas live in `_prompt.py`:
  `EMIT_BRANCH_PROPOSAL_TOOL_SCHEMA` (expander) and
  `EMIT_BRANCH_SCORE_TOOL_SCHEMA` (evaluator). Response parsing +
  softmax normalization is bridge-side post-processing in
  `_parse_expander_tool_response` and `_parse_evaluator_tool_response`.
  `LLMJudgeEvaluator.provider` signature is `Callable[[Node], float]`.
- **LD4 — FAILURE**: two-path `BranchSearchFailed` funnel.
  - Path-1 (bubble-out): `mcts_search` raises (`ExpanderContractError`
    or any raw expander provider exception; per R1-fold I1
    `PlanDepthExceeded` is engine-caught at `_populate_children`
    `phase="reject"` and does NOT bubble). The bridge wraps with
    try/except, emits one `:act/result(op=":mcts/search", error=...)`
    via `_emit_search_failure_act_result`, and raises
    `BranchSearchFailed` with `search_id=None`, `iter_count=None`,
    `terminated_by=None` (search never completed enough to have an id).
  - Path-2 (post-search detection): `mcts_search` returns cleanly but
    `MCTSResult.terminated_by == "all_evaluations_failed"`. The bridge
    inspects post-search and raises `BranchSearchFailed` with all four
    fields populated from the returned `MCTSResult`.
  - 2.3a's `PlanExecutionFailed` propagates UNCHANGED for winner-
    execution failures (LD4 invariant — `BranchSearchFailed` is
    reserved for SEARCH-layer failures only, not post-search execution).
- **LD5 — AUDIT**: the substrate engine emits `:mcts/iteration` (Merkle-
  chained, content-addressed) + `:mcts/search` start/end + `:llm/call`
  per dispatch (canonical chain via `:llm/call` with request_hash +
  response_hash + model + tokens, wrapped under `:mcts/iteration`
  provenance) + `:plan/done` provenance on the winner via 2.3a's
  emission path. NO new audit shapes were introduced. ZERO `:fork/*`
  datoms — `mcts_search` uses in-memory dict transposition at
  `_mcts.py:881-882`, NOT `s.txn.fork` (R0-fold B1).
- **LD6 — MCTS-CONFIG**: branch-bridge default
  `_BRANCH_BRIDGE_DEFAULT_CONFIG = MCTSConfig(max_iter=50, expander_k=4)`.
  Distinct from the engine default `MCTSConfig()` of `max_iter=200`.
  ALWAYS used unless `payload["mcts_config"]` overrides; bounds-check
  on overrides via `MAX_BRANCH_MAX_ITER=50` / `MAX_BRANCH_EXPANDER_K=4`.
  Closed-set narrowing: only `max_iter` and `expander_k` are
  payload-overridable; other `MCTSConfig` fields require subclassing
  (queued for 2.4a).
- **LD7 — SUBSTRATE-PREREQS**: NONE. All four SDK touchpoints
  (`s.plan.mcts_search`, `s.plan.judge`, `s.fact.transact`,
  `s.effect.perform`) ship today.

### Forced spec deviations

1. **FD1** — `mcts_search.started_at_ms` must be a positive int (NOT
   bool, NOT float; enforced at `_mcts.py:870-877`). The bridge uses
   `time.time_ns() // 1_000_000` at the call site; routing through
   `:sys/now` is queued for Phase 2.4a.
2. **FD2** — `MCTSConfig.__post_init__` rejects bool numerics via
   `isinstance(v, bool)` check FIRST. The bridge's `_resolve_mcts_config`
   coerces JSON `true`/`false` to `BranchPayloadValidation` BEFORE
   `MCTSConfig(...)` construction so the bridge's error contract is
   uniform (`BranchPayloadValidation` not `ValueError`).
3. **FD3** — `LLMExpander.provider` signature is `Callable[[Node, int],
   Sequence[tuple[Action, float]]]` — `(node, k)` returns up to k
   `(action, prior)` pairs. `propose(plan, *, k)` is keyword-only k
   on the engine side; the bridge passes positional.
4. **FD4** — `LLMJudgeEvaluator.provider` signature is
   `Callable[[Node], float]`; `evaluator.evaluate(plan)` is the public
   method. `0.0` is a finite valid score per `_is_finite_score` —
   accepting it for malformed-response cases does NOT trigger the
   engine's `phase="reject"` absorption path.
5. **FD5** (R0-fold B1) — `mcts_search` uses in-memory dict
   transposition at `_mcts.py:881-882`, NOT `s.txn.fork`. The G4 audit-
   shape assertion in `tests/coder/test_searcher_failure.py` explicitly
   requires zero `:fork/*` datoms. The phase-table description in the
   active Phase 2 design doc was updated to reflect this.
6. **FD6** — `_derive_search_id(initial_plan.id, config, started_at_ms)`
   is deterministic given `started_at_ms` but NOT cross-run byte-
   identical (wall-clock advances). Replay byte-identity (record →
   replay → same audit chain) holds; cross-run byte-identity is not a
   2.3b acceptance criterion.
7. **FD7** — `Action` ADT is closed-isinstance-strict at `_mcts.py:80-81`.
   `ComposeWithSkillAction` is rejected at the wrapper layer via BOTH
   a kind-string check AND a post-decode `isinstance` check (belt-and-
   braces — the LLM may mislabel the `kind` field; isinstance catches
   the case after decode). `ComposeWithSkillAction` is deferred to
   Phase 2.3c when the skill library lands.
8. **FD-T2.1** — EDN canonical form is bare-keyword + attrs-map:
   `[:seq {} [:fs/read {:path "x.txt"}]]`. The plan-author drafted
   tests with the quoted-keyword form `'[":seq" [":fs/read" ":path" "x.txt"]]'`
   which the parser rejects (parses `":seq"` as a string scalar, not
   a node-tag keyword). Caught at T2; canonical form used throughout.
9. **FD-T6.1** — `:fs/write` argument key is `bytes_or_text` per
   `src/persistence/effect/handlers/fs.py:60`, NOT `content`. Caught at
   T6; corrected throughout the test fixtures.
10. **FD-T7.1** — `uuid.uuid4().hex` and `dt.datetime.now(...)` in
    `_emit_search_failure_act_result` require `# noqa: wall-clock` per
    the ARIS R2 F5 wall-clock ban. Mirrors `_session.py:213` _act._record
    precedent for the same datom-emission pattern. Latency tracking
    (`latency_ms=0` placeholder) deferred to Phase 2.4a.
11. **FD-T8.1** — `Coder._escalate_branch` body uses a lazy
    `from persistence.coder._searcher import _escalate_branch_body`
    inside the function to avoid module-load circular import. Mirrors
    the 2.3a `_escalate_plan_body` lazy-import pattern.

### Test surface

- `tests/coder/test_searcher_errors.py` — 5 unit tests for
  `BranchPayloadValidation` + `BranchSearchFailed` shapes (T1, G1
  partial).
- `tests/coder/test_searcher_build.py` — 6 G1 tests for `_build_seed_plan`
  Stages 1+2 (T2).
- `tests/coder/test_searcher_validate.py` — 5 G2 tests for
  `_validate_seed_plan_for_2_3b` re-use of 2.3a strict validator (T2).
- `tests/coder/test_searcher_config.py` — 9 G7 tests for
  `_resolve_mcts_config` (5 from T3 + 4 from T3-fix closing falsifiability
  gaps: closed-set narrowing on real MCTSConfig field, non-Mapping
  reject, float reject, positivity).
- `tests/coder/test_searcher_expander.py` — 11 G3 tests for
  `_make_branch_expander` + `_softmax_normalize` (5 from T4 + 6 from
  T4-fix: empty `tool_calls`, `proposals=None` non-iterable, dry-run
  rejection of `:branch` leaf, `AddStepAction` happy path, parametrized
  malformed proposals).
- `tests/coder/test_searcher_evaluator.py` — 12 G3 tests for
  `_make_branch_evaluator` (3 from T5 + 9 from T5-fix: empty
  `tool_calls`, NaN/Inf guard, malformed-score parametrize, missing
  score field, non-Mapping `tool_calls[0]`).
- `tests/coder/test_searcher_search.py` — 10 tests covering BOTH the
  bridge-isolated layer (7: disk-marker invariant via mock, mcts_search
  kwargs, default vs override config, winner→`_escalate_plan_body`
  delegation, byte-budget + semantic short-circuit) AND end-to-end
  real-MCTS engine control (3: LD0 disk-marker under engine, ZERO
  `:fork/*` datoms, exactly-one `:plan/done`).
- `tests/coder/test_searcher_winner_failure.py` — 3 G5b tests for
  winner-execution failure inheriting 2.3a's `PlanExecutionFailed`
  unchanged.
- `tests/coder/test_searcher_failure.py` — 7 G5a tests for the two-path
  `BranchSearchFailed` funnel (Path-1 bubble-out + raw provider
  exception coverage; Path-2 post-search detection; both paths emit
  one `:act/result` with `op=':mcts/search'`; both paths emit zero
  `:plan/done`).
- `tests/coder/test_searcher_rejection.py` — 5 G6/G7 tests with
  `side_effect=AssertionError + assert_not_called()` spy double-
  protection (2.3a precedent).

Suite delta: 2454 → 2528 (+74 net; baseline at 2.3a merge was 2454).

### Test-fixture pattern

Most of the `_escalate_branch_body` tests mock
`coder.substrate.plan.mcts_search` to a `MagicMock` returning a hand-
crafted `MCTSResult`, which isolates BRIDGE behavior from ENGINE
behavior (engine correctness is `tests/plan/test_mcts.py`'s job —
substrate-side, separately tested).

**End-to-end real-MCTS G4 acceptance signal** (added at T9.1 to close
codex Impl R1 I1+I2): three tests in `test_searcher_search.py` run
`s.plan.mcts_search` end-to-end with a smart `make_callable_llm_handler`
call_fn that distinguishes expander vs evaluator by tool name. With
`max_iter=2, expander_k=2`, the search produces winner + loser child
plans; the engine selects the highest-q winner via PUCT; the bridge
executes ONLY the winner via `_escalate_plan_body`.

These tests pin three load-bearing invariants under real engine
control:
- **LD0** disk-marker single-execution invariant — only `winner.txt`
  exists post-search (zero `loser.txt`, zero `seed.txt`).
- **LD5** zero `:fork/*` datoms (R0-fold B1 — `mcts_search` uses
  in-memory dict transposition at `_mcts.py:881-882`, NOT
  `s.txn.fork`).
- **LD5** exactly one `:plan/done` datom (winner execution emits via
  2.3a's `_escalate_plan_body`; losers never reach it).

Total real-MCTS test runtime: ~50ms across the three tests.

The earlier "fixed-proposal LLM mock deadlocks on transposition dedup"
concern was specific to a fixture pattern that used `s.effect.perform =
scripted_fn` direct method assignment. The working pattern (above)
uses `make_callable_llm_handler` with the proper handler-stack
installation, which routes correctly through `Substrate._runtime`.

### Module layout

NEW:
- `src/persistence/coder/_searcher.py` (~660 LOC)
- `src/persistence/coder/_searcher_errors.py` (~50 LOC)
- 9 test files (~2000 LOC total)

MODIFIED:
- `src/persistence/coder/_session.py` (+24 / -7 — `_escalate_branch`
  body delegation + LD1 gate simplification)
- `src/persistence/coder/_prompt.py` (+~140 — 2 new tool-use schemas +
  2 new system prompts + `_BRANCH_EDN_GUIDANCE`)
- `src/persistence/coder/__init__.py` (+9 — `BranchPayloadValidation` +
  `BranchSearchFailed` re-exports)
- `tests/coder/test_session_stubs.py` (-1 stub-premise test, -1
  parametrize entry, +1 LD1-invariant test)
- `tests/coder/test_loop_e2e.py` (-1 stub-raise test)

## Phase 2.3a — 2026-05-06 (Plan AST escalation — `_escalate_plan` wired to `_planner._escalate_plan_body`)

Phase 2.3a fills the `_escalate_plan` stub that 2.2a left with
`CoderStubNotImplemented`. When the LLM emits `kind="plan"` with a
`payload["plan_edn"]` field, the new bridge validates the plan through a
three-stage ingestion pipeline (byte budget → `parse(strict=False)` →
semantic validator), registers all 10 coder substrate ops as plan-leaf
handlers on a fresh `Dispatcher`, and runs `s.plan.execute`. On success
every leaf's handler result is recorded as a `:act/result` datom in walk
order and one `:plan/done` provenance datom is emitted before returning.
On failure a partial `:act/result` trace is emitted for every completed
leaf, a failure-shaped `:act/result` is appended for the failing leaf
(recovered via a pre-walked id→Node map), and `PlanExecutionFailed` is
raised so the caller's contract mirrors `_act`'s error surface. Plan
execution is a TERMINAL mode-switch: `Coder.run()` returns immediately
after `_escalate_plan_body` via the early-return at `_session.py:79`.

### Locked decisions (LD1–LD8) — design ARIS R1 PASS (mean 8.14 / min 7.8)

- **LD0 — terminal mode-switch contract**: `_escalate_plan` is a one-call
  bridge; `_session.py` returns immediately after on either path.
- **LD1 — gate predicate closure**: `_should_escalate_plan` returns
  `decision.kind == "plan"` (keyword `"plan"`, NOT `:strategy/plan`
  attribute). See § 211 prose update below and design doc § 3.4.
- **LD2 — `:plan/done` provenance datom**: Written via `s.fact.transact`
  after all leaf `:act/result`s. NOT in `CANONICAL_AUDIT_WRAPPED_OPS`.
  Shape: `{plan_id, leaf_count, walk_order_node_ids}`. Presence implies
  status=ok (failure path raises before this datom is written).
- **LD3 — extended `:act/result` shape**: Plan-leaf results extend the 2.2a
  `{op, args_hash, result_summary, error, latency_ms}` envelope. The
  `result_summary` field packs plan-context keys `{plan_id, node_id, tag,
  handler_id}` alongside the handler's own result. Single datom shape so
  2.2b LD3 `_render_latest_action` works without branching. Handler output
  passes through `_summarize_result`'s 512-char cap before merging;
  plan-context keys WIN over same-named handler-returned keys
  (`{**base, **plan_context}` merge order).
- **LD4 — id→Node pre-walk**: `_collect_all_nodes` called BEFORE
  `s.plan.execute` to build the `id_to_node` map. Failure-path leaf
  attribution relies on this map; it cannot be built post-failure.
- **LD5 — 10-op handler registration**: All 10 coder substrate ops
  (`:fs/read`, `:fs/write`, `:fs/glob`, `:fs/grep`, `:shell/exec`,
  `:code/run`, `:git/diff`, `:git/status`, `:git/log`, `:git/commit`)
  registered as plan-leaf handlers on a fresh `Dispatcher` per invocation.
  One `_make_adapter(substrate, tag)` factory closes over the substrate;
  all adapters call `substrate.effect.perform(tag, dict(node.attrs))`.
- **LD6 — three-stage ingestion**: Stage 1 byte budget (`≤ 8192` bytes
  UTF-8); Stage 2 `parse(strict=False)` (FD2 — strict would reject all
  coder tags); Stage 3 semantic validator is the SOLE safety layer.
  Stage-1/2/3 violations surface as `PlanPayloadValidation`.
- **LD7 — `_planner_errors.py` exception module**: `PlanPayloadValidation`
  (invalid plan structure, `field=` context) and `PlanExecutionFailed`
  (runtime leaf failure, `plan_id=` + `failed_node_id=` + `cause=`).
- **LD8 — few-shot EDN prompt guidance**: `_PLAN_EDN_GUIDANCE` string
  added to `_prompt.py` and injected into `build_messages` for
  `kind="plan"` cycles. Disambiguates bare `:branch`/`:code` (banned
  plan-spec primitives) from `:code/run` (coder substrate op, allowed).

### Test gates

- **G1** — `tests/coder/test_planner_errors.py` + `test_planner_build.py`
  (6 tests). `PlanPayloadValidation` field/reason shape, missing `plan_edn`,
  wrong type, byte-budget exceeded, `ParseError` wrapping.
- **G2** — `tests/coder/test_planner_validate.py` (6 tests).
  `validate_plan_for_2_3a`: root-must-be-`:seq`, empty-root rejection,
  node-count budget, depth budget, banned-leaf rejection, unregistered-
  leaf rejection.
- **G3** — `tests/coder/test_planner_dispatcher.py` (4 + 4 = 8 tests).
  T0 factory (4): `s.plan.new_dispatcher()` curated factory contract.
  T3 expansion (4): `_register_substrate_handlers` registers all 10 tags,
  adapter calls `substrate.effect.perform` with correct tag + dict-coerced
  attrs, `_make_adapter` name derived from tag.
- **G4** — `tests/coder/test_planner_execute.py` (8 tests; 6 spec'd + 2
  T4-fix2 regression). Happy-path: `_escalate_plan_body` returns None,
  emits per-leaf `:act/result` + `:plan/done` in walk order, `plan_id` in
  both datoms, `walk_order_node_ids` in `:plan/done`. T4-fix2 regressions:
  `walk_order_node_ids` present (initial draft omitted field), correct
  `{**base, **plan_context}` key-precedence.
- **G5** — `tests/coder/test_planner_failure.py` (7 tests; 6 spec'd + 1
  T3-residual adapter property). Failure path: partial `:act/result` trace
  emitted for completed leaves, failure-shaped `:act/result` for failing
  leaf, `PlanExecutionFailed` raised, `_DEFAULT_HANDLER_ID` imported from
  `_execute.py` (not duplicated literal), `_summarize_result` 512-char cap
  applied on failure-leaf result.
- **G6** — `tests/coder/test_planner_rejection.py` (10 tests; 7 spec'd +
  2 T2-IMPORTANT closures + 1 T6-fix nested-empty-`:seq`). Interior `:par`
  rejected (`field="interior_tag"`) closing silent-skip hole. Nested empty
  `:seq` raises `field="plan_body"` not `field="leaf_tag"` (correct defect
  class). All 6 original validator edge cases pass.
- **G7 (collapsed at R0-fold I1)**: Drift-pin assertions for `:plan/done`
  outside `CANONICAL_AUDIT_WRAPPED_OPS` were absorbed into G4's `:plan/done`
  emission shape tests; no standalone gate needed.

Total: gross 48 new tests (G1-G6 = 6+6+8+8+7+10+3 T0/T1 standalone = 48),
T7 deleted 3 stub-assertion tests → **net +45**. Suite delta: 2408 → 2453.

### Forced spec deviations and implementation-time discoveries

1. **T2 FD1 — `Node.tag` is KEYWORD-FORM**: `_ast.py:__post_init__` requires
   `tag.startswith(":")`. `REGISTERED_LEAF_TAGS` and `_BANNED_LEAF_TAGS`
   store keyword-form (`:fs/read`, `:seq`). `leaf.tag` used DIRECTLY
   throughout — no `f":{tag}"` prepending. Impl plan incorrectly claimed bare
   form.
2. **T2 FD2 — `parse(strict=False)`**: `strict=True` rejects all coder ops
   (`:fs/read`, `:code/run`, `:git/*`, `:shell/exec`) because the plan-spec
   enum at `_parse.py:211` covers only the closed canonical set. Stage 3
   semantic validator (`validate_plan_for_2_3a`) is the SOLE safety layer for
   the 2.3a coder subset — it is strictly tighter than strict-mode for this
   surface (explicit 10-op allowlist + banned-leaf list + interior-tag `:seq`-
   only restriction).
3. **T2 FD3 — `walk()` returns `list[str]`**: Walk returns a list of node ID
   strings, not `list[Node]`. The `visitor` callback is used to accumulate
   `Node` objects; `_depth()` is a separate recursive helper.
4. **T2 FD4 — banned-tag pre-screen before `walk()`**: `_check_nodes_recursive`
   validates every node BEFORE `_collect_all_nodes` calls `walk()`. `walk()`
   raises `UnimplementedNodeKindError` for `:branch`/`:code` leaves
   (`_walk.py:49`); banned checks MUST precede the walk call.
5. **T3 FD — `dict(node.attrs)` coercion**: `node.attrs` is a frozen
   `Mapping`; `substrate.effect.perform` expects a plain `dict`. Each adapter
   wraps `node.attrs` with `dict()` at call time.
6. **T4 FD — `latency_ms=0` for plan leaves**: Per-leaf wall-clock timing
   deferred to 2.4a (`:sys/now` substrate op). All plan-leaf `:act/result`
   datoms emit `latency_ms=0` at 2.3a.
7. **T4-fix — `:plan/done` payload corrected**: Initial draft omitted
   `walk_order_node_ids` from the `:plan/done` payload. Corrected to
   `{plan_id, leaf_count, walk_order_node_ids}` per LD2; regression tests
   added (2 of G4's 8 tests).
8. **T4-fix — `_escalate_plan_body` signature aligned**: Initial impl had
   `(plan, dispatcher, *, substrate)` parameter drift vs spec `(coder,
   decision)`. Corrected; `coder.substrate` accessed via duck-typed attribute.
9. **T4-fix2 — `_summarize_leaf_result` pipes through `_summarize_result`**:
   LD3 contract from 2.2b requires handler output capped at 512 chars BEFORE
   merging plan-context. Without this, a `:fs/read` returning 60KB would land
   verbatim in `:act/result.v`. Merge order `{**base, **plan_context}` ensures
   plan-context keys win over handler-returned keys with the same name.
10. **T5-fix — `_DEFAULT_HANDLER_ID` imported from `_execute.py`**: Not
    duplicated as a literal. Coupling consistency with success-path
    `LeafResult.handler_id` — both paths use the same source of truth.
11. **T6 IMPORTANT closures — validator tightened (two coderabbit residuals)**:
    (a) Interior nodes restricted to `:seq` only: `[:seq {} [:par {} [:fs/read {}]]]`
    was previously accepted; `:par` is interior so leaf-only checks skipped it.
    Now raises `field="interior_tag"`, closing the silent-skip hole. (b) Nested
    empty `:seq` raises `field="plan_body"` (correct defect class) not
    `field="leaf_tag"` (wrong class). T6-fix added 1 regression test (total G6 = 10).
12. **T7 — EDN prompt disambiguation**: `_PLAN_EDN_GUIDANCE` clarifies bare
    `:branch`/`:code` (plan-spec primitives, banned in 2.3a) vs `:code/run`
    (coder substrate op, allowed). Without this, an LLM can confuse the two
    similarly named tags.

### New datoms

- **`:plan/done`** — one entity per `_escalate_plan_body` success. Written via
  `s.fact.transact`; NOT in `CANONICAL_AUDIT_WRAPPED_OPS` (same supporting-
  provenance shape as `:act/result` and `:llm/decision`). Shape:
  `{plan_id, leaf_count, walk_order_node_ids}`. Absence means failure (failure
  path raises before this write).
- **Extended `:act/result`** — plan-leaf shape has plan-context keys
  (`plan_id`, `node_id`, `tag`, `handler_id`) packed INSIDE `result_summary`
  alongside the handler's own result. Same outer envelope as 2.2a `_act` so
  `_render_latest_action` renders both paths without branching.

### W3 honest-rescope deferrals

- **Native traceback passthrough (v0.9.x)**: `PlanExecutionFailed.cause` carries
  the original exception but traceback is not surfaced verbatim to the prompt.
  The 512-char `_summarize_result` cap applies on the failure-leaf result.
  Full native traceback passthrough queued as v0.9.x.
- **Per-leaf wall-clock timing (2.4a)**: `latency_ms=0` for all plan leaves.
  `:sys/now` substrate op (2.4a) unlocks real per-leaf timing.
- **LLM EDN syntax-error rate (2.4a)**: How often the LLM generates malformed
  EDN in practice is unknown at 2.3a. Calibration data from 2.4a dogfood will
  inform whether `_PLAN_EDN_GUIDANCE` or parser feedback needs strengthening.
- **Multi-iteration plans (2.5+)**: Single plan execution per `run()` call at
  2.3a; multi-plan chaining and loop re-entry after plan completion deferred.
- **`:branch` interior tag (2.3b)**: MCTS branch escalation. Banned at 2.3a.
- **Skill library (2.3c)**: Named versioned `PlanAST`-backed skills. Deferred.

### Refs

- Design: `docs/plans/2026-04-30-phase-2-persistence-coder-design.md`
- New modules: `src/persistence/coder/_planner.py`,
  `src/persistence/coder/_planner_errors.py`
- New tests: `tests/coder/test_planner_build.py`,
  `tests/coder/test_planner_dispatcher.py`,
  `tests/coder/test_planner_errors.py`,
  `tests/coder/test_planner_execute.py`,
  `tests/coder/test_planner_failure.py`,
  `tests/coder/test_planner_rejection.py`,
  `tests/coder/test_planner_validate.py`
- Suite delta: 2408 → 2453 (+45 net; +48 gross − 3 T7 stub-test cleanup).

---

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
