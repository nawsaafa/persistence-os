"""T0/G3 — `s.plan.new_dispatcher()` curated factory.

Phase 2.3a substrate-prereq. Adds one curated method to `_PlanNamespace`
that returns a fresh `persistence.plan.Dispatcher` instance without
re-exporting the `Dispatcher` type itself (per `_facade.py:384-390`:
"Type vocabulary stays in `persistence.plan`").
"""
from __future__ import annotations

import pytest

from persistence.plan import Dispatcher
from persistence.sdk import Substrate


@pytest.fixture
def s():
    with Substrate.open("memory") as substrate:
        yield substrate


def test_new_dispatcher_returns_persistence_plan_dispatcher(s):
    d = s.plan.new_dispatcher()
    assert isinstance(d, Dispatcher)


def test_new_dispatcher_is_fresh_per_call(s):
    d1 = s.plan.new_dispatcher()
    d2 = s.plan.new_dispatcher()
    assert d1 is not d2
    # Independent registries — registering on d1 must not affect d2.
    d1.register(":fs/read", lambda node, env: None)
    assert d1.has_handler(":fs/read")
    assert not d2.has_handler(":fs/read")


def test_new_dispatcher_register_then_get(s):
    d = s.plan.new_dispatcher()
    handler = lambda node, env: {"stub": True}
    d.register(":fs/read", handler)
    assert d.get_handler(":fs/read") is handler


def test_new_dispatcher_is_experimental_decorated(s):
    """The method must carry `@experimental` like the rest of `s.plan.*`."""
    method = type(s.plan).new_dispatcher
    metadata = getattr(method, "__sdk_stability__", None)
    assert metadata is not None, (
        f"s.plan.new_dispatcher must be @experimental-decorated for SDK "
        f"surface consistency; method: {method!r}"
    )
    assert metadata.get("level") == "experimental", (
        f"s.plan.new_dispatcher.__sdk_stability__['level'] must be "
        f"'experimental', got: {metadata!r}"
    )
