"""Canonical Datom codec — single source of truth for backend codecs.

PG2 (Phase 1 stream #165) consolidates the row-encode / row-decode logic
that PG1 had locally redefined inside ``store/postgres.py``. The decision
captured by ADR-16 (see design doc §13a) is that **codec consolidation
preserves the existing storage-layer divergence** between the SQLite and
Postgres backends:

- SQLite stores datetimes as ISO-8601 ``TEXT`` (``.isoformat()`` round-
  trip via ``datetime.fromisoformat``).
- Postgres stores datetimes as native ``TIMESTAMPTZ``, passing
  ``datetime`` objects straight through psycopg 3's adapter (no
  ``.isoformat()`` round-trip — psycopg owns that boundary).

Both wire formats decode back to ``datetime`` objects that compare equal
under ``==`` (modulo any tz / microsecond fidelity the underlying driver
honours). The byte-identity invariant exercised by the PG2 Hypothesis
property is *Datom-level* (post-decode) equality, NOT raw row-tuple
equality at the encoded layer — that pins the right invariant for
downstream replay byte-identity (which walks decoded ``Datom`` objects
out of ``db.log()`` and never touches the raw row form).

The shared bits across both backends — canonical JSON for ``v`` /
``provenance`` (``json.dumps(..., sort_keys=True, default=str)``), the
``TX_PLACEHOLDER`` rewrite logic in ``_with_tx``, the column ordering of
the encoded tuple — live here as the canonical implementation. The
backend-specific bit — whether datetimes go out as ISO strings or as
native objects — is the only knob.

The two concrete codecs in this module are:

- :class:`TextDatomCodec` — used by :class:`~persistence.fact.SQLiteStore`
  and (for completeness) by :class:`~persistence.fact.InMemoryStore`'s
  optional round-trip path. Datetime fields encode to ISO strings;
  decode parses ISO strings back via ``datetime.fromisoformat``.
- :class:`NativeDatomCodec` — used by
  :class:`~persistence.store.postgres.PostgresStore`. Datetime fields
  pass through unchanged on encode and are returned as ``datetime``
  objects by psycopg's TIMESTAMPTZ adapter on decode.

Both share the same :func:`with_tx` placeholder-rewrite implementation
exported as a module-level helper so tests can pin the rewrite logic
without instantiating a codec.

History
-------

PG1 (`d911270`) shipped ``_encode`` / ``_decode_tuple`` / ``_with_tx``
locally redefined inside ``postgres.py`` to keep the module importable
without ``sqlite3`` in the dependency closure. This module breaks that
duplication: the codec lives here, the backends import the right concrete
class, and ``persistence.fact.store`` does NOT import ``persistence.store``
(so SQLite still loads without the ``[postgres]`` extra). See ADR-16 in
the design doc for the full decision record.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from persistence.fact.datom import Datom


# Sentinel value pinned in :data:`persistence.fact.store.TX_PLACEHOLDER`.
# Re-exported here so the codec module is the single source of truth for
# the placeholder convention; ``fact.store`` aliases this back as the
# public name.
TX_PLACEHOLDER: int = -1


# ---------------------------------------------------------------------------
# Canonical helpers
# ---------------------------------------------------------------------------
def canonical_json(value: Any) -> str:
    """Render ``value`` as canonical JSON.

    Canonical JSON for the substrate is ``json.dumps(value, default=str,
    sort_keys=True)``. The ``default=str`` fallback handles a few values
    that JSON does not serialise out of the box (e.g. ``Decimal``,
    ``datetime`` inside ``provenance`` / ``v``); tests historically rely
    on this behaviour. ``sort_keys=True`` is the byte-identity gate —
    two semantically-equal dicts produce identical bytes regardless of
    insertion order.
    """
    return json.dumps(value, default=str, sort_keys=True)


def with_tx(d: Datom, tx: int) -> Datom:
    """Return a copy of ``d`` with ``tx`` replaced — frozen-Datom safe.

    Also rewrites any ``provenance`` value equal to :data:`TX_PLACEHOLDER`
    to the real ``tx``; this carries forward the
    ``{"superseded_by_tx": <tx>}`` reference that ``DB.transact`` sets on
    companion retract datoms before the real tx is known. Identical
    semantics to the previous ``persistence.fact.store._with_tx`` and
    ``persistence.store.postgres._with_tx`` — the two have been merged
    into this single canonical implementation.
    """
    prov = d.provenance
    if prov and any(v == TX_PLACEHOLDER for v in prov.values()):
        prov = {k: (tx if v == TX_PLACEHOLDER else v) for k, v in prov.items()}
    return Datom(
        e=d.e,
        a=d.a,
        v=d.v,
        tx=tx,
        tx_time=d.tx_time,
        valid_from=d.valid_from,
        valid_to=d.valid_to,
        op=d.op,
        provenance=prov,  # type: ignore[arg-type]  # D2 ripple — see fact/db.py:251
        invalidated_by=d.invalidated_by,
    )


# ---------------------------------------------------------------------------
# DatomCodec — abstract surface
# ---------------------------------------------------------------------------
class DatomCodec(ABC):
    """Abstract codec for one row's worth of Datom <-> backend-tuple bytes.

    The codec is a 1:1 wrapper around a single :class:`Datom`; backends
    iterate per-row and feed the resulting tuples to ``executemany``. The
    column ordering produced by :meth:`encode` is fixed by the schema:

        ``(e, a, v, tx, tx_time, valid_from, valid_to, op, provenance,
        invalidated_by)``

    Both the SQLite and Postgres ``INSERT`` statements rely on this order
    being stable, so the abstract surface pins it. Subclasses choose how
    to render datetime values (ISO ``TEXT`` vs native ``TIMESTAMPTZ``).

    The abstract :meth:`decode` accepts a row in whatever shape the
    backend returns — a SQLite ``Row`` (mapping by column name), or a
    psycopg ``tuple`` (positional). Concrete subclasses unpack
    accordingly.
    """

    @abstractmethod
    def encode(self, d: Datom) -> tuple:
        """Render ``d`` as a 10-tuple for INSERT (column order pinned)."""

    @abstractmethod
    def decode(self, row: Any) -> Datom:
        """Parse ``row`` from the backend's read shape back into a Datom."""

    # Convenience pass-through so callers can use a codec wherever
    # ``with_tx`` was previously imported from ``fact.store``.
    @staticmethod
    def with_tx(d: Datom, tx: int) -> Datom:
        return with_tx(d, tx)


# ---------------------------------------------------------------------------
# TextDatomCodec — SQLite + InMemory wire (datetimes as ISO TEXT)
# ---------------------------------------------------------------------------
class TextDatomCodec(DatomCodec):
    """Codec used by SQLiteStore (and by InMemoryStore's optional round-trip).

    Datetime fields are rendered with :py:meth:`datetime.isoformat` and
    parsed with :py:meth:`datetime.fromisoformat`. ``v`` and
    ``provenance`` are :func:`canonical_json` (sorted keys). This is the
    pre-PG2 SQLite shape preserved verbatim — all existing rows in
    SQLite databases continue to round-trip identically.

    The :meth:`decode` row argument can be either a ``sqlite3.Row``
    (mapping access by column name, used by SQLiteStore's read paths) or
    a positional 10-tuple (used by tests + future backends that share the
    text wire shape). Both shapes are accepted; we feature-detect via
    ``hasattr(row, "keys")`` rather than ``isinstance`` so the codec
    stays loose-coupled to ``sqlite3``.
    """

    def encode(self, d: Datom) -> tuple:
        return (
            d.e,
            d.a,
            canonical_json(d.v),
            d.tx,
            d.tx_time.isoformat(),
            d.valid_from.isoformat(),
            d.valid_to.isoformat() if d.valid_to else None,
            d.op,
            canonical_json(d.provenance),
            d.invalidated_by,
        )

    def decode(self, row: Any) -> Datom:
        # SQLite's row_factory=Row gives mapping-style access; positional
        # tuples (used by tests) need index access. Feature-detect via
        # ``keys`` to keep this codec loose-coupled to sqlite3.
        if hasattr(row, "keys"):
            e = row["e"]
            a = row["a"]
            v = row["v"]
            tx = row["tx"]
            tx_time = row["tx_time"]
            valid_from = row["valid_from"]
            valid_to = row["valid_to"]
            op = row["op"]
            provenance = row["provenance"]
            invalidated_by = row["invalidated_by"]
        else:
            (
                e,
                a,
                v,
                tx,
                tx_time,
                valid_from,
                valid_to,
                op,
                provenance,
                invalidated_by,
            ) = row
        return Datom(
            e=e,
            a=a,
            v=json.loads(v),
            tx=tx,
            tx_time=datetime.fromisoformat(tx_time),
            valid_from=datetime.fromisoformat(valid_from),
            valid_to=datetime.fromisoformat(valid_to) if valid_to else None,
            op=op,
            provenance=json.loads(provenance),
            invalidated_by=invalidated_by,
        )


# ---------------------------------------------------------------------------
# NativeDatomCodec — Postgres wire (datetimes as TIMESTAMPTZ)
# ---------------------------------------------------------------------------
class NativeDatomCodec(DatomCodec):
    """Codec used by PostgresStore.

    Datetime fields are passed through to psycopg 3's TIMESTAMPTZ adapter
    on encode; psycopg returns ``datetime`` objects on decode (no ISO
    round-trip in either direction). ``v`` and ``provenance`` are
    canonical JSON (sorted keys) — same TEXT-column shape as SQLite per
    ADR-5 W1-revised.

    Why no ISO round-trip on Postgres
    ---------------------------------

    Forcing ISO strings on a TIMESTAMPTZ column requires either:

    1. Storing datetimes as TEXT — drops the operator-side temporal
       index efficiency that ADR-8 needs (``idx_datom_log_txtime`` on
       ``(tx_time, tx)``), and forces every ``since(t)`` comparator to
       cast strings to timestamps at query time, OR
    2. Calling ``.isoformat()`` before INSERT and ``datetime.fromisoformat()``
       after SELECT — adds two extra round-trips per row for zero
       semantic benefit, and risks losing tzinfo precision on
       edge-of-second microseconds when psycopg's adapter would have
       handled it natively.

    Neither buys anything: the Datom-level invariant (``Datom.tx_time
    == decoded.tx_time``) is preserved by psycopg's adapter without our
    intervention, and the operator-side temporal-index efficiency is
    preserved. ADR-16 pins this trade-off.

    The :meth:`decode` row argument is a positional 10-tuple from
    psycopg's default row factory.
    """

    def encode(self, d: Datom) -> tuple:
        return (
            d.e,
            d.a,
            canonical_json(d.v),
            d.tx,
            d.tx_time,
            d.valid_from,
            d.valid_to,
            d.op,
            canonical_json(d.provenance),
            d.invalidated_by,
        )

    def decode(self, row: Any) -> Datom:
        (
            e,
            a,
            v,
            tx,
            tx_time,
            valid_from,
            valid_to,
            op,
            provenance,
            invalidated_by,
        ) = row
        return Datom(
            e=e,
            a=a,
            v=json.loads(v),
            tx=tx,
            tx_time=tx_time,
            valid_from=valid_from,
            valid_to=valid_to,
            op=op,
            provenance=json.loads(provenance),
            invalidated_by=invalidated_by,
        )


__all__ = [
    "DatomCodec",
    "NativeDatomCodec",
    "TX_PLACEHOLDER",
    "TextDatomCodec",
    "canonical_json",
    "with_tx",
]
