"""Phase 2.2b — :git/* effect handlers (diff, status, log, commit).

Thin-wrapper-over-:shell/exec design (LD2 of
``docs/plans/2026-05-06-phase-2.2b-git-code-exec-design.md``). Each clause:

1. Validates op-specific args (raises :class:`GitArgValidation`).
2. Enforces ``cwd`` via ``_safe_resolve(args["cwd"], project_root)`` —
   imported from ``handlers.fs`` so the path-capability denial logic is
   single-sourced with the :fs/* handlers.
3. Constructs a deterministic argv with the
   ``git -c color.ui=false -c core.pager=cat`` prefix, alphabetically
   sorted paths, ``--`` separator, and an allowed-enum format for
   :git/log. Path sorting + ``--`` are required for replay byte-identity
   under any LLM-emitted argument order.
4. Wraps the inner ``runtime.perform(":shell/exec", ...)`` in
   ``with mask(audit_handler_name)`` so EXACTLY ONE outer
   ``:git/<sub>`` AuditEntry emits per call (the inner ``:shell/exec``
   audit is suppressed). The audit middleware itself is registered
   separately via the canonical audit stack — see
   :data:`persistence.effect._audit_stack.CANONICAL_AUDIT_WRAPPED_OPS`,
   which T1 of Phase 2.2b extended with the four ``:git/<sub>`` ops.

The handler factory takes a single ``project_root`` (`Path`) capability
and an ``audit_handler_name`` (default ``"audit"``, matching
:func:`persistence.effect.handlers.audit.make_audit_handler`). Install
via ``substrate.effect.install_handler(handler, position="bottom")``.

The five spy-visible kwargs the clauses pass to ``:shell/exec`` —
``argv``, ``cwd``, ``allowlist_version``, ``env_allowlist_subset``,
``timeout_s`` — do NOT appear in the audit ``args_hash`` because audit
hashes the LLM-emitted op args BEFORE the clause runs. The G1 spy in
``tests/effect/handlers/test_git_handler.py`` is therefore the
determinism contract for the inner shell call.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from persistence.effect.handlers.fs import _safe_resolve
from persistence.effect.handlers.shell import ALLOWLIST_VERSION
from persistence.effect.runtime import Handler, mask, perform


# The five env vars the :shell/exec contract honors (subset of
# ``handlers.shell.ENV_DEFAULT``). MUST be a list[str] — the underlying
# clause reads ``args.get("env_allowlist_subset", [])`` and iterates it.
# Order is intentional: PATH first for execvp resolution clarity;
# stability is the contract.
_ENV_SUBSET: list[str] = ["PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE"]

# Common argv prefix for every :git/<sub> call. ``color.ui=false`` and
# ``core.pager=cat`` together pin terminal output regardless of the
# user's git config — required for byte-identity under replay.
_GIT_PREFIX: list[str] = ["git", "-c", "color.ui=false", "-c", "core.pager=cat"]

# :git/log's only accepted format values. ``json`` and arbitrary
# format-strings are denied — they would either invoke external tooling
# (``--format=json`` is git ≥2.46) or admit non-deterministic outputs.
_ALLOWED_LOG_FORMATS: frozenset[str] = frozenset({"oneline", "short", "medium"})

# Default :shell/exec timeout. Stable contract per shell.py — bumped
# only when ALLOWLIST_VERSION bumps.
_DEFAULT_TIMEOUT_S: float = 30.0

# :git/log ``n`` validation bounds. Lower bound: 1 (zero or negative
# would silently expand to ``-n0`` which git treats as no-cap). Upper
# bound: 1000 (sanity cap; the agent should iterate by ref-range, not
# by million-row dumps).
_LOG_N_MIN: int = 1
_LOG_N_MAX: int = 1000


class GitArgValidation(ValueError):
    """Raised when :git/* args fail static validation.

    NOT a wrapper around git-CLI error strings — those are surfaced via
    the result dict's ``stderr`` and non-zero ``exit`` keys.
    """


def _resolve_cwd(args: dict[str, Any], project_root: Path) -> Path:
    """Resolve ``args["cwd"]`` (default: ``project_root``) inside
    ``project_root``. Raises :class:`FsCapabilityDenied` if the resolved
    path escapes the root."""
    cwd_raw = args.get("cwd", str(project_root))
    return _safe_resolve(cwd_raw, project_root)


def _validate_paths(paths: Any) -> list[str]:
    """Validate ``paths`` is ``list[str]``, raising
    :class:`GitArgValidation` on violation.

    Closes the I1/I2 fold gap: without this guard, ``paths="x.txt"``
    (a bare string) would silently pass through ``sorted(...)`` and
    expand to a per-character argv list (``['.', 't', 't', 'x', 'x']``),
    and ``paths=[1, 2]`` would later crash with a raw ``TypeError``
    inside argv-extension or :func:`pathlib.Path` construction.

    Empty list is OK for :git/diff / :git/status / :git/log (means
    "all paths"); :git/commit has its own non-empty check via
    :func:`_commit_clause_factory`.
    """
    if not isinstance(paths, list):
        raise GitArgValidation(
            f":git/* paths must be list[str], got {type(paths).__name__}"
        )
    for i, p in enumerate(paths):
        if not isinstance(p, str):
            raise GitArgValidation(
                f":git/* paths[{i}] must be str, got {type(p).__name__}"
            )
    return paths


def _delegate_to_shell(
    argv: list[str],
    cwd: Path,
    audit_handler_name: str,
) -> Any:
    """Wrap ``runtime.perform(":shell/exec", ...)`` in
    ``mask(audit_handler_name)`` so the inner :shell/exec audit is
    suppressed.

    All five :shell/exec kwargs are passed explicitly so the G1 spy can
    pin the determinism contract. ``cwd`` is converted to ``str`` —
    ``handlers.shell._shell_exec_clause`` requires a str (not a Path)
    per the stable :shell/exec args contract.
    """
    with mask(audit_handler_name):
        return perform(
            ":shell/exec",
            argv=argv,
            cwd=str(cwd),
            allowlist_version=ALLOWLIST_VERSION,
            env_allowlist_subset=_ENV_SUBSET,
            timeout_s=_DEFAULT_TIMEOUT_S,
        )


def _diff_clause_factory(project_root: Path, audit_handler_name: str):
    """:git/diff — ``ref`` defaults to ``HEAD``; ``cached`` toggles
    ``--cached``; paths are alphabetically sorted; ``--`` separator
    always present."""

    def clause(args: dict[str, Any], _k: Any, _ctx: dict[str, Any]) -> Any:
        cwd = _resolve_cwd(args, project_root)
        ref = args.get("ref", "HEAD")
        cached = args.get("cached", False)
        paths = sorted(_validate_paths(args.get("paths", [])))
        argv = list(_GIT_PREFIX) + ["diff", "--no-color"]
        if cached:
            argv.append("--cached")
        argv.append(ref)
        argv.append("--")
        argv.extend(paths)
        return _delegate_to_shell(argv, cwd, audit_handler_name)

    return clause


def _status_clause_factory(project_root: Path, audit_handler_name: str):
    """:git/status — always ``--porcelain --no-color`` for stable parsing;
    paths are alphabetically sorted; ``--`` separator always present."""

    def clause(args: dict[str, Any], _k: Any, _ctx: dict[str, Any]) -> Any:
        cwd = _resolve_cwd(args, project_root)
        paths = sorted(_validate_paths(args.get("paths", [])))
        argv = list(_GIT_PREFIX) + ["status", "--porcelain", "--no-color", "--"]
        argv.extend(paths)
        return _delegate_to_shell(argv, cwd, audit_handler_name)

    return clause


def _log_clause_factory(project_root: Path, audit_handler_name: str):
    """:git/log — ``n`` defaults to 10 (validated 1..1000); ``format``
    must be one of :data:`_ALLOWED_LOG_FORMATS`; paths sorted; ``--``
    separator always present."""

    def clause(args: dict[str, Any], _k: Any, _ctx: dict[str, Any]) -> Any:
        cwd = _resolve_cwd(args, project_root)
        n = args.get("n", 10)
        if not isinstance(n, int) or isinstance(n, bool) or not (
            _LOG_N_MIN <= n <= _LOG_N_MAX
        ):
            raise GitArgValidation(
                f":git/log n={n!r} must be int in [{_LOG_N_MIN}, {_LOG_N_MAX}]"
            )
        fmt = args.get("format", "oneline")
        if fmt not in _ALLOWED_LOG_FORMATS:
            raise GitArgValidation(
                f":git/log format={fmt!r} not in {sorted(_ALLOWED_LOG_FORMATS)}"
            )
        paths = sorted(_validate_paths(args.get("paths", [])))
        argv = list(_GIT_PREFIX) + [
            "log",
            "--no-color",
            "-n",
            str(n),
            f"--format={fmt}",
            "--",
        ]
        argv.extend(paths)
        return _delegate_to_shell(argv, cwd, audit_handler_name)

    return clause


def _commit_clause_factory(project_root: Path, audit_handler_name: str):
    """:git/commit — ``message`` must be non-empty; ``paths`` must be a
    non-empty list, with each path resolving inside ``project_root`` (so
    a stray absolute path can't slip past the cwd capability check);
    paths sorted; ``--`` separator always present."""

    def clause(args: dict[str, Any], _k: Any, _ctx: dict[str, Any]) -> Any:
        cwd = _resolve_cwd(args, project_root)
        message = args.get("message", "")
        if not isinstance(message, str) or not message.strip():
            raise GitArgValidation(":git/commit requires non-empty message")
        # _validate_paths runs BEFORE the empty-paths gate so wrong-type
        # errors (paths=[1]) surface as ":git/* paths[i] must be str"
        # rather than the less-specific "non-empty paths list" message,
        # AND so the per-path Path(p) loop below only sees strings
        # (closes I2 — no raw TypeError leak).
        paths = _validate_paths(args.get("paths", []))
        if not paths:
            raise GitArgValidation(":git/commit requires non-empty paths list")
        # Per-path safe-resolve — relative paths are resolved against cwd,
        # absolute paths are checked as-is. All must land inside
        # project_root.
        for p in paths:
            target = p if Path(p).is_absolute() else cwd / p
            _safe_resolve(target, project_root)
        sorted_paths = sorted(paths)
        argv = list(_GIT_PREFIX) + ["commit", "-m", message, "--"]
        argv.extend(sorted_paths)
        return _delegate_to_shell(argv, cwd, audit_handler_name)

    return clause


def make_git_handler(
    *,
    project_root: Path,
    audit_handler_name: str = "audit",
) -> Handler:
    """Phase 2.2b — :git/* thin-wrapper-over-:shell/exec handler factory.

    Returns a :class:`Handler` that wraps ``:git/{diff,status,log,commit}``
    and dispatches each via ``runtime.perform(":shell/exec", ...)``
    wrapped in ``with mask(audit_handler_name)`` so only ONE outer
    audit entry emits per :git/* call.

    Parameters
    ----------
    project_root
        Capability root. ``args["cwd"]`` (default project_root) is run
        through :func:`_safe_resolve` to deny paths outside this root,
        and :git/commit additionally per-path-validates each entry in
        ``args["paths"]`` against the same root.
    audit_handler_name
        Name of the audit middleware handler to mask while delegating
        to ``:shell/exec``. Default ``"audit"`` matches
        :func:`persistence.effect.handlers.audit.make_audit_handler`,
        which hardcodes ``audit_name = "audit"``.

    Install via ``substrate.effect.install_handler(handler,
    position="bottom")``. Audit middleware (registered separately via
    :data:`persistence.effect._audit_stack.CANONICAL_AUDIT_WRAPPED_OPS`,
    which T1 of Phase 2.2b extended with the four :git/<sub> ops)
    handles the outer audit emit.
    """
    return Handler(
        name="git",
        wraps={":git/diff", ":git/status", ":git/log", ":git/commit"},
        clauses={
            ":git/diff": _diff_clause_factory(project_root, audit_handler_name),
            ":git/status": _status_clause_factory(project_root, audit_handler_name),
            ":git/log": _log_clause_factory(project_root, audit_handler_name),
            ":git/commit": _commit_clause_factory(project_root, audit_handler_name),
        },
    )


__all__ = ["GitArgValidation", "make_git_handler"]
