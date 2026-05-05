"""Per-kind attrs validation for claim namespace (Phase 2.1c, Design §5).

Pydantic-backed schema validation with explicit cross-field invariants
enforced server-side, NOT left to clients (Design §5.1, R1 IMPORTANT I1).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, model_validator

from persistence.claim._registry import CLAIM_KINDS


class ClaimValidationError(Exception):
    """Raised when a claim's attrs fail schema or cross-field invariant validation."""


class UnknownClaimKindError(ClaimValidationError):
    """Raised when a kind is not in CLAIM_KINDS."""


class _ToolExecAttrs(BaseModel):
    tool: str = Field(min_length=1)
    args: dict[str, Any]
    body_hash: Optional[str] = None
    body_summary: str = Field(max_length=512)
    body_disposition: Literal["inline", "blobbed", "discarded"]
    started_at: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    exit_code: Optional[int] = None
    session_id: str = Field(min_length=1)
    parent_correlation_id: Optional[str] = None

    @model_validator(mode="after")
    def _body_disposition_invariant(self) -> "_ToolExecAttrs":
        if self.body_disposition == "blobbed" and self.body_hash is None:
            raise ValueError("body_disposition='blobbed' requires body_hash to be set")
        if self.body_disposition in ("inline", "discarded") and self.body_hash is not None:
            raise ValueError(
                f"body_disposition={self.body_disposition!r} requires body_hash to be None"
            )
        return self


class _BlobPutAttrs(BaseModel):
    hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    content_type: str
    session_id: str = Field(min_length=1)
    duplicate: bool


_SCHEMAS: dict[str, type[BaseModel]] = {
    ":claim/tool-exec": _ToolExecAttrs,
    ":claim/blob-put": _BlobPutAttrs,
}

# Drift guard: every kind in CLAIM_KINDS must have a registered schema, and
# vice versa. Failing fast at import time turns a future KeyError-on-validate
# into a clear load-time error.
assert frozenset(_SCHEMAS.keys()) == CLAIM_KINDS, (
    f"CLAIM_KINDS ↔ _SCHEMAS drift: {CLAIM_KINDS ^ frozenset(_SCHEMAS.keys())!r}"
)


def validate_attrs(kind: str, attrs: dict[str, Any]) -> dict[str, Any]:
    """Validate `attrs` against the registered schema for `kind`.

    Returns the validated (canonicalized) attrs dict on success.
    Raises:
      - UnknownClaimKindError if kind is not in CLAIM_KINDS
      - ClaimValidationError on any schema or cross-field invariant violation
    """
    if kind not in CLAIM_KINDS:
        raise UnknownClaimKindError(f"kind {kind!r} is not in CLAIM_KINDS")
    model_cls = _SCHEMAS[kind]
    try:
        validated = model_cls.model_validate(attrs)
    except ValidationError as e:
        raise ClaimValidationError(str(e)) from e
    return validated.model_dump()
