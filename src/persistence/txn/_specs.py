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
    keys,
    map_of,
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
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason="not a valid UUID string",
                hint="provide a UUID string in canonical 8-4-4-4-12 hex form",
            )
        # Normalise to canonical lowercase 8-4-4-4-12 form. uuid.UUID()
        # accepts uppercase and braced inputs; if we passed those through
        # they would fail the outer :persistence.fact/datom spec which
        # uses the strict uuid_() primitive.
        return Conformed(value=str(parsed), spec_key=self.spec_name)

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


class _EdnValueSpec(Spec):
    """Recursive EDN-conformant value: scalars + lists/tuples + str-keyed dicts.

    Used by ``:persistence.txn/intent-log`` to validate the kwargs of each
    queued effect intent at commit time. Rejects ``datetime``, dataclasses,
    custom classes — values that would not survive a wire round-trip onto
    the commit datom's provenance.

    The intentional rejection of ``datetime`` here pins the contract: callers
    that want to record a timestamp on an intent must call ``.isoformat()``
    explicitly before passing the value into ``tx.effect()``.
    """

    spec_name: str = ":persistence.txn/edn-value"

    def _conform(self, value):
        # Scalars: bool comes before int (bool is an int subclass) but we
        # accept both branches anyway. ``None`` is a valid EDN nil.
        if value is None or isinstance(value, (bool, int, float, str)):
            return Conformed(value=value, spec_key=self.spec_name)
        if isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                r = self._conform(item)
                if not r.is_ok:
                    return ConformError(
                        spec_key=self.spec_name,
                        value=value,
                        reason=f"non-EDN element at index {i}: {type(item).__name__}",
                        hint="EDN values are scalars (int/float/str/bool/None) "
                        "or recursive lists/dicts",
                    )
            return Conformed(value=list(value), spec_key=self.spec_name)
        if isinstance(value, dict):
            for k, v in value.items():
                if not isinstance(k, str):
                    return ConformError(
                        spec_key=self.spec_name,
                        value=value,
                        reason=f"non-string dict key: {type(k).__name__}",
                        hint="EDN map keys must be strings",
                    )
                r = self._conform(v)
                if not r.is_ok:
                    return ConformError(
                        spec_key=self.spec_name,
                        value=value,
                        reason=f"non-EDN value at key {k!r}: {type(v).__name__}",
                        hint="EDN values are scalars or recursive lists/dicts",
                    )
            return Conformed(value=dict(value), spec_key=self.spec_name)
        return ConformError(
            spec_key=self.spec_name,
            value=value,
            reason=f"non-EDN value: {type(value).__name__}",
            hint="EDN values are scalars (int/float/str/bool/None) or "
            "lists/tuples/str-keyed dicts",
        )

    def _generate(self):
        return None  # trivial example


_uuid_str_spec = _UuidStringSpec()
_non_negative_int_spec = _NonNegativeIntSpec()
_edn_value_spec = _EdnValueSpec()
_intent_element_spec = keys(
    required={
        ":op": str_(),
        ":kwargs": map_of(str_(), _edn_value_spec),
    },
)


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
    # v0.5.1: per-element fixed-key map shape replaces the v0.5.0a1 placeholder
    # ``seq_of(_uuid_str_spec)``, which was registered before the intent-log
    # was ever emitted on a commit datom.
    register(":persistence.txn/intent-log", seq_of(_intent_element_spec))
    register(":persistence.txn/commit", _uuid_str_spec)


__all__ = ["register_txn_specs"]
