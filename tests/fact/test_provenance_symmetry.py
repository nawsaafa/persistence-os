"""ARIS Round 5 W5-provenance-symmetry — ``_provenance_to_wire``
keywordifies ``:source`` values regardless of whether the key is
pre-keyworded (closes R3 R4-N4 LOW).

The Round 4 bug:

```python
def _provenance_to_wire(prov):
    for k, v in prov.items():
        if isinstance(k, str) and k.startswith(":"):
            out[k] = v  # <- early return skips value-keywordification
            continue
        wire_key = ":" + k if k in _PROVENANCE_KEYS else k
        if wire_key == ":source" and isinstance(v, str) and not v.startswith(":"):
            v = ":" + v
        out[wire_key] = v
```

When the input was ``{":source": "bare-value"}`` (pre-keyworded key,
bare value), the ``continue`` short-circuited before the value got
its colon — the wire emitted ``{":source": "bare-value"}`` which
fails self-conform because ``:source`` requires a keyword value.

Self-conform does catch the malformed output (loud, not silent), but
the asymmetry means ``_provenance_to_wire`` is not its own inverse on
the pre-keyworded-key + bare-value subdomain. Closes R3 R4-N4.

**Fix:** drop the ``continue`` — always run the value-keywordification
branch, regardless of key form.
"""
from __future__ import annotations

import datetime as dt
import uuid

import pytest

from persistence.fact.datom import Datom
from persistence.fact.wire import (
    _provenance_to_wire,
    datom_to_wire,
    wire_to_datom,
)


class TestProvenanceToWireValueKeywordification:
    """``_provenance_to_wire`` must keywordify the ``:source`` value
    regardless of whether the key is bare or pre-keyworded.
    """

    def test_bare_key_bare_value_keywordifies_both(self):
        """Existing contract — bare key + bare value → keyworded key + keyworded value."""
        out = _provenance_to_wire({"source": "dfi-agent"})
        assert out == {":source": ":dfi-agent"}

    def test_bare_key_keyworded_value_preserves_value(self):
        """Already-keyworded value passes through unchanged."""
        out = _provenance_to_wire({"source": ":dfi-agent"})
        assert out == {":source": ":dfi-agent"}

    def test_keyworded_key_bare_value_keywordifies_value(self):
        """R4-N4 reproducer: the pre-keyworded key must NOT short-circuit
        the value-keywordification branch. This test WILL FAIL on main
        before W5-provenance-symmetry.
        """
        out = _provenance_to_wire({":source": "bare-value"})
        assert out == {":source": ":bare-value"}

    def test_keyworded_key_keyworded_value_unchanged(self):
        """Idempotent on fully-canonicalised input."""
        out = _provenance_to_wire({":source": ":bare-value"})
        assert out == {":source": ":bare-value"}


class TestProvenanceRoundTripSymmetric:
    """``wire_to_datom ∘ datom_to_wire`` must round-trip symmetrically
    even when the input Datom's provenance has a pre-keyworded
    ``:source`` key with a bare value (an unusual but possible state,
    e.g. from a buggy upstream that half-keywordified).
    """

    def test_datom_with_prekeyworded_key_bare_value_roundtrips(self):
        """Before W5: datom_to_wire raised at self-conform because the
        bare-value snuck past the keywordification branch. After W5:
        wire emission is uniformly keyworded, self-conform passes,
        round-trip is identity."""
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
            # provenance has :source (pre-keyworded key) + "dfi" (bare
            # value). __post_init__ doesn't touch this because it
            # looks for "source" (bare), not ":source" (keyworded).
            provenance={":source": "dfi"},
            invalidated_by=None,
        )
        wire = datom_to_wire(d)
        # The wire form must have :source as an EDN keyword.
        assert wire[":datom/provenance"][":source"] == ":dfi"
        # Round-trip symmetrically.
        restored = wire_to_datom(wire)
        # After wire_to_datom, the provenance key is stripped back to
        # "source" (bare) and the value to "dfi" (bare).
        assert restored.provenance.get("source") == "dfi"
