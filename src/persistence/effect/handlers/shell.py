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
        # Allowlist check is by command stem (basename), not full path.
        # Threat model: path separators in argv[0] ARE accepted — rejecting
        # them would break sys.executable on macOS venvs (e.g.
        # /path/.venv/bin/python3). The basename contract is still safe:
        # absolute-path attacks like ["/usr/bin/curl", ...] get denied via
        # basename mismatch ("curl" not in ALLOWLIST_V1). Relative paths like
        # ["./echo", ...] pass (basename "echo") — deliberate per the
        # capability-denial-not-detection design: the LLM agent controlling
        # argv is a trusted component inside the substrate sandbox; OS-level
        # isolation is a separate v0.9.x track (see test_code_exec.py F4
        # xfail-strict). The allowlist denies unknown stems, it does not
        # attempt to detect all possible path-escape variants.
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
        # Stable-API defaults: env_allowlist_subset=[] and timeout_s=30.0 are
        # part of the stable `:shell/exec` contract. Changing either default
        # changes observable caller behavior; treat like an allowlist-content
        # change — bump ALLOWLIST_VERSION when updating.
        env_subset = args.get("env_allowlist_subset", [])  # stable default: []
        env = {k: os.environ[k] for k in env_subset if k in env_passthrough and k in os.environ}
        timeout_s = args.get("timeout_s", 30.0)  # stable default: 30.0 s

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
