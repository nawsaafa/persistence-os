"""ARIS Round 4 W4-wire-identity — ``Datom`` round-trip identity on the
extended input domain (closes R3 N2).

Round 3 (R3) re-ran the reproduction and confirmed:

- ``Datom(a=":project/wacc")`` round-trips to ``a="project/wacc"`` — the
  leading colon is stripped unconditionally by ``wire_to_datom``.
- ``Datom(provenance={"source": ":already-keyworded"})`` round-trips with
  ``source="already-keyworded"``.

Both are identity losses on the Datom side: the in-memory form cannot
distinguish "caller gave us a bare-string" from "caller gave us a
pre-keyworded-string that happened to collide." The R3 reviewer called
this "the single biggest drag on the composability grade."

Fix strategy (option A per the round-3 R3 report and the W4 prompt):
normalise at the ``Datom.__post_init__`` boundary. The in-memory form is
always bare (no leading colon); the wire form always prepends. Any input
with a leading colon is canonicalised to bare form on construction, so
``Datom(a=":x/y")`` and ``Datom(a="x/y")`` produce the same Datom.
Round-trip becomes identity by construction.

Same for ``provenance["source"]`` — if the user passes ``":bare"`` we
strip on construction, so the wire layer emits ``:bare`` uniformly.
"""
from __future__ import annotations

import datetime as dt
import uuid

import pytest

from persistence.fact.datom import Datom
from persistence.fact.wire import datom_to_wire, wire_to_datom


def _sample(a: str = "project/wacc", source: str = "dfi-agent") -> Datom:
    """A canonical Datom with overridable ``a`` and ``provenance['source']``
    so tests can exercise the extended input domain.
    """
    now = dt.datetime(2026, 4, 21, 12, tzinfo=dt.timezone.utc)
    return Datom(
        e=str(uuid.uuid4()),
        a=a,
        v=0.087,
        tx=17332,
        tx_time=now,
        valid_from=now,
        valid_to=None,
        op="assert",
        provenance={"source": source, "confidence": 0.82},
        invalidated_by=None,
    )


# ---------------------------------------------------------------------------
# 1. Datom.__post_init__ normalises `a` — leading colon is stripped
# ---------------------------------------------------------------------------


class TestDatomANormalisation:
    """The in-memory canonical form of ``Datom.a`` is bare (no leading
    colon). Any input starting with ``:`` is stripped on construction —
    both ``Datom(a=":x/y")`` and ``Datom(a="x/y")`` yield ``a="x/y"``."""

    def test_datom_a_strips_leading_colon(self):
        """Before W4-wire-identity: ``Datom(a=":project/wacc").a`` would
        return ``:project/wacc`` (unchanged). After: returns ``project/wacc``
        — identical to ``Datom(a="project/wacc").a``.
        """
        colon = Datom(
            e=str(uuid.uuid4()),
            a=":project/wacc",
            v=1,
            tx=1,
            tx_time=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            valid_from=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            valid_to=None,
            op="assert",
        )
        bare = Datom(
            e=str(uuid.uuid4()),
            a="project/wacc",
            v=1,
            tx=1,
            tx_time=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            valid_from=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            valid_to=None,
            op="assert",
        )
        assert colon.a == bare.a == "project/wacc"

    def test_datom_a_idempotent_on_bare_input(self):
        """The normalisation must be idempotent on bare-string input."""
        d = Datom(
            e=str(uuid.uuid4()),
            a="project/wacc",
            v=1,
            tx=1,
            tx_time=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            valid_from=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            valid_to=None,
            op="assert",
        )
        assert d.a == "project/wacc"


# ---------------------------------------------------------------------------
# 2. Datom.__post_init__ normalises provenance["source"]
# ---------------------------------------------------------------------------


class TestDatomProvenanceSourceNormalisation:
    """Same canonical-form rule applies to the ``provenance["source"]``
    value: bare on the Datom side, keywordified on the wire.
    """

    def test_provenance_source_strips_leading_colon(self):
        colon = _sample(source=":already-keyworded")
        bare = _sample(source="already-keyworded")
        assert colon.provenance["source"] == bare.provenance["source"] == (
            "already-keyworded"
        )

    def test_provenance_source_idempotent_on_bare_input(self):
        d = _sample(source="dfi-agent")
        assert d.provenance["source"] == "dfi-agent"

    def test_non_source_provenance_keys_unchanged(self):
        """Only ``source`` is normalised; other keys pass through."""
        now = dt.datetime(2026, 4, 21, 12, tzinfo=dt.timezone.utc)
        d = Datom(
            e=str(uuid.uuid4()),
            a="x/y",
            v=1,
            tx=1,
            tx_time=now,
            valid_from=now,
            valid_to=None,
            op="assert",
            provenance={"source": "agent", "model": ":llm/call", "confidence": 0.5},
        )
        # "model" retains its value unchanged (only `source` is the narrow
        # normalisation target).
        assert d.provenance["model"] == ":llm/call"
        assert d.provenance["confidence"] == 0.5


# ---------------------------------------------------------------------------
# 3. wire_to_datom ∘ datom_to_wire is identity on pre-keyworded inputs
# ---------------------------------------------------------------------------


class TestWireRoundTripIdentity:
    """Before W4-wire-identity, the composition ``wire_to_datom ∘ datom_to_wire``
    was not the identity on the extended input domain. After the fix,
    normalisation at ``Datom.__post_init__`` means the in-memory form is
    already canonical; the adapter-pair round-trip becomes identity on
    every input that constructs a Datom.
    """

    def test_round_trip_identity_on_prekeyworded_a(self):
        """This test WILL FAIL on main before W4-wire-identity:
        ``Datom(a=":x/y")`` round-tripped to ``Datom(a="x/y")``.
        """
        original = _sample(a=":project/wacc")
        restored = wire_to_datom(datom_to_wire(original))
        assert restored.a == original.a
        assert restored.a == "project/wacc"

    def test_round_trip_identity_on_prekeyworded_source(self):
        """``provenance["source"]`` round-trip identity on pre-keyworded
        input."""
        original = _sample(source=":already-keyworded")
        restored = wire_to_datom(datom_to_wire(original))
        assert restored.provenance["source"] == original.provenance["source"]
        assert restored.provenance["source"] == "already-keyworded"

    def test_round_trip_identity_on_both_prekeyworded(self):
        original = _sample(a=":project/wacc", source=":dfi-agent")
        restored = wire_to_datom(datom_to_wire(original))
        assert restored.a == original.a == "project/wacc"
        assert restored.provenance["source"] == original.provenance["source"] == (
            "dfi-agent"
        )
        assert restored.provenance["confidence"] == original.provenance["confidence"]

    def test_wire_always_emits_keyworded_form(self):
        """Regardless of how the Datom was constructed (colon or bare),
        the wire form uniformly emits ``":project/wacc"`` and ``":dfi-agent"``.
        """
        for a_input in ("project/wacc", ":project/wacc"):
            for source_input in ("dfi-agent", ":dfi-agent"):
                d = _sample(a=a_input, source=source_input)
                wire = datom_to_wire(d)
                assert wire[":datom/a"] == ":project/wacc"
                assert wire[":datom/provenance"][":source"] == ":dfi-agent"
