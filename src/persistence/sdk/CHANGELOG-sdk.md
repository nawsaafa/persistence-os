# persistence.sdk CHANGELOG

## v0.8.5a1 (unreleased — lands at Phase 2.0d sub-tag) — `s.txn.fold_into` (#145)

Phase 2.0c of the persistence-coder MVP (Phase 2 of the v1.0 roadmap).
Adds `s.txn.fold_into(seed, items, fn, choose, *, tx)` as a sibling of
`s.txn.fold` on the `_TxnNamespace` curated surface — a convenience
that runs `DB.fold` with an agent-extended `(acc, item, db) ->
(new_acc, facts, score)` reducer signature, applies a user-supplied
`choose` callback over the per-branch scores, and emits a single
`:fold/chosen` audit datom marking the winning branch. The audit
datom rides the existing Merkle chain at
`persistence.effect.handlers.audit` (no new chain code), same path as
`:plan/edit` and `:code/exec`.

Stays `@experimental('v0.8')` through v0.8.5a1; promotes to
`@stable('v0.9')` after Phase 2 dogfood survives without API change
per ADR-7. § 4.3 of `docs/plans/2026-04-30-phase-2-persistence-coder-design.md`
is the canonical contract.

### Added

- **`s.txn.fold_into(seed, items, fn, choose, *, tx, **kwargs) -> FoldIntoResult`**
  — convenience method on `_TxnNamespace`. `tx` is required keyword-only
  (mirrors `persistence.plan.edit_step`'s `*, tx` pattern) so the
  `:fold/chosen` audit datom always rides an enclosing dosync
  transaction. Calls outside dosync (or with `tx=None` inside dosync)
  raise `FoldIntoOutsideDosync`.
- **`FoldBranchScore`** (`@dataclass(frozen=True)`) — per-branch
  `(item, score, accumulator_after)` triple passed to `choose`. Frozen
  so `choose` cannot mutate the score list and the audit datom can
  quote scores back deterministically.
- **`FoldIntoResult`** (`@dataclass(frozen=True)`) — return value
  carrying `chosen_index`, `chosen_score`, `all_scores`,
  `chosen_accumulator`, `final_accumulator`, `total_datoms_committed`.
- **`FoldIntoOutsideDosync`** (`RuntimeError`) — raised when
  `fold_into` is called outside an active `db.dosync(...)` body or
  without a `tx` argument.
- **`FoldIntoChooseError`** (`RuntimeError`) — raised when `choose`
  callback or `fn` reducer violates its contract; original exception
  is `__cause__`. Single-classed (rather than separate `fn`-error and
  `choose`-error siblings) because both manifest as "the agent's
  speculation contract is broken" — callers want one `except` block.

### Audit datom shape (`:fold/chosen`)

```python
tx.effect(
    ":fold/chosen",
    chosen_index=int,         # 0-based index in the score list passed to choose
    chosen_score=float,       # winning branch's score, coerced to float
    all_scores=tuple[float],  # per-branch scores in items order
    branch_count=int,         # len(score list) — successful-only under skip
)
```

`_txn_commit` (commit_id) is auto-injected by
`persistence.txn.transaction._replay_effect_intents` at commit time;
the audit handler at `effect/handlers/audit.py` chains `:fold/chosen`
into the same Merkle chain as `:plan/edit` / `:code/exec`. **No new
chain code** — `:fold/chosen` rides the existing chain by being a
regular effect intent.

### `fn` contract (3-tuple shape)

`fold_into` extends `DB.fold`'s `(acc, item, db) -> (new_acc, facts)`
signature to `(acc, item, db) -> (new_acc, facts, score)`. The third
element is the score for **this** branch. A wrapper at the SDK
boundary strips the score back to the 2-tuple shape `DB.fold` expects
(so the underlying primitive is unchanged), while collecting
`(item, score, new_acc)` triples for `choose` via closure side-state.

`s.txn.fold` (existing surface) is **unchanged** — still takes a
2-tuple `fn`. Backward-compatible.

### `choose` contract

`Callable[[list[FoldBranchScore]], int]` returning the 0-based index
of the chosen branch. Validation:

- Non-int return -> `FoldIntoChooseError(TypeError)` (bool excluded
  too — silent True->1 routing is a footgun)
- Out-of-range int -> `FoldIntoChooseError(ValueError)`
- Arbitrary exception -> wrapped in `FoldIntoChooseError` with
  `__cause__` = original

### Score coercion (Decision 6 in scratch impl plan)

Scores from `fn` are coerced to `float` for both the audit datom and
`FoldIntoResult.all_scores`, even when `fn` returned `int` or `bool`.
Keeps the audit-datom JSON canonicalization byte-stable across
replays: `json.dumps(1)` vs `json.dumps(1.0)` differ at byte level
(`1` vs `1.0`), and the byte-identity property test demands
identical wire encoding.

Non-finite scores (NaN / +Inf / -Inf) raise
`FoldIntoChooseError(ValueError)` regardless of `on_error` — they
break audit-datom byte-identity by spec.

### `on_error` semantics (Decision 7)

- `"abort"` (default): if any branch's `fn` raises, `DB.fold` raises
  `FoldError`; `choose` is **never called**; no `:fold/chosen` datom
  is emitted.
- `"skip"`: skipped branches are dropped from the score list passed to
  `choose`. The `chosen_index` recorded in the audit datom is the
  index into the **successful-branches list**, not the original
  `items` list. `branch_count` reflects the successful count.
  Callers picking among MCTS rollouts should NOT have to pre-filter
  failures — the natural shape is "choose over what survived".
- `"checkpoint"`: same as `"abort"` from `fold_into`'s perspective.

**Contract violations bypass `on_error`** — wrong return arity,
non-numeric or non-finite score raise `FoldIntoChooseError` even
under `"skip"` mode. Programming bugs are not transient per-item
failures; the alternative (silent dropping) would mask agent contract
breaks.

### Determinism contract — § 4.3 acceptance gate

For fixed `(seed, items, fn, choose)` inputs (where `fn` and `choose`
are pure / deterministic), the `:fold/chosen` audit datom is
byte-identical across replays — verified via Hypothesis property test
at `@max_examples=200` in
`tests/store/test_fold_byte_identity.py::test_s_txn_fold_into_chosen_datom_byte_identity`.

The complementary `s.txn.fold` byte-identity property
(`test_s_txn_fold_byte_identity_across_replays`) was missing from the
suite before Phase 2.0c — landed alongside `fold_into` to close the
§ 4.3 gate for both surfaces.

### Carryover backlog (NOT in this release)

- The aspirational `:fold/probe` / `:fold/branch` / `:fold/score`
  3-datom emission shape from § 3.7 row + § 4.3 line 315 ("Datom
  shape: `:fold/probe` `:fold/branch` `:fold/score` `:fold/chosen`").
  Phase 2.0c ships **`:fold/chosen` only** — the others would require
  a true speculate-rollback-pick substrate primitive (a
  child-txn-per-branch model) which is **not** what the current
  `DB.fold` provides. Logged as **substrate-backlog #201 (proposed):
  full `:fold/probe`/`:fold/branch`/`:fold/score` datom emission via
  per-branch child-txn primitive on `DB.fold` — Phase 3 (v0.9.x)
  work.**
