"""v0.5.0a1 INT — exercises Phase A + B + E together with the v0.4
substrate primitives (Dispatcher, Provenance, fork, causal_history).
"""
from datetime import datetime, timezone

from pyrsistent import pmap

from persistence.fact.db import DB
from persistence.fact.projection import DictProjection, rebuild


def test_full_v05_int():
    """End-to-end: dosync write → branch → dosync write on branch →
    fork projection → causal_history walks both.
    """
    fixed_t = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    clock = lambda: fixed_t

    db = DB(clock=clock)
    r = db.ref("user-1")

    # 1. Dosync write on parent.
    @db.dosync
    def init(tx):
        tx.assoc(r, pmap({"name": "Alice", "balance": 100}))
    init()

    # 2. Branch at parent's now.
    branched = db.branch(db._clock(), [])

    # 3. Dosync write on branched.
    r_branched = branched.ref("user-1")

    @branched.dosync
    def boost(tx):
        tx.alter(r_branched, lambda cur: cur.set("balance", cur["balance"] + 1000))
    boost()

    # 4. Fork projection.
    proj = DictProjection()
    rebuild(db, proj)
    forked_proj = proj.fork("branch-1")
    assert forked_proj.as_dict() == {}, "fork must return fresh empty projection"

    # 5. Parent and branch see different values. Datom.__post_init__ strips
    # leading colon, so the attribute key is "value" not ":value".
    assert db.as_of(db._clock()).entity("user-1")["value"] == pmap({"name": "Alice", "balance": 100})
    assert branched.as_of(branched._clock()).entity("user-1")["value"] == pmap({"name": "Alice", "balance": 1100})

    # 6. causal_history on parent walks the dosync-emitted datoms.
    dag = db.causal_history("user-1")
    assert len(dag.seeds) >= 1, "causal_history should find at least the assoc datom"


def test_PLAN_CANONICAL_VERSION_still_1_after_int():
    from persistence.plan import PLAN_CANONICAL_VERSION
    assert PLAN_CANONICAL_VERSION == 1
