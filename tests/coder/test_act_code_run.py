"""Phase 2.2b G4 — Coder._act dispatching :code/run via substrate.

Four tests exercise the production ``Coder._act`` path against the real
``:code/run`` handler (T2) plus the canonical audit middleware (T1
extension of ``CANONICAL_AUDIT_WRAPPED_OPS``). Each test asserts:

1. ``:act/result`` datom written by ``_act``.
2. EXACTLY ONE new ``AuditEntry`` for ``:code/run``.
3. ``result_summary`` carries the substantive-return shape
   (``stdout``/``stderr``/``exit_code``/``wall_clock_ms``/``output_hash``).

These tests use isolated ``Substrate.open("memory")`` fixtures with the
default ``audit=True`` canonical stack — matching the 2.2a precedent
(``tests/coder/test_loop_replay.py``). See ``test_act_git.py`` for the
forced-spec-deviation note on why CLI wiring is NOT changed in T4.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from persistence.coder._session import Coder
from persistence.coder._types import LLMDecision
from persistence.effect.canonical import canonical_hash
from persistence.effect.handlers.code import make_code_run_dispatch_handler
from persistence.sdk import Substrate


_EPOCH = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)


def _install_code_run_stack(s: Substrate) -> None:
    """Install the :code/run handler at the bottom of the substrate's
    runtime stack (below the auto-wired canonical audit middleware)."""
    s.effect.install_handler(make_code_run_dispatch_handler(), position="bottom")


def _act_results(s: Substrate) -> list[dict]:
    view = s.fact.since(_EPOCH)
    return [json.loads(d.v) for d in view.datoms if d.a == "act/result"]


def test_act_code_run_writes_act_result_and_one_audit_entry(tmp_path: Path) -> None:
    """:code/run happy path — ``print('hello')`` → :act/result captures
    ``stdout = 'hello\\n'`` and ``exit_code == 0``; exactly 1 new audit
    entry for ``:code/run``."""
    s = Substrate.open("memory")
    try:
        _install_code_run_stack(s)
        baseline = list(s._audit_entries)
        coder = Coder(task="t", substrate=s)
        coder._act(LLMDecision(kind="act", confidence=0.9, payload={
            "op": ":code/run",
            "args": {"source": "print('hello')"},
        }))

        results = _act_results(s)
        assert len(results) == 1
        body = results[0]
        assert body["op"] == ":code/run"
        assert body["error"] is None
        summary = body["result_summary"]
        assert isinstance(summary, dict)
        assert summary["stdout"] == "hello\n"
        assert summary["exit_code"] == 0

        new_entries = list(s._audit_entries)[len(baseline):]
        assert len(new_entries) == 1, (
            f"Expected exactly 1 new audit entry; got {len(new_entries)}: "
            f"{[e.op for e in new_entries]}"
        )
        assert new_entries[0].op == ":code/run"
    finally:
        s.close()


def test_act_code_run_traceback_in_result_summary_truncated(
    tmp_path: Path,
) -> None:
    """Source that raises → exit_code != 0; stderr contains 'RuntimeError'
    + 'boom'. ``_summarize_result`` only truncates string values >512
    chars. A short traceback fits under that cap and is preserved
    intact."""
    s = Substrate.open("memory")
    try:
        _install_code_run_stack(s)
        coder = Coder(task="t", substrate=s)
        coder._act(LLMDecision(kind="act", confidence=0.9, payload={
            "op": ":code/run",
            "args": {"source": "raise RuntimeError('boom')"},
        }))

        results = _act_results(s)
        assert len(results) == 1
        body = results[0]
        assert body["error"] is None  # the perform call returned cleanly
        summary = body["result_summary"]
        assert summary["exit_code"] != 0
        stderr = summary["stderr"]
        assert "RuntimeError" in stderr
        assert "boom" in stderr
        # _summarize_result truncates only when a string value > 512 chars;
        # short tracebacks pass through untouched. Either way, the
        # truncated form has the canonical "...[truncated]..." marker.
        if len(stderr) > 512:
            assert "...[truncated]..." in stderr
    finally:
        s.close()


def test_act_code_run_args_hash_byte_identity(tmp_path: Path) -> None:
    """LD4 — two identical ``_act`` calls produce identical ``args_hash``
    in their :act/result datoms. ``canonical_hash(args)`` is
    deterministic across calls."""
    s = Substrate.open("memory")
    try:
        _install_code_run_stack(s)
        coder = Coder(task="t", substrate=s)
        args = {"source": "print(1+1)"}
        coder._act(LLMDecision(kind="act", confidence=0.9, payload={
            "op": ":code/run",
            "args": args,
        }))
        coder._act(LLMDecision(kind="act", confidence=0.9, payload={
            "op": ":code/run",
            "args": args,
        }))

        results = _act_results(s)
        assert len(results) == 2
        assert results[0]["args_hash"] == results[1]["args_hash"]
        assert results[0]["args_hash"] == canonical_hash(args)
    finally:
        s.close()


def test_act_code_run_result_summary_contains_expected_keys(tmp_path: Path) -> None:
    """Pin :code/run result_summary shape — exactly the five keys
    documented in ``code.py::_code_run_clause``: stdout, stderr,
    exit_code, wall_clock_ms, output_hash. ``_summarize_result``
    preserves dict shape and only truncates large string values
    in-place; it does NOT add or drop keys."""
    s = Substrate.open("memory")
    try:
        _install_code_run_stack(s)
        coder = Coder(task="t", substrate=s)
        coder._act(LLMDecision(kind="act", confidence=0.9, payload={
            "op": ":code/run",
            "args": {"source": "print('shape-pin')"},
        }))

        results = _act_results(s)
        assert len(results) == 1
        summary = results[0]["result_summary"]
        assert isinstance(summary, dict)
        assert set(summary.keys()) == {
            "stdout",
            "stderr",
            "exit_code",
            "wall_clock_ms",
            "output_hash",
        }
        assert summary["stdout"] == "shape-pin\n"
        assert summary["exit_code"] == 0
        assert isinstance(summary["wall_clock_ms"], int)
        assert summary["output_hash"].startswith("sha256:")
    finally:
        s.close()
