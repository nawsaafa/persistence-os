-- persistence.fact — Migration 0001: datom log + covering indexes
--
-- Portable between Postgres 14+ and SQLite 3.37+. The only dialect-dependent
-- shape is the JSON column type, which we store as TEXT; drivers coerce.
--
-- Per docs/agent1-fact-spec.md §1 / §4 and paper §5.1:
--   8-tuple datom + provenance + invalidated_by, with:
--     - EAVT  primary read path ("everything about entity E as-of t")
--     - AEVT  analytics ("all WACCs across all projects")
--     - AVET  lookup-by-value ("which project has WACC = 0.087?")
--     - VAET  reverse-ref graph traversal
--     - VT-E  bitemporal valid-time range scans (as-of-valid)
--     - LOG   tx-time ordered for incremental sync (since(t))
--
-- The (tx, a, e) uniqueness constraint guarantees we never accidentally write
-- two datoms for the same attribute-entity pair within one transaction — that
-- would break the auto-retraction invariant.

CREATE TABLE IF NOT EXISTS datom_log (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    e               TEXT    NOT NULL,
    a               TEXT    NOT NULL,
    v               TEXT    NOT NULL,     -- JSON-encoded value
    tx              INTEGER NOT NULL,
    tx_time         TEXT    NOT NULL,     -- ISO-8601 UTC
    valid_from      TEXT    NOT NULL,
    valid_to        TEXT,                 -- NULL = open interval
    op              TEXT    NOT NULL CHECK (op IN ('assert', 'retract')),
    provenance      TEXT    NOT NULL,     -- JSON
    invalidated_by  INTEGER               -- NULL until superseded
);

-- EAVT — entity → attr → value → tx (primary read path)
CREATE INDEX IF NOT EXISTS idx_datom_eavt ON datom_log (e, a, valid_from, tx);

-- AEVT — attr → entity → value → tx (analytics, sensitivity)
CREATE INDEX IF NOT EXISTS idx_datom_aevt ON datom_log (a, e, valid_from, tx);

-- AVET — attr → value → entity → tx (lookup by indexed attribute)
CREATE INDEX IF NOT EXISTS idx_datom_avet ON datom_log (a, v, e, tx);

-- VAET — value → attr → entity → tx (reverse-ref graph traversal)
CREATE INDEX IF NOT EXISTS idx_datom_vaet ON datom_log (v, a, e, tx);

-- VT-E — bitemporal valid-time range scans
CREATE INDEX IF NOT EXISTS idx_datom_vte  ON datom_log (valid_from, valid_to, e);

-- LOG — tx-time ordered, drives since(t) and replication
CREATE INDEX IF NOT EXISTS idx_datom_log_txtime ON datom_log (tx_time, tx);
