"""EffectHandler — record/replay wrapper for the 15-op effect catalog.

Integration point for Module 2 (persistence.effect): once the sibling Effect
worktree lands, `EffectHandler.call` can be composed as the bottom handler
below the replay-intercept layer. Until then this stub captures the contract
defined in agent3-effect-spec §6:

  Replay engine installs stack identical to prod except bottom three swapped:
     audit → policy → [replay-intercept] → [recorded-cache] → [clock/rng-replay] → raw-deny

We implement `[recorded-cache]` here. `raw-deny` is enforced by raising
`ReplayCacheMiss` for `:net/fetch` and `:tool/call` with no cached response.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable


# Ops that MUST NEVER execute a function body in replay mode without a cache
# hit. These touch external systems (Binance, Stripe, Supabase, arbitrary
# tools); silently re-executing them would violate honest constraint §8.
NON_REPLAYABLE_OPS = frozenset({":net/fetch", ":tool/call"})

# Ops where prompt-hash drift between record and replay is a loud error
# (prompt template changed since record ⇒ replay is meaningless).
PROMPT_HASH_OPS = frozenset({":llm/call"})


class ReplayCacheMiss(RuntimeError):
    """Replay attempted to execute an un-cached effect.

    For :net/fetch / :tool/call this is a hard failure — those have real-world
    side effects. For cacheable ops like :llm/call it signals the replay is
    diverging from the recorded trajectory.
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


@dataclass
class EffectHandler:
    """Record or replay a sequence of effectful calls.

    Mode `record`  — invoke fn, cache (args_hash ⇒ result); append to calls log.
    Mode `replay` — lookup by args_hash; raise ReplayCacheMiss if absent.

    Cache keys are (op, canonical-json(args)) SHA-256. Hashing canonically
    means dicts with different key order produce the same key.
    """

    mode: str = "record"  # "record" | "replay"
    cache: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)

    def call(self, op: str, args: dict, fn: Callable[[], Any]) -> Any:
        key = _args_hash(op, args)

        if self.mode == "replay":
            if key in self.cache:
                return self.cache[key]

            # External-side-effect ops MUST NEVER re-execute on miss.
            if op in NON_REPLAYABLE_OPS:
                raise ReplayCacheMiss(
                    f"replay mode encountered uncached {op} args={args!r}. "
                    f"External-side-effect ops ({', '.join(sorted(NON_REPLAYABLE_OPS))}) "
                    f"must be pre-recorded; replay refuses to re-invoke them."
                )

            # Cacheable ops (e.g., :llm/call): check for prompt-hash drift
            # (same other-args, different prompt_hash) before simulating.
            if op in PROMPT_HASH_OPS:
                other_args = {k: v for k, v in args.items() if k != "prompt_hash"}
                for rec in self.calls:
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
            if not self.cache:
                raise ReplayCacheMiss(
                    f"replay mode with empty cache hit {op} args={args!r}. "
                    f"Did you forget to pass the recorded cache?"
                )

            # Otherwise: genuine divergence in the counterfactual suffix --
            # simulate by executing fn and caching. This is the CAMO pattern:
            # cached effects are pinned, new-territory effects are simulated.
            result = fn()
            self.cache[key] = result
            self.calls.append({"op": op, "args": args, "key": key, "result": result})
            return result

        # Record mode ----------------------------------------------------
        if key in self.cache:
            # Append-only: identical call returns identical result.
            return self.cache[key]
        result = fn()
        self.cache[key] = result
        self.calls.append({"op": op, "args": args, "key": key, "result": result})
        return result

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
