"""POST /v1/blob/put + GET /v1/blob/get/{hash} (Phase 2.1c, Design §4.2, §4.3).

GET handler lands in T12 in this same file.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from persistence.claim import validate_attrs
from persistence.effect.canonical import canonical_dumps
from persistence.http.auth import require_auth
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
    now = dt.datetime.now(dt.timezone.utc)
    substrate.fact.transact([{
        "e": uuid.uuid4().hex,
        "a": ":claim/blob-put",
        "v": canonical_dumps(validated),
        "valid_from": now,
    }])

    return BlobPutResponse(hash=h, size_bytes=size, duplicate=duplicate)
