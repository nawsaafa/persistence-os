"""End-to-end test: replay handler wired into effect.Runtime.

ARIS Round 1 R1 F7 / R3 F2 regression check.

The invariant: a tiny agent program that calls ``perform(":llm/call", ...)``
and ``perform(":db/read", ...)`` against an ``effect.Runtime`` whose bottom
is a replay handler in RECORD mode, then replayed by swapping the bottom
handler for REPLAY mode on a fresh Runtime, must produce a byte-identical
trajectory (content-hash equal).

If this test fails:
- Either the replay handler is not a valid ``effect.Handler`` (wrong
  continuation signature) so it never ran under the runtime, OR
- The op namespace / NON_REPLAYABLE_OPS contract drifted again, OR
- The cache key scheme diverged between record and replay, OR
- Determinism leaked through some non-effect path (time/random/db).
"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from persistence.effect.runtime import (
    Handler,
    Runtime,
    Unhandled,
    perform,
    with_runtime,
)
from persistence.replay.effect_handler import (
    NON_REPLAYABLE_OPS,
    RefusedInReplay,
    ReplayCacheMiss,
    make_replay_handler,
)
from persistence.replay.trajectory import Trajectory, trajectory_hash


# ---------------------------------------------------------------------------
# Tiny agent program — exercises 2 :llm/call and 1 :db/read, all through
# persistence.effect.perform(). This is the real integration shape a Phase-2
# consumer would use. Zero direct calls to the replay.EffectHandler class.
# ---------------------------------------------------------------------------


def _raw_llm_handler(sequence):
    """Raw fake LLM handler that pops results off a pre-seeded queue.

    Used only during RECORD — in REPLAY the replay handler short-circuits
    above this so ``sequence`` is not consumed.
    """
    q = list(sequence)

    def clause(args, k, ctx):
        if not q:
            raise RuntimeError(
                "raw LLM exhausted — replay handler should have short-"
                "circuited; this means the record/replay wiring is broken"
            )
        return q.pop(0)

    return Handler(name="raw-llm", wraps={":llm/call"}, clauses={":llm/call": clause})


def _raw_db_read_handler(values):
    """Raw fake :db/read handler keyed by args['key'].

    :db/read is a *cacheable* op (pure read of a bitemporal snapshot) — in
    replay it MUST come from the cache, never from this raw handler.
    """
    served = []

    def clause(args, k, ctx):
        if args["key"] not in values:
            raise RuntimeError(f"no seeded :db/read for {args['key']!r}")
        served.append(args["key"])
        return {"value": values[args["key"]]}

    h = Handler(name="raw-db", wraps={":db/read"}, clauses={":db/read": clause})
    h.ctx["served"] = served  # expose for assertions
    return h


def _tiny_agent_program():
    """Performs 2 :llm/call and 1 :db/read via ``effect.perform``.

    Returns a tuple ``(llm_responses, db_response)`` so the test can assert
    that record and replay see the *same* values.
    """
    a = perform(":llm/call", prompt_hash="p1", model="mock")
    b = perform(":llm/call", prompt_hash="p2", model="mock")
    c = perform(":db/read", key="entity-42", as_of=1_000)
    return a, b, c


# ---------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------


def test_replay_handler_produces_valid_effect_handler():
    """make_replay_handler must return an object compatible with Runtime."""
    h = make_replay_handler(mode="record", wraps={":llm/call", ":db/read"})
    assert isinstance(h, Handler)
    assert ":llm/call" in h.clauses
    assert ":db/read" in h.clauses
    # Runtime accepts it in its handler list.
    rt = Runtime([h])
    assert ":llm/call" in set().union(*(set(hh.clauses) for hh in rt.handlers))


def test_record_then_replay_byte_identical_trajectory():
    """The load-bearing e2e invariant.

    1. RECORD: push raw-llm + raw-db + replay-handler(mode=record) onto a
       Runtime; run the tiny program; save the trajectory (cache + calls).
    2. REPLAY: push *only* replay-handler(mode=replay) onto a FRESH Runtime
       (no raw handlers — replay must serve everything from cache); run the
       same program; assert identical returns and identical trajectory hash.
    """
    # --- record ---
    llm_seq = [
        {"text": "buy", "model": "mock"},
        {"text": "wait", "model": "mock"},
    ]
    db_values = {"entity-42": "WACC=0.091"}
    raw_llm = _raw_llm_handler(llm_seq)
    raw_db = _raw_db_read_handler(db_values)

    record_handler = make_replay_handler(
        mode="record",
        wraps={":llm/call", ":db/read", ":net/fetch", ":tool/call"},
    )
    rt_record = Runtime([raw_llm, raw_db, record_handler])
    with with_runtime(rt_record):
        rec_a, rec_b, rec_c = _tiny_agent_program()
    # Persist the trajectory in the shape record() does.
    traj = Trajectory(
        agent="bridge-test",
        seeds={"llm": 0, "tool": 0, "env": 0},
        status="completed",
        cache=dict(record_handler.ctx["cache"]),
        call_log=list(record_handler.ctx["calls"]),
    )
    traj.outcome = {"replies": [rec_a, rec_b, rec_c]}
    traj.hash = trajectory_hash(traj)

    # --- replay ---
    replay_handler = make_replay_handler(
        mode="replay",
        wraps={":llm/call", ":db/read", ":net/fetch", ":tool/call"},
        cache=dict(traj.cache),
        calls=list(traj.call_log),
    )
    rt_replay = Runtime([replay_handler])  # NO raw handlers — cache-only!
    with with_runtime(rt_replay):
        rep_a, rep_b, rep_c = _tiny_agent_program()

    # Value-level equality: every effect returned the recorded value.
    assert rep_a == rec_a
    assert rep_b == rec_b
    assert rep_c == rec_c

    # ARIS Round 3 P-rigor-polish G2: the byte-identity check below used
    # to hash a Trajectory whose ``.facts`` was still [] — structurally
    # vacuous. Guard against that explicitly before the content-hash
    # comparison, and also assert value-level equality of the cache and
    # call_log (the *real* load-bearing state for replay determinism).
    assert len(traj.cache) > 0, (
        "recorded trajectory has empty cache — byte-identity check would be "
        "vacuous (no effect was ever replayed)"
    )
    assert len(traj.call_log) > 0, (
        "recorded trajectory has empty call_log — replay parity is vacuous"
    )

    # Trajectory hash equality: reconstruct a matching Trajectory from the
    # replay pass and check byte-identity via the same content hash the
    # replay engine uses (ignoring id/lineage per _HASH_IGNORE_FIELDS).
    replayed = Trajectory(
        agent="bridge-test",
        seeds={"llm": 0, "tool": 0, "env": 0},
        status="completed",
        cache=dict(replay_handler.ctx["cache"]),
        call_log=list(replay_handler.ctx["calls"]),
    )
    replayed.outcome = {"replies": [rep_a, rep_b, rep_c]}
    replayed.hash = trajectory_hash(replayed)

    # Value-level equality (G2): the actual state carriers (cache,
    # call_log, outcome) must match, not just the canonical hash.
    assert replayed.cache == traj.cache, (
        "replay cache diverged from record cache — value-level state drift"
    )
    assert replayed.call_log == traj.call_log, (
        "replay call_log diverged — arg/value/order drift"
    )
    assert replayed.outcome == traj.outcome, "outcome diverged"

    # Byte-identity on the canonical hash — the headline §4.5 Corollary.
    assert trajectory_hash(replayed) == trajectory_hash(traj)


def test_replay_refuses_net_fetch_on_cache_miss():
    """NON_REPLAYABLE_OPS: :net/fetch must hard-fail on miss even under Runtime."""

    def raw_net_clause(args, k, ctx):
        return {"status": 200, "body": "live-call"}

    raw_net = Handler(
        name="raw-net", wraps={":net/fetch"}, clauses={":net/fetch": raw_net_clause}
    )
    replay_handler = make_replay_handler(
        mode="replay",
        wraps={":net/fetch"},
        cache={},
    )
    # In replay mode with empty cache, :net/fetch must raise even though
    # a raw handler is present below — replay refuses to delegate for
    # external-side-effect ops.
    rt = Runtime([raw_net, replay_handler])
    with with_runtime(rt):
        with pytest.raises((RefusedInReplay, ReplayCacheMiss)) as exc_info:
            perform(":net/fetch", url="https://example.com", method="GET")
    assert ":net/fetch" in str(exc_info.value)


def test_non_replayable_ops_canonicalized_to_leading_colon():
    """Smoke test that the NON_REPLAYABLE_OPS set is the :-prefixed form
    matching what effect.Runtime.perform will see.

    Before the R1 F7 fix this set contained ``{":net/fetch", ":tool/call"}``
    while effect.perform dispatched ``"net/fetch"`` / ``"tool/call"`` — the
    membership check silently missed and NON_REPLAYABLE_OPS never fired.
    """
    for op in NON_REPLAYABLE_OPS:
        assert op.startswith(":"), (
            f"NON_REPLAYABLE_OPS must use leading-colon namespace for parity "
            f"with effect.Runtime dispatch; got {op!r}"
        )


def test_replay_passes_through_unwrapped_ops_to_lower_handlers():
    """A replay handler only wraps ops it was told to wrap.

    If the agent performs an op outside ``wraps``, dispatch must walk through
    the replay handler without consulting the cache, so sibling handlers
    below can still handle it.
    """
    raw_llm = _raw_llm_handler([{"text": "hit"}])
    # replay_handler only wraps :db/read — :llm/call must flow through.
    replay_handler = make_replay_handler(
        mode="replay",
        wraps={":db/read"},
        cache={},
    )
    rt = Runtime([raw_llm, replay_handler])
    with with_runtime(rt):
        out = perform(":llm/call", prompt_hash="p", model="mock")
    assert out == {"text": "hit"}


def test_prompt_hash_drift_raises_under_runtime():
    """If the agent's :llm/call prompt_hash differs from the recorded one,
    the runtime-wrapped replay handler must raise PromptHashMismatch."""
    from persistence.replay.effect_handler import PromptHashMismatch

    raw_llm = _raw_llm_handler([{"text": "v1"}])
    record_handler = make_replay_handler(mode="record", wraps={":llm/call"})
    rt = Runtime([raw_llm, record_handler])
    with with_runtime(rt):
        perform(":llm/call", prompt_hash="sha-v1", model="mock")

    replay_handler = make_replay_handler(
        mode="replay",
        wraps={":llm/call"},
        cache=dict(record_handler.ctx["cache"]),
        calls=list(record_handler.ctx["calls"]),
    )
    rt2 = Runtime([replay_handler])
    with with_runtime(rt2):
        with pytest.raises(PromptHashMismatch):
            perform(":llm/call", prompt_hash="sha-v2-drifted", model="mock")
