"""Shared fixtures for persistence.fact tests.

Every backend is exercised through the same conformance test suite. New
backends (e.g. a Postgres one) just need to add a branch to the ``store``
fixture below — no per-backend test duplication.

Tx allocation lives on the Store instance (see Store.next_tx), so each
test gets a fresh counter automatically via fresh Store construction —
no module-level reset hook required.
"""

from __future__ import annotations

import pytest

from persistence.fact import InMemoryStore, SQLiteStore


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    """Parametrized backend — every test using this fixture runs on both."""
    if request.param == "memory":
        return InMemoryStore()
    if request.param == "sqlite":
        return SQLiteStore(path=str(tmp_path / "datom_log.sqlite"))
    raise ValueError(f"unknown backend {request.param}")
