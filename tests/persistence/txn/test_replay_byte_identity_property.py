"""Hypothesis @given byte-identity property — closes v0.5.1 N4 (rev O §7.2),
the v0.5.2 N6 carry-forward (extends to ``tx.alter``), and the v0.5.2 N7
carry-forward (extends to ``tx.effect`` + audit-chain projection).

The deterministic two-run test in test_substrate_composition.py is the fast
smoke. This property test is the heavyweight backstop: 200 randomly-generated
transaction lists, two runs each, structural log + audit-chain equality.

v0.5.2 N6: the strategy now draws op tuples of two shapes —
``("assoc", eid, value)`` and ``("alter", eid, fn_id)`` — and ``_run_one``
dispatches on ``op[0]``. Per § N6, the ``_alter_fn_id_st`` strategy draws
STRING IDS from a curated module-level table, never closures: this sidesteps
the "Hypothesis can't shrink across closure boundaries" failure mode.

v0.5.2 N7: a third op shape ``("effect", op, kwargs)`` — kwargs drawn from
EDN-conformant primitives only (alpha-only keys; str/int/bool/None values),
which the ``_build_commit_provenance`` intent-log spec accepts cleanly
(never tickles the v0.5.1 N1 SpecError path). The byte-identity assertion
is widened to a tuple of (user-write log, audit-chain projection) so a
single property body covers both equalities.
"""
from datetime import datetime, timezone

import hypothesis.strategies as st
from hypothesis import given, settings, HealthCheck

from persistence.effect import make_audit_handler
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.runtime import Runtime, with_runtime
from persistence.fact.db import DB

from tests.persistence.txn.conftest import _recording_handler


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


# v0.5.2 N7 — effect-op alphabet for kwargs *keys* is alpha-only so the
# kwargs are unambiguously EDN-conformant (the registered
# ``:persistence.txn/intent-log`` element spec validates kwarg keys as
# strings; the wider `_EdnValueSpec` rejects datetime + custom classes,
# so leaving it at alpha-only here keeps the strategy 100% clear of
# the v0.5.1 N1 SpecError gate at commit time).
_alpha = "abcdefghijklmnopqrstuvwxyz"


@st.composite
def _effect_op(draw):
    op = draw(st.sampled_from([":metric/observe", ":log/write", ":io/print"]))
    kwargs = draw(
        st.dictionaries(
            keys=st.text(_alpha, min_size=1, max_size=8),
            values=st.one_of(
                st.text(max_size=16),
                st.integers(),
                st.booleans(),
                st.none(),
            ),
            max_size=4,
        )
    )
    return ("effect", op, kwargs)


_op_strategy = st.one_of(_assoc_op(), _alter_op(), _effect_op())


# A fixed timestamp for the audit handler's :clock/now. The audit
# handler reads the clock under mask, so this float drives every
# ``recorded_at`` and ``latency_ms`` (=0 because t1 - t0 = 0 with a
# fixed clock).  Identical fixed clocks across both runs keep
# ``recorded_at`` and ``latency_ms`` byte-identical, but the audit
# chain projection ALSO excludes ``recorded_at`` and ``latency_ms``
# defensively — see _audit_chain_projection.
_FIXED_AUDIT_TS = 1714403456.0  # arbitrary epoch float


def _audit_chain_projection(entries: list) -> list[tuple]:
    """Project a list of ``AuditEntry`` to the comparable tuple shape.

    Per § N7 line 131: the comparison must project to a structurally
    deterministic tuple and EXCLUDE per-run UUIDs (``entry_id`` and
    the ``commit_id`` linkage field) plus wall-clock fields. The
    design doc names ``(op, args_hash, result_hash, prev_hash,
    txn_commit)`` as the canonical projection; the field set below
    is the realised, byte-identity-preserving version after
    confirming that ``prev_hash`` and the raw ``txn_commit`` UUID
    are *transitively* per-run-tainted under the as-shipped audit
    handler.

    Field-by-field rationale:

    - ``op`` — pure function of the strategy draw. INCLUDED.
    - ``args_hash`` — ``canonical_hash(args)`` after ``_txn_commit``
      strip (``handlers/audit.py:419-421``); pure function of
      strategy kwargs. INCLUDED.
    - ``result_hash`` — ``canonical_hash(result)`` where ``result``
      is :func:`tests.persistence.txn.conftest._stable_result`
      (also strips ``_txn_commit`` before digesting). Pure function
      of (op, kwargs). INCLUDED.
    - ``txn_commit`` — the raw value is a per-run UUID and is
      EXCLUDED. We project the binary "set / unset" pattern instead;
      every audited intent in our setup carries a ``txn_commit``,
      so this is constant True for our property, but it pins the
      shape: any path that produces an unset slot would surface as
      a difference (and it's the symmetric companion of the
      ``txn_commit_present is True`` audit-replay invariant).
    - ``position`` — index of the entry in the chain. The natural
      surrogate for the "linkage" question that ``prev_hash`` would
      answer in a UUID-free chain. ``prev_hash`` itself is the
      content hash of the previous entry, and ``content`` includes
      ``txn_commit`` (``handlers/audit.py:477-478``), so
      ``prev_hash`` is transitively per-run-tainted whenever the
      previous entry came from a dosync. Two runs of the same body
      produce a chain of the same length with the same linkage
      *pattern*; both observations are reduced to "the i-th entry
      sits at position i" via this slot.

    EXCLUDED additionally (per-run / wall-clock / config-shadow):
      ``id`` (content hash, transitively tainted), ``recorded_at``
      (clock-handler-driven; matches under fixed clock but excluded
      defensively), ``latency_ms`` (= 0 under fixed clock; excluded
      defensively), ``prev_hash`` (see ``position`` above),
      ``handler_chain``, ``principal``, ``run_id``, ``parent``,
      ``policy_id``, ``verdict``, ``error``.

    The "drop ``prev_hash`` + project to ``position``" step is the
    realised version of § N7's "exclude per-run UUIDs" — the
    design-doc tuple sketch was written before confirming that
    ``txn_commit`` was hashed into ``id`` (and therefore into the
    next entry's ``prev_hash``). The realised projection still
    delivers the same byte-identity invariant: same chain length,
    same op order, same args hashes, same result hashes, same
    txn-link pattern.
    """
    return [
        (
            i,  # chain position (replaces prev_hash; see docstring)
            entry.op,
            entry.args_hash,
            entry.result_hash,
            entry.txn_commit is not None,
        )
        for i, entry in enumerate(entries)
    ]


def _run_one(ops: list[tuple], fixed_t: datetime) -> tuple[list[tuple], list[tuple]]:
    """Apply ``ops`` against a fresh DB with frozen clock; return
    ``(user_write_log, audit_chain_projection)``.

    Per § N6 byte-identity scope: commit datoms carry per-run UUIDs and are
    filtered out via ``not d.a.startswith("persistence.txn/")``. The clock
    is frozen via ``clock=lambda: fixed_t`` so user-write ``valid_from`` IS
    deterministic across runs.

    Per § N7 audit-chain scope (line 131): the audit chain is captured
    via ``make_audit_handler`` wrapping the deterministic
    :func:`_recording_handler` (no wall-clock, no RNG, no UUIDs in the
    leaf result), under a :func:`make_fixed_clock_handler` so the audit
    handler's ``recorded_at`` is also deterministic. The returned
    audit projection is :func:`_audit_chain_projection` — see its
    docstring for the included/excluded fields.

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

    # Audit handler stack: [recording_leaf, audit_wrapper, fixed_clock]
    # — Runtime invokes outermost-first, so we list innermost first. The
    # audit handler reads :clock/now under mask, so the clock handler
    # must be present in the stack.
    audit_entries: list = []
    records: list = []
    rt = Runtime(
        handlers=[
            make_fixed_clock_handler(ts=_FIXED_AUDIT_TS),
            _recording_handler(records),
            make_audit_handler(
                audit_entries,
                wraps=(":metric/observe", ":log/write", ":io/print"),
            ),
        ]
    )

    with with_runtime(rt):
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
            elif kind == "effect":
                _, effect_op, kwargs = op

                @db.dosync
                def body(tx, effect_op=effect_op, kwargs=kwargs):
                    tx.effect(effect_op, **kwargs)
                    # tx.effect alone never commits — the commit gate
                    # requires either a write_set entry or an effect
                    # intent. Effect intents DO trigger a commit datom
                    # (see ``_build_commit_provenance``), so this body
                    # commits cleanly with an empty write_set + 1
                    # intent.
                body()
            else:
                raise AssertionError(f"unknown op kind: {kind!r}")

    user_log = [
        (d.e, d.a, d.v, d.op, d.valid_from)
        for d in db.log()
        if not d.a.startswith("persistence.txn/")
    ]
    audit_proj = _audit_chain_projection(audit_entries)
    return user_log, audit_proj


@given(
    ops=st.lists(_op_strategy, min_size=1, max_size=10),
)
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_replay_byte_identity_assoc_alter_effect(ops):
    """Two runs of the same op list against fresh frozen-clock DBs produce
    identical user-write logs AND identical audit-chain projections
    (structural comparison; commit datoms + per-run UUIDs excluded).

    v0.5.2 N6: op list now interleaves ``tx.assoc`` and ``tx.alter`` draws.
    v0.5.2 N7: op list now also interleaves ``tx.effect`` draws — the
    body's effect intents traverse the audit handler stack at commit
    time, and the projected audit chain is asserted equal across runs.
    """
    fixed_t = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    log1, audit1 = _run_one(ops, fixed_t)
    log2, audit2 = _run_one(ops, fixed_t)
    assert log1 == log2
    assert audit1 == audit2
