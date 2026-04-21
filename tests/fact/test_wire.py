"""Datom ↔ wire-form adapter — ARIS R3 F1.

The canonical ``:persistence.fact/datom`` spec is the wire form: EDN-keyword
keys, tz-aware datetimes, and the Python ``Datom`` dataclass is the
in-memory form. :mod:`persistence.fact.wire` is the single round-trip
boundary: both converters conform through
``spec.parse(":persistence.fact/datom", ...)`` so every adapter call
exercises the spec.
"""
from __future__ import annotations

import datetime as dt
import uuid

import pytest

from persistence import spec as S
from persistence.fact.datom import Datom
from persistence.fact.wire import datom_to_wire, wire_to_datom


def _sample_datom() -> Datom:
    now = dt.datetime(2026, 4, 21, 12, tzinfo=dt.timezone.utc)
    return Datom(
        e=str(uuid.uuid4()),
        a="project/wacc",  # in-memory form is bare (no leading colon); wire prepends
        v=0.087,
        tx=17332,
        tx_time=now,
        valid_from=now,
        valid_to=None,
        op="assert",
        provenance={"source": "dfi-agent", "confidence": 0.82},
        invalidated_by=None,
    )


class TestDatomToWire:
    def test_wire_conforms_to_fact_spec(self):
        d = _sample_datom()
        wire = datom_to_wire(d)
        result = S.conform(":persistence.fact/datom", wire)
        assert result.is_ok, S.explain_for_llm(":persistence.fact/datom", wire)

    def test_wire_uses_leading_colon_keys(self):
        d = _sample_datom()
        wire = datom_to_wire(d)
        assert ":datom/e" in wire
        assert ":datom/a" in wire
        assert ":datom/tx" in wire
        assert ":datom/tx-time" in wire
        # No bare (no-colon) duplicate keys leak through.
        assert "datom/e" not in wire
        assert "e" not in wire

    def test_op_is_keyworded(self):
        d = _sample_datom()
        wire = datom_to_wire(d)
        assert wire[":datom/op"] == ":assert"

    def test_retract_op_keyworded(self):
        d = _sample_datom()
        d = Datom(
            e=d.e, a=d.a, v=d.v, tx=d.tx,
            tx_time=d.tx_time, valid_from=d.valid_from, valid_to=d.valid_from,
            op="retract",
            provenance=d.provenance,
        )
        wire = datom_to_wire(d)
        assert wire[":datom/op"] == ":retract"

    def test_provenance_keys_prefixed(self):
        d = _sample_datom()
        wire = datom_to_wire(d)
        prov = wire[":datom/provenance"]
        # known provenance keys carry leading colons
        assert ":source" in prov
        assert ":confidence" in prov
        # and no bare duplicates sneak through
        assert "source" not in prov


class TestWireToDatom:
    def test_roundtrip_preserves_fields(self):
        original = _sample_datom()
        restored = wire_to_datom(datom_to_wire(original))
        assert restored.e == original.e
        assert restored.a == original.a
        assert restored.v == original.v
        assert restored.tx == original.tx
        assert restored.tx_time == original.tx_time
        assert restored.valid_from == original.valid_from
        assert restored.valid_to == original.valid_to
        assert restored.op == original.op

    def test_rejects_non_conforming_wire(self):
        # Missing required key :datom/e.
        bad = datom_to_wire(_sample_datom())
        del bad[":datom/e"]
        with pytest.raises(Exception):
            wire_to_datom(bad)

    def test_rejects_content_hash_tx(self):
        """Datom dataclass requires ``tx: int``. The spec now accepts a
        content-hash tx too (for audit-emitted wire) but wire_to_datom is
        the native-Datom path — content-hash-tx wire cannot be a Datom and
        must be rejected explicitly rather than silently coerced.
        """
        wire = datom_to_wire(_sample_datom())
        wire[":datom/tx"] = "sha256:" + "a" * 64
        with pytest.raises(TypeError, match="int"):
            wire_to_datom(wire)
