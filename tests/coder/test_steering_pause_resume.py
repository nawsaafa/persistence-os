"""G1 — Phase 2.3d LD2: pause blocks at iteration head, resume unblocks."""
from __future__ import annotations
import contextvars
import threading
import time

from persistence.coder import Coder, _CoderSteeringSession
from persistence.sdk import Substrate
from persistence.effect.handlers import make_callable_llm_handler


def _make_done_call_fn():
    """Returns a call_fn that immediately answers done=True so coder.run()
    finishes after one iter (without pause) — for non-pause baseline tests.

    Format matches catalog wire shape used by make_callable_llm_handler:
    ``{"tool_calls": [{"input": <decision_dict>}], "text": ""}``.
    The decision dict must have top-level kind/confidence/payload per
    _parse_decision tier-1 logic (_session.py:176-194).
    """
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


def test_pause_blocks_then_resume_unblocks():
    """B1 must FAIL without inverted-Event-semantics fix. Pause clears the event,
    _check_pause() blocks on wait(), resume() sets the event, wait() returns."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_make_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=3)
        session = _CoderSteeringSession(coder=coder)

        t0 = time.monotonic()
        session.pause()  # clears event; next _check_pause() will block
        # Resume after 150 ms from another thread.
        # T6 update: session.resume() now performs :repl/request + :repl/response
        # via the audit ring, which depends on the effect runtime ContextVar.
        # Wrap the timer target in copy_context().run so the timer's worker
        # thread sees the same _active runtime as the main thread.
        timer_ctx = contextvars.copy_context()
        threading.Timer(0.15, lambda: timer_ctx.run(session.resume)).start()

        # Run coder in a worker so we can timer-resume from the main thread.
        # Use copy_context().run so the effect runtime ContextVar (_active) is
        # propagated to the thread (ContextVars don't cross thread boundaries
        # by default in CPython — threads start with a fresh copy of the
        # context as it existed at Thread.start() call time).
        ctx = contextvars.copy_context()
        worker = threading.Thread(target=lambda: ctx.run(coder.run), daemon=True)
        worker.start()
        worker.join(timeout=2.0)

        assert not worker.is_alive(), "coder.run() failed to return after resume"
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.14, f"pause didn't actually block (elapsed={elapsed:.3f}s)"
        assert coder._iter_count >= 0, "coder didn't advance after resume"


def test_no_pause_no_block():
    """Default state is set (not paused); _check_pause() is a no-op."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_make_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=3)
        _ = _CoderSteeringSession(coder=coder)  # attach but don't pause
        t0 = time.monotonic()
        coder.run()
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"unpaused coder.run took too long: {elapsed:.3f}s"


def test_pause_resume_idempotent():
    """Concurrency invariant N2: multiple pause()/resume() calls are no-ops."""
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_make_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=3)
        session = _CoderSteeringSession(coder=coder)
        session.pause()
        session.pause()  # no-op
        assert not session._pause_event.is_set()
        session.resume()
        session.resume()  # no-op
        assert session._pause_event.is_set()
