"""Phase 2.2a G4 — _act dispatch + :act/result emission."""
from __future__ import annotations
import datetime as dt
import json

import pytest

from persistence.coder._session import Coder
from persistence.coder._types import LLMDecision
from persistence.effect.canonical import canonical_hash
from persistence.effect.runtime import Handler
from persistence.sdk import Substrate


def _scripted(returns):
    """A handler whose op returns `returns` from a fixed dict.

    Substantive-return op: leaf handler returns dict directly (T2/T3
    pattern), NOT k(...) — calling k continues the stack and would
    raise Unhandled. Underscore-prefix unused params per project
    convention.
    """
    def _clause_factory(op_name):
        def _clause(args, _k, _ctx):
            return returns[op_name]
        return _clause
    return Handler(
        name="scripted",
        wraps=set(returns.keys()),
        clauses={op: _clause_factory(op) for op in returns},
    )


def test_act_dispatches_op_and_emits_act_result():
    s = Substrate.open("memory")
    s.effect.install_handler(_scripted({":fs/read": {"bytes_or_text": "x", "size": 1, "sha256": "h", "mtime": 0.0}}), position="bottom")
    coder = Coder(task="t", substrate=s)
    coder._act(LLMDecision(kind="act", confidence=0.9, payload={
        "op": ":fs/read",
        "args": {"path": "/tmp/x"},
    }))
    view = s.fact.since(dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc))
    results = [d for d in view.datoms if d.a == "act/result"]  # bare — Datom strips colon
    assert len(results) == 1
    body = json.loads(results[0].v)
    assert body["op"] == ":fs/read"
    assert body["error"] is None
    assert body["args_hash"] == canonical_hash({"path": "/tmp/x"})
    s.close()


def test_act_provenance_survives_perform_failure():
    s = Substrate.open("memory")
    def _raises(args, _k, _ctx): raise RuntimeError("boom")
    s.effect.install_handler(Handler(name="bad", wraps={":fs/read"},
                                     clauses={":fs/read": _raises}), position="bottom")
    coder = Coder(task="t", substrate=s)
    with pytest.raises(RuntimeError, match="boom"):
        coder._act(LLMDecision(kind="act", confidence=0.9, payload={
            "op": ":fs/read", "args": {"path": "/x"},
        }))
    view = s.fact.since(dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc))
    results = [d for d in view.datoms if d.a == "act/result"]  # bare
    assert len(results) == 1
    body = json.loads(results[0].v)
    assert body["error"].startswith("RuntimeError: boom")
    assert body["result_summary"] is None
    s.close()


def test_act_rejects_non_act_kind():
    s = Substrate.open("memory")
    coder = Coder(task="t", substrate=s)
    with pytest.raises(ValueError, match="kind="):
        coder._act(LLMDecision(kind="plan", confidence=0.9, payload={}))
    s.close()


def test_act_rejects_missing_op():
    s = Substrate.open("memory")
    coder = Coder(task="t", substrate=s)
    with pytest.raises(ValueError, match="missing/invalid op"):
        coder._act(LLMDecision(kind="act", confidence=0.9, payload={"args": {}}))
    s.close()


def test_act_rejects_non_string_op():
    s = Substrate.open("memory")
    coder = Coder(task="t", substrate=s)
    with pytest.raises(ValueError):
        coder._act(LLMDecision(kind="act", confidence=0.9, payload={"op": 42, "args": {}}))
    s.close()


def test_act_rejects_op_without_leading_colon():
    s = Substrate.open("memory")
    coder = Coder(task="t", substrate=s)
    with pytest.raises(ValueError):
        coder._act(LLMDecision(kind="act", confidence=0.9, payload={"op": "fs/read", "args": {}}))
    s.close()


def test_act_args_hash_matches_audit_chain_hash():
    """LD4 — :act/result.args_hash uses persistence.effect.canonical.canonical_hash,
    same helper as audit middleware. Hashes must agree byte-for-byte."""
    s = Substrate.open("memory")
    s.effect.install_handler(_scripted({":fs/read": {"x": 1}}), position="bottom")
    coder = Coder(task="t", substrate=s)
    args = {"path": "/tmp/y", "encoding": "utf-8"}
    coder._act(LLMDecision(kind="act", confidence=0.9, payload={
        "op": ":fs/read", "args": args,
    }))
    expected = canonical_hash(args)
    view = s.fact.since(dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc))
    body = json.loads([d for d in view.datoms if d.a == "act/result"][0].v)  # bare
    assert body["args_hash"] == expected
    s.close()


def test_act_records_latency_ms_non_negative():
    s = Substrate.open("memory")
    s.effect.install_handler(_scripted({":fs/read": {"x": 1}}), position="bottom")
    coder = Coder(task="t", substrate=s)
    coder._act(LLMDecision(kind="act", confidence=0.9, payload={
        "op": ":fs/read", "args": {},
    }))
    view = s.fact.since(dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc))
    body = json.loads([d for d in view.datoms if d.a == "act/result"][0].v)  # bare
    assert body["latency_ms"] >= 0
    s.close()
