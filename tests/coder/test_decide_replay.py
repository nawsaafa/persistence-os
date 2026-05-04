"""Phase 2.1b — _decide replay byte-identity (G3, design § 4).

Recorded :llm/messages + :llm/decision datoms must produce a
substrate state that replays byte-identical when _decide is run again
against a fresh substrate with a deterministic mocked LLM provider.
"""
from __future__ import annotations

import json

from persistence.coder import Coder
from persistence.coder._types import Observation
from persistence.effect.handlers import make_callable_llm_handler
from persistence.sdk import Substrate


def _deterministic_call_fn(model, messages, tools=None, **_):
    """Fixed return — every invocation produces identical output."""
    return {
        "text": "",
        "tool_calls": [{
            "id": "tu_replay_001", "name": "emit_decision",
            "input": {"kind": "act", "confidence": 0.85,
                      "payload": {"tool": "fs/write"}},
        }],
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "fingerprint": "fp-replay",
    }


def test_decide_replay_produces_byte_identical_datom_values():
    """Two _decide invocations on two fresh substrates with the same
    deterministic call_fn produce datoms with byte-identical canonical
    values for :llm/messages and :llm/decision."""
    def run_once():
        s_cm = Substrate.open("memory")
        s = s_cm.__enter__()
        try:
            s.effect.install_handler(
                make_callable_llm_handler(call_fn=_deterministic_call_fn),
                position="bottom",
            )
            # Pin the time + uuid sources to get determinism for this G3
            # test. _decide reads dt.datetime.now and uuid.uuid4 — patch
            # the module-level bindings on persistence.coder._session for
            # the duration of this call. (datetime.datetime itself is an
            # immutable C type, so we swap the entire dt/uuid module
            # references on _session instead.)
            import datetime as _real_dt
            import uuid as _real_uuid

            from persistence.coder import _session as _session_mod

            class _FixedDateTime(_real_dt.datetime):
                @classmethod
                def now(cls, tz=None):
                    return _real_dt.datetime(
                        2026, 5, 5, 12, 0, 0, tzinfo=_real_dt.timezone.utc
                    )

            class _FixedDtModule:
                datetime = _FixedDateTime
                timezone = _real_dt.timezone

            uuid_seq = iter(["c0" * 16, "d0" * 16])

            class _FixedUuidModule:
                @staticmethod
                def uuid4():
                    return type("U", (), {"hex": next(uuid_seq)})()

            real_dt = _session_mod.dt
            real_uuid = _session_mod.uuid
            try:
                _session_mod.dt = _FixedDtModule  # type: ignore[assignment]
                _session_mod.uuid = _FixedUuidModule  # type: ignore[assignment]
                Coder(task="replay-test", substrate=s)._decide(Observation())
            finally:
                _session_mod.dt = real_dt  # type: ignore[assignment]
                _session_mod.uuid = real_uuid  # type: ignore[assignment]

            # Use s._db.log() per Task 9's adjustment (curated s.fact.history
            # requires (e, a) args; raw DB log is the all-datoms iterator).
            msgs_v = next(d.v for d in s._db.log() if d.a == "llm/messages")
            decs_v = next(d.v for d in s._db.log() if d.a == "llm/decision")
            return msgs_v, decs_v
        finally:
            s_cm.__exit__(None, None, None)

    m1, d1 = run_once()
    m2, d2 = run_once()
    assert m1 == m2  # canonical-JSON byte-identity
    assert d1 == d2

    # Spot-check the decision payload survived round-trip
    parsed = json.loads(d1)
    assert parsed["kind"] == "act"
    assert parsed["confidence"] == 0.85
    assert parsed["parsed_via"] == "tool_use"
