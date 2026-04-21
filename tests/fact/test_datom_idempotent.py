"""ARIS Round 5 W5-datom-idempotent — ``Datom.__post_init__``
canonicalisation is idempotent on double-colon inputs (closes
R3 R4-N3 LOW).

Round 4 W-wire used ``a[1:]`` to strip the leading colon from
``Datom.a`` and ``provenance["source"]``. That works on ``":x"`` but
fails on ``"::x"`` — the double-colon input collapses to ``":x"``
(still has a colon), so construction isn't idempotent on the
extended input domain.

The fix is a one-liner: ``lstrip(":")`` instead of ``[1:]``.
Same change applies to ``provenance["source"]``.
"""
from __future__ import annotations

import datetime as dt
import uuid

from persistence.fact.datom import Datom


def _base(**overrides) -> dict:
    now = dt.datetime(2026, 4, 21, 12, tzinfo=dt.timezone.utc)
    base = dict(
        e=str(uuid.uuid4()),
        a="project/wacc",
        v=1,
        tx=1,
        tx_time=now,
        valid_from=now,
        valid_to=None,
        op="assert",
        provenance={},
        invalidated_by=None,
    )
    base.update(overrides)
    return base


class TestDatomAIdempotentOnDoubleColon:
    """``Datom(a="::x/y")`` must collapse to ``"x/y"`` — the same as
    ``Datom(a="x/y")`` and ``Datom(a=":x/y")``.
    """

    def test_double_colon_collapses_fully(self):
        """Before W5-datom-idempotent: ``Datom(a="::x/y").a`` was
        ``":x/y"`` (only first colon stripped). After: ``"x/y"``."""
        d_double = Datom(**_base(a="::x/y"))
        d_single = Datom(**_base(a=":x/y"))
        d_bare = Datom(**_base(a="x/y"))
        assert d_double.a == d_single.a == d_bare.a == "x/y"

    def test_triple_colon_collapses_fully(self):
        """Edge-of-edge: ``lstrip`` handles arbitrary repetition."""
        d = Datom(**_base(a=":::x/y"))
        assert d.a == "x/y"


class TestProvenanceSourceIdempotentOnDoubleColon:
    """``provenance["source"]`` must canonicalise double-colon inputs
    to bare form — matches ``a`` behaviour.
    """

    def test_provenance_source_double_colon_collapses_fully(self):
        d = Datom(**_base(provenance={"source": "::bare"}))
        assert d.provenance["source"] == "bare"

    def test_provenance_source_triple_colon_collapses_fully(self):
        d = Datom(**_base(provenance={"source": ":::bare"}))
        assert d.provenance["source"] == "bare"
