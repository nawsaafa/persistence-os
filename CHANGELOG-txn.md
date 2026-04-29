# persistence.txn — module changelog

## v0.5.2 — 2026-04-29

Clojure-parity closure. The deferred-from-v0.5.0a1-§10 surface
(`commute`, `ensure`, atoms) plus the deferred-from-v0.5.1-N4
Hypothesis byte-identity coverage all land. See
`docs/plans/2026-04-29-v0.5.2-clojure-parity-design.md` for design
rationale; ARIS R1 PASS at mean 8.6 / min 8.

### Closed (6 of 6)
- N6 — `tx.alter` byte-identity Hypothesis property at
  `max_examples=200` (curated `_ALTER_FNS` table sidesteps
  closure-shrink failure modes).
- N7 — `tx.effect` byte-identity Hypothesis property at
  `max_examples=200`; combined `assoc | alter | effect` mix passes;
  audit-chain projection helper (`(position, op, args_hash,
  result_hash, txn_commit_present)` — per-run UUIDs excluded);
  NEW deterministic `_recording_handler` at
  `tests/persistence/txn/conftest.py`.
- N8 — `Ref.spec_attr` regex tightened to EDN-keyword grammar.
  Rejects `0/foo` (leading-digit segment), `foo/bar/baz`
  (multi-`/`), `/foo`, `foo/` (empty segments), `-1`, `+42`, `.5`
  (special leader + digit). Permits `foo123` (trailing digits),
  `-foo`/`+bar`/`.baz` (special leader + non-digit), single-char
  `-`/`+`/`.`. Backwards-compatible (zero existing non-default
  `spec_attr` values changed).
- F1 — Atoms (single-cell CAS over datom-refs). New `Atom` frozen
  dataclass with `deref`, `swap(fn)`, `compare_and_set(old, new)`,
  `reset(value)`. `db.atom(eid, *, initial)` constructor (rejects
  duplicate eid). CAS uses `with db.store._lock:` mirroring
  `_commit_attempt:325`. Atom-in-dosync prohibition raises
  `AtomInDosyncProhibited` (intentional Clojure-parity deviation —
  rationale: avoids non-replayable hole in audit chain).
- F2 — `tx.ensure(ref)` read-set padding. Returns deref'd snapshot
  value AND adds ref to `ensure_set`. Conflict-detection union at
  commit reads `read_set | ensure_set | write_set`. Provenance
  emits `:persistence.txn/ensure-set` alongside read-set so an
  auditor can distinguish "deref'd for value" vs "padded for
  conflict-detection only". Bank-transfer write-skew test passes
  deterministically.
- F3 — `tx.commute(ref, fn_id, *args)` two-phase eager-at-body +
  reapply-at-commit. Curated registry (4 fns: `inc-by`, `sum-into`,
  `set-union`, `dict-merge-shallow`); `dict-merge-shallow` ships
  with documented "commutative ONLY on disjoint keys; deterministic
  LWW on overlap" caveat. `register_commute(fn_id, fn)` gated by
  `PERSISTENCE_TXN_ALLOW_RUNTIME_REGISTRATION` env sentinel
  (mirrors `register_coercion` at `plan/_coerce.py:103`). Commute
  refs deliberately NOT added to read_set — conflict-free by
  design. 4 intra-txn semantics cases specified (multiple commutes
  same ref / commute-then-set / set-then-commute / deref-after-
  commute). Provenance emits `:persistence.txn/commute-log` in
  body-order.

### Module surface (delta from v0.5.1)
- `Atom`, `AtomCASExhausted`, `AtomInDosyncProhibited` exported
  from `persistence.txn`.
- `register_commute`, `unregister_commute`, `lookup_commute`
  exported from `persistence.txn`.
- `Transaction` gains `.ensure(ref) -> Any` and
  `.commute(ref, fn_id, *args) -> Any`.
- `DB` gains `.atom(eid, *, initial) -> Atom`.
- New boundary specs: `:persistence.txn/ensure-set` (`seq_of(str_)`),
  `:persistence.txn/commute-log` (`seq_of(keys({":ref", ":fn-id",
  ":args"}))`).
- `Ref.spec_attr` regex tightened (see N8 closure above).

### Suite
931 + 7 xfailed v0.5.1 baseline → **129 txn module tests** (was 88;
+41) and **1559 + 7 xfailed full suite** (was 1492 at branch start;
+67 across all phases). All threading + Hypothesis tests
deterministic across 5+ consecutive runs.

### ARIS gate
- R1 design fitness: PASS at mean 8.6 / min 8 after 2 W-cycles
  (`docs/plans/2026-04-29-v0.5.2-clojure-parity-design.md` § "ARIS
  R1 status").
- R2 code quality: PASS at mean **8.75 / min 8.6** after 2 rounds
  (codex `gpt-5.2` hard-mode high-reasoning). Round-1 PASSed gate at
  8.53 / 8.2 with 2 MAJORs flagged; W1 closed both in `ed3ad4a`
  (MAJOR-1: `db.atom()` allocation race — spanning `store._lock` at
  `_db_extension.py:240` + regression test
  `test_db_atom_concurrent_allocation_linearises`; MAJOR-2:
  `Transaction.commute()` docstring case-3 contradiction — rewrote
  194-211 + adjacent eager-base + `_build_commute_facts`). Round-2
  closed at 8.75 / 8.6, zero new MAJORs, 1 nit closed in `b0f4abe`
  (literal "exactly one winner" assertions). See
  `review-stage/v0.5.2-clojure-parity-r2/AUTO_REVIEW.md`.
- R3 + R4 skipped — same warrant as v0.4.0a1 / v0.5.1 / v0.6.0a1
  (zero proposition / paper claim change).

### Predecessor
`v0.5.1` at `f6bbf91` (substrate at `v0.7.0a1` `bbbeacc`).

## v0.5.1 — 2026-04-27

Rev O narrowings closure. See top-level `CHANGELOG.md` for the v0.5.1
section and `docs/plans/2026-04-27-v0.5.1-rev-o-narrowings-design.md`
for design rationale on each item.

### Closed (5 of 5 from rev O)
- N1 — read-set + intent-log emitted on commit datom provenance.
- N2 — `:effect/txn-commit` promoted to first-class AuditEntry field
  (also closes a latent `args_hash` corruption found mid-impl).
- N3 — per-ref attribute spec via `Ref.spec_attr`.
- N4 — Hypothesis `@given` byte-identity property at `max_examples=200`
  (single-shot `assoc` only; `tx.alter` / `tx.effect` deferred to v0.5.2).
- N5 — helper-extraction refactor: `_commit_attempt` dedups CM and
  decorator commit paths.

### Module surface (delta from v0.5.0a1)
- `Ref.spec_attr: str = "value"` (excluded from eq/hash via
  `field(compare=False)`).
- `Runtime.perform(op, args, *, txn_commit=None)` typed kwarg path.
- `AuditEntry.txn_commit: str | None = None`.
- `_EdnValueSpec` registered under `:persistence.txn/edn-value`.
- `:persistence.txn/intent-log` registered shape replaced —
  v0.5.0a1 placeholder `seq_of(_uuid_str_spec)` → real per-element
  `keys({":op": str_, ":kwargs": map_of(str_, edn-value)})`.

### Suite
912 + 7 xfailed v0.5.0a1 baseline → 931 + 7 xfailed (+19 tests).

### ARIS gate
- R1 design fitness: 8.06 → re-pass after W1 fix-pass `df1a3ec`.
- R2 code quality: PASS at 9.19 / 8.5 (zero MAJORs on shipped code).
- R3 + R4 skipped — same warrant as v0.4.0a1 (no proposition / paper
  claim change).

### Predecessor
`v0.5.0a1` at `9377b86`.

## v0.5.0a1 — 2026-04-27

Initial release. See top-level `CHANGELOG.md` for the v0.5.0a1 section.

### Module surface
- `Ref`, `Transaction`, `freeze`, `is_immutable_value`
- Errors: `TxnError`, `TxnRetryExhausted`, `TxnDeadlineExceeded`,
  `RefBranchMismatch`, `RefValueNotImmutable`, `EffectInIoBlock`,
  `NestedDosyncNotSupported`
- DB methods (attached at import time): `db.ref`, `db.new_ref`,
  `db.dosync`
- Boundary specs: `:persistence.txn/{commit-id, started-at,
  committed-at, retry-count, read-set, non-deterministic-retry,
  intent-log, commit}`

### Design constraints
- Single-process STM only (multi-process via Postgres SERIALIZABLE
  deferred to v0.6.x).
- InMemoryStore for tests only — production deployments require
  SQLiteStore or future PostgresStore.
- Long-lived branches with many `dosync` calls leak via
  `copy.deepcopy(provenance)`; design pin: branches must be short-lived.
- Default `max_retries=256`; opt-in `deadline=` for non-deterministic
  wall-clock retry budget.

### Design-vs-impl narrowings (rev O — see design doc § 4.1, § 4.3, § 6, § 7.2)
- `:persistence.txn/read-set` and `:persistence.txn/intent-log` are
  spec-registered but NOT emitted into the commit datom's provenance.
  Reconstructable from `db.store.since(t_start)` and the audit chain
  respectively. Promotion deferred to v0.5.1. → **closed in v0.5.1 (N1).**
- The audit-chain `:effect/txn-commit` field shipped as a `_txn_commit`
  kwarg passed to `runtime.perform`, not as a first-class AuditEntry
  schema field. Promotion deferred to v0.5.1. → **closed in v0.5.1 (N2).**
- The CM form (`with db.dosync()`) is single-shot — raises
  `TxnRetryExhausted` on conflict. The decorator form is the canonical
  retryable form. → kept by design; CM/decorator commit-path dedup
  delivered in v0.5.1 (N5) preserves the asymmetry.
- Per-ref attribute specs (`ref.spec_attr`) collapsed to a single global
  `:value` spec key in `_spec_validate_writes`. Per-ref specs deferred
  to v0.5.1. → **closed in v0.5.1 (N3).**
- Replay byte-identity test ships as a deterministic two-run structural
  comparison rather than a Hypothesis `@given` property at
  `max_examples=200`. Hypothesis upgrade deferred to v0.5.1.
  → **closed in v0.5.1 (N4).** Hypothesis property covers single-shot
  `assoc` transactions; `tx.alter` / `tx.effect` byte-identity coverage
  deferred to v0.5.2.

### References
- Design doc: `docs/plans/2026-04-27-v0.5-txn-design.md`
- Impl plan: `docs/plans/2026-04-27-v0.5-txn-impl.md`
