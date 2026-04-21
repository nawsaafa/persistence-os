"""Wire-form adapter for :class:`persistence.fact.datom.Datom`.

The canonical ``:persistence.fact/datom`` spec (see
:mod:`persistence.spec._canonical`) is the authoritative wire shape: a
dict with EDN-keyword keys (leading colons), tz-aware datetimes for
``:datom/tx-time``/``:datom/valid-from``, and either a UUID / content-hash
for ``:datom/e``, int / content-hash for ``:datom/tx``.

The :class:`Datom` dataclass is the in-memory form used throughout
:mod:`persistence.fact`. Two converters close the loop:

- :func:`datom_to_wire` — Datom → wire dict. Conforms the result through
  ``spec.parse(":persistence.fact/datom", ...)`` before returning, so a
  production Datom that fails the spec raises immediately instead of
  corrupting downstream consumers.
- :func:`wire_to_datom` — wire dict → Datom. Conforms the input through
  ``spec.parse(...)`` first; then coerces the int/str tx and datetime
  instants back into the dataclass slots.

Both functions run the spec check every call. This is the R3 F8 boundary
invocation — every time a Datom crosses this module, the registered spec
is exercised.

Scope note
----------

``wire_to_datom`` only accepts wire dicts whose ``:datom/e`` is a UUID (or
UUID-shaped string) and whose ``:datom/tx`` is an int. The spec also allows
content-hash strings for both (see the audit→fact boundary); those wire
dicts correspond to audit datoms that live in their own append-only log,
not the :class:`Datom` dataclass used by :class:`persistence.fact.db.DB`.
Converting them here would be a silent type corruption.
"""
from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any

from persistence import spec as _S
from persistence.fact.datom import Datom

__all__ = ["datom_to_wire", "wire_to_datom"]


#: Known provenance keys that round-trip with leading colons on the wire.
#:
#: ``:source`` is a keyword on the wire (not a bare string); ``:signature``,
#: ``:model``, ``:prompt-hash``, ``:confidence``, ``:episode`` are the spec's
#: optional provenance keys.
_PROVENANCE_KEYS = (
    "source",
    "model",
    "prompt-hash",
    "confidence",
    "signature",
    "episode",
)


def _provenance_to_wire(prov: dict[str, Any]) -> dict[str, Any]:
    """Prepend ``":"`` to each known provenance key; leave unknown keys alone.

    The ``:source`` slot specifically is the one that must become an EDN
    keyword (the spec's ``_keyword_spec``). A bare string ``"dfi-agent"``
    becomes ``":dfi-agent"``; already-keyworded values pass through.
    """
    out: dict[str, Any] = {}
    for k, v in prov.items():
        # Preserve already-keyworded keys verbatim.
        if isinstance(k, str) and k.startswith(":"):
            out[k] = v
            continue
        wire_key = ":" + k if k in _PROVENANCE_KEYS else k
        if wire_key == ":source" and isinstance(v, str) and not v.startswith(":"):
            v = ":" + v
        out[wire_key] = v
    return out


def _provenance_from_wire(prov: dict[str, Any]) -> dict[str, Any]:
    """Inverse of :func:`_provenance_to_wire`."""
    out: dict[str, Any] = {}
    for k, v in prov.items():
        bare_key = k[1:] if isinstance(k, str) and k.startswith(":") else k
        if bare_key == "source" and isinstance(v, str) and v.startswith(":"):
            v = v[1:]
        out[bare_key] = v
    return out


def datom_to_wire(datom: Datom) -> dict[str, Any]:
    """Convert a :class:`Datom` to the canonical wire shape.

    The returned dict is guaranteed to conform to ``:persistence.fact/datom``
    — the call raises :class:`persistence.spec.ConformError` (via
    :func:`persistence.spec.parse`) if the input Datom happens to violate
    the registered contract (e.g. naive datetime slipping through the
    dataclass __post_init__).
    """
    wire: dict[str, Any] = {
        ":datom/e": datom.e,
        ":datom/a": datom.a if datom.a.startswith(":") else ":" + datom.a,
        ":datom/v": datom.v,
        ":datom/tx": datom.tx,
        ":datom/tx-time": datom.tx_time,
        ":datom/valid-from": datom.valid_from,
        ":datom/valid-to": datom.valid_to,
        ":datom/op": ":" + datom.op,
        ":datom/provenance": _provenance_to_wire(datom.provenance),
        ":datom/invalidated-by": datom.invalidated_by,
    }
    # Boundary invocation — R3 F8. Raise if the spec is unhappy; better a
    # loud failure at the adapter than silent bad data downstream.
    _S.parse(":persistence.fact/datom", wire)
    return wire


def wire_to_datom(wire: dict[str, Any]) -> Datom:
    """Convert a wire dict to a :class:`Datom`.

    Conforms through ``spec.parse(":persistence.fact/datom", wire)`` first.
    The Datom dataclass has a narrower type set than the spec (``tx: int``,
    ``e: str`` holding a UUID) — wire dicts with content-hash tx or
    non-UUID-shaped e are rejected with :class:`TypeError` (see the
    module docstring).
    """
    _S.parse(":persistence.fact/datom", wire)

    tx = wire[":datom/tx"]
    if not isinstance(tx, int):
        raise TypeError(
            f":datom/tx must be an int to become a Datom; got {type(tx).__name__}. "
            "Wire dicts with content-hash tx belong to the audit log, not the "
            "Fact DB; convert via a domain-specific adapter."
        )

    e = wire[":datom/e"]
    if isinstance(e, _uuid.UUID):
        e_str = str(e)
    elif isinstance(e, str):
        e_str = e
    else:
        raise TypeError(
            f":datom/e must be a UUID or UUID-shaped str; got {type(e).__name__}"
        )

    a = wire[":datom/a"]
    if isinstance(a, str) and a.startswith(":"):
        a = a[1:]

    op_raw = wire[":datom/op"]
    op = op_raw[1:] if isinstance(op_raw, str) and op_raw.startswith(":") else op_raw

    tx_time = wire[":datom/tx-time"]
    valid_from = wire[":datom/valid-from"]
    valid_to = wire.get(":datom/valid-to")

    return Datom(
        e=e_str,
        a=a,
        v=wire[":datom/v"],
        tx=tx,
        tx_time=_coerce_datetime(tx_time),
        valid_from=_coerce_datetime(valid_from),
        valid_to=_coerce_datetime(valid_to) if valid_to is not None else None,
        op=op,
        provenance=_provenance_from_wire(wire.get(":datom/provenance", {})),
        invalidated_by=wire.get(":datom/invalidated-by"),
    )


def _coerce_datetime(value: Any) -> _dt.datetime:
    """Accept datetime or ISO-8601 string; reject naive datetimes."""
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            raise TypeError("datetime is naive; tz-aware required")
        return value
    if isinstance(value, str):
        parsed = _dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise TypeError("ISO-8601 string lacks tzinfo")
        return parsed
    raise TypeError(f"expected datetime or ISO-8601 str, got {type(value).__name__}")
