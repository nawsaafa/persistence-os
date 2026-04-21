"""Shared fixtures for persistence.fact tests.

Every backend is exercised through the same conformance test suite. New
backends (e.g. a Postgres one) just need to add a branch to the ``store``
fixture below — no per-backend test duplication.
"""

from __future__ import annotations

import itertools

import pytest

from persistence.fact import InMemoryStore, SQLiteStore


@pytest.fixture(autouse=True)
def _reset_tx_counter():
    """Reset the monotonic transaction counter before each test."""
    from persistence.fact import db as db_mod

    db_mod._tx_counter = itertools.count(1)
    yield


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    """Parametrized backend — every test using this fixture runs on both."""
    if request.param == "memory":
        return InMemoryStore()
    if request.param == "sqlite":
        return SQLiteStore(path=str(tmp_path / "datom_log.sqlite"))
    raise ValueError(f"unknown backend {request.param}")
