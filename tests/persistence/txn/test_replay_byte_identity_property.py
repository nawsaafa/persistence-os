"""Hypothesis @given byte-identity property — closes v0.5.1 N4 (rev O §7.2)
and the v0.5.2 N6 carry-forward (extends to ``tx.alter``).

The deterministic two-run test in test_substrate_composition.py is the fast
smoke. This property test is the heavyweight backstop: 200 randomly-generated
transaction lists, two runs each, structural log equality.

v0.5.2 N6: the strategy now draws op tuples of two shapes —
``("assoc", eid, value)`` and ``("alter", eid, fn_id)`` — and ``_run_one``
dispatches on ``op[0]``. Per § N6, the ``_alter_fn_id_st`` strategy draws
STRING IDS from a curated module-level table, never closures: this sidesteps
the "Hypothesis can't shrink across closure boundaries" failure mode.
"""
from datetime import datetime, timezone

import hypothesis.strategies as st
from hypothesis import given, settings, HealthCheck

from persistence.fact.db import DB


# Strategy: ascii-letters-and-digits eids, 1-12 chars, never empty.
# v0.5.1 W1 fix-pass — R2 NIT 3: ``whitelist_categories=`` is the
# pre-Hypothesis-6.x spelling (deprecated since 6.0); current spelling
# is ``categories=``. The behavior is identical (positive list of
# Unicode general-category codes); the rename silences the
# DeprecationWarning that would otherwise fire on every property run.
_eid_alphabet = st.characters(categories=["Ll", "Lu", "Nd"])


@st.composite
def _eid_st(draw):
    return draw(st.text(alphabet=_eid_alphabet, min_size=1, max_size=12))


# Strategy: scalar values OR small recursive containers (pyrsistent-friendly).
# Top-level values must be immutable per is_immutable_value, so we coerce
# lists/dicts via freeze() inside the body.
@st.composite
def _immutable_value_st(draw):
    return draw(
        st.recursive(
            st.integers(min_value=-1000, max_value=1000)
            | st.text(min_size=0, max_size=10)
            | st.booleans(),
            lambda children: st.lists(children, max_size=4),
            max_leaves=4,
        )
    )


# Curated table of pure module-level functions for tx.alter strategy.
# Per § N6 design rationale: the strategy draws STRING IDS, never closures —
# sidesteps Hypothesis shrink-across-closure-boundary failure modes. The
# byte-identity invariant is over the filtered structural projection of the
# user-write log; the fn runs inside the body, sees the same snapshot, and
# produces the same value across both runs. Determinism on snapshot+input
# is all that's required.
#
# Robustness note: `int_inc` and `int_double` may be drawn against eids that
# a previous assoc wrote a string/bool/list to. They coerce ``v is None`` to
# 0, but if v is e.g. a string, ``"foo" + 1`` raises TypeError. We handle
# this in `_run_one` via try/except (option (b) in the task spec) — both
# runs fail identically, so the byte-identity property still holds (the log
# truncates at the same point in both runs).
_ALTER_FNS = {
    "int_inc": lambda v: (v if v is not None else 0) + 1,
    "int_double": lambda v: (v if v is not None else 0) * 2,
    "to_default_zero": lambda v: 0 if v is None else v,
    "identity": lambda v: v,
}


@st.composite
def _alter_fn_id_st(draw):
    # sorted() pins iteration order so Hypothesis shrink is stable.
    return draw(st.sampled_from(sorted(_ALTER_FNS.keys())))


@st.composite
def _assoc_op(draw):
    return ("assoc", draw(_eid_st()), draw(_immutable_value_st()))


@st.composite
def _alter_op(draw):
    return ("alter", draw(_eid_st()), draw(_alter_fn_id_st()))


_op_strategy = st.one_of(_assoc_op(), _alter_op())


def _run_one(ops: list[tuple], fixed_t: datetime) -> list[tuple]:
    """Apply ``ops`` against a fresh DB with frozen clock; return
    the structurally-comparable user-write log (commit datoms filtered).

    Per § N6 byte-identity scope: commit datoms carry per-run UUIDs and are
    filtered out via ``not d.a.startswith("persistence.txn/")``. The clock
    is frozen via ``clock=lambda: fixed_t`` so user-write ``valid_from`` IS
    deterministic across runs.

    Cross-type alter handling (option (b) per task spec): if a previous
    assoc wrote a non-int value and the strategy then draws ``int_inc`` /
    ``int_double``, the lambda raises TypeError inside the body. We let
    that exception abort the per-op dosync, which leaves the DB log
    unchanged at that step. Because both runs draw the same op sequence
    against fresh frozen-clock DBs, both runs hit the same TypeError at the
    same step — the projected logs remain byte-identical.
    """
    from persistence.txn import freeze

    clock = lambda: fixed_t
    db = DB(clock=clock)
    for op in ops:
        kind = op[0]
        if kind == "assoc":
            _, eid, value = op
            value = freeze(value)  # coerces lists -> PVector

            @db.dosync
            def body(tx, eid=eid, value=value):
                tx.assoc(db.ref(eid), value)
            try:
                body()
            except TypeError:
                # Cross-type write rejected by an immutable-value check, etc.
                # Both runs fail identically → log stays in lockstep.
                pass
        elif kind == "alter":
            _, eid, fn_id = op
            fn = _ALTER_FNS[fn_id]

            @db.dosync
            def body(tx, eid=eid, fn=fn):
                tx.alter(db.ref(eid), fn)
            try:
                body()
            except TypeError:
                # e.g. int_inc applied to a previously-assoc'd string.
                # Both runs raise the same TypeError on the same op → log
                # is unchanged for both → byte-identity preserved.
                pass
        else:
            raise AssertionError(f"unknown op kind: {kind!r}")
    return [
        (d.e, d.a, d.v, d.op, d.valid_from)
        for d in db.log()
        if not d.a.startswith("persistence.txn/")
    ]


@given(
    ops=st.lists(_op_strategy, min_size=1, max_size=10),
)
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_replay_byte_identity_assoc_alter(ops):
    """Two runs of the same op list against fresh frozen-clock DBs produce
    identical user-write logs (structural comparison; commit datoms
    excluded — they carry per-run UUIDs).

    v0.5.2 N6: op list now interleaves ``tx.assoc`` and ``tx.alter`` draws.
    """
    fixed_t = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    log1 = _run_one(ops, fixed_t)
    log2 = _run_one(ops, fixed_t)
    assert log1 == log2
