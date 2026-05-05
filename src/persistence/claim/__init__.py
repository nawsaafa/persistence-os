"""Persistence claims layer (Phase 2.1c, @experimental("v0.9.x")).

Trust-boundary module: owns the ``:claim/*`` kind namespace, schema validation,
and identity-attestation hooks. Imported by ``persistence.http``; never imports
from ``persistence.http``.

See Design doc §5 (claims layer epistemic posture), §8 (module layout).
"""
from __future__ import annotations

from persistence.claim._identity import CallerIdentity
from persistence.claim._registry import CLAIM_KINDS, is_claim_kind
from persistence.claim._validate import (
    ClaimValidationError,
    UnknownClaimKindError,
    validate_attrs,
)

__all__ = [
    "CLAIM_KINDS",
    "is_claim_kind",
    "validate_attrs",
    "ClaimValidationError",
    "UnknownClaimKindError",
    "CallerIdentity",
]
