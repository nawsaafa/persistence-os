"""Phase 2.2b G3 — Coder._act dispatching :git/* via substrate.

Four tests exercise the production ``Coder._act`` path against the real
``:git/*`` handler stack (T3) plus the canonical audit middleware (T1
extension of ``CANONICAL_AUDIT_WRAPPED_OPS``). Each test asserts:

1. ``:act/result`` datom written by ``_act`` (via ``s.fact.transact``).
2. EXACTLY ONE new ``AuditEntry`` for the outer ``:git/<sub>`` op
   (the inner ``:shell/exec`` audit is suppressed by the
   ``with mask("audit")`` block inside the git clause).

These tests use isolated ``Substrate.open("memory")`` fixtures with the
default ``audit=True`` canonical stack — matching the 2.2a precedent
(``tests/coder/test_loop_replay.py``). The plan-T4 step 4.4 wiring of
``:fs/*`` and ``:shell/exec`` in ``__main__.py`` does NOT exist; only
the LLM provider handler is installed there. CLI wiring of fs/shell/
git/code-run is deferred to 2.4a hardening or a follow-up task. See
the commit body for forced-spec-deviation notes.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

import pytest

from persistence.coder._session import Coder
from persistence.coder._types import LLMDecision
from persistence.effect.handlers.fs import FsCapabilityDenied
from persistence.effect.handlers.git import make_git_handler
from persistence.effect.handlers.shell import make_shell_handler
from persistence.sdk import Substrate


_EPOCH = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)


def _init_repo(repo: Path) -> None:
    """Initialise a minimal git repo with one committed file at HEAD.

    Sets ``user.email`` / ``user.name`` so subsequent commits don't
    fail with ``Author identity unknown``. Required for both diff
    (HEAD must exist) and commit tests.
    """
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "x.txt").write_text("v0\n")
    subprocess.run(
        ["git", "add", "x.txt"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _install_git_stack(s: Substrate, project_root: Path) -> None:
    """Install :shell/exec + :git/* handlers at the bottom of the
    substrate's runtime stack — both go below the canonical audit
    middleware (which the substrate auto-wires under ``audit=True``)."""
    s.effect.install_handler(make_shell_handler(), position="bottom")
    s.effect.install_handler(
        make_git_handler(project_root=project_root), position="bottom"
    )


def _act_results(s: Substrate) -> list[dict]:
    view = s.fact.since(_EPOCH)
    return [json.loads(d.v) for d in view.datoms if d.a == "act/result"]


def test_act_git_diff_writes_act_result_and_one_audit_entry(tmp_path: Path) -> None:
    """:git/diff happy path — :act/result datom written + exactly 1 new
    audit entry for ``:git/diff``. Inner ``:shell/exec`` audit is masked
    out by the git clause's ``with mask("audit")`` block."""
    project_root = tmp_path / "repo"
    project_root.mkdir()
    _init_repo(project_root)

    s = Substrate.open("memory")
    try:
        _install_git_stack(s, project_root)
        baseline = list(s._audit_entries)
        coder = Coder(task="t", substrate=s)
        coder._act(LLMDecision(kind="act", confidence=0.9, payload={
            "op": ":git/diff",
            "args": {"cwd": str(project_root)},
        }))

        results = _act_results(s)
        assert len(results) == 1
        body = results[0]
        assert body["op"] == ":git/diff"
        assert body["error"] is None
        # _summarize_result preserves dict shape; :shell/exec returns
        # {exit, stdout, stderr, wall_clock_ms}.
        summary = body["result_summary"]
        assert isinstance(summary, dict)
        assert {"exit", "stdout", "stderr"} <= set(summary.keys())

        new_entries = list(s._audit_entries)[len(baseline):]
        assert len(new_entries) == 1, (
            f"Expected exactly 1 new audit entry; got {len(new_entries)}: "
            f"{[e.op for e in new_entries]}"
        )
        assert new_entries[0].op == ":git/diff"
    finally:
        s.close()


def test_act_git_commit_writes_act_result_and_one_audit_entry(
    tmp_path: Path,
) -> None:
    """:git/commit happy path — stage a fresh file, commit via _act,
    assert :act/result.exit == 0 and exactly 1 new audit entry for
    :git/commit."""
    project_root = tmp_path / "repo"
    project_root.mkdir()
    _init_repo(project_root)
    # Stage a NEW file so the commit has something to commit.
    (project_root / "y.txt").write_text("y0\n")
    subprocess.run(
        ["git", "add", "y.txt"], cwd=project_root, check=True, capture_output=True
    )

    s = Substrate.open("memory")
    try:
        _install_git_stack(s, project_root)
        baseline = list(s._audit_entries)
        coder = Coder(task="t", substrate=s)
        coder._act(LLMDecision(kind="act", confidence=0.9, payload={
            "op": ":git/commit",
            "args": {
                "message": "add y",
                "paths": ["y.txt"],
                "cwd": str(project_root),
            },
        }))

        results = _act_results(s)
        assert len(results) == 1
        body = results[0]
        assert body["op"] == ":git/commit"
        assert body["error"] is None
        summary = body["result_summary"]
        assert summary["exit"] == 0, (
            f"Expected git commit to succeed; got exit={summary['exit']}, "
            f"stderr={summary['stderr']!r}"
        )

        new_entries = list(s._audit_entries)[len(baseline):]
        assert len(new_entries) == 1, (
            f"Expected exactly 1 new audit entry; got {len(new_entries)}: "
            f"{[e.op for e in new_entries]}"
        )
        assert new_entries[0].op == ":git/commit"
    finally:
        s.close()


def test_act_git_commit_nothing_to_commit_returns_exit_one_passthrough(
    tmp_path: Path,
) -> None:
    """:git/commit with no staged changes returns ``exit != 0`` from
    git — but :shell/exec returns the result dict (exit + stderr),
    NOT raises. So _act records :act/result with the non-zero exit
    in ``result_summary`` and ``error`` is still None (the perform
    call returned cleanly)."""
    project_root = tmp_path / "repo"
    project_root.mkdir()
    _init_repo(project_root)

    s = Substrate.open("memory")
    try:
        _install_git_stack(s, project_root)
        baseline = list(s._audit_entries)
        coder = Coder(task="t", substrate=s)
        # x.txt is already committed at HEAD via _init_repo; with no
        # staged changes git commit exits non-zero with "nothing to commit".
        coder._act(LLMDecision(kind="act", confidence=0.9, payload={
            "op": ":git/commit",
            "args": {
                "message": "noop",
                "paths": ["x.txt"],
                "cwd": str(project_root),
            },
        }))

        results = _act_results(s)
        assert len(results) == 1
        body = results[0]
        assert body["op"] == ":git/commit"
        assert body["error"] is None  # :shell/exec returned cleanly
        summary = body["result_summary"]
        assert summary["exit"] != 0, (
            f"Expected non-zero exit from no-op commit; got {summary['exit']}"
        )
        # Git's "nothing to commit" message lands in stdout (clean tree)
        # OR stderr depending on subcommand path. Accept either.
        combined = (summary.get("stdout", "") or "") + (
            summary.get("stderr", "") or ""
        )
        assert "nothing to commit" in combined or "no changes added" in combined, (
            f"Expected 'nothing to commit' in output; got "
            f"stdout={summary.get('stdout')!r}, stderr={summary.get('stderr')!r}"
        )

        new_entries = list(s._audit_entries)[len(baseline):]
        assert len(new_entries) == 1, (
            f"Expected exactly 1 new audit entry; got {len(new_entries)}: "
            f"{[e.op for e in new_entries]}"
        )
        assert new_entries[0].op == ":git/commit"
    finally:
        s.close()


def test_act_git_diff_cwd_outside_project_root_raises_and_records_error(
    tmp_path: Path,
) -> None:
    """:git/diff with cwd outside project_root raises FsCapabilityDenied
    inside the clause; ``_act`` catches at line 226 → records
    :act/result with ``error="FsCapabilityDenied: ..."`` AND
    ``result_summary=None``, then re-raises. The audit middleware
    emits an entry with verdict='error' for the failed :git/diff."""
    project_root = tmp_path / "repo"
    project_root.mkdir()
    _init_repo(project_root)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    s = Substrate.open("memory")
    try:
        _install_git_stack(s, project_root)
        baseline = list(s._audit_entries)
        coder = Coder(task="t", substrate=s)
        with pytest.raises(FsCapabilityDenied):
            coder._act(LLMDecision(kind="act", confidence=0.9, payload={
                "op": ":git/diff",
                "args": {"cwd": str(elsewhere)},
            }))

        # _act recorded :act/result BEFORE re-raising (provenance survives
        # failure — 2.2a R0 B1 invariant).
        results = _act_results(s)
        assert len(results) == 1
        body = results[0]
        assert body["op"] == ":git/diff"
        assert body["result_summary"] is None
        assert body["error"] is not None
        assert body["error"].startswith("FsCapabilityDenied: ")

        # Audit middleware also emitted an entry for the failed call —
        # verdict='error' branch in audit.py finally block.
        new_entries = list(s._audit_entries)[len(baseline):]
        assert len(new_entries) == 1, (
            f"Expected exactly 1 new audit entry on the failed call; got "
            f"{len(new_entries)}: {[e.op for e in new_entries]}"
        )
        assert new_entries[0].op == ":git/diff"
        assert new_entries[0].verdict == "error"
    finally:
        s.close()
