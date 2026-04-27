# persistence.txn — module changelog

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
