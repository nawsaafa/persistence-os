"""EffectHandler tests — record/replay/args-hash/external-API guard."""
from __future__ import annotations

import pytest

from persistence.replay.effect_handler import (
    EffectHandler,
    ReplayCacheMiss,
    PromptHashMismatch,
)


def test_record_mode_invokes_fn_and_caches_result():
    h = EffectHandler(mode="record")
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return {"text": "buy"}

    out = h.call(":llm/call", {"prompt_hash": "abc"}, fn)
    assert out == {"text": "buy"}
    assert calls["n"] == 1
    # Cache now has one entry keyed by canonical args-hash.
    assert len(h.cache) == 1


def test_record_mode_reuses_same_key_across_calls_with_same_args():
    h = EffectHandler(mode="record")

    def fn():
        return {"text": "buy"}

    h.call(":llm/call", {"prompt_hash": "abc"}, fn)
    h.call(":llm/call", {"prompt_hash": "abc"}, fn)
    # Only one distinct cache entry (same op + same args).
    assert len(h.cache) == 1


def test_replay_mode_returns_cached_response_without_invoking_fn():
    rec = EffectHandler(mode="record")
    rec.call(":llm/call", {"prompt_hash": "abc"}, lambda: {"text": "buy"})

    rep = EffectHandler(mode="replay", cache=rec.cache)
    sentinel = {"called": False}

    def fn():
        sentinel["called"] = True
        return {"text": "OTHER"}

    out = rep.call(":llm/call", {"prompt_hash": "abc"}, fn)
    assert out == {"text": "buy"}
    assert sentinel["called"] is False


def test_replay_mode_cache_miss_for_pure_op_raises_loud_error():
    # :llm/call is cacheable but wasn't recorded — must not silently execute.
    h = EffectHandler(mode="replay", cache={})
    with pytest.raises(ReplayCacheMiss):
        h.call(":llm/call", {"prompt_hash": "unseen"}, lambda: {"text": "x"})


def test_replay_mode_refuses_net_fetch_on_cache_miss():
    h = EffectHandler(mode="replay", cache={})
    with pytest.raises(ReplayCacheMiss) as exc_info:
        h.call(
            ":net/fetch",
            {"url": "https://api.binance.com/v3/ticker", "method": "GET"},
            lambda: {"status": 200, "body": {}},
        )
    # The error message must mention the op name so the operator can diagnose.
    assert ":net/fetch" in str(exc_info.value)


def test_replay_mode_refuses_tool_call_on_cache_miss():
    h = EffectHandler(mode="replay", cache={})
    with pytest.raises(ReplayCacheMiss):
        h.call(
            ":tool/call",
            {"name": "stripe.charge", "input": {"amount": 100}},
            lambda: {"result": "ok"},
        )


def test_cache_is_read_only_in_replay_mode():
    rec = EffectHandler(mode="record")
    rec.call(":llm/call", {"prompt_hash": "abc"}, lambda: {"text": "buy"})
    cached_key = next(iter(rec.cache.keys()))

    rep = EffectHandler(mode="replay", cache=rec.cache)
    # Even a hit must NOT mutate the cache entry.
    before = rep.cache[cached_key]
    rep.call(":llm/call", {"prompt_hash": "abc"}, lambda: {"text": "new"})
    assert rep.cache[cached_key] is before


def test_record_mode_cache_is_append_only_same_key_reuses_not_overwrites():
    # If the agent asks twice with the same args, record-mode should return
    # the originally-captured result, not re-invoke.
    h = EffectHandler(mode="record")
    results = [{"text": "first"}, {"text": "second"}]

    def fn():
        return results.pop(0)

    first = h.call(":llm/call", {"prompt_hash": "abc"}, fn)
    second = h.call(":llm/call", {"prompt_hash": "abc"}, fn)
    assert first == {"text": "first"}
    assert second == {"text": "first"}  # identical — append-only
    assert len(results) == 1  # fn invoked exactly once


def test_prompt_hash_mismatch_raises():
    # If replay-mode encounters an llm/call whose prompt_hash differs from a
    # recorded entry with identical other-args, the handler must raise
    # PromptHashMismatch (prompt template drifted since record).
    rec = EffectHandler(mode="record")
    rec.call(
        ":llm/call",
        {"prompt_hash": "sha-v1", "model": "opus-4.7"},
        lambda: {"text": "buy"},
    )

    # Replay handler receives BOTH the cache and the structured call log --
    # the latter is needed for drift detection.
    rep = EffectHandler(mode="replay", cache=rec.cache, calls=list(rec.calls))
    with pytest.raises(PromptHashMismatch):
        rep.call(
            ":llm/call",
            {"prompt_hash": "sha-v2-drifted", "model": "opus-4.7"},
            lambda: {"text": "x"},
        )


def test_canonical_args_hashing_is_key_order_insensitive():
    h = EffectHandler(mode="record")
    h.call(":llm/call", {"a": 1, "b": 2}, lambda: {"ok": True})
    # Same dict in different key order should hit the SAME cache entry.
    h.call(":llm/call", {"b": 2, "a": 1}, lambda: {"ok": False})
    assert len(h.cache) == 1
