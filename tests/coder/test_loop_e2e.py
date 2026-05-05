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


def test_kind_plan_halts_loop_with_stub_raise(tmp_path: Path):
    """kind=plan raises CoderStubNotImplemented(2.3a); :llm/decision provenance survives."""
    s = Substrate.open("memory")
    s.effect.install_handler(
        make_callable_llm_handler(
            call_fn=_scripted_decisions([
                {"kind": "plan", "confidence": 0.9, "payload": {}},
            ])
        ),
        position="bottom",
    )
    coder = Coder(task="t", substrate=s)
    with pytest.raises(CoderStubNotImplemented, match="2.3a"):
        coder.run()
    # :llm/decision MUST be transacted before raise (provenance survives)
    view = s.fact.since(dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc))
    decisions = [d for d in view.datoms if d.a == "llm/decision"]  # bare
    assert len(decisions) == 1
    s.close()


def test_kind_branch_halts_loop_with_stub_raise(tmp_path: Path):
    """kind=branch raises CoderStubNotImplemented(2.3b)."""
    s = Substrate.open("memory")
    s.effect.install_handler(
        make_callable_llm_handler(
            call_fn=_scripted_decisions([
                {"kind": "branch", "confidence": 0.9, "payload": {}},
            ])
        ),
        position="bottom",
    )
    coder = Coder(task="t", substrate=s)
    with pytest.raises(CoderStubNotImplemented, match="2.3b"):
        coder.run()
    s.close()
