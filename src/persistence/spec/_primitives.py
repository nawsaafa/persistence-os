"""Primitive scalar specs: int, float, bool, str, bytes, uuid, inst."""
from __future__ import annotations

import datetime as dt
import random
import string
import uuid
from dataclasses import dataclass
from typing import Any

from ._types import ConformError, Conformed, ConformResult, Spec

_rng = random.Random()


# ----- helpers --------------------------------------------------------------

def _type_err(spec_key: str, value: Any, expected: str) -> ConformError:
    got = type(value).__name__
    return ConformError(
        spec_key=spec_key,
        value=value,
        reason=f"expected {expected}, got {got}",
        hint=f"provide a value of type {expected}",
    )


# ----- scalar specs ---------------------------------------------------------

@dataclass(frozen=True)
class _IntSpec(Spec):
    spec_name: str = ":persistence.spec/int"

    def _conform(self, value: Any) -> ConformResult:
        # bool is a subclass of int; we explicitly reject it to avoid the classic
        # Python trap where `isinstance(True, int)` returns True.
        if isinstance(value, bool):
            return _type_err(self.spec_name, value, "int (not bool)")
        if isinstance(value, int):
            return Conformed(value=value, spec_key=self.spec_name)
        return _type_err(self.spec_name, value, "int")

    def _generate(self) -> int:
        return _rng.randint(-1_000_000, 1_000_000)

    def to_edn(self) -> Any:
        return {":spec": "int"}


@dataclass(frozen=True)
class _FloatSpec(Spec):
    spec_name: str = ":persistence.spec/float"

    def _conform(self, value: Any) -> ConformResult:
        # no silent int -> float coercion
        if isinstance(value, bool) or isinstance(value, int):
            return _type_err(self.spec_name, value, "float (not int)")
        if isinstance(value, float):
            return Conformed(value=value, spec_key=self.spec_name)
        return _type_err(self.spec_name, value, "float")

    def _generate(self) -> float:
        return _rng.uniform(-1000.0, 1000.0)

    def to_edn(self) -> Any:
        return {":spec": "float"}


@dataclass(frozen=True)
class _BoolSpec(Spec):
    spec_name: str = ":persistence.spec/bool"

    def _conform(self, value: Any) -> ConformResult:
        # must be exactly bool; reject numbers that "look truthy"
        if isinstance(value, bool):
            return Conformed(value=value, spec_key=self.spec_name)
        return _type_err(self.spec_name, value, "bool")

    def _generate(self) -> bool:
        return _rng.random() < 0.5

    def to_edn(self) -> Any:
        return {":spec": "bool"}


@dataclass(frozen=True)
class _StrSpec(Spec):
    spec_name: str = ":persistence.spec/str"

    def _conform(self, value: Any) -> ConformResult:
        if isinstance(value, str):
            return Conformed(value=value, spec_key=self.spec_name)
        return _type_err(self.spec_name, value, "str")

    def _generate(self) -> str:
        n = _rng.randint(0, 16)
        return "".join(_rng.choices(string.ascii_letters + string.digits, k=n))

    def to_edn(self) -> Any:
        return {":spec": "str"}


@dataclass(frozen=True)
class _BytesSpec(Spec):
    spec_name: str = ":persistence.spec/bytes"

    def _conform(self, value: Any) -> ConformResult:
        if isinstance(value, (bytes, bytearray)):
            return Conformed(value=bytes(value), spec_key=self.spec_name)
        return _type_err(self.spec_name, value, "bytes")

    def _generate(self) -> bytes:
        n = _rng.randint(0, 32)
        return _rng.randbytes(n)

    def to_edn(self) -> Any:
        return {":spec": "bytes"}


@dataclass(frozen=True)
class _UuidSpec(Spec):
    """UUID spec: accepts uuid.UUID or a canonical UUID string, refines to UUID.

    This is a bounded refinement: the wire format is a string (EDN/JSON) but the
    conformed value is a uuid.UUID so downstream business logic can rely on the
    type. We do NOT accept arbitrary strings — only those that parse as UUIDs.
    """

    spec_name: str = ":persistence.spec/uuid"

    def _conform(self, value: Any) -> ConformResult:
        if isinstance(value, uuid.UUID):
            return Conformed(value=value, spec_key=self.spec_name)
        if isinstance(value, str):
            try:
                return Conformed(value=uuid.UUID(value), spec_key=self.spec_name)
            except (ValueError, AttributeError):
                return ConformError(
                    spec_key=self.spec_name,
                    value=value,
                    reason="string is not a valid UUID",
                    hint="provide a canonical UUID like '550e8400-e29b-41d4-a716-446655440000'",
                )
        return _type_err(self.spec_name, value, "uuid.UUID or UUID-formatted str")

    def _generate(self) -> uuid.UUID:
        return uuid.UUID(int=_rng.getrandbits(128))

    def to_edn(self) -> Any:
        return {":spec": "uuid"}


@dataclass(frozen=True)
class _InstSpec(Spec):
    """Instant spec: timezone-aware datetime (or ISO-8601 string refined to one).

    Naive datetimes are rejected. Agent systems are distributed; a naive
    timestamp is structurally ambiguous and every serious clock failure in
    practice traces back to someone accepting one.
    """

    spec_name: str = ":persistence.spec/inst"

    def _conform(self, value: Any) -> ConformResult:
        if isinstance(value, dt.datetime):
            if value.tzinfo is None:
                return ConformError(
                    spec_key=self.spec_name,
                    value=value,
                    reason="datetime is naive (no tzinfo)",
                    hint="attach a timezone, e.g. datetime.now(timezone.utc)",
                )
            return Conformed(value=value, spec_key=self.spec_name)
        if isinstance(value, str):
            try:
                parsed = dt.datetime.fromisoformat(value)
            except ValueError:
                return ConformError(
                    spec_key=self.spec_name,
                    value=value,
                    reason="string is not ISO-8601",
                    hint="use ISO-8601 with timezone, e.g. '2026-04-20T12:00:00+00:00'",
                )
            if parsed.tzinfo is None:
                return ConformError(
                    spec_key=self.spec_name,
                    value=value,
                    reason="ISO-8601 string lacks timezone",
                    hint="append '+00:00' or 'Z' for UTC",
                )
            return Conformed(value=parsed, spec_key=self.spec_name)
        return _type_err(self.spec_name, value, "datetime or ISO-8601 str")

    def _generate(self) -> dt.datetime:
        epoch = _rng.randint(0, 2_000_000_000)
        return dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc)

    def to_edn(self) -> Any:
        return {":spec": "inst"}


# ----- singleton constructors (called like S.int_(), S.str_()) --------------

_INT_SINGLETON = _IntSpec()
_FLOAT_SINGLETON = _FloatSpec()
_BOOL_SINGLETON = _BoolSpec()
_STR_SINGLETON = _StrSpec()
_BYTES_SINGLETON = _BytesSpec()
_UUID_SINGLETON = _UuidSpec()
_INST_SINGLETON = _InstSpec()


def int_() -> Spec:
    """Spec for Python ``int`` (not bool)."""
    return _INT_SINGLETON


def float_() -> Spec:
    """Spec for Python ``float`` (strict: not int)."""
    return _FLOAT_SINGLETON


def bool_() -> Spec:
    """Spec for Python ``bool``."""
    return _BOOL_SINGLETON


def str_() -> Spec:
    """Spec for Python ``str``."""
    return _STR_SINGLETON


def bytes_() -> Spec:
    """Spec for ``bytes``/``bytearray`` (refined to ``bytes``)."""
    return _BYTES_SINGLETON


def uuid_() -> Spec:
    """Spec for UUID (accepts ``uuid.UUID`` or canonical UUID string, refines to UUID)."""
    return _UUID_SINGLETON


def inst() -> Spec:
    """Spec for timezone-aware datetime (or ISO-8601 string refined to one)."""
    return _INST_SINGLETON
