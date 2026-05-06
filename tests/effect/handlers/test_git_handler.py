"""Phase 2.2b G1 — :git/* thin-wrapper-over-:shell/exec handler unit coverage.

Each :git/<sub> clause:

1. Validates op-specific args -> ``GitArgValidation`` on failure.
2. Enforces ``cwd`` via ``_safe_resolve(args["cwd"], project_root)`` from fs.py.
3. Constructs a deterministic argv with the
   ``git -c color.ui=false -c core.pager=cat`` prefix, alphabetically sorted
   paths, ``--`` separator, and an allowed-enum format for ``:git/log``.
4. Delegates to ``:shell/exec`` via the module-level
   ``runtime.perform(":shell/exec", ...)`` wrapped in
   ``with mask(audit_handler_name)`` so ONE outer ``:git/<sub>`` AuditEntry
   emits per call (the inner ``:shell/exec`` audit is suppressed).

The argv-determinism tests use a ``:shell/exec`` *spy* handler that
captures the args dict the clause passes down. None of the five spy-
visible kwargs (``argv``, ``cwd``, ``allowlist_version``,
``env_allowlist_subset``, ``timeout_s``) appear in the audit
``args_hash`` — audit hashes the LLM-emitted op args BEFORE the clause
runs — so the spy IS the determinism contract.

Design § 2 LD2 of
``docs/plans/2026-05-06-phase-2.2b-git-code-exec-design.md``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from persistence.effect.handlers.audit import AuditEntry, make_audit_handler
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.handlers.fs import FsCapabilityDenied
from persistence.effect.handlers.git import GitArgValidation, make_git_handler
from persistence.effect.handlers.shell import (
    ALLOWLIST_VERSION,
    _shell_exec_clause,
    ALLOWLIST_V1,
    ENV_DEFAULT,
)
from persistence.effect.runtime import Handler, Runtime, with_runtime


_GIT_PREFIX_ARGV: list[str] = ["git", "-c", "color.ui=false", "-c", "core.pager=cat"]
_EXPECTED_ENV_SUBSET: list[str] = ["PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE"]


def _spy_shell_handler() -> tuple[Handler, list[dict]]:
    """Return ``(handler, captured)`` — handler returns a canned shell-exec
    result dict and appends each call's ``args`` to ``captured``."""
    captured: list[dict] = []

    def spy_clause(args: dict, _k, _ctx) -> dict:
        captured.append(args)
        return {"exit": 0, "stdout": "", "stderr": "", "wall_clock_ms": 1}

    handler = Handler(
        name="shell-spy",
        wraps={":shell/exec"},
        clauses={":shell/exec": spy_clause},
    )
    return handler, captured


# --------------------------------------------------------------------------
# Table-driven argv-determinism tests (7 parametrized rows in one function)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "op,op_args,expected_argv_tail",
    [
        # :git/diff default (ref=HEAD, no paths, no cached).
        (":git/diff", {}, ["diff", "--no-color", "HEAD", "--"]),
        # :git/diff cached + paths sorted alphabetically.
        (
            ":git/diff",
            {"cached": True, "paths": ["b.txt", "a.txt"]},
            ["diff", "--no-color", "--cached", "HEAD", "--", "a.txt", "b.txt"],
        ),
        # :git/status no paths.
        (":git/status", {}, ["status", "--porcelain", "--no-color", "--"]),
        # :git/status with paths sorted alphabetically.
        (
            ":git/status",
            {"paths": ["b.txt", "a.txt"]},
            ["status", "--porcelain", "--no-color", "--", "a.txt", "b.txt"],
        ),
        # :git/log default (n=10, format=oneline, no paths).
        (
            ":git/log",
            {},
            ["log", "--no-color", "-n", "10", "--format=oneline", "--"],
        ),
        # :git/log n=5 + format=short + sorted paths.
        (
            ":git/log",
            {"n": 5, "format": "short", "paths": ["b.txt", "a.txt"]},
            [
                "log",
                "--no-color",
                "-n",
                "5",
                "--format=short",
                "--",
                "a.txt",
                "b.txt",
            ],
        ),
        # :git/commit with message + sorted paths.
        (
            ":git/commit",
            {"message": "msg", "paths": ["b.txt", "a.txt"]},
            ["commit", "-m", "msg", "--", "a.txt", "b.txt"],
        ),
    ],
)
def test_git_argv_construction(
    op: str,
    op_args: dict,
    expected_argv_tail: list[str],
    tmp_path: Path,
) -> None:
    """Pin argv shape per op via a :shell/exec spy; ALL FIVE call kwargs
    verified (argv, cwd, allowlist_version, env_allowlist_subset, timeout_s).

    None of those keys appear in the audit args_hash — the spy IS the
    determinism contract.
    """
    project_root = tmp_path / "proj"
    project_root.mkdir()

    # For paths to satisfy the per-path :git/commit safe-resolve check
    # (paths must resolve inside project_root), touch the listed files.
    for p in op_args.get("paths", []):
        (project_root / p).write_text("")

    spy, captured = _spy_shell_handler()
    git_handler = make_git_handler(project_root=project_root)
    rt = Runtime(handlers=[spy, git_handler])

    with with_runtime(rt) as r:
        r.perform(op, {**op_args, "cwd": str(project_root)})

    assert len(captured) == 1
    call = captured[0]
    assert call["argv"] == _GIT_PREFIX_ARGV + expected_argv_tail
    assert call["cwd"] == str(project_root.resolve())
    assert call["allowlist_version"] == ALLOWLIST_VERSION
    assert call["env_allowlist_subset"] == _EXPECTED_ENV_SUBSET
    assert call["timeout_s"] == 30.0


def test_git_default_cwd_is_project_root(tmp_path: Path) -> None:
    """When ``args["cwd"]`` is omitted, the clause defaults to project_root.

    Documents the ``args.get("cwd", str(project_root))`` fallback.
    """
    project_root = tmp_path / "proj"
    project_root.mkdir()

    spy, captured = _spy_shell_handler()
    git_handler = make_git_handler(project_root=project_root)
    rt = Runtime(handlers=[spy, git_handler])

    with with_runtime(rt) as r:
        r.perform(":git/status", {})

    assert len(captured) == 1
    assert captured[0]["cwd"] == str(project_root.resolve())


# --------------------------------------------------------------------------
# Single-audit-entry test — mask("audit") suppresses inner :shell/exec
# --------------------------------------------------------------------------


def test_git_emits_single_outer_audit_entry(tmp_path: Path) -> None:
    """A real :git/* call through the audit middleware produces EXACTLY ONE
    AuditEntry — the outer ``:git/<sub>`` — because the clause wraps its
    inner ``runtime.perform(":shell/exec", ...)`` in ``with mask("audit")``.

    Uses the real shell handler over an actual git repo so the underlying
    git invocation succeeds.
    """
    project_root = tmp_path / "repo"
    project_root.mkdir()
    subprocess.run(
        ["git", "init"],
        cwd=project_root,
        check=True,
        capture_output=True,
    )

    entries: list[AuditEntry] = []
    shell = Handler(
        name="shell",
        wraps={":shell/exec"},
        clauses={":shell/exec": _shell_exec_clause(ALLOWLIST_V1, ENV_DEFAULT)},
    )
    git_handler = make_git_handler(project_root=project_root)
    clock = make_fixed_clock_handler(ts=1_712_000_000.0)
    audit = make_audit_handler(
        entries,
        wraps={":git/diff", ":git/status", ":git/log", ":git/commit", ":shell/exec"},
    )
    # Stack order (innermost-first): raw shell, raw git wrapper, clock, audit.
    rt = Runtime(handlers=[shell, git_handler, clock, audit])

    with with_runtime(rt) as r:
        r.perform(":git/status", {"cwd": str(project_root)})

    assert len(entries) == 1
    assert entries[0].op == ":git/status"


# --------------------------------------------------------------------------
# Validation-error tests
# --------------------------------------------------------------------------


def test_git_cwd_outside_project_root_raises_FsCapabilityDenied(
    tmp_path: Path,
) -> None:
    """``args["cwd"]`` is run through ``_safe_resolve(cwd, project_root)``
    so a cwd outside the project root raises ``FsCapabilityDenied``."""
    project_root = tmp_path / "proj"
    project_root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    spy, _captured = _spy_shell_handler()
    git_handler = make_git_handler(project_root=project_root)
    rt = Runtime(handlers=[spy, git_handler])

    with with_runtime(rt) as r, pytest.raises(FsCapabilityDenied):
        r.perform(":git/status", {"cwd": str(elsewhere)})


def test_git_commit_empty_message_raises_GitArgValidation(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    (project_root / "x.txt").write_text("")

    spy, _captured = _spy_shell_handler()
    git_handler = make_git_handler(project_root=project_root)
    rt = Runtime(handlers=[spy, git_handler])

    with with_runtime(rt) as r, pytest.raises(GitArgValidation):
        r.perform(
            ":git/commit",
            {"cwd": str(project_root), "message": "", "paths": ["x.txt"]},
        )


def test_git_commit_empty_paths_raises_GitArgValidation(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()

    spy, _captured = _spy_shell_handler()
    git_handler = make_git_handler(project_root=project_root)
    rt = Runtime(handlers=[spy, git_handler])

    with with_runtime(rt) as r, pytest.raises(GitArgValidation):
        r.perform(
            ":git/commit",
            {"cwd": str(project_root), "message": "msg", "paths": []},
        )


def test_git_log_disallowed_format_raises_GitArgValidation(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()

    spy, _captured = _spy_shell_handler()
    git_handler = make_git_handler(project_root=project_root)
    rt = Runtime(handlers=[spy, git_handler])

    with with_runtime(rt) as r, pytest.raises(GitArgValidation):
        r.perform(":git/log", {"cwd": str(project_root), "format": "json"})


def test_git_log_n_zero_raises_GitArgValidation(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()

    spy, _captured = _spy_shell_handler()
    git_handler = make_git_handler(project_root=project_root)
    rt = Runtime(handlers=[spy, git_handler])

    with with_runtime(rt) as r, pytest.raises(GitArgValidation):
        r.perform(":git/log", {"cwd": str(project_root), "n": 0})


def test_git_log_n_too_large_raises_GitArgValidation(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()

    spy, _captured = _spy_shell_handler()
    git_handler = make_git_handler(project_root=project_root)
    rt = Runtime(handlers=[spy, git_handler])

    with with_runtime(rt) as r, pytest.raises(GitArgValidation):
        r.perform(":git/log", {"cwd": str(project_root), "n": 10_000})


# --------------------------------------------------------------------------
# T9.1 ARIS Impl R1 fold — paths validation across all 4 :git/* ops
# --------------------------------------------------------------------------


def test_git_diff_paths_as_string_raises_validation(tmp_path: Path) -> None:
    """I1 fold — :git/diff with paths as a string (not list) raises
    GitArgValidation. Without validation, ``sorted("x.txt")`` would
    silently expand to ``['.', 't', 't', 'x', 'x']`` — char-by-char
    argv injection."""
    project_root = tmp_path / "proj"
    project_root.mkdir()

    spy, _captured = _spy_shell_handler()
    git_handler = make_git_handler(project_root=project_root)
    rt = Runtime(handlers=[spy, git_handler])

    with with_runtime(rt) as r, pytest.raises(GitArgValidation, match="must be list"):
        r.perform(":git/diff", {"cwd": str(project_root), "paths": "x.txt"})


def test_git_status_paths_with_non_str_element_raises_validation(
    tmp_path: Path,
) -> None:
    """I1 fold — :git/status with paths=[1] raises GitArgValidation
    (would otherwise crash later in argv-extension)."""
    project_root = tmp_path / "proj"
    project_root.mkdir()

    spy, _captured = _spy_shell_handler()
    git_handler = make_git_handler(project_root=project_root)
    rt = Runtime(handlers=[spy, git_handler])

    with with_runtime(rt) as r, pytest.raises(GitArgValidation, match="must be str"):
        r.perform(":git/status", {"cwd": str(project_root), "paths": [1]})


def test_git_commit_paths_with_non_str_element_raises_validation(
    tmp_path: Path,
) -> None:
    """I2 fold — :git/commit with paths=[1] raises GitArgValidation,
    NOT raw TypeError from ``Path(1)``. _validate_paths runs BEFORE
    the per-path resolution loop."""
    project_root = tmp_path / "proj"
    project_root.mkdir()

    spy, _captured = _spy_shell_handler()
    git_handler = make_git_handler(project_root=project_root)
    rt = Runtime(handlers=[spy, git_handler])

    with with_runtime(rt) as r, pytest.raises(GitArgValidation, match="must be str"):
        r.perform(
            ":git/commit",
            {"cwd": str(project_root), "message": "x", "paths": [1]},
        )


def test_git_log_paths_as_dict_raises_validation(tmp_path: Path) -> None:
    """I1 fold — :git/log with paths as dict raises GitArgValidation."""
    project_root = tmp_path / "proj"
    project_root.mkdir()

    spy, _captured = _spy_shell_handler()
    git_handler = make_git_handler(project_root=project_root)
    rt = Runtime(handlers=[spy, git_handler])

    with with_runtime(rt) as r, pytest.raises(GitArgValidation, match="must be list"):
        r.perform(":git/log", {"cwd": str(project_root), "paths": {"foo": "bar"}})
