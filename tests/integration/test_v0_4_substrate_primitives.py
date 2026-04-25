"""v0.4.0a1 — integration test exercising Phases A + C + D together.

Builds a DB with Dispatcher-driven effect handlers writing typed Provenance,
forks a ProjectionAdapter on branch, walks causal_history across the branch
boundary. Asserts clean isolation between parent and branch projection sinks.
"""
from __future__ import annotations

from datetime import datetime, timezone


def test_v0_4_full_integration_phase_a_c_d_together():
    """Cohesive smoke test: Dispatcher + fork() + Provenance/causal_history."""
    from persistence.fact import DB, InMemoryStore, Provenance
    from persistence.fact.projection import DictProjection, rebuild
    from persistence.plan import Dispatcher, Node

    # ts used as the branch point — must be AFTER all transaction tx_times.
    # We freeze the DB clock to ts_frozen (one day prior) so that every
    # transact() call stamps tx_time = ts_frozen, guaranteeing as_of(ts)
    # captures all seed datoms regardless of wall-clock drift.
    ts_frozen = datetime(2026, 4, 24, tzinfo=timezone.utc)
    ts = datetime(2026, 4, 25, tzinfo=timezone.utc)

    # ---- Phase A: Dispatcher dispatches by tag --------------------------
    dispatched_msgs: list[str] = []
    d = Dispatcher()
    d.register(":record", lambda n, _env: dispatched_msgs.append(n.attrs.get("msg", "")))
    plan = Node(
        tag=":seq",
        children=(
            Node(tag=":record", attrs={"msg": "hello"}),
            Node(tag=":record", attrs={"msg": "world"}),
        ),
    )
    d.dispatch(plan, env={})
    assert dispatched_msgs == ["hello", "world"]

    # ---- Phase D: typed Provenance + causal_history ---------------------
    p: Provenance = {
        "source": "integration-test",
        "handler_id": "h-1",
        "parent_provenance_hash": "h-parent",
    }

    db = DB(InMemoryStore(), clock=lambda: ts_frozen)
    db = db.transact(
        [{"e": "p-1", "a": "x", "v": 42}],
        provenance=p,  # type: ignore[arg-type]  # mirrors db.transact() D2-ripple
    )
    dag = db.causal_history("p-1")
    assert len(dag.seeds) == 1
    # Phase D parent_provenance_hash recorded in parents map
    parent_hashes = [h for hs in dag.parents.values() for h in hs]
    assert "h-parent" in parent_hashes

    # ---- Phase C: ProjectionAdapter.fork() across DB.branch -------------
    parent_proj = DictProjection()
    rebuild(db, parent_proj)
    assert parent_proj.get("p-1") == {"x": 42}

    # Branch the DB and fork the projection
    branched_db = db.branch(ts, assertions=[{"e": "p-2", "a": "y", "v": "branched"}])
    fork_proj = parent_proj.fork(branch_id="b-1")
    rebuild(branched_db, fork_proj)

    # Branch projection sees BOTH the seed datom AND the new branched assertion
    assert fork_proj.get("p-1") == {"x": 42}
    assert fork_proj.get("p-2") == {"y": "branched"}

    # Parent projection unchanged
    assert parent_proj.get("p-2") == {}

    # Phase A + Phase D together: causal_history on the BRANCH
    branch_dag = branched_db.causal_history("p-1")
    assert len(branch_dag.seeds) >= 1
    branch_parent_hashes = [h for hs in branch_dag.parents.values() for h in hs]
    assert "h-parent" in branch_parent_hashes
