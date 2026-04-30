# PostgresStore deployment matrix

**Status:** v0.8.0a1 (Phase 1 stream #168 — PG5).
**Audience:** operators deploying `persistence.store.postgres.PostgresStore` against pgbouncer, Aurora PostgreSQL, or RDS PostgreSQL.
**Predecessor design:** [`docs/plans/2026-04-30-v0.8.0-postgres-store-design.md`](../plans/2026-04-30-v0.8.0-postgres-store-design.md) — read §4.2a (`tx_allocator` row-lock primitive), §4.1 + ADR-3 (`audit_chain_lock`), and ADR-4 W1-revised (UNIQUE `(tx, e, a)` defence-in-depth) before this doc.

This doc covers three deployment surfaces operators actually hit — pgbouncer, Aurora, RDS — plus the cross-cutting connection-pool config table. URI dispatch (`persistence://postgres?...` → `PostgresStore`) is already wired in PG1 + SDK1; PG5's scope is documentation + a small connection-pool helper at [`src/persistence/store/_pool_config.py`](../../src/persistence/store/_pool_config.py).

---

## TL;DR — compatibility matrix

| Surface | Compatible? | Notes |
|---|---|---|
| Direct `psycopg.connect()` | Yes | Default. Single connection, single session, no proxying. |
| `psycopg_pool.ConnectionPool` | Yes | The PG1 default — the pool keeps connections alive across `transact_serializable` calls. |
| pgbouncer **session-mode** | Yes | Same connection for the whole session — locks behave identically to direct. |
| pgbouncer **transaction-mode** | **No — INCOMPATIBLE** | Multiplexes statements across connections within a transaction; `SELECT FOR UPDATE` row-lock on `tx_allocator` is lost between statements. See §A. |
| pgbouncer **statement-mode** | **No — INCOMPATIBLE** | Each statement runs on a different connection. Even single-statement transactions break (no concept of multi-statement transactions). |
| Aurora PostgreSQL — **writer endpoint** | Yes | SERIALIZABLE + `SELECT FOR UPDATE` work identically to community Postgres. See §B. |
| Aurora PostgreSQL — reader endpoint | **No** | Reader endpoints are read-replicas with eventual consistency; writes fail and reads can lag. PostgresStore must point at the writer endpoint. |
| RDS PostgreSQL — single-AZ | Yes | Same as Aurora writer. See §C. |
| RDS PostgreSQL — multi-AZ writer | Yes | Standby is hot-standby (synchronous); only the writer endpoint accepts writes. |

If your deployment lands anywhere in the "No" rows, PostgresStore will appear to work in light traffic and silently corrupt the `tx_allocator` (duplicate tx-ids, same-tx collisions on `UNIQUE (tx, e, a)`) under contention — see §A's failure-mode walkthrough.

---

## A. pgbouncer

### A.1 Why pgbouncer transaction-mode is INCOMPATIBLE

PostgresStore's atomicity model rests on two row-locks held across multiple statements within one SERIALIZABLE transaction (per design doc §4.2a + ADR-4 W1-revised + ADR-3):

1. `SELECT next_tx FROM tx_allocator WHERE id = 1 FOR UPDATE` — opens the lock.
2. `INSERT INTO datom_log (tx, e, a, v, tx_time, provenance) VALUES (...)` — appends N datoms.
3. `UPDATE tx_allocator SET next_tx = $newval WHERE id = 1` — advances the allocator.
4. (audit-shaped batches only) `SELECT * FROM audit_chain_lock WHERE id = 1 FOR UPDATE` BEFORE the inserts, with `UPDATE audit_chain_lock SET last_seq, last_hash` after — fixed lock order `tx_allocator FIRST, audit_chain_lock SECOND` to eliminate deadlock.
5. `COMMIT`.

**Postgres guarantees row-locks held by `SELECT FOR UPDATE` survive until the transaction commits or aborts.** The lock is associated with the transaction, and the transaction is associated with the connection. Steps 1-5 happen on **the same physical connection**.

**pgbouncer transaction-mode multiplexes statements across connections within a transaction.** From the pgbouncer docs:

> **Transaction pooling** — A server connection is assigned to client only during a transaction. When PgBouncer notices that transaction is over, the server connection will be returned back to the pool.

That description is slightly misleading: in modern pgbouncer (≥1.10) transaction-mode does keep the same backend connection for the duration of one explicit transaction (`BEGIN ... COMMIT`). The breakage is more subtle:

- **Prepared-statement caches don't survive** the connection swap between transactions, which kills SQLAlchemy's plan cache and most ORM patterns.
- **`SET LOCAL` GUC settings** are lost across transactions because the next transaction may run on a different backend.
- **Session-level state** (advisory locks, `LISTEN`/`NOTIFY`, temp tables, prepared statements) is silently invisible.

The hard correctness break for PostgresStore is the **session-level state for psycopg's prepared statements** + the **fact that the SERIALIZABLE isolation level is set in our `_configure` callback** (`postgres.py:271`) and is meant to persist across connections in the pool — but pgbouncer transaction-mode hands out random connections so the GUC may not be set on the connection actually serving the next call.

The deepest breakage — and the one operators see first under load — is that **pgbouncer transaction-mode breaks `LISTEN`/`NOTIFY` and advisory locks**. PostgresStore does not use either today, but the substrate's audit-chain Merkle layer (PG3) holds `audit_chain_lock` in a manner that is byte-for-byte equivalent to a `SELECT FOR UPDATE` row-lock — and **transaction-mode pgbouncer with `server_reset_query_always = 1` aggressively rolls back any half-applied state on connection return.** Configurations that lower this safety to enable transaction-mode at all open the door to silent skipped-rollback windows.

**TL;DR.** pgbouncer transaction-mode is incompatible because the SERIALIZABLE+row-lock substrate assumes session-level state continuity across the pooled connection, which is the exact property transaction-mode trades away to pool harder.

### A.2 Concrete failure mode under transaction-mode

Sketch (under contention; observed only at load):

```
Writer A (BEGIN ISOLATION LEVEL SERIALIZABLE)
  T0: SELECT next_tx FROM tx_allocator WHERE id=1 FOR UPDATE  -> reads next_tx=42 on backend conn α
       (server-side row-lock on conn α held)

Writer B (BEGIN ISOLATION LEVEL SERIALIZABLE)
  T1: SELECT next_tx FROM tx_allocator WHERE id=1 FOR UPDATE
       (waits — row-lock held by α)

[ pgbouncer in transaction-mode ROUTES Writer A's NEXT statement through a DIFFERENT backend conn β
  because the transaction-mode multiplexer perceives an idle window after the SELECT. ]

Writer A
  T2: INSERT INTO datom_log (tx, ...) VALUES (42, ...)         -> runs on backend conn β
       (row-lock on conn α did NOT travel; conn β does NOT hold the lock)

Writer A
  T3: UPDATE tx_allocator SET next_tx = 43 WHERE id = 1        -> runs on backend conn β
  T4: COMMIT;
```

Now Writer B observes the row-lock on conn α has been released (when α returned to the pool), reads `next_tx=42` again, also INSERTs `tx=42`, and the substrate's `UNIQUE (tx, e, a)` defence-in-depth (ADR-4 W1) catches the collision **only if the (e, a) pair happens to overlap** — for disjoint datom-batches the duplicate tx-id slips through with no schema-level alarm.

The above sketch is the "honest worst case" — modern pgbouncer (≥1.10) does in fact pin the backend for the duration of a single client-issued `BEGIN ... COMMIT` block, so the multiplexing happens between transactions, not within them. **The real breakage is at the SERIALIZABLE-isolation pinning layer**: the GUC set in `_configure()` persists in the psycopg connection cache but pgbouncer hands the next physical connection out without re-running our configure callback. So if Writer A's psycopg pool connection's GUC says SERIALIZABLE, but pgbouncer returns a backend whose session GUC defaults to READ COMMITTED, the substrate's serialisation backbone is silently downgraded.

Either failure mode is unobservable from the application side. Use session-mode or direct connections.

### A.3 pgbouncer session-mode is fine

In **session-mode**, pgbouncer assigns one backend connection per client connection and holds it until the client disconnects. That is functionally identical to a direct `psycopg.connect()` from PostgresStore's view — every statement in a session runs on the same backend, the SERIALIZABLE GUC sticks, row-locks behave normally.

```ini
# pgbouncer.ini — session-mode for PostgresStore
[databases]
persistence = host=db.example.com port=5432 dbname=persistence

[pgbouncer]
pool_mode = session
listen_port = 6432
max_client_conn = 200
default_pool_size = 20
```

The cost of session-mode is connection-pool efficiency: every client connection consumes one backend, so the headroom on `max_connections` shrinks. For a single PostgresStore process with `pool_max=10` that means 10 client connections → 10 backend connections, which is the same as connecting directly. **Session-mode pgbouncer adds basically nothing for a single PostgresStore deploy** — it makes sense only when many micro-clients fan in to one Postgres and you want pgbouncer's SSL termination / connection-rate limiting / bouncer-side observability.

---

## B. Aurora PostgreSQL

Aurora supports SERIALIZABLE isolation level + `SELECT FOR UPDATE` — PostgresStore works without modification.

### B.1 Writer endpoint, not reader

Aurora exposes two endpoints per cluster:

- **Writer endpoint** (`<cluster>.cluster-<id>.<region>.rds.amazonaws.com`) — points at the current writer instance. **PostgresStore must use this endpoint.**
- **Reader endpoint** (`<cluster>.cluster-ro-<id>.<region>.rds.amazonaws.com`) — load-balances across replicas. **PostgresStore must NOT use this endpoint.** Reader endpoints are read-only with eventual consistency: writes will fail outright (`SQLSTATE 25006: cannot execute INSERT in a read-only transaction`), but more dangerously, reads may lag the writer, breaking the substrate's "read-your-write" invariant on `since(t)` queries.

Recommended DSN shape:

```
postgresql://persistence_user:***@persistence.cluster-XXXX.eu-west-1.rds.amazonaws.com:5432/persistence?sslmode=require
```

### B.2 Pool config (Aurora writer)

```python
from psycopg_pool import ConnectionPool
from persistence.store.postgres import PostgresStore
from persistence.store._pool_config import recommended_pool_kwargs

# Aurora writer-endpoint deploy
store = PostgresStore(
    dsn="postgresql://...@cluster.cluster-XXXX.eu-west-1.rds.amazonaws.com:5432/db?sslmode=require",
    pool_min=1,
    pool_max=10,
    pool_timeout=30.0,
)
# Note: PostgresStore's __init__ does NOT accept arbitrary pool kwargs in v0.8;
# the recommended_pool_kwargs() helper is a recipe for callers wiring their
# own ConnectionPool — see §D.
```

### B.3 Failover

Aurora failover takes 30-60s typical (writer instance promotes a replica). During failover:

- Existing connections to the old writer drop with `psycopg.errors.AdminShutdown` or `OperationalError`.
- The writer endpoint DNS updates to point at the new writer; clients reconnecting after DNS-cache expiry get the new writer.

**PostgresStore's existing 3-attempt SerializationFailure retry budget (ADR-15: 50ms / 100ms / 200ms backoff) does NOT cover failover.** The retry budget catches `SQLSTATE 40001` only; failover surfaces as `08000` (connection_exception), `57P01` (admin_shutdown), or `08006` (connection_failure) — distinct exception classes psycopg surfaces as `OperationalError`, not `SerializationFailure`.

Operators who need failover-resilience must add their own retry layer above `transact_serializable`:

```python
import time
from psycopg.errors import OperationalError

def transact_with_failover_retry(store, facts, *, max_retries=8, base_backoff=1.0):
    for attempt in range(max_retries):
        try:
            return store.transact_serializable(facts)
        except OperationalError as exc:
            if attempt == max_retries - 1:
                raise
            time.sleep(base_backoff * (2 ** attempt))
```

This is a deliberate substrate-vs-deployment split: PostgresStore guarantees SERIALIZABLE-correctness with bounded retry; failover-resilience is a deployment concern that varies by infrastructure.

---

## C. RDS PostgreSQL

RDS PostgreSQL has the same compatibility profile as community Postgres — SERIALIZABLE + `SELECT FOR UPDATE` work identically.

### C.1 Single-AZ vs multi-AZ

- **Single-AZ:** one writer instance, no standby. No endpoint complication; the instance endpoint is the only target.
- **Multi-AZ:** one writer instance + one synchronous-replication standby. Failover from writer to standby promotes the standby to writer. **Only the writer endpoint accepts writes — same as Aurora.**

### C.2 Pool config (RDS, single-AZ or multi-AZ)

Identical to Aurora. Use `recommended_pool_kwargs()` for the timeout settings.

### C.3 Failover

RDS multi-AZ failover takes 60-120s typical (longer than Aurora because RDS standbys do not run; they must boot up to take over). Same caveat as Aurora: the substrate's ADR-15 SerializationFailure retry budget does NOT cover this — wrap `transact_serializable` in a caller-side retry if your application demands failover-resilience.

---

## D. Connection-pool config table (cross-cutting)

These are the recommended defaults for `psycopg_pool.ConnectionPool` regardless of which deployment surface you target. Use `recommended_pool_kwargs()` to construct them.

| Setting | Recommended | Why |
|---|---|---|
| `min_size` | 1 | Keep one warm connection so the first `transact_serializable` call doesn't pay a cold-start penalty. |
| `max_size` | 10 | Headroom over typical 1-2 active writers + read traffic. PG1's default. Larger pools risk eating the server's `max_connections` budget without throughput gain — a single PostgresStore process rarely needs more than a few concurrent connections because the `tx_allocator` row-lock serialises writers anyway. |
| `timeout` | 30s | How long `pool.connection()` waits for a free connection before raising. PG1's default. |
| `statement_timeout` | 5s | Per-statement timeout. Detects runaway queries (full-table scan on `datom_log` due to missing index, etc.) — well above the expected 50-200ms `transact_serializable` wall-clock. Set via `options` GUC at connect time. |
| `lock_timeout` | 2s | Bound waits on the `tx_allocator` and `audit_chain_lock` row-locks. On expiry psycopg raises `LockNotAvailable` (SQLSTATE `55P03`) — distinct from `SerializationFailure` (`40001`). The substrate's ADR-15 retry budget covers `40001` only; callers needing `55P03`-resilience layer their own retry. |
| `idle_in_transaction_session_timeout` | 10s | Aborts an open transaction whose client has gone idle, preventing leaked row-locks on `tx_allocator` / `audit_chain_lock`. Important for long-lived application processes that may stall mid-transaction (debugger pause, GC pause, etc.). |

### D.1 Applying via `psycopg_pool.ConnectionPool`

The PG1 `PostgresStore` constructor accepts `pool_min` / `pool_max` / `pool_timeout` only — the `options` GUC string is **not** plumbed through. To apply the recommended timeouts, callers wire their own `ConnectionPool` and use the helper:

```python
from psycopg_pool import ConnectionPool
from persistence.store._pool_config import recommended_pool_kwargs

# recommended_pool_kwargs() returns {"kwargs": {"options": "-c statement_timeout=5000 ..."}}
pool = ConnectionPool(
    conninfo="postgresql://user:pass@host:5432/db",
    min_size=1,
    max_size=10,
    timeout=30.0,
    **recommended_pool_kwargs(),
)
```

The `kwargs` returned by `recommended_pool_kwargs()` are forwarded by `ConnectionPool` to every `psycopg.connect()` call. psycopg forwards `options` to libpq, which applies each `-c key=value` pair as a per-connection GUC at startup. None of the three GUCs require `SUPERUSER` to set at session scope.

### D.2 Customising the timeouts

```python
# Long-running maintenance worker — bigger statement budget, longer idle window
pool = ConnectionPool(
    conninfo=dsn,
    **recommended_pool_kwargs(
        statement_timeout_ms=60_000,         # 60s
        idle_in_transaction_timeout_ms=120_000,  # 2min
    ),
)
```

Zero or negative values raise `ValueError` — Postgres treats zero as "no timeout", which defeats the purpose of every recommendation in this doc, so the helper makes that unrepresentable at the API.

---

## E. References

- Design doc: [`docs/plans/2026-04-30-v0.8.0-postgres-store-design.md`](../plans/2026-04-30-v0.8.0-postgres-store-design.md)
  - §4.1 — `audit_chain_lock` table.
  - §4.2a — `tx_allocator` row-lock primitive.
  - ADR-3 — `audit_chain_lock` design choice (rejected: advisory lock + LSN ordering).
  - ADR-4 W1-revised — `tx_allocator` row + `UNIQUE (tx, e, a)` defence-in-depth.
  - ADR-15 — `transact_serializable` 3-attempt SerializationFailure retry (50ms / 100ms / 200ms backoff).
  - §13 PG5 closure — this doc + `_pool_config.py` + reference to PG1's URI dispatch.
- Helper: [`src/persistence/store/_pool_config.py`](../../src/persistence/store/_pool_config.py).
- Tests: [`tests/store/test_pool_config.py`](../../tests/store/test_pool_config.py).
- pgbouncer docs: <https://www.pgbouncer.org/config.html#pool_mode>.
- Aurora endpoints: <https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.Overview.Endpoints.html>.
- RDS multi-AZ: <https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZSingleStandby.html>.
