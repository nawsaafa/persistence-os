# persistence.sdk CHANGELOG

## v0.9.0a1 (unreleased) — Phase 7 Capability re-export (2026-05-11)

Phase 7 (`persistence-orchestrate` Anthropic Skill) adds `Capability` and
`CapabilitySet` to the curated SDK surface as re-exports from
`persistence.repl._caps` (the v0.7.0a1 closed-set ADT). Closes codex Impl R1
I1 finding — Phase 7's emitted orchestrator skills now import via the stable
`from persistence.sdk import Capability` rather than a private module path.

The re-export forced a substrate-side companion fix: `persistence.repl` now
lazy-imports `WSServer` via PEP 562 `__getattr__` so the package is
importable in fresh wheel installs without aiohttp (preserves the 2.4c G1
`test_built_wheel_installs_and_runs_coder_cli` smoke). See
`src/persistence/orchestrate/CHANGELOG-orchestrate.md` for the full Phase 7
receipt.

## v0.9.0a1 (unreleased) — Phase 2.4c lockfile snapshot + preflight manifest

Phase 2.4c freezes the v0.9.0a1 distribution surface. The curated
SDK surface is now pinned by an explicit **policy artifact** —
`tests/preflight_manifest.toml` — independent of the `_facade.py`
namespace introspection. Adding/removing/renaming a curated method
requires explicit manifest edit + reviewer sign-off; auto-generation
would invert "lockfile" semantics into "snapshot of whatever currently
exists."

### Added

- **`tests/preflight_manifest.toml`** — v0.9.0a1 agent allowed-entrypoints
  contract. Closed allowlist of curated SDK methods the persistence-coder
  agent (and Phase 7 skill consumers) are allowed to call. Schema:
  `[meta]` (version + authored + phase) + top-level `escape_callsites = []`
  + per-namespace `[allowed.fact]`, `[allowed.effect]`, `[allowed.txn]`,
  `[allowed.plan]`, `[allowed.audit]`, `[allowed.replay]` tables with
  `method = { note = "..." }` entries. `escape_callsites = []` is the
  v0.9.0a1 contract: any non-empty list is a v0.9.x rescope, not a 2.4c
  addition. Per LD-2 codex consensus (REJECT-FOR-NEW-OPTION-Z, 2026-05-11):
  *"Allowed entrypoints is policy, not reflection."*

- **`tests/sdk/test_preflight_manifest.py`** — G2 subset-resolution test
  + escape-callsites empty assertion + anti-regression test that scans
  the primary test body via `inspect.getsource` for snapshot-equality
  patterns (R1-fold I1). 3 tests, all PASS.

- **`tests/sdk/test_lockfile_distribution_smoke.py`** — G1 wheel-build
  + fresh-venv install + coder CLI smoke (LD-1 codex consensus
  NEW-OPTION-Z). Validates BOTH dev-env reproducibility (`uv lock --check`)
  AND consumer-side install (`pip install dist/*.whl` in fresh venv).

### Stability gates

- The 24 curated `s.plan.*` methods (incl. `s.plan.judge` from 2.0f),
  6 `s.fact.*`, 3 `s.effect.*`, 6 `s.txn.*` (incl. `fork` + `fold_into`),
  2 `s.audit.*`, 3 `s.replay.*` are enumerated in the manifest. Future
  v0.9.x SDK5 spec-doc generator will cross-check tier metadata against
  `__sdk_stability__` decorations (FD-LD2-stability-coverage).

## v0.9.0a1 (unreleased) — Phase 2.0f `s.plan.judge` curated SDK method

Phase 2.0f adds a curated invocation surface for the existing
`Evaluator` Protocol. Bhatt principle 5 (multi-agent collaboration —
different models cross-check each other) and principle 3 (tests as
guardrails — failing test as MCTS terminal-bad signal) both want a
standalone judge entry point: "score this plan with this evaluator,
return scalar, no MCTS."

### Added

- **`s.plan.judge(plan, *, evaluator) -> float`.** New curated method
  on `_PlanNamespace`, thin pass-through to the new top-level
  `persistence.plan.judge` function. Required keyword arg `evaluator`
  (any object satisfying the `Evaluator` Protocol — `LLMJudgeEvaluator`,
  `_StaticEvaluator`, or a custom implementation). Returns the float
  from `evaluator.evaluate(plan)`. Pure thin wrapper: no MCTS, no
  defaults, no rubric encoding. Caller embeds any criteria inside the
  evaluator's provider closure (typically
  `LLMJudgeEvaluator(provider=lambda p: my_llm.score(p, criteria='...'))`).
  Annotated `@experimental` with reason
  `"Phase 2.0f curated judge surface — Bhatt principle 5"`. Mirrors
  the established 24-method curated pattern from 2.0c-prime #147;
  widens the curated-method count 24 → 25.
- **`persistence.plan.judge`.** New top-level function exported from
  `persistence.plan`. Lives next to `LLMJudgeEvaluator` in `_mcts.py`.
  Pure thin wrapper over `evaluator.evaluate(plan)`. Substrate-side
  function the curated `s.plan.judge` SDK method delegates to.
- **`s.effect.install_handler(handler, *, position="bottom")` — curated handler-install surface** (`_facade.py`, `_EffectNamespace`). Replaces the prior pattern of reaching through `s.escape.effect` to mutate `Runtime.handlers` directly. `position="bottom"` inserts at innermost (provider-handler slot); `position="top"` appends at outermost (middleware slot). Idempotent: re-installing by `name` replaces in place. Phase 2.1b consumer: `persistence.coder.__main__` installs the chosen `:llm/call` provider handler under the canonical audit middleware. Class docstring updated to steer callers to the curated method first; `s.escape.effect` reserves to "raw runtime, advanced/test-only use".

### Compatibility

- `dir(s)` namespace count unchanged at 10 — `judge` is a method on
  `_PlanNamespace`, not a new top-level namespace entry. No migration
  note for adapter-author introspection (the 9 → 10 widening landed at
  2.0c-prime).
- G1 lockfile (Phase 2.4c) will add `s.plan.judge` to the
  persistence-coder agent's allowed-set.
- No substrate change. No audit chain change. No new primitives. No
  re-execution-replay implications. No `__version__` bump beyond the
  v0.9.0a1 unreleased target.

## v0.8.5a1 (unreleased — lands at Phase 2.0d sub-tag) — Phase 2.0d W4 micro-pass

Phase 2.0d W4 — concurrency scope honesty note. The substrate-
completion claim shipping at the v0.8.5a1 sub-tag is scoped to
**single-process Python under the GIL** for in-process audit-stack
install and effect-handler dispatch. Multi-process Postgres
SERIALIZABLE serialisation already shipped at v0.8.0a1 (PG1-PG6 —
`PostgresStore` + `transact_serializable` + cross-process Hypothesis
property test); that path is unchanged. No new in-process threaded
concurrency guarantees are claimed for v0.8.5a1. Threaded
multi-runtime concurrency is queued as a separate v0.9.x track.

## v0.8.5a1 (unreleased — lands at Phase 2.0d sub-tag) — Phase 2.0d W2 fix-pass

Phase 2.0d W2 (R2.2 ARIS hard-mode fix-pass) closes 2 NEW MAJORs +
2 NEW MINORs surfaced by the codex review at HEAD `8e06fa1` after
W1 landed. R2.2 raw at `/tmp/aris-r2-2-v0.8.5a1-raw.txt`.

### Fixed

- **R2.2 M5 — `AuditStackMissing` broke dosync atomicity (W1
  mis-architecture).** The W1 implementation raised
  `AuditStackMissing` from inside `_replay_effect_intents` AFTER
  `db.transact_batch` had already committed the body's facts —
  refs carried committed values despite the exception, audit
  datoms were silently lost. The W2 fix hoists the check to a
  PRE-commit gate at the top of `_commit_attempt` (Commit gate 0,
  before spec-validate / lock / transact_batch). True all-or-
  nothing: on `AuditStackMissing` no facts (refs / commute reapply
  / staged fold_into facts / commit datom) land in `db.log()`.
  New helper `persistence.txn._runtime_active()` is the shared
  predicate for the pre-gate and the post-commit replay path.
  `_replay_effect_intents`'s post-commit raise is kept as
  defense-in-depth (unreachable under the normal commit_attempt
  flow). `tests/txn/test_audit_stack_missing.py` extended:
  `test_intent_log_without_runtime_raises` now asserts `db.log()`
  length is unchanged + `view.entity(r.eid) == {}`; new
  `test_audit_stack_missing_pre_gates_before_transact_batch` spies
  on `db.transact_batch` and asserts call_count == 0 when the gate
  trips.
- **R2.2 m6 — `fold_into` provenance dropped (cleanup).** The W1
  `prov_for_batch` construction in `src/persistence/sdk/_fold_into.py`
  was dead code (built then bound to `_`). Per-staged-fact
  provenance cannot be expressed at the `transact_batch` layer
  (one provenance dict per call). Removed the dead block; comment
  updated to document the rationale and point at the
  `:fork/chosen` audit datom's `txn_commit` field as the load-
  bearing trace for replay/debug consumers. `provenance`
  argument is still accepted on the `fold_into` signature for
  forward-compat (still forwarded to `db.fork`).

### Documentation

- **R2.2 PARTIAL M4 + m5 — `:code/exec` docstring drift.** Module
  docstring (`src/persistence/effect/handlers/code.py:24`)
  switched from "fresh interpreter via `sys.executable -I -S`" to
  "`-s -P -S`" with the W1 rationale (PYTHONHASHSEED inheritance).
  Capability-denial layer 4 updated to reflect M6 (pathlib
  removed). New layer 5 documents the W1 (M1) curated
  `__builtins__` denial set and the explicit retention of
  `__import__` (IMPORT_NAME opcode resolution). Layer 7 (working
  dir) corrected — RLIMIT_FSIZE=0 only denies writes; reads are
  denied at the capability layer (no `open`, no `pathlib`, no
  `os`). Bootstrap-shim comment (line 652 region) corrected: the
  W1 comment listed `__import__` in the denied builtins set,
  which was wrong — the actual `_DENIED_BUILTINS` tuple has six
  names and `__import__` is intentionally retained. Comment
  rewritten with the IMPORT_NAME rationale.
- **AuditStackMissing class docstring** (`src/persistence/txn/errors.py`)
  tightened opening line ("a dosync attempts to commit with"
  rather than "intent-replay finds") and added a Phase 2.0d W2
  paragraph documenting the pre-commit hoist and the all-or-
  nothing atomicity semantics.
- **`test_open_is_denied` docstring** tightened — the "host-FS-
  reads denied" narrative is honest only post-M6 (because pathlib
  was the open()-bypass vector). Added cross-reference to
  `test_pathlib_import_is_denied`.

## v0.8.5a1 (unreleased — lands at Phase 2.0d sub-tag) — Phase 2.0d W1 fix-pass

Phase 2.0d W1 (R2 ARIS hard-mode fix-pass) closes 4 MAJORs + 4 MINORs
surfaced by the codex review at HEAD `4e118e9`. See
`review-stage/aris-r2-v0.8.5a1-raw.txt` for the full review.

### Added

- **`Substrate.open(uri, *, audit=True)`** — installs the canonical
  audit handler stack by default at substrate-construction time, so
  `:plan/edit` / `:fork/*` / `:code/exec` / `:fold/chosen` audit
  intents queued in a `dosync` reach the Merkle chain at
  `persistence.effect.handlers.audit` without callers needing to
  wrap usage in `with with_runtime(...)`. Pass `audit=False` only
  for sandbox tests where Merkle-chain enforcement is undesirable;
  in that regime, do not queue audit-emitting intents (you will get
  `persistence.txn.AuditStackMissing` at commit per the W1 fail-fast
  guard). (R2 MAJOR M2.)
- **`persistence.effect.canonical_audit_stack(entries)`** — public
  factory returning a `Runtime` with the canonical audit handler
  stack covering every audit-emitting op shipped through Phase 2.0a /
  2.0b / 2.0c / 2.0c-ext; backed by `persistence.effect._audit_stack`.
  `persistence.effect.CANONICAL_AUDIT_OPS` is the canonical op tuple.
  (R2 MAJOR M2.)
- **`persistence.txn.AuditStackMissing`** — raised by
  `_replay_effect_intents` when the intent log is non-empty but no
  effect runtime is active. Defense-in-depth guard for adapters that
  bypass the `Substrate.open` default install. (R2 MAJOR M2.)
- **`Transaction.staged_facts`** + **`tx.add_facts(facts)`** — opaque
  fact-dict staging on the transaction so surfaces like
  `s.txn.fold_into` can commit chosen-branch facts atomically with
  the outer dosync (rolled back on outer raise). Pre-W1
  `db.transact_batch` mid-dosync committed immediately. (R2 MAJOR M3.)

### Fixed

- **R2 M1 — `:code/exec` sandbox host-file-read + nondeterminism.**
  Curated `__builtins__` removes `open` / `eval` / `ex`+`ec` /
  `compile` / `input` / `breakpoint` from the user-source globals
  (capability-denial-not-detection per ADR-5).
  `PYTHONHASHSEED=0` and `PYTHONDONTWRITEBYTECODE=1` pinned in
  `child_env`; child argv switched from `-I` (which suppressed
  `PYTHON*` env vars) to `-s -P -S` so the seed pin actually takes
  effect. `__import__` stays callable so the import statement still
  works; the import filter rejects deny-listed top-level names
  whether reached via statement or direct call.
- **R2 M3 — `s.txn.fold_into` rolled-back chosen-facts atomicity.**
  Chosen-branch facts now stage onto `tx.staged_facts` via
  `tx.add_facts`; commit-time `_commit_attempt` folds them into the
  single atomic `transact_batch` call alongside `write_set` +
  commute reapply + commit datom. Outer-raise rolls them back along
  with the rest of the txn. The `:fork/*` audit datoms already rode
  the outer commit via `tx.effect` (Phase 2.0a precedent).
- **R2 M4 — stale `s.txn.fold_into` + `s.audit.*` docstrings.**
  `_TxnNamespace.fold_into` rewritten to reflect Phase 2.0c-ext
  rewire on `DB.fork` + canonical 4-datom audit shape; the pre-W1
  `:fold/chosen` mention was wrong. `_AuditNamespace` rewritten to
  reflect the Phase 2.0d W1 default-install regime; verify_chain
  now reads the AuditEntry-only canonical mirror.
- **R2 m1 — `_fork.py` `choose` callback contract.** Pre-W1 doc
  claimed the result list was immutable (false: list is mutable).
  Weakened the doc to "callers MUST NOT mutate; mutation produces
  undefined behaviour" per the W1 brief (cheaper than the
  hash-before/hash-after enforcement variant).
- **R2 m2 — `s.audit.verify_chain()` exploded on dict-shaped
  entries.** Pre-W1, `s.escape.*` first-access entries (plain dicts)
  in `_audit_entries` would crash `verify_chain` with
  `AttributeError` on `[-1].id`. Fixed by routing `verify_chain`
  through the `_canonical_audit_entries` mirror (AuditEntry-only) by
  default; falls back to filter-and-pass under `audit=False`.
- **R2 m3 — `db.py:_raise_fold_error` annotated `-> NoReturn`.**
  Closes 5 pre-existing Pyright "possibly unbound" warnings on
  `facts` / `new_acc` at lines 806-821 without changing runtime.
- **R2 m4 — RLIMIT_FSIZE preexec docstring overclaim.** Rewrote the
  comment: writes are denied (SIGXFSZ on overrun); reads remain
  possible. The M1 `open()` removal closes the host-file-read
  vector at the capability layer, not RLIMIT_FSIZE.

### Changed

- `_TxnNamespace.fold_into` `@experimental` reason string updated
  to reflect the post-W1 staging behaviour and Phase 2.0c-ext
  rewire on `DB.fork`.

## v0.8.5a1 (unreleased — lands at Phase 2.0d sub-tag) — `s.txn.fork` + `s.txn.fold_into` rewire (#145 + #145ext) + `s.plan` curated namespace (#147)

Phase 2.0c + Phase 2.0c-extended of the persistence-coder MVP (Phase 2
of the v1.0 roadmap). Adds `s.txn.fork(items, fn, choose, *, seed, tx)`
as the substrate-true speculate-rollback-pick primitive on the
`_TxnNamespace`, and rewires `s.txn.fold_into` on top of it. Folds in
the carryover #201 from the original 2.0c release: the canonical
4-datom audit shape from § 3.7 + § 4.3 (`:fork/probe` + `:fork/branch`
× N + `:fork/score` × N + `:fork/chosen`) with rollback semantics for
non-chosen branches now ships within Phase 2.0c.

The Path-A foldl-with-`:fold/chosen`-marker impl shipped at v0.8.0a1
is **superseded within Phase 2.0c**. `DB.fold` itself (the foldl/reduce
primitive) is unchanged — both primitives co-exist:

- `DB.fold` / `s.txn.fold` — foldl/reduce; commits all items' facts as
  it iterates; emits `:fold/chosen` when used via the legacy chosen-
  marker pattern.
- `DB.fork` / `s.txn.fork` — speculate-rollback-pick; commits only
  chosen branch's facts; emits `:fork/probe` + `:fork/branch` × N +
  `:fork/score` × N + `:fork/chosen`.

Stays `@experimental('v0.8')` through v0.8.5a1; promotes to
`@stable('v0.9')` after Phase 2 dogfood survives without API change
per ADR-7. § 4.3 of `docs/plans/2026-04-30-phase-2-persistence-coder-design.md`
is the canonical contract.

### Added

- **`s.txn.fork(items, fn, choose, *, seed=None, tx, on_error='stop',
  provenance=None) -> ForkResult`** — substrate-true speculate /
  score / pick / rollback primitive. `fn(branch_state, item) ->
  branch_state` operates on opaque Python state, not on the
  substrate; per-branch isolation is structural; rollback is trivial
  (non-chosen branches' state is just discarded Python objects).
  `tx` is required keyword-only so the canonical 4-datom audit
  emission rides the enclosing dosync.
- **`ForkBranchResult`** (`@dataclass(frozen=True)`) — per-branch
  outcome with `branch_index`, `branch_id` (16-hex content-address),
  `item`, `branch_state`, optional `score` (populated by adapter
  layers like `fold_into`), optional `error` (populated under
  `on_error='continue'`).
- **`ForkResult`** (`@dataclass(frozen=True)`) — return value carrying
  `chosen_index`, `chosen_state`, immutable `all_branches` tuple,
  `txn_commit_uuid`.
- **`ForkOutsideDosync`** (`RuntimeError`) — raised when `DB.fork`
  is called outside an active `db.dosync(...)` body or without a
  `tx` argument.
- **`ForkChooseError`** (`RuntimeError`) — raised when `choose`
  callback violates its contract; original exception is `__cause__`.

### Changed

- **`s.txn.fold_into` rewired on top of `DB.fork`.** Public signature
  unchanged; downstream callers unaffected at the API level. Internal
  behavior changes (intentional, called out in the v0.8.0a1 -> v0.8.5a1
  delta):
  1. Each branch starts from `seed` in **isolation** (NOT from the
     previous branch's accumulator — `DB.fork` is not a foldl).
     `chosen_accumulator` is now `seed + chosen_item`, not the
     foldl-accumulated sum.
  2. **Only the chosen branch's facts are committed** to the
     substrate — non-chosen branches' facts are rolled back (never
     reach `db.history()`). `total_datoms_committed` now reflects
     only the chosen branch's count.
  3. `fn` raising under `on_error='abort'` propagates the original
     exception directly (NO `FoldError` wrapper — that was Path-A's
     `DB.fold`-routed shape, now superseded).
  4. Audit emission switched to the canonical 4-datom shape
     (`:fork/probe` + `:fork/branch` × N + `:fork/score` × N +
     `:fork/chosen`); the legacy `:fold/chosen` op is no longer
     emitted by `fold_into`. `DB.fold` keeps `:fold/chosen` for users
     who want the foldl-with-marker pattern.
  5. `checkpoint_every` kwarg now raises `ValueError` if non-zero
     (no longer meaningful under `DB.fork`'s per-branch isolation;
     documented as deprecated rather than silent semantic drift).

### Audit datom shape — `:fork/*` 4-datom emission

Per `fold_into` / `fork` call, in this order under the outer dosync
(so all share `txn_commit` and a stable Merkle prev-hash chain of
`2 + 2*N` entries):

```python
# 1. :fork/probe — one
tx.effect(":fork/probe",
    seed_hash=str,         # 16-hex sha256 over canonical-JSON of seed
    items_hash=str,        # ditto over the items list
    fn_hash=str,           # over (qualname, module) tuple
    choose_hash=str,       # ditto for choose callable
    branch_count=int,
)

# 2. :fork/branch — one per branch
tx.effect(":fork/branch",
    branch_index=int,
    branch_id=str,         # 16-hex content-hash of (item, branch_state)
    item_hash=str,
    branch_state_hash=str,
)

# 3. :fork/score — one per branch
tx.effect(":fork/score",
    branch_index=int,
    score_value=Any,       # canonical-JSON-stringified for non-scalars
    score_hash=str,
)

# 4. :fork/chosen — one
tx.effect(":fork/chosen",
    chosen_index=int,      # index into ALL-branches list (DB.fork's view)
    chosen_branch_id=str,
    chosen_state_hash=str,
)
```

`_txn_commit` (commit_id) is auto-injected by
`persistence.txn.transaction._replay_effect_intents` at commit time;
the audit handler at `effect/handlers/audit.py` chains the `:fork/*`
intents into the same Merkle chain as `:plan/edit` / `:code/exec`.
**No new chain code** — `:fork/*` rides the existing chain by being
regular effect intents.

### Determinism contract — § 4.3 acceptance gate

For fixed `(seed, items, fn, choose)` inputs (where `fn` and `choose`
are pure / deterministic), the FULL 4-datom intent sequence is
byte-identical across replays — verified via Hypothesis property
tests at `@max_examples=200` in
`tests/store/test_fold_byte_identity.py`:

- `test_s_txn_fold_into_4_datom_shape_byte_identity` — full intent
  sequence (probe + branch × N + score × N + chosen) is byte-
  identical (kwargs-equality + canonical-JSON-bytes).
- `test_s_txn_fold_into_chosen_datom_byte_identity` — single
  `:fork/chosen` payload byte-identity (backward-regression cover).
- `test_s_txn_fold_into_rolls_back_non_chosen_branches` — for any
  `(seed, items)` input + deterministic `fn` + argmax `choose`, only
  the chosen branch's eid appears in committed substrate facts.
  Non-chosen branch eids (modulo collisions with the chosen eid) MUST
  be absent.

### Why two primitives (`fold` vs `fork`)

`fold` is a transactional foldl/reduce that commits every item's
facts as it iterates — the right shape for "accumulate over a
sequence with audit-traceable provenance". `fork` is the
speculate-rollback-pick primitive — the right shape for "evaluate N
candidate branches, pick the best, discard the rest". The two
primitives are semantically distinct; the namespaces (`:fold/*` vs
`:fork/*`) reflect that to avoid silent meaning-drift across replays
of trajectories upgraded between v0.8.0a1 and v0.8.5a1.

### Carryover backlog (still open)

- Re-execution-replay for `:fork/*` (re-run `fn` on replay rather than
  audit-replaying recorded results). Per § 3.7 audit-replay is the
  default for all gates and re-exec is opt-in only for `:code/exec` in
  v0.9; the new datoms have audit-replay as the load-bearing contract.
  Re-exec for `:fork/*` lands in v0.10 alongside FS-snapshot work.

### Closed (folded into 2.0c-extended)

- **#201** (proposed under v0.8.0a1 carryover): full
  `:fold/probe`/`:fold/branch`/`:fold/score` datom emission via
  per-branch child-txn primitive on `DB.fold`. Resolved as a separate
  `DB.fork` primitive with `:fork/*` namespace (intentional split per
  ADR-7); `DB.fold` keeps its v0.8.0a1 semantics intact.

### Added — Phase 2.0c-prime #147 `s.plan` curated namespace

- New curated `s.plan.*` SDK namespace exposes Plan AST + parse +
  execute + optimize + promote + MCTS + edit (Phase 2.0a) + registries
  + skill-library factory via thin pass-throughs to `persistence.plan`.
  Each method is `@experimental("v0.8.5a1")`, matching the
  `s.txn.fold` / `s.txn.fold_into` / `s.txn.fork` ADR-7 surface-naming
  precedent. The full curated method inventory:
  - **Plan AST + parse**: `parse`, `unparse`, `walk`.
  - **Execute**: `execute`.
  - **Edit (Phase 2.0a)**: `edit_step`, `insert_step_after`,
    `insert_step_before`, `delete_step`.
  - **Optimize**: `optimize`.
  - **Promote**: `promote`, `gate_g1_replay_byte_identity`,
    `gate_g2_audit_chain`, `gate_g3_score_delta`, `gate_g4_stub`.
  - **MCTS**: `mcts_search`, `mcts_promote`, `apply_action`.
  - **Registries** (mutate global state — caveat documented per
    method): `register_metric`, `unregister_metric`, `lookup_metric`,
    `register_coercion`, `unregister_coercion`, `lookup_coercion`.
  - **Skill library**: `skill_library` (factory).
- Curated SDK type re-exports added: `Node`, `ExecutionResult`,
  `OptimizedPlan`, `PromotionRecord`, `TrainingExample`, `LeafResult`,
  `FailureInfo`. Other `persistence.plan` types — configuration /
  protocol vocabulary (`MCTSConfig`, `Action` subclasses, `Evaluator`,
  `Expander`, `Dispatcher`, `Handler`, `MetricRef`, `Coercion`,
  `SkillLibrary`, error classes) — remain accessible only via direct
  import from `persistence.plan`. The split keeps the SDK contract
  surface narrow per ADR-1 without forcing every adapter to import
  the whole plan module just to type-hint a return value.
- `dir(s)` contract surface widens from 9 to 10 entries (adds
  `plan` between `open` and `replay` in lexical position). Adapter
  authors who pinned against `len(dir(s)) == 9` MUST update; the
  contract surface widening is the v0.8.5a1 SDK-gap closure for the
  existing `persistence.plan` substrate.
- No substrate behavior change. No audit chain change. No new
  primitives. Strictly a curated re-export layer over the existing
  `persistence.plan` module.
- Adapter authors who previously reached via `s.escape.plan` (which
  fires a `:sdk/escape-hatch-access` audit entry on first access) can
  now bind to the curated `s.plan.*` surface; the escape-hatch path
  remains available unchanged for any sub-surface not yet curated.
- Closes #147.

### Added — Phase 2.0d (#148 closed-as-redundant — folded into 2.0c-prime contract closure)

- Added 13 MCTS configuration + protocol re-exports to
  `persistence.sdk` (`MCTSConfig`, `MCTSEdge`, `MCTSNode`,
  `MCTSResult`, `MCTSPromotionResult`, `Action`, `AddStepAction`,
  `SubstituteLeafAction`, `ComposeWithSkillAction`, `Evaluator`,
  `Expander`, `LLMExpander`, `LLMJudgeEvaluator`).
- **Decision**: After Phase 2.0c-prime exposed
  `s.plan.mcts_search` / `mcts_promote` / `apply_action`, the only
  remaining MCTS gap was config/protocol vocabulary still requiring
  direct `persistence.plan` imports. A separate `s.mcts` namespace
  was evaluated and rejected — same lifecycle as `s.plan`, so a
  50-line re-export expansion under the 2.0c-prime SDK contract is
  the right shape.
- **Migration**: Adapter authors who imported
  `from persistence.plan import MCTSConfig, AddStepAction,
  LLMJudgeEvaluator, ...` may now import from `persistence.sdk`
  directly. Both paths remain valid (the underlying module's surface
  is unchanged); the `persistence.sdk` import becomes the
  contract-pinned form once v0.8.5a1 is tagged.
- The remaining un-re-exported plan-module names (`Dispatcher` /
  `Handler` — dispatch-system types, non-MCTS; `MetricRef` /
  `Coercion` / `SkillLibrary` — registry/factory types; plan-level
  error classes) stay in `persistence.plan` because their canonical
  home is the underlying module.
- Substrate untouched. Audit chain untouched. No new primitives.
  No `__version__` bump in this commit (lands with v0.8.5a1
  sub-tag at Phase 2.0d Stage 4 after ARIS R2).
- Closes #148 (as folded into the 2.0c-prime SDK contract).
