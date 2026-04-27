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

### References
- Design doc: `docs/plans/2026-04-27-v0.5-txn-design.md`
- Impl plan: `docs/plans/2026-04-27-v0.5-txn-impl.md`
