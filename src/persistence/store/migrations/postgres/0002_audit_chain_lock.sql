-- persistence.store — Postgres migration 0002: audit_chain_lock
--
-- PG3 (Phase 1 stream #166) lands the ``audit_chain_lock`` single-row
-- table that orders the audit-chain Merkle linkage across processes.
-- Every audit-emitting ``transact_serializable`` call takes
-- ``SELECT FOR UPDATE`` on this row before INSERTing audit datoms, so
-- two concurrent writers hash-chain in commit order rather than write
-- order. ``last_seq`` / ``last_hash`` are denormalised performance
-- hints (the source of truth lives in ``datom_log``); they make the
-- chain head findable in O(1) for the SDK MCP ``audit_tail`` resource
-- without an ``ORDER BY seq DESC LIMIT 1`` scan.
--
-- See:
--   docs/plans/2026-04-30-v0.8.0-postgres-store-design.md
--     ADR-3   audit_chain_lock single-row primitive.
--     ADR-13  persist_repl_audit migration to transact_serializable.
--     §13     "audit_chain_lock deferred" RESOLVED in PG3.
--
-- Lock ordering inside ``transact_serializable`` (audit-aware path):
--   1. SELECT FOR UPDATE on ``tx_allocator`` (tx-id assignment).
--   2. SELECT FOR UPDATE on ``audit_chain_lock`` (chain-head lock).
--   3. INSERT into ``datom_log``.
--   4. UPDATE ``audit_chain_lock`` (denormalised last_seq + last_hash).
-- This ordering is fixed and prevents deadlock between concurrent
-- audit-emitting transactions.

CREATE TABLE IF NOT EXISTS audit_chain_lock (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    last_seq    BIGINT  NOT NULL DEFAULT 0,
    last_hash   TEXT    NOT NULL DEFAULT ''
);

INSERT INTO audit_chain_lock (id, last_seq, last_hash)
VALUES (1, 0, '')
ON CONFLICT (id) DO NOTHING;
