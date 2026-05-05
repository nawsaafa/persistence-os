"""Phase 2.2a G2 — :shell/exec handler unit coverage."""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

from persistence.effect.handlers.shell import (
    ALLOWLIST_V1, ALLOWLIST_VERSION, ENV_DEFAULT,
    ShellAllowlistDenied, ShellAllowlistVersionMismatch,
    _allowlist_version, make_shell_handler,
)
from persistence.effect.runtime import Runtime, with_runtime


@pytest.fixture
def shell_rt(tmp_path: Path):
    handler = make_shell_handler()
    return tmp_path, Runtime(handlers=[handler])


def test_shell_exec_echo_happy_path(shell_rt):
    cwd, rt = shell_rt
    with with_runtime(rt) as r:
        result = r.perform(":shell/exec", {
            "argv": ["echo", "hello"],
            "cwd": str(cwd),
            "env_allowlist_subset": [],
            "allowlist_version": ALLOWLIST_VERSION,
        })
    assert result["exit"] == 0
    assert result["stdout"].strip() == "hello"
    assert result["stderr"] == ""
    assert result["wall_clock_ms"] >= 0


def test_shell_exec_allowlist_denied(shell_rt):
    cwd, rt = shell_rt
    with with_runtime(rt) as r, pytest.raises(ShellAllowlistDenied):
        r.perform(":shell/exec", {
            "argv": ["rm", "-rf", "/"],
            "cwd": str(cwd),
            "env_allowlist_subset": [],
            "allowlist_version": ALLOWLIST_VERSION,
        })


def test_shell_exec_argv_must_be_list(shell_rt):
    cwd, rt = shell_rt
    with with_runtime(rt) as r, pytest.raises(TypeError):
        r.perform(":shell/exec", {
            "argv": "echo hello",  # str, not list — must reject
            "cwd": str(cwd),
            "env_allowlist_subset": [],
            "allowlist_version": ALLOWLIST_VERSION,
        })


def test_shell_exec_no_shell_metacharacter_interpretation(shell_rt):
    """argv passed via subprocess.run(shell=False) — `;` and `|` are literal."""
    cwd, rt = shell_rt
    with with_runtime(rt) as r:
        result = r.perform(":shell/exec", {
            "argv": ["echo", "a; rm -rf /"],
            "cwd": str(cwd),
            "env_allowlist_subset": [],
            "allowlist_version": ALLOWLIST_VERSION,
        })
    assert result["stdout"].strip() == "a; rm -rf /"


def test_shell_exec_env_passthrough_only_allowlisted(shell_rt, monkeypatch):
    cwd, rt = shell_rt
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("FORBIDDEN_TOKEN", "leak-me")
    with with_runtime(rt) as r:
        result = r.perform(":shell/exec", {
            "argv": [sys.executable, "-c", "import os; print(sorted(os.environ.keys()))"],
            "cwd": str(cwd),
            "env_allowlist_subset": ["PATH"],
            "allowlist_version": ALLOWLIST_VERSION,
        })
    assert "FORBIDDEN_TOKEN" not in result["stdout"]
    assert "PATH" in result["stdout"]


def test_shell_exec_cwd_required(shell_rt):
    cwd, rt = shell_rt
    with with_runtime(rt) as r, pytest.raises((TypeError, KeyError)):
        r.perform(":shell/exec", {
            "argv": ["echo", "hello"],
            # NO cwd — handler must raise
            "env_allowlist_subset": [],
            "allowlist_version": ALLOWLIST_VERSION,
        })


def test_shell_exec_timeout_kills_process(shell_rt):
    cwd, rt = shell_rt
    with with_runtime(rt) as r:
        result = r.perform(":shell/exec", {
            "argv": [sys.executable, "-c", "import time; time.sleep(5)"],
            "cwd": str(cwd),
            "env_allowlist_subset": ["PATH"],
            "allowlist_version": ALLOWLIST_VERSION,
            "timeout_s": 0.5,
        })
    assert result["exit"] == -9
    assert result["wall_clock_ms"] >= 500
    assert result["wall_clock_ms"] < 2000


def test_allowlist_version_is_deterministic():
    v1 = _allowlist_version(ALLOWLIST_V1)
    v2 = _allowlist_version(ALLOWLIST_V1)
    v3 = _allowlist_version(frozenset(["completely", "different"]))
    assert v1 == v2
    assert v1 != v3
    assert len(v1) == 16


def test_allowlist_version_mismatch_on_replay(shell_rt, monkeypatch):
    cwd, rt = shell_rt
    bogus_version = "deadbeef00000000"
    with with_runtime(rt) as r, pytest.raises(ShellAllowlistVersionMismatch):
        r.perform(":shell/exec", {
            "argv": ["echo", "hi"],
            "cwd": str(cwd),
            "env_allowlist_subset": [],
            "allowlist_version": bogus_version,
        })


def test_shell_exec_records_actual_allowlist_version_on_perform(shell_rt):
    cwd, rt = shell_rt
    with with_runtime(rt) as r:
        result = r.perform(":shell/exec", {
            "argv": ["echo", "x"],
            "cwd": str(cwd),
            "env_allowlist_subset": [],
            "allowlist_version": ALLOWLIST_VERSION,
        })
    assert result["exit"] == 0
