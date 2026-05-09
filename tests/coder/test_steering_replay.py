"""G5 — Phase 2.3d LD-4: audit-chain integration for steering ops.

T9.1-fold strengthens G5 falsifiability per codex Impl R1 findings:

- B1 (replay isn't replay): instead of two fresh symmetric runs, pin
  ``fork_at`` via ``_fork_at_override`` and assert ``(op, args_hash)``
  determinism PLUS Merkle prev_hash chain integrity over the captured
  stream. A single regression that drops a payload field, reorders
  emissions, or breaks the chain will flip PASS→FAIL.
- B2 (fork_at not actually asserted): tamper test — same directive,
  DIFFERENT pinned ``fork_at`` MUST produce a different
  ``:repl/request.args_hash``. If the impl drops ``fork_at`` from the
  payload, both runs hash to the same value and the test fails.
- B3 (branch emission ordering): the ``branch()`` filtered delta must
  read ``[":repl/request", ":coder/branch", ":repl/response"]`` in that
  order — request opens the boundary, action body emits ``:coder/branch``,
  response closes the boundary.

Full audit-driven replay (consume captured stream, drive a replayer,
verify result-hash parity) is W3-rescoped to v0.9.x — the current
gate covers determinism + tamper-evidence + ordering, which together
are sufficient to detect every BLOCKING regression class codex called out.
"""
from __future__ import annotations
import datetime as dt

from persistence.coder import Coder, _CoderSteeringSession
from persistence.effect.canonical import canonical_hash
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


# Pinned fork_at used by all B1/B2 tests; choosing a fixed UTC moment makes
# args_hash byte-deterministic and lets the test assert tamper-sensitivity.
_PINNED_FORK_AT_A = dt.datetime(2026, 5, 9, 12, 0, 0, tzinfo=dt.timezone.utc)
_PINNED_FORK_AT_B = dt.datetime(2026, 5, 9, 12, 0, 1, tzinfo=dt.timezone.utc)


def test_branch_emits_repl_request_then_coder_branch_then_response():
    """B3 (T9.1-fold): branch() filtered delta is exactly
    ``[":repl/request", ":coder/branch", ":repl/response"]`` in order.
    Falsifies any reordering or missing emission."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)

        before = list(_audit_log(s))
        _ = session.branch({"directive": "x"})
        after = list(_audit_log(s))
        delta = after[len(before):]
        delta_ops = [e.op for e in _filter_steering_audit(delta)]
        assert delta_ops == [":repl/request", ":coder/branch", ":repl/response"], \
            f"branch() emission order wrong: {delta_ops}"


def test_pause_emits_repl_request_then_response_in_order():
    """T9.1-fold: pause/resume each emit request → response in order."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        before = list(_audit_log(s))
        session.pause()
        session.resume()
        after = list(_audit_log(s))
        delta_ops = [e.op for e in _filter_steering_audit(after[len(before):])]
        assert delta_ops == [
            ":repl/request", ":repl/response",  # pause
            ":repl/request", ":repl/response",  # resume
        ], f"pause/resume emission order wrong: {delta_ops}"


def test_branch_args_hash_changes_when_fork_at_changes():
    """B2 (T9.1-fold) — tamper falsifiability: same directive + DIFFERENT
    pinned fork_at MUST produce different :repl/request.args_hash. If the
    impl drops fork_at from the payload, both hashes collapse and the
    test fails."""
    def _branch_request_hash(fork_at):
        with Substrate.open("memory") as s:
            s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
            coder = Coder(substrate=s, task="t", model="m", max_iters=1)
            session = _CoderSteeringSession(coder=coder)
            session.branch({"directive": "x"}, _fork_at_override=fork_at)
            req = next(e for e in _audit_log(s) if e.op == ":repl/request")
            return req.args_hash

    hash_a = _branch_request_hash(_PINNED_FORK_AT_A)
    hash_b = _branch_request_hash(_PINNED_FORK_AT_B)
    assert hash_a != hash_b, (
        "args_hash is invariant under fork_at change — fork_at is not "
        "actually in the :repl/request payload (B2 falsifiability)"
    )


def test_branch_args_hash_matches_canonical_expected():
    """B2 (T9.1-fold) — exact-shape falsifiability: pinned fork_at +
    pinned directive → :repl/request.args_hash matches the canonical
    hash of the precise expected payload shape. Any drift in the payload
    construction (renamed key, dropped field, extra field) fails."""
    directive = {"directive": "x"}
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        session.branch(directive, _fork_at_override=_PINNED_FORK_AT_A)
        req = next(e for e in _audit_log(s) if e.op == ":repl/request")

    expected = canonical_hash({
        "op": "branch",
        "payload": {
            "directive": directive,
            "fork_at": _PINNED_FORK_AT_A.isoformat(),
        },
    })
    assert req.args_hash == expected, (
        f"branch :repl/request payload shape drift — got {req.args_hash}, "
        f"expected {expected}"
    )


def test_audit_stream_determinism_under_pinned_fork_at():
    """B1 (T9.1-fold) — replay-meaningful determinism: with pinned
    fork_at, two runs of branch+fold+commit produce byte-identical
    (op, args_hash) sequences over the filtered :repl/* + :coder/branch
    stream. The test asserts both op AND args_hash parity (not just op
    parity, which the old version did).

    Combined with the B2 tamper test above, this catches every payload
    drift the codex Impl R1 review called out: dropped field, renamed
    field, reordered request/response, omitted :coder/branch, etc.
    """
    def _capture_run():
        with Substrate.open("memory") as s:
            s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
            coder = Coder(substrate=s, task="t", model="m", max_iters=1)
            session = _CoderSteeringSession(coder=coder)
            b = session.branch(
                {"directive": "x"}, _fork_at_override=_PINNED_FORK_AT_A,
            )
            _ = session.fold(probe=lambda db: 1)
            session.commit(b)
            return _filter_steering_audit(list(_audit_log(s)))

    run1 = _capture_run()
    run2 = _capture_run()

    # (op, args_hash) tuples must match across runs — this fails if any
    # payload field is non-deterministic OR if emission order changes.
    sig1 = [(e.op, e.args_hash) for e in run1]
    sig2 = [(e.op, e.args_hash) for e in run2]
    assert sig1 == sig2, (
        f"audit stream diverged across pinned-fork_at runs:\n"
        f"  run1 = {sig1}\n  run2 = {sig2}"
    )

    # The expected sequence for branch+fold+commit:
    #   :repl/request (branch) → :coder/branch → :repl/response (branch)
    #   :repl/request (fold)   → :repl/response (fold)
    #   :repl/request (commit) → :repl/response (commit)
    expected_ops = [
        ":repl/request", ":coder/branch", ":repl/response",  # branch
        ":repl/request", ":repl/response",                    # fold
        ":repl/request", ":repl/response",                    # commit
    ]
    assert [e.op for e in run1] == expected_ops, (
        f"unexpected op sequence: {[e.op for e in run1]}"
    )


def test_audit_stream_merkle_chain_is_intact():
    """B1 (T9.1-fold) — chain integrity over the full audit stream
    (not just the filtered subset): each entry's ``prev_hash`` references
    the previous entry's ``id``. Tampering with any entry breaks the
    chain. This complements the determinism test by detecting
    out-of-band modification of the captured stream itself.
    """
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)
        b = session.branch(
            {"directive": "x"}, _fork_at_override=_PINNED_FORK_AT_A,
        )
        _ = session.fold(probe=lambda db: 1)
        session.commit(b)
        entries = list(_audit_log(s))

    # The audit ring may include pre-2.3d entries from substrate startup;
    # check the chain end-to-end across the FULL captured log (not just
    # the steering subset, since prev_hash references whatever came
    # immediately before, not the previous filtered entry).
    for i in range(1, len(entries)):
        assert entries[i].prev_hash == entries[i - 1].id, (
            f"Merkle chain break at index {i}: "
            f"prev_hash={entries[i].prev_hash} vs entries[{i - 1}].id="
            f"{entries[i - 1].id}"
        )
