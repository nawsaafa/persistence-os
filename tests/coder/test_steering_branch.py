"""G2 — Phase 2.3d LD-1: db.branch() parent isolation + fork_at determinism.

The test is the codex-revised version from the consensus skill R1 verdict.
"""
from __future__ import annotations
import datetime as dt

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


def test_branch_returns_branch_id():
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        branch_id = session.branch({"directive": "explore option B"})
        assert isinstance(branch_id, str)
        assert branch_id in session.branches
        assert branch_id != "parent"


def test_branch_isolation_parent_unchanged():
    """G2 (codex): writes on the branch never leak into parent."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        # Pre-seed parent
        t0 = dt.datetime.now(dt.timezone.utc)
        s.fact.transact([{"e": "x", "a": ":k", "v": "v0", "valid_from": t0}])
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)

        branch_id = session.branch({"directive": "x"})
        branch_db = session.branches[branch_id]

        # Snapshot parent BEFORE branch write (use direct list per FD-T2.1)
        parent_pre = list(s._db.log())

        # Write to the branch (using its own .transact)
        _ = branch_db.transact([{
            "e": "branch_only_entity",
            "a": ":k",
            "v": "branch_only_value",
            "valid_from": dt.datetime.now(dt.timezone.utc),
        }])

        # Parent unchanged
        parent_post = list(s._db.log())
        assert parent_pre == parent_post, \
            "branch write leaked into parent — db.branch() isolation broken"


def test_two_branches_from_same_fork_at_have_identical_prefixes():
    """G2 (codex): determinism — two branches off same fork_at byte-identical."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        t0 = dt.datetime.now(dt.timezone.utc)
        s.fact.transact([{"e": "x", "a": ":k", "v": "v0", "valid_from": t0}])
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)

        # Force same fork_at by passing _fork_at_override.
        fork_at = dt.datetime.now(dt.timezone.utc)
        b1_id = session.branch({"directive": "d"}, _fork_at_override=fork_at)
        b2_id = session.branch({"directive": "d"}, _fork_at_override=fork_at)

        b1_seed = list(session.branches[b1_id].as_of(fork_at).datoms)
        b2_seed = list(session.branches[b2_id].as_of(fork_at).datoms)
        assert b1_seed == b2_seed, \
            "two branches from same fork_at have differing prefixes — determinism broken"


def test_branch_id_unique_across_calls():
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        ids = {session.branch({"directive": f"d{i}"}) for i in range(5)}
        assert len(ids) == 5, "branch_ids collided"
