"""Phase 2.1c — validate_attrs + cross-field invariants (Design §5.1, §10.1)."""
import pytest

from persistence.claim._validate import (
    ClaimValidationError,
    UnknownClaimKindError,
    validate_attrs,
)

# ---------- :claim/tool-exec ----------

def _valid_tool_exec_attrs(**overrides):
    base = {
        "tool": "Bash",
        "args": {"command": "ls"},
        "body_hash": None,
        "body_summary": "5 files",
        "body_disposition": "inline",
        "started_at": 1714839028000,
        "duration_ms": 12,
        "exit_code": 0,
        "session_id": "session-2026-05-04-abc",
        "parent_correlation_id": None,
    }
    return {**base, **overrides}


def test_validate_attrs_tool_exec_valid_inline():
    out = validate_attrs(":claim/tool-exec", _valid_tool_exec_attrs())
    assert out["body_disposition"] == "inline"
    assert out["body_hash"] is None


def test_validate_attrs_tool_exec_valid_blobbed():
    out = validate_attrs(":claim/tool-exec", _valid_tool_exec_attrs(
        body_disposition="blobbed",
        body_hash="sha256:" + "a" * 64,
    ))
    assert out["body_hash"].startswith("sha256:")


def test_validate_attrs_tool_exec_valid_discarded():
    out = validate_attrs(":claim/tool-exec", _valid_tool_exec_attrs(
        body_disposition="discarded",
        body_hash=None,
    ))
    assert out["body_disposition"] == "discarded"


def test_validate_attrs_tool_exec_missing_required_raises():
    bad = _valid_tool_exec_attrs()
    del bad["tool"]
    with pytest.raises(ClaimValidationError):
        validate_attrs(":claim/tool-exec", bad)


def test_validate_attrs_tool_exec_body_summary_oversize_raises():
    with pytest.raises(ClaimValidationError):
        validate_attrs(":claim/tool-exec", _valid_tool_exec_attrs(body_summary="x" * 513))


def test_validate_attrs_tool_exec_body_disposition_literal_enforced():
    with pytest.raises(ClaimValidationError):
        validate_attrs(":claim/tool-exec", _valid_tool_exec_attrs(body_disposition="bogus"))


# ---------- cross-field invariants (Design §5.1) ----------

def test_blobbed_disposition_requires_body_hash():
    with pytest.raises(ClaimValidationError, match="body_hash"):
        validate_attrs(":claim/tool-exec", _valid_tool_exec_attrs(
            body_disposition="blobbed",
            body_hash=None,
        ))


def test_inline_disposition_forbids_body_hash():
    with pytest.raises(ClaimValidationError, match="body_hash"):
        validate_attrs(":claim/tool-exec", _valid_tool_exec_attrs(
            body_disposition="inline",
            body_hash="sha256:" + "a" * 64,
        ))


def test_discarded_disposition_forbids_body_hash():
    with pytest.raises(ClaimValidationError, match="body_hash"):
        validate_attrs(":claim/tool-exec", _valid_tool_exec_attrs(
            body_disposition="discarded",
            body_hash="sha256:" + "a" * 64,
        ))


# ---------- :claim/blob-put ----------

def _valid_blob_put_attrs(**overrides):
    base = {
        "hash": "sha256:" + "b" * 64,
        "size_bytes": 1024,
        "content_type": "application/octet-stream",
        "session_id": "session-1",
        "duplicate": False,
    }
    return {**base, **overrides}


def test_validate_attrs_blob_put_valid():
    out = validate_attrs(":claim/blob-put", _valid_blob_put_attrs())
    assert out["hash"].startswith("sha256:")


def test_validate_attrs_blob_put_missing_hash_raises():
    bad = _valid_blob_put_attrs()
    del bad["hash"]
    with pytest.raises(ClaimValidationError):
        validate_attrs(":claim/blob-put", bad)


# ---------- unknown kind ----------

def test_validate_attrs_unknown_kind_raises():
    with pytest.raises(UnknownClaimKindError):
        validate_attrs(":not/a-claim", {"anything": "goes"})


def test_validate_attrs_fact_kind_raises():
    with pytest.raises(UnknownClaimKindError):
        validate_attrs(":llm/decision", {"kind": "act"})
