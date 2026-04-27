"""Hypothesis @given byte-identity property — closes v0.5.1 N4 (rev O §7.2).

The deterministic two-run test in test_substrate_composition.py is the fast
smoke. This property test is the heavyweight backstop: 200 randomly-generated
transaction lists, two runs each, structural log equality.
"""
from datetime import datetime, timezone

import hypothesis.strategies as st
from hypothesis import given, settings, HealthCheck

from persistence.fact.db import DB


# Strategy: ascii-letters-and-digits eids, 1-12 chars, never empty.
ref_eid_strategy = st.text(
    alphabet=st.characters(whitelist_categories=["Ll", "Lu", "Nd"]),
    min_size=1, max_size=12,
)

# Strategy: scalar values OR small recursive containers (pyrsistent-friendly).
# Top-level values must be immutable per is_immutable_value, so we coerce
# lists/dicts via freeze() inside the body.
value_strategy = st.recursive(
    st.integers(min_value=-1000, max_value=1000)
    | st.text(min_size=0, max_size=10)
    | st.booleans(),
    lambda children: st.lists(children, max_size=4),
    max_leaves=4,
)


def _run_one(transactions: list[dict], fixed_t: datetime) -> list[tuple]:
    """Apply `transactions` against a fresh DB with frozen clock; return
    the structurally-comparable user-write log (commit datoms filtered).
    """
    from persistence.txn import freeze

    clock = lambda: fixed_t
    db = DB(clock=clock)
    for txn in transactions:
        eid = txn["ref_eid"]
        value = freeze(txn["value"])  # coerces lists -> PVector

        @db.dosync
        def body(tx, eid=eid, value=value):
            tx.assoc(db.ref(eid), value)
        body()
    return [
        (d.e, d.a, d.v, d.op, d.valid_from)
        for d in db.log()
        if not d.a.startswith("persistence.txn/")
    ]


@given(
    transactions=st.lists(
        st.fixed_dictionaries({
            "ref_eid": ref_eid_strategy,
            "value": value_strategy,
        }),
        min_size=1, max_size=10,
    ),
)
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_replay_byte_identity_property(transactions):
    """Two runs of the same transaction list against fresh frozen-clock
    DBs produce identical user-write logs (structural comparison;
    commit datoms excluded — they carry per-run UUIDs).
    """
    fixed_t = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    log1 = _run_one(transactions, fixed_t)
    log2 = _run_one(transactions, fixed_t)
    assert log1 == log2
