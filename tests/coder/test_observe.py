"""Phase 2.2a G3 — _observe body coverage."""
from __future__ import annotations
import datetime as dt
import json

import pytest
from hypothesis import given, settings, strategies as st

from persistence.coder._session import Coder
from persistence.coder._types import Observation
from persistence.effect.canonical import canonical_dumps
from persistence.sdk import Substrate


def _transact_decision(s: Substrate, kind: str, confidence: float) -> str:
    e = f"d-{dt.datetime.now().timestamp()}-{kind}"
    s.fact.transact([{
        "e": e, "a": ":llm/decision",
        "v": canonical_dumps({"kind": kind, "confidence": confidence, "payload": {}}),
        "valid_from": dt.datetime.now(dt.timezone.utc),
    }])
    return e


def _transact_action(s: Substrate, op: str) -> str:
    e = f"a-{dt.datetime.now().timestamp()}-{op}"
    s.fact.transact([{
        "e": e, "a": ":act/result",
        "v": canonical_dumps({"op": op, "args_hash": "h", "result_summary": None,
                              "error": None, "latency_ms": 0}),
        "valid_from": dt.datetime.now(dt.timezone.utc),
    }])
    return e


def test_observe_empty_substrate():
    s = Substrate.open("memory")
    coder = Coder(task="t", substrate=s)
    coder._session_start_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    coder._iter_count = 0
    obs = coder._observe()
    assert obs.iter_count == 0
    assert obs.recent_decisions == ()
    assert obs.recent_actions == ()
    s.close()


def test_observe_returns_trailing_three():
    s = Substrate.open("memory")
    coder = Coder(task="t", substrate=s)
    coder._session_start_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    coder._iter_count = 3
    for kind in ["act", "act", "act"]:
        _transact_decision(s, kind, 0.9)
    obs = coder._observe()
    assert len(obs.recent_decisions) == 3
    assert obs.iter_count == 3
    s.close()


def test_observe_caps_at_observe_depth():
    s = Substrate.open("memory")
    coder = Coder(task="t", substrate=s)
    coder._session_start_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    coder._iter_count = 7
    for _ in range(7):
        _transact_decision(s, "act", 0.9)
    obs = coder._observe()
    assert len(obs.recent_decisions) == 5  # observe_depth default
    s.close()


def test_observe_partitions_decisions_and_actions():
    s = Substrate.open("memory")
    coder = Coder(task="t", substrate=s)
    coder._session_start_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    coder._iter_count = 4
    _transact_decision(s, "act", 0.9)
    _transact_action(s, ":fs/read")
    _transact_decision(s, "act", 0.8)
    _transact_action(s, ":fs/write")
    obs = coder._observe()
    assert len(obs.recent_decisions) == 2
    assert len(obs.recent_actions) == 2
    s.close()


def test_observe_iter_count_reflects_field():
    s = Substrate.open("memory")
    coder = Coder(task="t", substrate=s)
    coder._session_start_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    coder._iter_count = 17
    obs = coder._observe()
    assert obs.iter_count == 17
    s.close()


def test_observe_excludes_pre_session_datoms():
    s = Substrate.open("memory")
    _transact_decision(s, "act", 0.9)  # BEFORE session start
    coder = Coder(task="t", substrate=s)
    coder._session_start_dt = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1)  # future boundary
    coder._iter_count = 0
    obs = coder._observe()
    assert obs.recent_decisions == ()
    s.close()
