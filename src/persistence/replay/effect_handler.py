"""EffectHandler — record/replay wrapper for the effect catalog.

Two surfaces live here:

1. :class:`EffectHandler` — the standalone record/replay object used by
   ``persistence.replay.engine.record`` / ``replay`` with a thunk-based
   ``call(op, args, fn)`` API. Backwards-compatible for the replay engine
   and its tests.

2. :func:`make_replay_handler` — returns a proper ``persistence.effect.Handler``
   that can be pushed onto an ``effect.Runtime`` handler stack, making
   record/replay work against the *real* production handler chain. This is
   the ARIS Round 1 R1 F7 / R3 F2 fix.

Both share the same cache/calls state model so a trajectory captured via
the ``effect.Runtime`` path can be replayed via the standalone class and
vice-versa. Op names are the leading-colon catalog form (``":llm/call"``,
``":net/fetch"``) consistent with agent3-effect-spec §1 EDN conventions.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable


# Ops that MUST NEVER execute a function body in replay mode without a cache
# hit. These touch external systems (Binance, Stripe, Supabase, arbitrary
# tools); silently re-executing them would violate honest constraint §8.
#
# Leading-colon namespace matches effect.CATALOG keys and what
# effect.Runtime.perform dispatches. Before the R1 F7 fix this set used
# ``{":net/fetch", ":tool/call"}`` while the runtime dispatched the bare
# ``"net/fetch"`` / ``"tool/call"`` forms — membership silently missed.
NON_REPLAYABLE_OPS = frozenset({":net/fetch", ":tool/call"})

# Ops where prompt-hash drift between record and replay is a loud error
# (prompt template changed since record ⇒ replay is meaningless).
PROMPT_HASH_OPS = frozenset({":llm/call"})


class ReplayCacheMiss(RuntimeError):
    """Replay attempted to execute an un-cached effect.

    For cacheable ops like :llm/call this signals the replay is diverging
    from the recorded trajectory (and the handler simulates by running ``fn``
    if the cache is non-empty). For NON_REPLAYABLE_OPS this is a hard
    failure — see :class:`RefusedInReplay`.
    """


class RefusedInReplay(ReplayCacheMiss):
    """Replay encountered a NON_REPLAYABLE_OP with no cache hit.

    External-side-effect ops (``:net/fetch``, ``:tool/call``, …) must never
    re-execute under replay — replaying a Stripe charge or a Binance order
    would double-side-effect. Kept as a distinct subclass of
    :class:`ReplayCacheMiss` for backward compatibility with tests that
    pattern-match on the base class.
    """


class PromptHashMismatch(RuntimeError):
    """Replay encountered an :llm/call with a prompt_hash that differs from
    what was recorded. The agent's prompt template has drifted — replay is
    meaningless. Fail loudly per agent4-replay-spec §8.
    """


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _args_hash(op: str, args: dict) -> str:
    raw = _canonical({"op": op, "args": args}).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _prompt_hash(op: str, args: dict) -> str | None:
    if op in PROMPT_HASH_OPS:
        return args.get("prompt_hash")
    return None


def _serve_or_miss(
    op: str,
    args: dict,
    *,
    mode: str,
    cache: dict,
    calls: list,
    execute: Callable[[], Any],
) -> Any:
    """Shared record/replay dispatch core.

    Used by both ``EffectHandler.call`` (thunk-based) and the
    ``effect.Handler`` clause produced by ``make_replay_handler``. The two
    callers only differ in how they compute the "execute" continuation —
    one calls a zero-arg ``fn``, the other calls ``k(args)`` to walk the
    effect.Runtime handler chain.
    """
    key = _args_hash(op, args)

    if mode == "replay":
        if key in cache:
            return cache[key]

        # External-side-effect ops MUST NEVER re-execute on miss.
        if op in NON_REPLAYABLE_OPS:
            raise RefusedInReplay(
                f"replay mode encountered uncached {op} args={args!r}. "
                f"External-side-effect ops ({', '.join(sorted(NON_REPLAYABLE_OPS))}) "
                f"must be pre-recorded; replay refuses to re-invoke them."
            )

        # Cacheable ops (e.g., :llm/call): check for prompt-hash drift
        # (same other-args, different prompt_hash) before simulating.
        if op in PROMPT_HASH_OPS:
            other_args = {k: v for k, v in args.items() if k != "prompt_hash"}
            for rec in calls:
                if rec["op"] != op:
                    continue
                rec_other = {
                    k: v for k, v in rec["args"].items() if k != "prompt_hash"
                }
                if rec_other == other_args:
                    raise PromptHashMismatch(
                        f"{op} prompt_hash={args.get('prompt_hash')!r} "
                        f"differs from recorded "
                        f"{rec['args'].get('prompt_hash')!r} for "
                        f"matching other-args={other_args!r}. Prompt "
                        f"template drifted since record — replay cannot "
                        f"proceed."
                    )

        # Empty-cache replay is almost certainly an error (the operator
        # either forgot to pass the recorded cache, or this is a first
        # call ever). Raise so they notice.
        if not cache:
            raise ReplayCacheMiss(
                f"replay mode with empty cache hit {op} args={args!r}. "
                f"Did you forget to pass the recorded cache?"
            )

        # Otherwise: genuine divergence in the counterfactual suffix --
        # simulate by executing and caching. This is the CAMO pattern:
        # cached effects are pinned, new-territory effects are simulated.
        result = execute()
        cache[key] = result
        calls.append({"op": op, "args": args, "key": key, "result": result})
        return result

    # Record mode ----------------------------------------------------
    if key in cache:
        # Append-only: identical call returns identical result.
        return cache[key]
    result = execute()
    cache[key] = result
    calls.append({"op": op, "args": args, "key": key, "result": result})
    return result


@dataclass
class EffectHandler:
    """Record or replay a sequence of effectful calls (thunk-based API).

    Mode ``record``  — invoke fn, cache (args_hash ⇒ result); append to calls log.
    Mode ``replay`` — lookup by args_hash; raise ReplayCacheMiss if absent
    (or :class:`RefusedInReplay` for NON_REPLAYABLE_OPS).

    Cache keys are (op, canonical-json(args)) SHA-256. Hashing canonically
    means dicts with different key order produce the same key.

    This standalone class is preserved for the replay engine
    (``persistence.replay.engine.record`` / ``replay``) which drives it via
    ``handler.call(op, args, fn)``. For integration with
    ``persistence.effect.Runtime`` use :func:`make_replay_handler` instead —
    it returns a proper ``effect.Handler`` that plugs into the handler
    stack with the ``(args, k, ctx)`` continuation signature.
    """

    mode: str = "record"  # "record" | "replay"
    cache: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)

    def call(self, op: str, args: dict, fn: Callable[[], Any]) -> Any:
        return _serve_or_miss(
            op,
            args,
            mode=self.mode,
            cache=self.cache,
            calls=self.calls,
            execute=fn,
        )

    # ------------------------------------------------------------------
    # Internal: reconstruct the op name from a cache key by scanning the
    # `calls` log when we have it, otherwise best-effort by looking at
    # the cached entry's structure. We store op in the calls log during
    # record — in replay mode the cache may be bare dict (e.g., loaded
    # from JSON), so we track op via a parallel map when possible.

    def _op_of(self, key: str) -> str | None:
        for rec in self.calls:
            if rec["key"] == key:
                return rec["op"]
        # Fallback: inspect cached value shape.
        val = self.cache.get(key, {})
        if isinstance(val, dict) and "logprobs" in val:
            return ":llm/call"
        return None


# ---------------------------------------------------------------------------
# effect.Runtime bridge — the R1 F7 / R3 F2 fix.
# ---------------------------------------------------------------------------


def make_replay_handler(
    *,
    mode: str = "record",
    wraps: "set[str] | frozenset[str]" = frozenset({":llm/call"}),
    cache: dict | None = None,
    calls: list | None = None,
):
    """Produce a ``persistence.effect.Handler`` that does record/replay.

    This is the integration bridge missing from Phase 1 (ARIS R1 F7, R3 F2).
    Push the returned handler onto an ``effect.Runtime`` stack and the
    standard ``effect.perform(":llm/call", ...)`` call site will be
    transparently recorded (in ``record`` mode) or served from the cache
    (in ``replay`` mode).

    Parameters
    ----------
    mode
        ``"record"`` | ``"replay"``.
    wraps
        Set of op names this handler participates in. Pass every op whose
        execution should be captured or replayed — typically
        ``{":llm/call", ":db/read", ":mem/read", ":net/fetch", ":tool/call"}``.
        Must be leading-colon catalog form.
    cache
        Mapping of args-hash → result. In ``record`` mode the handler
        populates it; in ``replay`` mode it reads from it. Defaults to an
        empty dict.
    calls
        Structured log of ``{op, args, key, result}`` entries. Needed in
        replay mode for prompt-hash drift detection (see
        :class:`PromptHashMismatch`). Defaults to an empty list.

    Returns
    -------
    persistence.effect.Handler
        Name ``"replay"``. Its ``ctx`` dict exposes the live ``cache`` and
        ``calls`` lists the caller passed in, so trajectories produced via
        the Runtime path and via the standalone :class:`EffectHandler` are
        wire-compatible.

    Notes
    -----
    - The clause for each op runs ``k(args)`` to delegate to the next
      handler down the stack in record mode; in replay mode it returns
      the cached value *without* calling ``k`` (so lower raw handlers
      never fire — honoring §8 replay honesty).
    - NON_REPLAYABLE_OPS (``:net/fetch``, ``:tool/call``) raise
      :class:`RefusedInReplay` on miss even if a raw handler exists below.
    - Unwrapped ops (not in ``wraps``) pass through untouched: dispatch
      skips this handler for those ops entirely.
    """
    # Import locally to avoid any import cycle with persistence.effect
    # during module init — the effect module does not import replay.
    from persistence.effect.runtime import Handler

    if mode not in ("record", "replay"):
        raise ValueError(f"mode must be 'record' or 'replay'; got {mode!r}")

    live_cache: dict = cache if cache is not None else {}
    live_calls: list = calls if calls is not None else []

    wraps_set = set(wraps)

    # Build one clause per wrapped op. We close over ``op`` so the clause
    # body can still identify which op it is handling (the effect.Handler
    # clause signature does not pass op explicitly).
    clauses: dict[str, Any] = {}
    for op in wraps_set:

        def make_clause(op_name: str):
            def clause(args, k, ctx):
                def _execute():
                    # Record mode (or simulated-miss in replay) — delegate
                    # down the rest of the handler stack. The next handler
                    # below us produces the raw result we then cache.
                    return k(args)

                return _serve_or_miss(
                    op_name,
                    args,
                    mode=ctx["mode"],
                    cache=ctx["cache"],
                    calls=ctx["calls"],
                    execute=_execute,
                )

            return clause

        clauses[op] = make_clause(op)

    return Handler(
        name="replay",
        wraps=wraps_set,
        clauses=clauses,
        ctx={"mode": mode, "cache": live_cache, "calls": live_calls},
    )


__all__ = [
    "EffectHandler",
    "NON_REPLAYABLE_OPS",
    "PROMPT_HASH_OPS",
    "PromptHashMismatch",
    "RefusedInReplay",
    "ReplayCacheMiss",
    "make_replay_handler",
]
