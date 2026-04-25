"""Phase D — DB.causal_history walks parent_provenance_hash chains."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from persistence.fact import CausalDAG, DB, Datom, InMemoryStore


def test_causal_dag_dataclass_shape():
    """CausalDAG is a frozen dataclass with seeds + parents fields."""
    ts = datetime(2026, 4, 25, tzinfo=timezone.utc)
    d = Datom(
        e="e-1", a="x", v=1,
        tx=1, tx_time=ts, valid_from=ts, valid_to=None, op="assert",
    )
    dag = CausalDAG(seeds=[d], parents={})
    assert dag.seeds == [d]
    assert dag.parents == {}
    # Frozen — assignment fails with FrozenInstanceError
    with pytest.raises(FrozenInstanceError):
        dag.seeds = []  # type: ignore[misc]


def test_causal_history_no_chain_returns_seeds_only():
    """Datoms without parent_provenance_hash yield empty parents dict."""
    db = DB(InMemoryStore())
    db = db.transact([{"e": "p-1", "a": "x", "v": 42}])

    dag = db.causal_history("p-1")
    assert len(dag.seeds) == 1
    assert dag.seeds[0].e == "p-1"
    assert dag.parents == {}  # no chain


def test_causal_history_walks_explicit_parent_provenance_hash():
    """A datom whose provenance has parent_provenance_hash → DAG records edge."""
    db = DB(InMemoryStore())
    # transact(provenance=) is tx-wide — applied to all datoms in the tx.
    db = db.transact(
        [{"e": "p-1", "a": "x", "v": 1}],
        provenance={"parent_provenance_hash": "parent-hash-A"},
    )
    dag = db.causal_history("p-1")
    assert len(dag.seeds) == 1
    # parents dict records the parent hash via the seed's canonical id.
    parent_hashes: list[str] = []
    for parent_list in dag.parents.values():
        parent_hashes.extend(parent_list)
    assert "parent-hash-A" in parent_hashes


def test_causal_history_max_depth_bounds_walk():
    """max_depth caps the walk; deeper chains are truncated.

    v0.4.0a1 single-level extraction means max_depth >= 1 yields the same
    one-level result; max_depth=0 yields no parents at all.
    """
    db = DB(InMemoryStore())
    db = db.transact(
        [{"e": "p-1", "a": "x", "v": 1}],
        provenance={"parent_provenance_hash": "h-A"},
    )

    # max_depth=0 → no parents extracted at all
    dag_zero = db.causal_history("p-1", max_depth=0)
    assert dag_zero.parents == {}

    # max_depth=1 → one-level result (the seed's direct parent)
    dag_one = db.causal_history("p-1", max_depth=1)
    total_edges = sum(len(v) for v in dag_one.parents.values())
    assert total_edges <= 1


def test_causal_history_cycle_safe():
    """A self-referencing chain does not infinite-loop.

    Single-level extraction is trivially cycle-safe (no recursion in the
    walker), but the test future-proofs for v0.5 multi-level walking.
    """
    db = DB(InMemoryStore())
    db = db.transact(
        [{"e": "p-1", "a": "x", "v": 1}],
        provenance={"parent_provenance_hash": "self-loop"},
    )

    # Should terminate without StackOverflow / RecursionError
    dag = db.causal_history("p-1", max_depth=16)
    # No assertion on shape — just the absence of a hang/crash is the test
    assert dag is not None
