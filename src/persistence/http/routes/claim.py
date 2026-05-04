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
import json
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request

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
    ClaimQueryResponse,
    ClaimRecord,
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


# ---------------------------------------------------------------------------
# GET /v1/claim/query
# ---------------------------------------------------------------------------

# Forced-spec-deviation (T10): Datom.__post_init__ strips the leading colon
# from ``a`` at construction time (see src/persistence/fact/datom.py ~L155:
# ``object.__setattr__(self, "a", self.a.lstrip(":"))``) so stored datom.a is
# always the bare form (e.g. "claim/tool-exec", not ":claim/tool-exec").
# CLAIM_KINDS, however, stores the colon-prefixed convention.
# We build _BARE_CLAIM_KINDS once at import time for O(1) membership checks
# against datom.a, and restore the colon when building ClaimRecord.kind.
_BARE_CLAIM_KINDS: frozenset[str] = frozenset(k.lstrip(":") for k in CLAIM_KINDS)


@router.get("/v1/claim/query", response_model=ClaimQueryResponse)
async def query(
    request: Request,
    _: Any = require_auth(),
    session_id: Optional[str] = None,
    since: int = 0,
    limit: int = Query(default=100, ge=1, le=1000),
    kind: Optional[str] = None,
) -> ClaimQueryResponse:
    """GET /v1/claim/query — return claim datoms for a session.

    Design §4.4 contract:
    - session_id is REQUIRED → 400 missing_session_id if absent.
    - kind filter: if provided, must be in CLAIM_KINDS → 400 not_a_claim_kind.
    - Mixed-namespace discipline (G3): only datoms whose ``a`` is in
      CLAIM_KINDS are returned, even without a kind filter.
    - Pagination: returns datoms with tx > since, up to limit. next_since is
      the tx of the last returned claim (or since if empty).
    """
    if session_id is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "missing_session_id", "detail": "session_id query param is required"},
        )
    if kind is not None and kind not in CLAIM_KINDS:
        raise HTTPException(
            status_code=400,
            detail={"error": "not_a_claim_kind", "detail": f"kind {kind!r} not in CLAIM_KINDS"},
        )

    # Normalise the optional kind filter to the bare form for datom.a
    # comparison (colon is stripped by Datom.__post_init__).
    bare_kind: Optional[str] = kind.lstrip(":") if kind is not None else None

    substrate = request.app.state.substrate
    matched: list[ClaimRecord] = []

    # Iterate all datoms via substrate._db.log() — the canonical all-datoms
    # iterator (used by test_decide_replay.py and confirmed by T9 notes).
    # The log is in insertion order so we can stop early once we have `limit`
    # matches. Datoms are monotonically increasing in tx, so filtering
    # tx > since then taking the first `limit` is correct for keyset pagination.
    for datom in substrate._db.log():
        if datom.tx <= since:
            continue
        # G3: only claim-namespace datoms; bare_kind is already stripped
        if datom.a not in _BARE_CLAIM_KINDS:
            continue
        if bare_kind is not None and datom.a != bare_kind:
            continue
        # Parse the JSON value (stored as canonical-JSON string by emit)
        try:
            attrs = json.loads(datom.v) if isinstance(datom.v, str) else datom.v
        except (json.JSONDecodeError, TypeError):
            continue  # malformed v — skip defensively
        if not isinstance(attrs, dict) or attrs.get("session_id") != session_id:
            continue
        # Restore the colon-prefixed kind convention in the response
        kind_with_colon = f":{datom.a}" if not datom.a.startswith(":") else datom.a
        ts = int(datom.valid_from.timestamp() * 1000) if hasattr(datom.valid_from, "timestamp") else 0
        matched.append(ClaimRecord(
            tx=datom.tx,
            kind=kind_with_colon,
            attrs=attrs,
            ts=ts,
            caller_identity=None,
        ))
        if len(matched) >= limit:
            break

    next_since = matched[-1].tx if matched else since
    return ClaimQueryResponse(claims=matched, next_since=next_since)
