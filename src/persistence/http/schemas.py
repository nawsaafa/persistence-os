"""HTTP request/response Pydantic models (Phase 2.1c, Design §4)."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ClaimSubmission(BaseModel):
    kind: str
    attrs: dict[str, Any]


class ClaimEmitRequest(BaseModel):
    claims: list[ClaimSubmission] = Field(min_length=1)


class ClaimEmitResponse(BaseModel):
    tx: int
    claim_ids: list[str]
    audit_chain_head: str  # Phase 2.1c.6: wired via :claim/emit perform on canonical audit chain
    caller_identity: Optional[str] = None


class ClaimRecord(BaseModel):
    tx: int
    kind: str
    attrs: dict[str, Any]
    ts: int
    caller_identity: Optional[str] = None


class ClaimQueryResponse(BaseModel):
    claims: list[ClaimRecord]
    next_since: int


class BlobPutResponse(BaseModel):
    hash: str
    size_bytes: int
    duplicate: bool


class ErrorResponse(BaseModel):
    error: str
    detail: str
