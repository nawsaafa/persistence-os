"""Phase 2.2a G5 — coder loop e2e. THE load-bearing acceptance signal."""
from __future__ import annotations
import datetime as dt
from pathlib import Path

import pytest

from persistence.coder._session import Coder, CoderStubNotImplemented
from persistence.effect.handlers.fs import make_fs_handler
from persistence.effect.handlers.callable import make_callable_llm_handler
from persistence.sdk import Substrate


def _scripted_decisions(decisions: list[dict]):
    """Returns a call_fn that yields one decision per call.

    Pre-flag B resolution: make_callable_llm_handler calls call_fn with
    kwargs only: call_fn(model=..., messages=..., tools=..., temperature=...,
    max_tokens=...). Inner def must accept **kwargs and ignore them.
    """
    iterator = iter(decisions)

    def _call_fn(*, model, messages, tools=None, temperature=None, max_tokens=None):
        return {"tool_calls": [{"input": next(iterator)}], "text": ""}

    return _call_fn


def _scripted_decisions_capturing_messages(decisions: list[dict], capture: list):
    """Variant of _scripted_decisions that appends each call's `messages`
    to `capture` (caller-supplied list). Lets tests assert what content
    the LLM saw on iter N.
    """
    iterator = iter(decisions)

    def _call_fn(*, model, messages, tools=None, temperature=None, max_tokens=None):
        capture.append(messages)
        return {"tool_calls": [{"input": next(iterator)}], "text": ""}

    return _call_fn


def test_coder_loop_runs_two_iters_emits_act_results(tmp_path: Path):
    """Load-bearing: the 2.2a SHIP signal. Maps to design § 2 acceptance.

    3 iters: iter 1 :fs/read, iter 2 :fs/write, iter 3 :fs/read+done.
    Asserts: exactly 3 :llm/decision + 2 :act/result + 5 audit entries
    (3 :llm/call wrapped + 1 :fs/read wrapped + 1 :fs/write wrapped).

    Pre-flag A: d.a uses bare names (no leading colon) — Datom.__post_init__
    strips leading colon per datom.py:175 lstrip(":").
    Pre-flag C: AuditEntry.op KEEPS leading colon — enforced by
    AuditEntry.__post_init__ which raises ValueError if not op.startswith(":").
    So audit op filter keys use ":llm/call", ":fs/read", ":fs/write".
    """
    project_root = tmp_path / "p"
    scratch_dir = tmp_path / "s"
    project_root.mkdir()
    scratch_dir.mkdir()
    (project_root / "input.txt").write_text("hello\n")

    s = Substrate.open("memory")
    s.effect.install_handler(
        make_fs_handler(project_root=project_root, scratch_dir=scratch_dir),
        position="bottom",
    )
    s.effect.install_handler(
        make_callable_llm_handler(
            call_fn=_scripted_decisions([
                {
                    "kind": "act",
                    "confidence": 0.9,
                    "payload": {
                        "op": ":fs/read",
                        "args": {"path": str(project_root / "input.txt")},
                    },
                },
                {
                    "kind": "act",
                    "confidence": 0.9,
                    "payload": {
                        "op": ":fs/write",
                        "args": {
                            "path": str(scratch_dir / "out.txt"),
                            "bytes_or_text": "summary",
                        },
                    },
                },
                {
                    "kind": "act",
                    "confidence": 0.9,
                    "payload": {
                        "op": ":fs/read",
                        "args": {"path": str(scratch_dir / "out.txt")},
                        "done": True,
                    },
                },
            ])
        ),
        position="bottom",
    )

    coder = Coder(task="read input, write summary", substrate=s, max_iters=10)
    coder.run()

    view = s.fact.since(dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc))
    decisions = [
        d for d in view.datoms if d.a == "llm/decision" and d.op == "assert"
    ]  # bare — Datom.__post_init__ strips leading colon
    actions = [
        d for d in view.datoms if d.a == "act/result" and d.op == "assert"
    ]  # bare
    audit_entries = list(s.audit.entries())

    assert len(decisions) == 3
    assert len(actions) == 2  # iter 3 done short-circuits _act

    # Audit chain: 3 :llm/call wrapped + 1 :fs/read wrapped + 1 :fs/write wrapped = 5
    # AuditEntry.op keeps leading colon (effect-level, not fact-level).
    op_counts: dict[str, int] = {}
    for ae in audit_entries:
        if hasattr(ae, "op"):  # AuditEntry instances only; skip escape-hatch dicts
            op_counts[ae.op] = op_counts.get(ae.op, 0) + 1

    assert op_counts.get(":llm/call", 0) == 3
    assert op_counts.get(":fs/read", 0) == 1
    assert op_counts.get(":fs/write", 0) == 1
    assert sum(op_counts.values()) == 5

    s.close()


def test_max_iters_cap_honored(tmp_path: Path):
    """Loop stops at max_iters even when LLM keeps emitting decisions."""
    s = Substrate.open("memory")
    project_root = tmp_path / "p"
    scratch_dir = tmp_path / "s"
    project_root.mkdir()
    scratch_dir.mkdir()
    s.effect.install_handler(
        make_fs_handler(project_root=project_root, scratch_dir=scratch_dir),
        position="bottom",
    )
    s.effect.install_handler(
        make_callable_llm_handler(
            call_fn=_scripted_decisions(
                [
                    {
                        "kind": "act",
                        "confidence": 0.9,
                        "payload": {
                            "op": ":fs/glob",
                            "args": {
                                "pattern": "*",
                                "root": str(project_root),
                                "flags": {},
                            },
                        },
                    }
                ]
                * 30
            )
        ),
        position="bottom",
    )
    coder = Coder(task="t", substrate=s, max_iters=5)
    coder.run()
    view = s.fact.since(dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc))
    decisions = [d for d in view.datoms if d.a == "llm/decision"]  # bare
    assert len(decisions) == 5
    s.close()


def test_done_short_circuits_before_act(tmp_path: Path):
    """done=True in payload exits loop BEFORE _act — no :act/result emitted."""
    s = Substrate.open("memory")
    project_root = tmp_path / "p"
    scratch_dir = tmp_path / "s"
    project_root.mkdir()
    scratch_dir.mkdir()
    s.effect.install_handler(
        make_fs_handler(project_root=project_root, scratch_dir=scratch_dir),
        position="bottom",
    )
    s.effect.install_handler(
        make_callable_llm_handler(
            call_fn=_scripted_decisions([
                {
                    "kind": "act",
                    "confidence": 0.9,
                    "payload": {
                        "op": ":fs/glob",
                        "args": {
                            "pattern": "*",
                            "root": str(project_root),
                            "flags": {},
                        },
                        "done": True,
                    },
                },
            ])
        ),
        position="bottom",
    )
    coder = Coder(task="t", substrate=s)
    coder.run()
    view = s.fact.since(dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc))
    actions = [d for d in view.datoms if d.a == "act/result"]  # bare
    assert len(actions) == 0  # done short-circuits BEFORE _act
    s.close()


# test_kind_plan_halts_loop_with_stub_raise removed — _escalate_plan filled in Phase 2.3a T7.


# test_kind_branch_halts_loop_with_stub_raise removed — _escalate_branch filled in Phase 2.3b T8.


# ---------------------------------------------------------------------------
# Phase 2.2b G5b — 5-iter e2e + LD3 traceback proof + cwd-denied provenance
# ---------------------------------------------------------------------------


def test_coder_loop_5_iters_emits_9_audit_entries_g5b(tmp_path: Path):
    """G5b — 5-iter scripted scenario. Asserts exactly 9 audit entries:
    5 :llm/call + 1 :fs/read + 1 :code/run + 1 :git/diff + 1 :git/commit.
    Inner :shell/exec calls from the :git/* clauses are masked
    (LD2 contract); they MUST NOT appear as audit entries.
    """
    import subprocess

    from persistence.effect.handlers.code import make_code_run_dispatch_handler
    from persistence.effect.handlers.git import make_git_handler
    from persistence.effect.handlers.shell import make_shell_handler

    project_root = tmp_path / "p"
    scratch_dir = tmp_path / "s"
    project_root.mkdir()
    scratch_dir.mkdir()
    (project_root / "input.txt").write_text("hello\n")

    # Init real git repo: user config + initial commit (HEAD must exist
    # for :git/diff against HEAD to work).
    subprocess.run(
        ["git", "init"], cwd=project_root, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=project_root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=project_root, check=True
    )
    (project_root / "x.txt").write_text("v1\n")
    subprocess.run(
        ["git", "add", "x.txt"], cwd=project_root, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=project_root,
        check=True,
        capture_output=True,
    )
    # Modify file so :git/diff has output:
    (project_root / "x.txt").write_text("v2\n")

    s = Substrate.open("memory")
    s.effect.install_handler(
        make_fs_handler(project_root=project_root, scratch_dir=scratch_dir),
        position="bottom",
    )
    s.effect.install_handler(make_shell_handler(), position="bottom")
    s.effect.install_handler(
        make_git_handler(project_root=project_root), position="bottom"
    )
    s.effect.install_handler(make_code_run_dispatch_handler(), position="bottom")

    s.effect.install_handler(
        make_callable_llm_handler(
            call_fn=_scripted_decisions([
                # iter 1: :fs/read
                {
                    "kind": "act",
                    "confidence": 0.9,
                    "payload": {
                        "op": ":fs/read",
                        "args": {"path": str(project_root / "input.txt")},
                    },
                },
                # iter 2: :code/run
                {
                    "kind": "act",
                    "confidence": 0.9,
                    "payload": {
                        "op": ":code/run",
                        "args": {"source": "print('coder ran')"},
                    },
                },
                # iter 3: :git/diff
                {
                    "kind": "act",
                    "confidence": 0.9,
                    "payload": {
                        "op": ":git/diff",
                        "args": {"cwd": str(project_root)},
                    },
                },
                # iter 4: :git/commit
                {
                    "kind": "act",
                    "confidence": 0.9,
                    "payload": {
                        "op": ":git/commit",
                        "args": {
                            "message": "v2",
                            "paths": ["x.txt"],
                            "cwd": str(project_root),
                        },
                    },
                },
                # iter 5: done
                {
                    "kind": "act",
                    "confidence": 0.9,
                    "payload": {
                        "op": ":fs/read",
                        "args": {"path": str(project_root / "input.txt")},
                        "done": True,
                    },
                },
            ])
        ),
        position="bottom",
    )

    coder = Coder(task="exercise all 4 ops", substrate=s, max_iters=10)
    coder.run()

    view = s.fact.since(dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc))
    decisions = [
        d for d in view.datoms if d.a == "llm/decision" and d.op == "assert"
    ]
    actions = [
        d for d in view.datoms if d.a == "act/result" and d.op == "assert"
    ]
    audit_entries = list(s.audit.entries())

    assert len(decisions) == 5  # 5 iters, all "act"
    assert len(actions) == 4  # iter 5 short-circuits via done=True

    op_counts: dict[str, int] = {}
    for ae in audit_entries:
        if hasattr(ae, "op"):
            op_counts[ae.op] = op_counts.get(ae.op, 0) + 1

    assert op_counts.get(":llm/call", 0) == 5
    assert op_counts.get(":fs/read", 0) == 1
    assert op_counts.get(":code/run", 0) == 1
    assert op_counts.get(":git/diff", 0) == 1
    assert op_counts.get(":git/commit", 0) == 1
    # CRITICAL: :shell/exec MUST be masked inside :git/* clauses (LD2).
    assert op_counts.get(":shell/exec", 0) == 0
    assert sum(op_counts.values()) == 9

    s.close()


def test_coder_loop_code_run_traceback_reaches_iter3_prompt_verbatim_g5b(
    tmp_path: Path,
):
    """G5b — LD3 e2e proof. iter 2 dispatches :code/run with source that
    raises RuntimeError. The next iter's prompt MUST contain the stderr
    traceback verbatim (proves _render_latest_action bypasses the [:200]
    cap and the upstream _summarize_result 512-char cap is wide enough
    for typical Python tracebacks of <512 chars).
    """
    from persistence.effect.handlers.code import make_code_run_dispatch_handler

    project_root = tmp_path / "p"
    scratch_dir = tmp_path / "s"
    project_root.mkdir()
    scratch_dir.mkdir()
    (project_root / "x.txt").write_text("warmup\n")

    s = Substrate.open("memory")
    s.effect.install_handler(
        make_fs_handler(project_root=project_root, scratch_dir=scratch_dir),
        position="bottom",
    )
    s.effect.install_handler(make_code_run_dispatch_handler(), position="bottom")

    captured_messages: list = []
    s.effect.install_handler(
        make_callable_llm_handler(
            call_fn=_scripted_decisions_capturing_messages(
                [
                    # iter 1: :fs/read warmup
                    {
                        "kind": "act",
                        "confidence": 0.9,
                        "payload": {
                            "op": ":fs/read",
                            "args": {"path": str(project_root / "x.txt")},
                        },
                    },
                    # iter 2: :code/run raises
                    {
                        "kind": "act",
                        "confidence": 0.9,
                        "payload": {
                            "op": ":code/run",
                            "args": {
                                "source": "raise RuntimeError('SENTINEL_BOOM_2_2_B')"
                            },
                        },
                    },
                    # iter 3: done — but iter 3's prompt is built BEFORE _act
                    # is short-circuited, so iter 3 captures iter 2's :code/run
                    # result via _render_latest_action.
                    {
                        "kind": "act",
                        "confidence": 0.9,
                        "payload": {
                            "op": ":fs/read",
                            "args": {"path": str(project_root / "x.txt")},
                            "done": True,
                        },
                    },
                ],
                captured_messages,
            )
        ),
        position="bottom",
    )

    coder = Coder(task="trigger traceback", substrate=s, max_iters=10)
    coder.run()

    # Iter 3's prompt is captured_messages[2]. Extract the user-message text.
    iter3_prompt_text = captured_messages[2][0]["content"]
    # The traceback for `raise RuntimeError('SENTINEL_BOOM_2_2_B')` includes
    # the literal string "SENTINEL_BOOM_2_2_B" and "RuntimeError". With LD3,
    # both must appear in the prompt VERBATIM (not truncated to 200 chars).
    assert "SENTINEL_BOOM_2_2_B" in iter3_prompt_text
    assert "RuntimeError" in iter3_prompt_text
    # And the LD3 header line must be present:
    assert "Latest action output:" in iter3_prompt_text

    s.close()


def test_coder_loop_git_diff_cwd_denied_surfaces_error_g5b(tmp_path: Path):
    """G5b — :git/diff with cwd outside project_root raises FsCapabilityDenied.
    _act re-raises after recording :act/result with error field. The loop
    HALTS on the re-raise (no iter 4 to inspect).

    What we assert: :act/result for the failed :git/diff IS recorded
    (provenance survives), with error field populated.
    """
    import json

    from persistence.effect.handlers.fs import FsCapabilityDenied
    from persistence.effect.handlers.git import make_git_handler
    from persistence.effect.handlers.shell import make_shell_handler

    project_root = tmp_path / "p"
    elsewhere = tmp_path / "elsewhere"
    project_root.mkdir()
    elsewhere.mkdir()
    scratch_dir = tmp_path / "s"
    scratch_dir.mkdir()

    s = Substrate.open("memory")
    s.effect.install_handler(
        make_fs_handler(project_root=project_root, scratch_dir=scratch_dir),
        position="bottom",
    )
    s.effect.install_handler(make_shell_handler(), position="bottom")
    s.effect.install_handler(
        make_git_handler(project_root=project_root), position="bottom"
    )

    s.effect.install_handler(
        make_callable_llm_handler(
            call_fn=_scripted_decisions([
                # iter 1: :git/diff with cwd outside project_root
                {
                    "kind": "act",
                    "confidence": 0.9,
                    "payload": {
                        "op": ":git/diff",
                        "args": {"cwd": str(elsewhere)},
                    },
                },
            ])
        ),
        position="bottom",
    )

    coder = Coder(task="t", substrate=s, max_iters=5)
    with pytest.raises(FsCapabilityDenied):
        coder.run()

    # Provenance survives the raise — :act/result IS written.
    view = s.fact.since(dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc))
    actions = [d for d in view.datoms if d.a == "act/result" and d.op == "assert"]
    assert len(actions) == 1
    body = json.loads(actions[0].v)
    assert body["error"] is not None
    assert body["error"].startswith("FsCapabilityDenied: ")
    assert body["result_summary"] is None

    s.close()
