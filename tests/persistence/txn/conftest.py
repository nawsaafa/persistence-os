"""Pytest fixtures for the txn byte-identity property tests.

Per v0.5.2 § N7 (docs/plans/2026-04-29-v0.5.2-clojure-parity-design.md
lines 111-135), the byte-identity property must extend to ``tx.effect``
intents, which fire through the effect runtime at commit time and
project onto the audit chain. Comparing two runs of an effect-bearing
body therefore requires a *deterministic* leaf handler — one whose
return value (and therefore the audit handler's ``result_hash``) is a
pure function of ``(op, kwargs)``, with NO wall-clock, RNG, UUID, or
other ambient-state input.

This conftest exposes :func:`_recording_handler` (a module-level
factory, NOT a pytest fixture) that the byte-identity property test
imports directly. The factory shape matches existing handler
factories (``persistence.effect.handlers.raw.make_*``) — call it once
per ``DB`` to get a fresh ``Handler`` with a fresh recording list, so
two runs of the same op sequence each see a fresh handler with
identical input → identical output.

Determinism guarantees (gates per § N7 line 133):

- :func:`_recording_handler` does NOT call ``time.monotonic()``,
  ``time.time()``, ``os.urandom()``, or ``uuid.uuid4()``.
- Result is a pure projection of ``(op, kwargs_minus_txn_commit)``
  via ``hashlib.sha256(json.dumps([op, sorted_kwargs], sort_keys=True))``.
- The ``_txn_commit`` sentinel injected by ``Runtime.perform`` (see
  ``src/persistence/effect/runtime.py:191``) is stripped before
  hashing — symmetric with the audit handler's args-side strip
  (``src/persistence/effect/handlers/audit.py:419``). Otherwise two
  runs would produce DIFFERENT result_hashes because each gets a
  freshly-allocated commit_id UUID.

The audit handler's ``result_hash`` is a ``canonical_hash(result)``;
since ``result`` here is a deterministic dict, the audit-chain
projection ``(op, args_hash, result_hash, prev_hash, txn_commit)``
is structurally identical across two runs of the same op list.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from persistence.effect.runtime import Handler


# Ops the recording handler answers. Must match the effect-op strategy
# in test_replay_byte_identity_property.py exactly — any op the
# strategy draws but the handler doesn't ``wraps`` would raise
# Unhandled at commit time and abort the property mid-flight.
_EFFECT_OPS = (":metric/observe", ":log/write", ":io/print")


def _stable_result(op: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Deterministic projection of ``(op, kwargs)`` into a result dict.

    The audit handler's ``result_hash`` is ``canonical_hash(result)``
    (``handlers/audit.py:435``), so as long as this output is a pure
    function of input, the result_hash is too.

    ``json.dumps(..., sort_keys=True)`` matches the canonical-hash
    convention; ``sha256(...).hexdigest()`` gives a wall-clock-free
    digest. The ``_txn_commit`` sentinel is stripped because the
    audit handler also strips it before computing args_hash — both
    sides must see the same kwargs view for the byte-identity
    invariant to hold across two runs (each run allocates a fresh
    commit_id, so leaking it into result_hash would break parity).
    """
    clean_kwargs = {k: v for k, v in kwargs.items() if k != "_txn_commit"}
    payload = json.dumps([op, clean_kwargs], sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {"op": op, "digest": digest}


def _recording_handler(records: list[tuple[str, dict[str, Any]]]) -> Handler:
    """Build a deterministic Handler that records every dispatch.

    Parameters
    ----------
    records
        Caller-owned list. Each ``(op, kwargs_minus_sentinel)`` tuple
        is appended in dispatch order. Tests inspect this to confirm
        the same intents fired across both runs.

    The returned handler:

    - Wraps :data:`_EFFECT_OPS` exactly (same set the property
      strategy draws from).
    - Returns :func:`_stable_result` — a pure function of ``(op,
      kwargs)`` with no ambient inputs.
    - Strips the ``_txn_commit`` sentinel from the recorded kwargs
      so the per-run commit_id UUID does not pollute the recording.
    """

    def make_clause(op_name: str):
        def clause(args: dict[str, Any], k, ctx) -> Any:
            recorded = {k_: v for k_, v in args.items() if k_ != "_txn_commit"}
            ctx["records"].append((op_name, recorded))
            return _stable_result(op_name, args)

        return clause

    return Handler(
        name="recording",
        wraps=set(_EFFECT_OPS),
        clauses={op: make_clause(op) for op in _EFFECT_OPS},
        ctx={"records": records},
    )
