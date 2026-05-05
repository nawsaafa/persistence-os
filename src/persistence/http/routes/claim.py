"""POST /v1/claim/emit (Phase 2.1c, Design §4.1).

GET /v1/claim/query lands in Task 10 in this same file.

Forced-spec-deviation (T9): impl plan used ``txn.commit_claims(canonicalized)``
which does not exist. Adapted to ``substrate.fact.transact(datoms)`` per the
substrate write API (see src/persistence/fact/db.py:195-242 and the Coder
session precedent in src/persistence/coder/_session.py:79-90).

Accessor notes:
- ``substrate.fact.transact(datoms)`` returns a new DB; the real tx id is
  recoverable via ``substrate._db.store.next_tx() - 1`` (next_tx is a method,
  not a property; it returns max(tx)+1; after transact the max is the one just
  assigned). T14 fix: call next_tx() not next_tx.
- ``audit_chain_head``: Phase 2.1c.6 wires the canonical audit chain via
  ``substrate.effect.perform(":claim/emit", {...})`` AFTER ``s.fact.transact``.
  The perform call anchors an AuditEntry on ``_canonical_audit_entries`` via
  the audit middleware wrapping ``:claim/emit`` (added to
  CANONICAL_AUDIT_WRAPPED_OPS in T1). Head is the most recent entry's id.
  See docs/plans/2026-05-04-phase-2.1c.6-audit-chain-wiring-design.md § 3.2.

Forced-spec-deviation (T14 — oversize_body):
  Design §4.1 specifies a 1 MiB body cap with 413 oversize_body. FastAPI
  parses the Pydantic model before the handler body executes, so we cannot
  intercept oversize bodies via ``req: ClaimEmitRequest`` injection. Fix:
  emit() reads the raw body manually (``await request.body()``), checks the
  size, then parses via ``ClaimEmitRequest.model_validate_json()``. This
  mirrors how routes/blob.py handles blob size limits.
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
from persistence.http.routes._audit import extract_audit_chain_head
from persistence.http.schemas import (
    ClaimEmitRequest,
    ClaimEmitResponse,
    ClaimQueryResponse,
    ClaimRecord,
)

router = APIRouter()


def _extract_tx(substrate: Any) -> int:
    """Return the tx id of the most recently committed datom.

    ``store.next_tx()`` is a callable (not a property) that returns
    ``max(tx) + 1``; after a successful transact the max tx IS the one just
    written, so ``next_tx() - 1`` gives us the real id.

    Raises RuntimeError if the store accessor is unavailable — broad silent
    fallback (except Exception → 0) was removed in Phase 2.1c R1.1 (ARIS
    finding F3 IMPORTANT) to surface substrate accessor drift loudly.
    """
    try:
        return int(substrate._db.store.next_tx()) - 1
    except (AttributeError, TypeError) as exc:
        raise RuntimeError(
            f"_extract_tx: substrate._db.store.next_tx() is unavailable or returned a "
            f"non-integer — check substrate accessor contract. Original error: {exc!r}"
        ) from exc  # TODO T14: surface if real tx accessor differs


def _kind_counts(canonicalized: list[tuple[str, dict]]) -> dict[str, int]:
    """Count claims per kind for the :claim/emit audit args.

    Phase 2.1c.6: included in the audit entry's args_hash so the audit
    log captures the kind distribution of each emit batch.
    """
    counts: dict[str, int] = {}
    for kind, _attrs in canonicalized:
        counts[kind] = counts.get(kind, 0) + 1
    return counts


# 1 MiB body cap for /v1/claim/emit (Design §4.1)
MAX_EMIT_BYTES: int = 1024 * 1024


@router.post("/v1/claim/emit", response_model=ClaimEmitResponse)
async def emit(
    request: Request,
    _: Any = require_auth(),
) -> ClaimEmitResponse:
    """POST /v1/claim/emit — atomically transact a batch of validated claims.

    Reads raw body first to enforce the 1 MiB size cap (Design §4.1) before
    Pydantic parsing. Pre-validates EVERY claim's kind + attrs BEFORE any
    commit so the whole batch is atomic: if any claim fails validation, none
    are written.

    Forced-spec-deviation (T14 — oversize_body): FastAPI parses Pydantic
    models before the handler body runs, so we cannot intercept an oversize
    JSON body via a typed ``req`` parameter. We read the raw body here,
    check size, then call ``ClaimEmitRequest.model_validate_json()`` manually.
    """
    # --- Size gate (before parse — Design §4.1) ---
    raw_body = await request.body()
    if len(raw_body) > MAX_EMIT_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "error": "oversize_body",
                "detail": f"body size {len(raw_body)} exceeds {MAX_EMIT_BYTES}",
            },
        )

    # --- Parse (manual, since we own the raw body now) ---
    try:
        req = ClaimEmitRequest.model_validate_json(raw_body)
    except Exception as parse_exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "malformed_body", "detail": str(parse_exc)},
        ) from parse_exc

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
    now = dt.datetime.now(dt.timezone.utc)  # noqa: wall-clock — provenance ts; routes through :sys/now in 2.4a
    datoms: list[dict] = []
    claim_ids: list[str] = []
    for kind, attrs in canonicalized:
        claim_id = uuid.uuid4().hex  # noqa: wall-clock — entity-id (txn precedent)
        claim_ids.append(claim_id)
        datoms.append({
            "e": claim_id,
            "a": kind,
            "v": canonical_dumps(attrs),
            "valid_from": now,
        })
    # transact is atomic: all datoms land in one tx or none do.
    # Provenance survives any subsequent audit-perform failure (matches
    # Phase 2.1b :llm/messages-survives-:llm/call-failure pattern).
    substrate.fact.transact(datoms)
    real_tx = _extract_tx(substrate)

    # --- Phase 3 (Phase 2.1c.6): anchor audit entry on the canonical chain ---
    # Per design § 3.2: fact-write happens first; the perform anchors the
    # AuditEntry on _canonical_audit_entries via the audit middleware
    # wrapping :claim/emit (added to CANONICAL_AUDIT_WRAPPED_OPS in T1).
    substrate.effect.perform(":claim/emit", {
        "claim_ids": claim_ids,
        "tx": real_tx,
        "kind_counts": _kind_counts(canonicalized),
    })

    # --- Phase 4: extract response fields ---
    audit_chain_head = extract_audit_chain_head(substrate)

    return ClaimEmitResponse(
        tx=real_tx,
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
    limit: int = Query(default=50, ge=1, le=500),
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
