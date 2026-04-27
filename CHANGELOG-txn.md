# persistence.txn — module changelog

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
  respectively. Promotion deferred to v0.5.1.
- The audit-chain `:effect/txn-commit` field shipped as a `_txn_commit`
  kwarg passed to `runtime.perform`, not as a first-class AuditEntry
  schema field. Promotion deferred to v0.5.1.
- The CM form (`with db.dosync()`) is single-shot — raises
  `TxnRetryExhausted` on conflict. The decorator form is the canonical
  retryable form.
- Per-ref attribute specs (`ref.spec_attr`) collapsed to a single global
  `:value` spec key in `_spec_validate_writes`. Per-ref specs deferred
  to v0.5.1.
- Replay byte-identity test ships as a deterministic two-run structural
  comparison rather than a Hypothesis `@given` property at
  `max_examples=200`. Hypothesis upgrade deferred to v0.5.1.

### References
- Design doc: `docs/plans/2026-04-27-v0.5-txn-design.md`
- Impl plan: `docs/plans/2026-04-27-v0.5-txn-impl.md`
