"""Phase 2.1c — Pydantic request/response model tests."""
import pytest
from pydantic import ValidationError

from persistence.http.schemas import (
    ClaimEmitRequest,
    ClaimEmitResponse,
    ClaimQueryResponse,
    BlobPutResponse,
    ErrorResponse,
)


def test_claim_emit_request_accepts_minimal_valid_shape():
    req = ClaimEmitRequest.model_validate({
        "claims": [{
            "kind": ":claim/tool-exec",
            "attrs": {"tool": "Bash", "args": {}, "body_summary": "ok",
                      "body_disposition": "inline", "body_hash": None,
                      "started_at": 0, "duration_ms": 0, "exit_code": 0,
                      "session_id": "s", "parent_correlation_id": None},
        }],
    })
    assert len(req.claims) == 1


def test_claim_emit_request_rejects_empty_claims_list():
    with pytest.raises(ValidationError):
        ClaimEmitRequest.model_validate({"claims": []})


def test_claim_emit_response_shape():
    r = ClaimEmitResponse(tx=42, claim_ids=["d-1"], audit_chain_head="sha256:x", caller_identity=None)
    assert r.model_dump()["caller_identity"] is None


def test_error_response_shape_locked():
    e = ErrorResponse(error="bearer_required", detail="missing")
    assert e.model_dump() == {"error": "bearer_required", "detail": "missing"}
