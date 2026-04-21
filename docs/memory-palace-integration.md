# Memory Palace Integration — Wiring `persistence.fact` into ai-box

Target integration point: the ai-box backend's mem0 write path (the one that
currently serves the Memory Palace's 44 buildings, auto-router, and
`memory.add()` endpoint). The goal of Phase 1 per `docs/agent1-fact-spec.md`
§7 is **additive, not rewrite**: every legacy mutation continues to happen,
but a datom lands in the authoritative log *first*.

No Memory Palace code is modified by this worktree. This document is the
recipe a follow-up PR uses to wire the interceptor. Tests live entirely in
this repository and rely on a duck-typed fake client; no runtime dependency
on mem0 is introduced.

---

## 1. Python wiring pattern

Legacy call site (hypothetical, matches the ai-box shape):

```python
# ai-box/backend/memory/palace.py (current)
from mem0 import Memory

mem = Memory()

def store(event: dict) -> None:
    mem.add(
        messages=[{"role": "user", "content": event["text"]}],
        user_id=event["building"],
        metadata={"source": event["source"]},
    )
```

Wired version using the interceptor:

```python
# ai-box/backend/memory/palace.py (after integration)
from datetime import datetime, timezone
from mem0 import Memory
from persistence.fact import DB, SQLiteStore  # or a Postgres store
from persistence.fact.interceptors.mem0_adapter import Mem0Interceptor

# One fact store per tenant. In ai-box this is instantiated at app startup
# and threaded through FastAPI dependency injection.
_fact_db = DB(SQLiteStore(path="/var/lib/ai-box/datom_log.sqlite"))

_palace = Mem0Interceptor(
    db=_fact_db,
    mem0=Memory(),
    principal={"tenant": "nawfal-egh", "agent": "memory-palace"},
)

def store(event: dict) -> None:
    _palace.add(
        e=event["building"],
        a="memory/text",
        v=event["text"],
        valid_from=datetime.now(timezone.utc),
        provenance={"source": event["source"]},
        # Legacy kwargs — passed straight through to mem0.Memory.add:
        messages=[{"role": "user", "content": event["text"]}],
        user_id=event["building"],
        metadata={"source": event["source"]},
    )
```

What changed:

- `mem.add(...)` becomes `_palace.add(e=..., a=..., v=..., ...)`.
- The `e, a, v, valid_from` kwargs land as a datom in the fact store **before**
  mem0 sees the write.
- All other kwargs pass through unchanged to mem0.

Read paths do not change. They continue to hit mem0 + Kuzu. Both remain
materialized projections; if they drift, we rebuild from the log using
`persistence.fact.projection.rebuild` with a mem0-aware adapter (shipped
in a later phase).

## 2. SQL migration step

For operators running Postgres (production ai-box VPS shape):

```bash
# 1. Ensure the db user can CREATE TABLE in the target schema.
psql "$DATABASE_URL" -c "CREATE SCHEMA IF NOT EXISTS persistence;"

# 2. Apply the migration. The SQL is identical for SQLite and Postgres —
#    driver-level coercion handles the TEXT/JSON question.
psql "$DATABASE_URL" \
    -f vendor/persistence-os/src/persistence/fact/migrations/0001_datom_log.sql

# 3. Sanity-check the indexes exist.
psql "$DATABASE_URL" -c "\d+ datom_log"
```

For SQLite (single-operator / laptop deploy):

```bash
sqlite3 /var/lib/ai-box/datom_log.sqlite < \
    src/persistence/fact/migrations/0001_datom_log.sql
```

Python applies the same file automatically on `SQLiteStore(path=...)`
construction — you only need the manual step if you want to pre-create the
file with the right permissions.

## 3. Rollback procedure

The interceptor is a strict superset of the legacy path. Rolling back is
three actions, in order:

1. **Revert the Python call sites.** Replace `_palace.add(...)` /
   `_palace.update(...)` with the original `mem.add(...)` / `mem.update(...)`.
   This restores the legacy semantics immediately. Deploy.
2. **Freeze (do not drop) the datom log.** The log contains the historical
   ground truth for everything that happened while the interceptor was
   active. Preserve it for audit; no data is lost by rolling back writes.
3. **(Optional) archive the log.** If the log must come offline:
   ```bash
   pg_dump "$DATABASE_URL" -t datom_log -F c -f datom_log_archive.pgdump
   psql "$DATABASE_URL" -c "DROP TABLE persistence.datom_log;"
   ```
   Or for SQLite: move the file to cold storage. Never delete — agent3 (audit)
   expects to be able to replay anything the interceptor ever saw.

Rollback is safe because no legacy call site was removed — the interceptor
is a strict wrapper. The legacy mem0 / Kuzu state is untouched.

## 4. VPS test plan

The operator (user) runs this sequence on the VPS to validate the wiring
once the ai-box PR lands. All steps can be executed read-only except step 5
which writes a small number of datoms.

### 4.1 Pre-check — fact store is healthy

```bash
cd /root/ai-box && \
  .venv/bin/python -c "
from persistence.fact import DB, SQLiteStore
db = DB(SQLiteStore(path='/var/lib/ai-box/datom_log.sqlite'))
print('log size:', len(list(db.log())))
"
```

Expected: prints an integer (0 on a fresh deploy).

### 4.2 Confirm migration landed

```bash
sqlite3 /var/lib/ai-box/datom_log.sqlite \
  "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='datom_log';"
```

Expected output (six indexes):

```
idx_datom_eavt
idx_datom_aevt
idx_datom_avet
idx_datom_vaet
idx_datom_vte
idx_datom_log_txtime
```

### 4.3 Smoke write — one memory through the interceptor

```bash
curl -X POST https://ai-box.<operator-domain>/memory/add \
  -H 'Content-Type: application/json' \
  -d '{"building": "bankability", "text": "WACC ceiling test = 8.7%",
       "source": "vps-smoke-test"}'
```

### 4.4 Confirm the datom landed

```bash
sqlite3 /var/lib/ai-box/datom_log.sqlite \
  "SELECT e, a, v, op FROM datom_log ORDER BY seq DESC LIMIT 1;"
```

Expected: `bankability|memory/text|"WACC ceiling test = 8.7%"|assert`

### 4.5 Confirm the legacy projection also received the write

```bash
curl https://ai-box.<operator-domain>/memory/recall?query=WACC%20ceiling
```

Expected: the smoke memory appears in results. If the datom is present but
the mem0 projection is empty, the interceptor skipped the legacy write
(step 1 succeeded, step 2 failed) — debug mem0 directly and rebuild from the
log.

### 4.6 Counterfactual query (end-to-end spec §8 replay)

```bash
cd /root/ai-box && \
  .venv/bin/python -m persistence.fact.demo
```

Expected output (verbatim):

```
Now:      {'project/wacc': 0.091}
April 15: {'project/wacc': 0.087}
Branch:   {'project/wacc': 0.095}
```

This confirms the full query API (transact, as_of, as_of_valid, branch)
runs cleanly against the production Python environment.

---

## 5. What this PR does NOT do

- It does not change any Memory Palace code. The interceptor lives in
  `persistence-os` and is imported by ai-box — no files in `ai-box/backend`
  are touched by the persistence-os worktree.
- It does not enforce the fact store as the sole source of truth. The
  legacy path still runs. Once the operator has a week of logs and the
  regulator-replay test (agent3 module) passes, a follow-up PR flips the
  default to "datom is canonical, projection is derived".
- It does not backfill historical memories. Phase 2 of
  `docs/agent1-fact-spec.md` §7 covers that — it runs once, synthesizes
  datoms from existing Kuzu nodes with `source: :backfill-2026-04-21`, and
  is deliberately separate from the write-path wiring.
