"""POST /v1/blob/put + GET /v1/blob/get/{hash} (Phase 2.1c, Design §4.2, §4.3).

GET handler lands in T12 in this same file.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from persistence.claim import validate_attrs
from persistence.effect.canonical import canonical_dumps
from persistence.http.auth import require_auth
from persistence.http.routes._audit import extract_audit_chain_head
from persistence.http.schemas import BlobPutResponse


MAX_BLOB_BYTES = 16 * 1024 * 1024  # 16 MiB

router = APIRouter()


@router.post("/v1/blob/put", response_model=BlobPutResponse)
async def put(
    request: Request,
    _: Any = require_auth(),
) -> BlobPutResponse:
    """POST /v1/blob/put — store raw bytes in the CAS and emit a :claim/blob-put datom.

    Design §4.2 contract:
    - X-Session-Id header REQUIRED → 400 missing_session_id if absent/empty.
    - Body size limit MAX_BLOB_BYTES (16 MiB) → 413 oversize_body if exceeded.
    - Idempotent: same content twice → duplicate=True on second call; one file
      on disk, two :claim/blob-put datoms in substrate (both referencing same hash).
    """
    session_id = request.headers.get("X-Session-Id")
    if not session_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "missing_session_id", "detail": "X-Session-Id header is required"},
        )

    content = await request.body()
    if len(content) > MAX_BLOB_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "error": "oversize_body",
                "detail": f"body size {len(content)} exceeds {MAX_BLOB_BYTES}",
            },
        )

    blob_store = request.app.state.blob_store
    h, size, duplicate = blob_store.put(content)

    # Emit :claim/blob-put datom for the audit trail.
    # One datom per call — both first and duplicate puts are recorded (Design §4.2).
    substrate = request.app.state.substrate
    attrs = {
        "hash": h,
        "size_bytes": size,
        "content_type": request.headers.get("Content-Type", "application/octet-stream"),
        "session_id": session_id,
        "duplicate": duplicate,
    }
    # Canonical-validate — defensive; should always pass since we built the dict.
    validated = validate_attrs(":claim/blob-put", attrs)
    now = substrate.effect.perform(":sys/now", {})
    substrate.fact.transact([{
        "e": uuid.uuid4().hex,  # noqa: wall-clock — entity-id (txn precedent)
        "a": ":claim/blob-put",
        "v": canonical_dumps(validated),
        "valid_from": now,
    }])

    # --- Phase 2.1c.6: anchor audit entry on the canonical chain ---
    # Per design § 3.2 / § 3.4: fact-write happens first; the perform anchors
    # the AuditEntry on _canonical_audit_entries via the audit middleware
    # wrapping :blob/put (added to CANONICAL_AUDIT_WRAPPED_OPS in T1).
    substrate.effect.perform(":blob/put", {
        "hash": h,
        "size_bytes": size,
        "session_id": session_id,
        "duplicate": duplicate,
    })
    audit_chain_head = extract_audit_chain_head(substrate)

    return BlobPutResponse(hash=h, size_bytes=size, duplicate=duplicate, audit_chain_head=audit_chain_head)


@router.get("/v1/blob/get/{blob_hash}")
async def get_blob(
    blob_hash: str,
    request: Request,
    _: Any = require_auth(),
) -> Response:
    """GET /v1/blob/get/{blob_hash} — retrieve raw bytes from the CAS by hash.

    Design §4.3 contract:
    - hash path param must match ^sha256:[0-9a-f]{64}$ → 400 malformed_hash if not.
    - Returns raw bytes with Content-Type: application/octet-stream on 200.
    - 404 blob_not_found with uniform JSON envelope if hash is valid but unknown.
    - 401 bearer_required if auth fails (via require_auth dependency).
    """
    blob_store = request.app.state.blob_store
    try:
        content = blob_store.get(blob_hash)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "malformed_hash", "detail": str(e)},
        ) from e
    if content is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "blob_not_found", "detail": f"hash {blob_hash!r} not found"},
        )
    return Response(content=content, media_type="application/octet-stream")
