"""Phase 2.1c — hermetic CLI smoke + uvicorn proxy-header pin assertion (Design §10.7).

The proxy-header pin is load-bearing for B2 closure (R1 BLOCKING B2,
folded R1.1, runtime-pinned R1.2 N3). If a future change accidentally
enables proxy-header trust, this test fails loudly.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_main_module_invokes_uvicorn_with_proxy_headers_disabled(monkeypatch):
    """Inspect __main__.py's uvicorn.run call args via mock."""
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        # short-circuit instead of actually starting a server

    monkeypatch.setenv("PERSISTENCE_API_KEY", "x")
    monkeypatch.setenv("PERSISTENCE_HTTP_LOOPBACK_BYPASS", "0")
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", fake_run)

    from persistence.http.__main__ import main
    main()

    assert captured.get("proxy_headers") is False, (
        "uvicorn must be started with proxy_headers=False to prevent "
        "X-Forwarded-* from rewriting request.client.host (Design §7.1, R1.2 N3)"
    )
    assert captured.get("forwarded_allow_ips") == "", (
        "forwarded_allow_ips must be empty string to disable proxy header trust"
    )


def test_main_hermetic_subprocess(tmp_path):
    """Hermetic CLI smoke (F1 lesson from Phase 2.1a): pass env= and cwd=,
    don't rely on inherited PYTHONPATH=src."""
    env = {
        **os.environ,
        "PERSISTENCE_API_KEY": "x",
        "PERSISTENCE_BLOB_ROOT": str(tmp_path / "blobs"),
        "PERSISTENCE_HTTP_PORT": "47999",  # avoid collision
    }
    # Quick sanity: --help should exit 0
    result = subprocess.run(
        [sys.executable, "-m", "persistence.http", "--help"],
        env=env, cwd=tmp_path, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, f"stderr={result.stderr.decode()}"
