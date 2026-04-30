-- persistence.store — Postgres migration 0001: datom log + tx_allocator
--
-- PG6 (Phase 1 stream #169) extracts this DDL from the inline
-- ``_SCHEMA_DDL`` string that PG1 shipped in
-- ``src/persistence/store/postgres.py`` so the migration history is on-
-- disk (one file per migration) rather than embedded in the backend
-- module. PG3 (#166) lands ``0002_audit_chain_lock.sql`` against the
-- same convention.
--
-- See:
--   docs/plans/2026-04-30-v0.8.0-postgres-store-design.md
--     §4.1   datom_log table shape (mirror of SQLite, type-promoted).
--     §4.2a  tx_allocator row-lock primitive (ADR-4 W1-revised).
--     §13    "Migration-file split deferred" RESOLVED in PG6.
--
-- The DDL below is byte-identical to PG1's inline string except that the
-- index + constraint comments are restored (they were dropped during
-- inlining for compactness). All statements are idempotent (CREATE TABLE
-- IF NOT EXISTS / CREATE INDEX IF NOT EXISTS / INSERT ... ON CONFLICT
-- DO NOTHING) so the runner's "apply each migration in its own
-- transaction" model can re-run a partially-applied migration without
-- ill effect.

CREATE TABLE IF NOT EXISTS datom_log (
    seq             BIGSERIAL    PRIMARY KEY,
    e               TEXT         NOT NULL,
    a               TEXT         NOT NULL,
    v               TEXT         NOT NULL,    -- canonical JSON (sort_keys=True)
    tx              BIGINT       NOT NULL,
    tx_time         TIMESTAMPTZ  NOT NULL,
    valid_from      TIMESTAMPTZ  NOT NULL,
    valid_to        TIMESTAMPTZ,
    op              TEXT         NOT NULL CHECK (op IN ('assert', 'retract')),
    provenance      TEXT         NOT NULL,    -- canonical JSON
    invalidated_by  BIGINT,
    -- Defence-in-depth behind the tx_allocator row-lock primitive
    -- (ADR-4 W1-revised). Two writers that ever produce the same
    -- (tx, e, a) pair → one INSERT raises 23505 (unique_violation),
    -- which the Txn-side retry loop treats as equivalent to 40001
    -- (SerializationFailure).
    UNIQUE (tx, e, a)
);

-- EAVT — entity → attr → value → tx (primary read path).
CREATE INDEX IF NOT EXISTS idx_datom_eavt
    ON datom_log (e, a, valid_from, tx);

-- AEVT — attr → entity → value → tx (analytics, sensitivity).
CREATE INDEX IF NOT EXISTS idx_datom_aevt
    ON datom_log (a, e, valid_from, tx);

-- AVET — attr → value → entity → tx (lookup by indexed attribute).
CREATE INDEX IF NOT EXISTS idx_datom_avet
    ON datom_log (a, v, e, tx);

-- VAET — value → attr → entity → tx (reverse-ref graph traversal).
CREATE INDEX IF NOT EXISTS idx_datom_vaet
    ON datom_log (v, a, e, tx);

-- VT-E — bitemporal valid-time range scans.
CREATE INDEX IF NOT EXISTS idx_datom_vte
    ON datom_log (valid_from, valid_to, e);

-- LOG — tx-time ordered, drives since(t) and replication; (tx_time, tx)
-- is the strict-monotonic projection for the MVCC primitive in §6 of the
-- design doc.
CREATE INDEX IF NOT EXISTS idx_datom_log_txtime
    ON datom_log (tx_time, tx);

-- Tx-allocator primitive (§4.2a + ADR-4 W1-revised). Single-row table
-- whose ``next_tx`` is row-locked with SELECT FOR UPDATE inside every
-- ``transact_serializable`` call so two concurrent writers cannot ever
-- be assigned the same tx-id. Plan-independent: no reliance on SSI
-- predicate-lock placement on a MAX() aggregate.
CREATE TABLE IF NOT EXISTS tx_allocator (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    next_tx     BIGINT  NOT NULL DEFAULT 1
);

INSERT INTO tx_allocator (id, next_tx) VALUES (1, 1)
    ON CONFLICT (id) DO NOTHING;
