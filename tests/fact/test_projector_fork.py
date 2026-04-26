"""Phase C — ProjectionAdapter.fork(): one method on existing Protocol."""
from __future__ import annotations

import inspect
from datetime import datetime, timezone

from persistence.fact import DB, Datom, InMemoryStore
from persistence.fact.projection import DictProjection, ProjectionAdapter, rebuild


def test_projection_adapter_protocol_includes_fork():
    """ProjectionAdapter Protocol declares fork(branch_id) -> ProjectionAdapter."""
    # Protocol method signature is exposed via __annotations__ or callable check
    assert hasattr(ProjectionAdapter, "fork")
    # Caller-shape: takes self + branch_id (str)
    sig = inspect.signature(ProjectionAdapter.fork)
    assert "branch_id" in sig.parameters


def test_dict_projection_fork_returns_fresh_adapter():
    """DictProjection.fork(branch_id) returns a NEW empty DictProjection."""
    parent = DictProjection()
    parent.apply(
        Datom(
            e="p-1", a="x", v=42,
            tx=1,
            tx_time=datetime(2026, 4, 25, tzinfo=timezone.utc),
            valid_from=datetime(2026, 4, 25, tzinfo=timezone.utc),
            valid_to=None, op="assert",
        )
    )
    assert parent.get("p-1") == {"x": 42}

    fork = parent.fork(branch_id="b-1")

    # Fork must be a DictProjection (or any ProjectionAdapter)
    assert isinstance(fork, DictProjection)

    # Fork starts EMPTY — caller is responsible for rebuild()
    assert fork.as_dict() == {}

    # Fork is a different instance (no shared state)
    assert fork is not parent


def test_dict_projection_fork_writes_do_not_leak_to_parent():
    """Writes to a forked adapter NEVER touch the parent's sink."""
    ts = datetime(2026, 4, 25, tzinfo=timezone.utc)

    parent = DictProjection()
    parent.apply(Datom(
        e="p-1", a="x", v=42,
        tx=1, tx_time=ts, valid_from=ts, valid_to=None, op="assert",
    ))

    fork = parent.fork(branch_id="b-1")
    fork.apply(Datom(
        e="p-1", a="x", v=999,           # different value in branch
        tx=2, tx_time=ts, valid_from=ts, valid_to=None, op="assert",
    ))
    fork.apply(Datom(
        e="p-2", a="y", v="branched",
        tx=3, tx_time=ts, valid_from=ts, valid_to=None, op="assert",
    ))

    # Parent unchanged
    assert parent.get("p-1") == {"x": 42}
    assert parent.get("p-2") == {}

    # Fork has its own writes
    assert fork.get("p-1") == {"x": 999}
    assert fork.get("p-2") == {"y": "branched"}


def test_dict_projection_fork_then_rebuild_reproduces_parent():
    """rebuild(db, fork) populates fork from log — should match parent."""
    db = DB(InMemoryStore())
    db = db.transact([{"e": "p-1", "a": "x", "v": 42}])
    db = db.transact([{"e": "p-1", "a": "y", "v": "hello"}])

    # Build parent projection via rebuild
    parent = DictProjection()
    rebuild(db, parent)
    assert parent.get("p-1") == {"x": 42, "y": "hello"}

    # Fork starts empty, then rebuild populates from same log
    fork = parent.fork(branch_id="b-1")
    assert fork.as_dict() == {}
    rebuild(db, fork)

    # Now fork should match parent — full snapshot equality, not just one entity
    assert fork.as_dict() == parent.as_dict()


_FORK_ISOLATION_TS = datetime(2026, 4, 25, tzinfo=timezone.utc)


from hypothesis import given, settings, strategies as st  # noqa: E402


@given(
    parent_writes=st.lists(
        st.tuples(
            st.text(min_size=1, max_size=8),
            st.text(min_size=1, max_size=8),
            st.integers(),
        ),
        max_size=5,
    ),
    fork_writes=st.lists(
        st.tuples(
            st.text(min_size=1, max_size=8),
            st.text(min_size=1, max_size=8),
            st.integers(),
        ),
        max_size=5,
    ),
)
@settings(max_examples=50, deadline=None)
def test_dict_projection_fork_isolation_property(parent_writes, fork_writes):
    """Property: for any sequence of writes to fork, parent unchanged.

    Hypothesis-driven; max_examples=50.
    """
    ts = _FORK_ISOLATION_TS
    parent = DictProjection()
    for i, (e, a, v) in enumerate(parent_writes):
        parent.apply(Datom(
            e=e, a=a, v=v,
            tx=i + 1, tx_time=ts, valid_from=ts,
            valid_to=None, op="assert",
        ))
    parent_snapshot_before_fork = parent.as_dict()

    fork = parent.fork(branch_id="b-1")
    for i, (e, a, v) in enumerate(fork_writes):
        fork.apply(Datom(
            e=e, a=a, v=v,
            tx=100 + i, tx_time=ts, valid_from=ts,
            valid_to=None, op="assert",
        ))

    # Parent snapshot UNCHANGED after fork writes
    assert parent.as_dict() == parent_snapshot_before_fork
