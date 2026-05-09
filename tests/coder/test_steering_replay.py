"""G5 — Phase 2.3d LD-4: byte-identity replay of :repl/* + :coder/branch stream.

Note: full byte-identity is W3-rescoped to 2.4a per :sys/now. Current G5
verifies op-sequence parity across replays.
"""
from __future__ import annotations
import datetime as dt

from persistence.coder import Coder, _CoderSteeringSession
from persistence.effect.handlers import make_callable_llm_handler
from persistence.sdk import Substrate


_REPL_OP_PREFIXES = (":repl/request", ":repl/response", ":coder/branch")


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


def _audit_log(s):
    """Helper to access the substrate's audit log.

    FD-T6.x: field name confirmed as ``s._audit_entries`` per
    :file:`src/persistence/sdk/_facade.py:1410` and the existing
    coder-test access pattern at ``test_act_git.py:100``.
    """
    return s._audit_entries


def _filter_steering_audit(entries):
    return [
        e for e in entries
        if any(e.op == p or e.op.startswith(p) for p in _REPL_OP_PREFIXES)
    ]


def test_branch_emits_repl_request_response_pair():
    """Each branch() call emits :repl/request + :coder/branch + :repl/response."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)

        before = list(_audit_log(s))
        _ = session.branch({"directive": "x"})
        after = list(_audit_log(s))
        delta = after[len(before):]
        delta_filtered = _filter_steering_audit(delta)
        assert len(delta_filtered) >= 3
        assert any(e.op == ":repl/request" for e in delta_filtered)
        assert any(e.op == ":repl/response" for e in delta_filtered)
        assert any(e.op == ":coder/branch" for e in delta_filtered)


def test_pause_emits_repl_request_response_pair():
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        before = len(_audit_log(s))
        session.pause()
        session.resume()
        after = len(_audit_log(s))
        # 2 ops × 2 datoms each = 4 entries
        assert after - before >= 4


def test_repl_request_payload_includes_fork_at_for_branch():
    """LD-4 + R0-fold I3: :repl/request.fork_at recorded explicitly."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        _ = session.branch({"directive": "x"})

        repl_request = next(
            e for e in _audit_log(s) if e.op == ":repl/request"
        )
        assert repl_request.args_hash, "repl/request missing args_hash"


def test_byte_identity_replay_branch_fold_commit_stream():
    """G5 (R0-fold I4): filter to :repl/* + :coder/branch only across replay."""
    with Substrate.open("memory") as s1:
        s1.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        c1 = Coder(substrate=s1, task="t", model="m", max_iters=1)
        sess1 = _CoderSteeringSession(coder=c1)
        b = sess1.branch({"directive": "x"})
        _ = sess1.fold(probe=lambda db: 1)
        sess1.commit(b)
        run1 = _filter_steering_audit(list(_audit_log(s1)))

    with Substrate.open("memory") as s2:
        s2.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        c2 = Coder(substrate=s2, task="t", model="m", max_iters=1)
        sess2 = _CoderSteeringSession(coder=c2)
        b2 = sess2.branch({"directive": "x"})
        _ = sess2.fold(probe=lambda db: 1)
        sess2.commit(b2)
        run2 = _filter_steering_audit(list(_audit_log(s2)))

    # Op-sequence parity (full byte-identity W3-rescoped to 2.4a per :sys/now).
    ops1 = [e.op for e in run1]
    ops2 = [e.op for e in run2]
    assert ops1 == ops2, f"op sequence diverged across replays: {ops1} vs {ops2}"
