"""Claim namespace registry (Phase 2.1c, Design §5).

Owns the closed set of `:claim/*` kinds the substrate accepts via the HTTP
surface. Distinct from the broader datom-kind registry — this is the
*claims-only* subset, deliberately separate to make the trust delta visible
in the data itself (see Design §2.1, §5.0).
"""
from __future__ import annotations

CLAIM_KINDS: frozenset[str] = frozenset({
    ":claim/tool-exec",
    ":claim/blob-put",
})


def is_claim_kind(kind: str) -> bool:
    """True iff `kind` is in the claim namespace AND registered.

    Used by the HTTP surface to enforce kind restriction (§4) and by the
    query route to filter claim-only datoms out of the audit log (§4.4).
    """
    return kind in CLAIM_KINDS
