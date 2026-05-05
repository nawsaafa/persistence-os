"""Phase 2.2a — :shell/exec effect handler with allowlist + sha256 versioning."""
from __future__ import annotations
import hashlib
import os
import subprocess
import time
from typing import Any

from persistence.effect.canonical import canonical_dumps
from persistence.effect.runtime import Handler


ALLOWLIST_V1: frozenset[str] = frozenset({
    "ls", "cat", "echo", "head", "tail", "wc",
    "grep", "find", "git",
    "python", "python3", "uv", "pip", "pytest",
})

ENV_DEFAULT: frozenset[str] = frozenset({
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE",
})


def _allowlist_version(allowlist: frozenset[str]) -> str:
    return hashlib.sha256(
        canonical_dumps(sorted(allowlist)).encode()
    ).hexdigest()[:16]


ALLOWLIST_VERSION: str = _allowlist_version(ALLOWLIST_V1)


class ShellAllowlistDenied(Exception):
    """argv[0] is not in the allowlist."""


class ShellAllowlistVersionMismatch(Exception):
    """Recorded allowlist_version disagrees with handler's current version."""


def _shell_exec_clause(allowlist: frozenset[str], env_passthrough: frozenset[str]):
    current_version = _allowlist_version(allowlist)

    def _clause(args: dict[str, Any], _k: Any, _ctx: dict[str, Any]) -> Any:
        argv = args["argv"]
        if not isinstance(argv, list) or not all(isinstance(s, str) for s in argv):
            raise TypeError(f"argv must be list[str], got {type(argv).__name__}")
        if not argv:
            raise ShellAllowlistDenied("argv is empty")
        # Allowlist is by command stem, not full path. Relative paths like
        # "./echo" pass the check (basename is "echo"). This is deliberate
        # per the capability-denial-not-detection threat model — the agent
        # controlling argv is a trusted component, and absolute-path attacks
        # like ["/usr/bin/curl", ...] still get denied because their basename
        # is not in the allowlist.
        stem = os.path.basename(argv[0])
        if stem not in allowlist:
            raise ShellAllowlistDenied(
                f"argv[0]={argv[0]!r} (stem={stem!r}) not in allowlist (version {current_version})"
            )
        recorded_version = args["allowlist_version"]
        if recorded_version != current_version:
            raise ShellAllowlistVersionMismatch(
                f"recorded={recorded_version} current={current_version}"
            )
        cwd = args["cwd"]  # KeyError if missing — required
        env_subset = args.get("env_allowlist_subset", [])
        env = {k: os.environ[k] for k in env_subset if k in env_passthrough and k in os.environ}
        timeout_s = args.get("timeout_s", 30.0)

        t0 = time.monotonic()  # noqa: wall-clock — subprocess wall-time measurement is outside audit-clock domain
        try:
            cp = subprocess.run(
                argv, cwd=cwd, env=env,
                capture_output=True, text=True,
                timeout=timeout_s, shell=False,
            )
            return {
                "exit": cp.returncode,
                "stdout": cp.stdout,
                "stderr": cp.stderr,
                "wall_clock_ms": int((time.monotonic() - t0) * 1000),  # noqa: wall-clock
            }
        except subprocess.TimeoutExpired as e:
            # NOTE: text=True does NOT guarantee str on TimeoutExpired.stdout —
            # CPython _check_timeout joins raw byte chunks before decode. The
            # bytes branch below is empirically live on macOS, not dead code.
            return {
                "exit": -9,
                "stdout": (e.stdout or b"").decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
                "stderr": (e.stderr or b"").decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or ""),
                "wall_clock_ms": int((time.monotonic() - t0) * 1000),  # noqa: wall-clock
            }

    return _clause


def make_shell_handler(
    *,
    allowlist: frozenset[str] = ALLOWLIST_V1,
    env_passthrough: frozenset[str] = ENV_DEFAULT,
    name: str = "shell",
) -> Handler:
    return Handler(
        name=name,
        wraps={":shell/exec"},
        clauses={":shell/exec": _shell_exec_clause(allowlist, env_passthrough)},
    )
