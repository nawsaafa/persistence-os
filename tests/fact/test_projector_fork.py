"""Phase C — ProjectionAdapter.fork(): one method on existing Protocol."""
from __future__ import annotations

import pytest


def test_projection_adapter_protocol_includes_fork():
    """ProjectionAdapter Protocol declares fork(branch_id) -> ProjectionAdapter."""
    from persistence.fact.projection import ProjectionAdapter

    # Protocol method signature is exposed via __annotations__ or callable check
    assert hasattr(ProjectionAdapter, "fork")
    # Caller-shape: takes self + branch_id (str)
    import inspect
    sig = inspect.signature(ProjectionAdapter.fork)
    assert "branch_id" in sig.parameters


def test_dict_projection_fork_returns_fresh_adapter():
    """DictProjection.fork(branch_id) returns a NEW empty DictProjection."""
    from persistence.fact import Datom
    from persistence.fact.projection import DictProjection
    from datetime import datetime, timezone

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
