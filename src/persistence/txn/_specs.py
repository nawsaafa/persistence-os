"""Spec definitions for :persistence.txn/* datom attributes.

Registered into the global spec registry at txn module import time so
parse-don't-validate boundary code (db.transact spec-conform pass)
catches malformed txn datoms before they hit the log.

See design doc § 4 for the full per-attribute schema.
"""
from __future__ import annotations

import uuid

from persistence.spec import (
    bool_,
    inst,
    register,
    seq_of,
    str_,
)
from persistence.spec._types import ConformError, Conformed, Spec


class _UuidStringSpec(Spec):
    """UUID in canonical string form (8-4-4-4-12 hex)."""

    spec_name: str = ":persistence.txn/uuid-string"

    def _conform(self, value):
        if not isinstance(value, str):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected str, got {type(value).__name__}",
                hint="provide a UUID string in canonical 8-4-4-4-12 hex form",
            )
        try:
            uuid.UUID(value)
        except (ValueError, AttributeError):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason="not a valid UUID string",
                hint="provide a UUID string in canonical 8-4-4-4-12 hex form",
            )
        return Conformed(value=value, spec_key=self.spec_name)

    def _generate(self):
        return str(uuid.uuid4())  # noqa: wall-clock


class _NonNegativeIntSpec(Spec):
    """Non-negative int — int (excluding bool) where v >= 0."""

    spec_name: str = ":persistence.txn/non-negative-int"

    def _conform(self, value):
        if isinstance(value, bool) or not isinstance(value, int):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected int, got {type(value).__name__}",
                hint="provide a non-negative int (retry count)",
            )
        if value < 0:
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"int is negative: {value}",
                hint="provide a non-negative int (retry count)",
            )
        return Conformed(value=value, spec_key=self.spec_name)

    def _generate(self):
        return 0


_uuid_str_spec = _UuidStringSpec()
_non_negative_int_spec = _NonNegativeIntSpec()


def register_txn_specs() -> None:
    """Idempotent registration of all :persistence.txn/* specs.

    Called once at module import; safe to re-call (re-registration is
    atomic, see spec/_registry.py).
    """
    register(":persistence.txn/commit-id", _uuid_str_spec)
    register(":persistence.txn/started-at", inst())
    register(":persistence.txn/committed-at", inst())
    register(":persistence.txn/retry-count", _non_negative_int_spec)
    register(":persistence.txn/read-set", seq_of(str_()))
    register(":persistence.txn/non-deterministic-retry", bool_())
    register(":persistence.txn/intent-log", seq_of(_uuid_str_spec))
    register(":persistence.txn/commit", _uuid_str_spec)


__all__ = ["register_txn_specs"]
