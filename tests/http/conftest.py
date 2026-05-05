# tests/http/conftest.py
"""Shared fixtures for the http test package (Phase 2.1c)."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(tmp_path):
    """Build a TestClient backed by a fresh Substrate + BlobStore.

    Teardown calls substrate.close() to reset the audit ContextVar
    (persistence.effect.runtime._active) so it does not leak into subsequent
    tests (sdk/txn tests that expect the ContextVar to be unset).
    """
    os.environ["PERSISTENCE_API_KEY"] = "test-token"
    os.environ["PERSISTENCE_HTTP_LOOPBACK_BYPASS"] = "1"
    os.environ["PERSISTENCE_BLOB_ROOT"] = str(tmp_path / "blobs")
    from persistence.http.server import build_app

    app = build_app()
    client = TestClient(app, client=("127.0.0.1", 9999))
    try:
        yield client
    finally:
        app.state.substrate.close()
