"""G3 — Phase 2.3d LD0: snapshot/context_at don't mutate active_db.

R0-fold B3: read-only ops still emit 2 :repl/* datoms each (audit chain
traffic), but the active_db (the substrate fact store) is unchanged.
"""
from __future__ import annotations

import datetime as dt

from persistence.coder import Coder, _CoderSteeringSession
from persistence.effect.handlers import make_callable_llm_handler
from persistence.sdk import Substrate


def _make_done_call_fn():
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


def test_snapshot_returns_last_n_datoms():
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_make_done_call_fn()))
        # Pre-seed some datoms so snapshot has something to read.
        for i in range(5):
            s.fact.transact([
                {"e": f"e{i}", "a": ":k", "v": f"v{i}",
                 "valid_from": dt.datetime.now(dt.timezone.utc)}
            ])
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)

        snap = session.snapshot(n=3)
        assert isinstance(snap, list)
        assert len(snap) == 3, f"expected last 3 datoms, got {len(snap)}"


def test_snapshot_default_n_is_50():
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_make_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        snap = session.snapshot()  # default n=50
        assert isinstance(snap, list)


def test_snapshot_does_not_mutate_active_db():
    """R0-fold B3: read-only op leaves active_db unchanged."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_make_done_call_fn()))
        s.fact.transact([
            {"e": "x", "a": ":k", "v": "v", "valid_from": dt.datetime.now(dt.timezone.utc)}
        ])
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        # Datom is frozen + slots — direct list equality is structural.
        # canonical_dumps cannot handle dataclasses (raises TypeError) so
        # we compare by list-of-Datom equality directly.
        pre = list(s._db.log())
        _ = session.snapshot(n=10)
        post = list(s._db.log())
        assert pre == post, "snapshot mutated active_db"


def test_context_at_returns_dbview_at_t():
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_make_done_call_fn()))
        t0 = dt.datetime.now(dt.timezone.utc)
        s.fact.transact([{"e": "x", "a": ":k", "v": "v0", "valid_from": t0}])
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)

        view = session.context_at(t=t0)
        assert hasattr(view, "datoms"), "context_at didn't return a DBView"


def test_context_at_does_not_mutate_active_db():
    """R0-fold B3: read-only op."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_make_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        t0 = dt.datetime.now(dt.timezone.utc)
        pre = list(s._db.log())
        _ = session.context_at(t=t0)
        post = list(s._db.log())
        assert pre == post


def test_snapshot_branch_id_kwarg_active_default():
    """R0-fold I2: snapshot accepts branch_id kwarg; default 'active'."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_make_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        snap_default = session.snapshot(n=10)
        snap_active = session.snapshot(n=10, branch_id="active")
        snap_parent = session.snapshot(n=10, branch_id="parent")
        assert snap_default == snap_active == snap_parent
