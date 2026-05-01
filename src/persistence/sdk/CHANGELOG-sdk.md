# persistence.sdk CHANGELOG

## v0.8.5a1 (unreleased — lands at Phase 2.0d sub-tag) — `s.txn.fork` + `s.txn.fold_into` rewire (#145 + #145ext)

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
