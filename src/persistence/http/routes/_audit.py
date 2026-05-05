"""Shared audit-chain head extraction helper for HTTP routes (Phase 2.1c.6).

Both /v1/claim/emit and /v1/blob/put need to read the canonical audit
chain head after their respective :claim/emit / :blob/put effect performs.
This helper is the single source of truth for that lookup.
"""
from __future__ import annotations


def extract_audit_chain_head(substrate) -> str:
    """Return the substrate's canonical audit chain head id.

    Phase 2.1c.6: audit chain advances on every :claim/emit / :blob/put
    perform. Returns the most recent AuditEntry.id from
    _canonical_audit_entries.
    """
    entries = substrate._canonical_audit_entries
    if entries is None or len(entries) == 0:
        # Substrate opened with audit=False, or no perform yet — neither
        # should occur on the production HTTP path (build_app always opens
        # with audit=True default; the perform happens before this helper
        # is called). Defensive: surface the gap loud rather than return
        # a sentinel that masks real bugs.
        raise RuntimeError(
            "audit chain head requested but canonical chain is empty; "
            "indicates a missing :claim/emit or :blob/put perform, or audit=False mode"
        )
    return entries[-1].id
