"""G4 — Phase 2.3d LD-5: fold(probe) iterates parent + all children."""
from __future__ import annotations

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


def test_fold_includes_parent_plus_three_children():
    """R0-fold B4: scores has 4 entries — parent + 3 children, key 'parent' present."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        b1 = session.branch({"d": "1"})
        b2 = session.branch({"d": "2"})
        b3 = session.branch({"d": "3"})

        scores = session.fold(probe=lambda db: len(list(db.log())))
        assert isinstance(scores, dict)
        assert len(scores) == 4, f"expected 4 entries (parent + 3), got {len(scores)}"
        assert "parent" in scores
        assert b1 in scores and b2 in scores and b3 in scores


def test_fold_deterministic_across_calls():
    """Same probe over same branches returns same scores."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        session.branch({"d": "1"})
        session.branch({"d": "2"})

        scores1 = session.fold(probe=lambda db: len(list(db.log())))
        scores2 = session.fold(probe=lambda db: len(list(db.log())))
        assert scores1 == scores2


def test_fold_with_no_children_returns_only_parent():
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        scores = session.fold(probe=lambda db: 42)
        assert scores == {"parent": 42}


def test_fold_probe_receives_db_argument():
    """Probe is called with each branch's DB."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        session.branch({"d": "1"})

        seen_dbs = []

        def probe(db):
            seen_dbs.append(db)
            return len(seen_dbs)

        session.fold(probe=probe)
        assert len(seen_dbs) == 2  # parent + b1
