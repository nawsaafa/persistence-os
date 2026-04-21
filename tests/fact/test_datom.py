"""Tests for the 8-tuple Datom dataclass.

Per agent1-fact-spec §1 and paper §4.1, a datom is the 8-tuple:
    ⟨e, a, v, τ, τ_sys, ν_from, ν_to, ω⟩
with a provenance record π and an optional `invalidated_by` pointer.
"""

from datetime import datetime, timezone

import pytest

from persistence.fact import Datom


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


class TestDatomShape:
    def test_has_all_eight_fields_plus_provenance_and_invalidated_by(self):
        d = Datom(
            e="p-042",
            a="project/wacc",
            v=0.087,
            tx=1,
            tx_time=_dt(2026, 4, 14),
            valid_from=_dt(2026, 4, 14),
            valid_to=None,
            op="assert",
            provenance={"source": "dfi-agent", "confidence": 0.82},
        )
        assert d.e == "p-042"
        assert d.a == "project/wacc"
        assert d.v == 0.087
        assert d.tx == 1
        assert d.tx_time == _dt(2026, 4, 14)
        assert d.valid_from == _dt(2026, 4, 14)
        assert d.valid_to is None
        assert d.op == "assert"
        assert d.provenance == {"source": "dfi-agent", "confidence": 0.82}
        assert d.invalidated_by is None

    def test_datom_is_frozen(self):
        d = Datom(
            e="e",
            a="a",
            v=1,
            tx=1,
            tx_time=_dt(2026, 1, 1),
            valid_from=_dt(2026, 1, 1),
            valid_to=None,
            op="assert",
            provenance={},
        )
        with pytest.raises(Exception):  # FrozenInstanceError is a dataclass-specific error
            d.v = 2  # type: ignore[misc]

    def test_op_must_be_assert_or_retract(self):
        with pytest.raises(ValueError):
            Datom(
                e="e",
                a="a",
                v=1,
                tx=1,
                tx_time=_dt(2026, 1, 1),
                valid_from=_dt(2026, 1, 1),
                valid_to=None,
                op="maybe",  # invalid
                provenance={},
            )

    def test_tx_times_must_be_tz_aware(self):
        with pytest.raises(ValueError):
            Datom(
                e="e",
                a="a",
                v=1,
                tx=1,
                tx_time=datetime(2026, 1, 1),  # naive
                valid_from=_dt(2026, 1, 1),
                valid_to=None,
                op="assert",
                provenance={},
            )
