"""Phase 2.1c — curated s.claim namespace exposure (Design §8)."""
from __future__ import annotations

import pytest

from persistence.sdk import Substrate


def test_s_claim_exposes_kinds_frozenset():
    with Substrate.open("memory") as s:
        assert isinstance(s.claim.kinds, frozenset)
        assert ":claim/tool-exec" in s.claim.kinds
        assert ":claim/blob-put" in s.claim.kinds


def test_s_claim_is_kind_discriminator():
    with Substrate.open("memory") as s:
        assert s.claim.is_kind(":claim/tool-exec") is True
        assert s.claim.is_kind(":llm/decision") is False


def test_s_claim_validate_returns_canonical_attrs():
    with Substrate.open("memory") as s:
        out = s.claim.validate(":claim/blob-put", {
            "hash": "sha256:" + "c" * 64,
            "size_bytes": 0,
            "content_type": "application/octet-stream",
            "session_id": "smoke",
            "duplicate": False,
        })
        assert out["hash"].startswith("sha256:")


def test_s_claim_validate_raises_on_fact_kind():
    from persistence.claim._validate import UnknownClaimKindError
    with Substrate.open("memory") as s:
        with pytest.raises(UnknownClaimKindError):
            s.claim.validate(":llm/decision", {"kind": "act"})


def test_s_claim_identity_stub_returns_none():
    with Substrate.open("memory") as s:
        assert s.claim.identity.attest(caller_id="x", payload=b"", signature=None) is None


def test_persistence_claim_module_public_api():
    """Top-level imports usable without diving into _-prefixed modules."""
    from persistence.claim import (
        CLAIM_KINDS,
        is_claim_kind,
        validate_attrs,
        ClaimValidationError,
        UnknownClaimKindError,
        CallerIdentity,
    )
    assert ":claim/tool-exec" in CLAIM_KINDS
