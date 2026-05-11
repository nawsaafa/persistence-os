"""Phase 2.4c LD-1 G1 — wheel-build + fresh-venv install + coder CLI smoke.

R0-fold B3a: this gate validates the CONSUMER-side install contract
(`pip install dist/*.whl` into a fresh interpreter). The DEV-environment
reproducibility contract (`uv lock --check`) is a separate concern
validated in step (1).

R0-fold B3b: HTTP smoke is NOT in base G1 — `persistence.http` requires
`[http]` extras (fastapi/uvicorn/pydantic) not in the base wheel. The
sister test `tests/sdk/test_lockfile_distribution_smoke_http.py` is a
W3 rescope for the v0.9.x distribution track.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.xfail(
    strict=True,
    reason="Phase 2.4c LD-1: built wheel install + coder CLI smoke not yet wired",
)
@pytest.mark.slow  # 30-60s subprocess test
def test_built_wheel_installs_and_runs_coder_cli(tmp_path: Path) -> None:
    """G1 — uv lock --check + uv build + fresh-venv install of built
    wheel + run python -m persistence.coder --task hello. Catches
    packaging-time / install-time / import-time failures that
    `uv lock --check` alone misses.
    """
    # (1) Copy repo to tmp_path (avoid polluting source tree with dist/)
    # R0-fold N1: add .pytest_cache + .mypy_cache to ignore patterns
    repo_copy = tmp_path / "repo"
    shutil.copytree(
        REPO_ROOT,
        repo_copy,
        ignore=shutil.ignore_patterns(
            ".git", "*.pyc", "__pycache__", ".venv", "dist", "build",
            "*.egg-info", ".pytest_cache", ".mypy_cache",
        ),
    )

    # (2) uv lock --check (no drift between uv.lock and pyproject.toml)
    r = subprocess.run(
        ["uv", "lock", "--check"],
        cwd=str(repo_copy),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, f"uv lock --check failed:\n{r.stderr}"

    # (3) uv build — produce wheel + sdist
    r = subprocess.run(
        ["uv", "build", "--no-progress"],
        cwd=str(repo_copy),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, f"uv build failed:\n{r.stderr}"
    wheels = list((repo_copy / "dist").glob("persistence-0.9.0a1-*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"

    # (4) Fresh venv install of the BUILT WHEEL (not editable)
    venv_dir = tmp_path / "smoke-venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        timeout=60,
    )
    venv_py = venv_dir / "bin" / "python"
    venv_pip = venv_dir / "bin" / "pip"
    r = subprocess.run(
        [str(venv_pip), "install", "--no-input", str(wheels[0])],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, f"pip install of built wheel failed:\n{r.stderr}"

    # (5) python -m persistence.coder --task hello — echo-mode banner-mask
    # R0-fold B3b: HTTP smoke is NOT here; sister test G1[http] handles it.
    r = subprocess.run(
        [str(venv_py), "-m", "persistence.coder", "--task", "hello"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 1, (
        f"coder smoke: expected exit 1 (echo-floor), got {r.returncode}:\n"
        f"stdout={r.stdout}\nstderr={r.stderr}"
    )
    assert "Traceback (most recent call last):" not in r.stderr, (
        f"coder smoke: raw traceback leaked:\n{r.stderr}"
    )
    assert (
        "persistence-coder: echo handler can't drive a real agent loop"
        in r.stderr
    )
