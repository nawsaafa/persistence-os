"""POST /v1/claim/emit (Phase 2.1c, Design §4.1).

GET /v1/claim/query lands in Task 10 in this same file.

Forced-spec-deviation (T9): impl plan used ``txn.commit_claims(canonicalized)``
which does not exist. Adapted to ``substrate.fact.transact(datoms)`` per the
substrate write API (see src/persistence/fact/db.py:195-242 and the Coder
session precedent in src/persistence/coder/_session.py:79-90).

Accessor notes:
- ``substrate.fact.transact(datoms)`` returns a new DB; the real tx id is
  recoverable via ``substrate._db.store.next_tx - 1`` (next_tx returns
  max(tx)+1; after transact the max is the one just assigned).
- ``audit_chain_head``: readable from ``substrate._canonical_audit_entries``
  if non-empty (last entry's ``.id`` attribute). Placeholder
  ``"sha256:placeholder"`` used when canonical chain is absent/empty (e.g.
  audit=False test substrate). T14 will wire against build_app real values and
  surface any deviation via tests.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from persistence.claim import (
    CLAIM_KINDS,
    ClaimValidationError,
    UnknownClaimKindError,
    validate_attrs,
)
from persistence.effect.canonical import canonical_dumps
from persistence.http.auth import require_auth
from persistence.http.schemas import (
    ClaimEmitRequest,
    ClaimEmitResponse,
)

router = APIRouter()


def _extract_tx(substrate: Any) -> int:
    """Return the tx id of the most recently committed datom.

    ``store.next_tx`` returns ``max(tx) + 1``; after a successful transact the
    max tx IS the one just written, so ``next_tx - 1`` gives us the real id.
    Falls back to 0 (TODO T14 placeholder) if the store is missing or empty.
    """
    try:
        return int(substrate._db.store.next_tx) - 1
    except Exception:
        return 0  # TODO T14: surface if real tx accessor differs


def _extract_audit_chain_head(substrate: Any) -> str:
    """Return the current audit-chain head hash.

    Reads from ``substrate._canonical_audit_entries[-1].id`` when the canonical
    audit chain is present and non-empty (``Substrate.open(audit=True)`` path).
    Falls back to ``"sha256:placeholder"`` when audit=False or chain is empty.
    TODO T14: verify the real head accessor against build_app wiring.
    """
    try:
        canonical = substrate._canonical_audit_entries
        if canonical:
            last = canonical[-1]
            head = getattr(last, "id", None)
            if isinstance(head, str) and head:
                return head
    except Exception:
        pass
    return "sha256:placeholder"  # TODO T14 — wire to real audit head


@router.post("/v1/claim/emit", response_model=ClaimEmitResponse)
async def emit(
    req: ClaimEmitRequest,
    request: Request,
    _: Any = require_auth(),
) -> ClaimEmitResponse:
    """POST /v1/claim/emit — atomically transact a batch of validated claims.

    Pre-validates EVERY claim's kind + attrs BEFORE any commit so the whole
    batch is atomic: if any claim fails validation, none are written.
    """
    # --- Phase 1: pre-validate all claims (no writes yet) ---
    canonicalized: list[tuple[str, dict]] = []
    for sub in req.claims:
        if sub.kind not in CLAIM_KINDS:
            raise HTTPException(
                status_code=400,
                detail={"error": "not_a_claim_kind", "detail": f"kind {sub.kind!r} not in CLAIM_KINDS"},
            )
        try:
            attrs = validate_attrs(sub.kind, sub.attrs)
        except UnknownClaimKindError as e:
            raise HTTPException(
                status_code=400,
                detail={"error": "not_a_claim_kind", "detail": str(e)},
            ) from e
        except ClaimValidationError as e:
            raise HTTPException(
                status_code=422,
                detail={"error": "attrs_validation_failed", "detail": str(e)},
            ) from e
        canonicalized.append((sub.kind, attrs))

    # --- Phase 2: build datoms and transact atomically ---
    # Forced-spec-deviation: impl plan used txn.commit_claims(...) which
    # does not exist. Using substrate.fact.transact per DB.transact contract.
    substrate = request.app.state.substrate
    now = dt.datetime.now(dt.timezone.utc)
    datoms: list[dict] = []
    claim_ids: list[str] = []
    for kind, attrs in canonicalized:
        claim_id = uuid.uuid4().hex
        claim_ids.append(claim_id)
        datoms.append({
            "e": claim_id,
            "a": kind,
            "v": canonical_dumps(attrs),
            "valid_from": now,
        })
    # transact is atomic: all datoms land in one tx or none do.
    substrate.fact.transact(datoms)

    # --- Phase 3: extract response fields ---
    # tx: store.next_tx - 1 after successful transact gives the real tx id.
    tx = _extract_tx(substrate)
    # audit_chain_head: from canonical audit entries if present.
    audit_chain_head = _extract_audit_chain_head(substrate)

    return ClaimEmitResponse(
        tx=tx,
        claim_ids=claim_ids,
        audit_chain_head=audit_chain_head,
        caller_identity=None,  # CallerIdentity stub returns None in 2.1c (T4)
    )
