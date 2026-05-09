"""commit(branch_id) — session-level pointer swap (LD-1, R0-fold B4)."""
from __future__ import annotations
import datetime as dt

import pytest

from persistence.coder import Coder, _CoderSteeringSession
from persistence.effect.handlers import make_callable_llm_handler
from persistence.sdk import Substrate


def _done_call_fn():
    def call_fn(*, model, messages, tools=None, temperature=None, max_tokens=None):
        return {
            "tool_calls": [{
                "input": {
                    "kind": "act",
                    "confidence": 0.9,
                    "payload": {"done": True},
                },
            }],
            "text": "",
        }
    return call_fn


def test_commit_swaps_active_branch_id():
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        b1 = session.branch({"d": "1"})
        assert session.active_branch_id == "parent"
        session.commit(b1)
        assert session.active_branch_id == b1


def test_commit_unknown_branch_id_raises():
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        with pytest.raises(KeyError):
            session.commit("nonexistent")


def test_commit_does_not_mutate_parent_db():
    """LD-1 acceptance: parent DB stays unmodified after commit."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        t0 = dt.datetime.now(dt.timezone.utc)
        s.fact.transact([{"e": "x", "a": ":k", "v": "v0", "valid_from": t0}])
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)

        parent_pre = list(s._db.log())

        b1 = session.branch({"d": "1"})
        session.commit(b1)

        parent_post = list(s._db.log())
        assert parent_pre == parent_post


def test_commit_to_parent_after_commit_to_child():
    """Operator can switch back to parent after committing to child."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        b1 = session.branch({"d": "1"})
        session.commit(b1)
        session.commit("parent")
        assert session.active_branch_id == "parent"
