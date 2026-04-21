"""Cache handler tests — canonical-JSON args hash, hit/miss."""
from persistence.effect.handlers.cache import make_cache_handler
from persistence.effect.handlers.raw import make_echo_llm_handler
from persistence.effect.runtime import Handler, Runtime, perform, with_runtime


def test_cache_miss_delegates_and_records():
    cache = make_cache_handler(wraps={":llm/call"})
    raw = make_echo_llm_handler()
    rt = Runtime([raw, cache])
    with with_runtime(rt):
        out = perform(":llm/call", model="m", messages=[{"role": "user", "content": "x"}])
    assert out["text"] == "echo:x"
    # Cache should now hold exactly one entry.
    assert len(cache.ctx["store"]) == 1


def test_cache_hit_returns_cached_without_calling_down():
    cache = make_cache_handler(wraps={":llm/call"})
    downstream_calls = {"n": 0}

    def raw_clause(args, k, ctx):
        downstream_calls["n"] += 1
        return {"text": "first-response", "n": downstream_calls["n"]}

    raw = Handler(name="raw", wraps={":llm/call"}, clauses={":llm/call": raw_clause})
    rt = Runtime([raw, cache])
    args = {"model": "m", "messages": [{"role": "user", "content": "same"}]}
    with with_runtime(rt):
        a = perform(":llm/call", **args)
        b = perform(":llm/call", **args)
    assert a == b
    assert downstream_calls["n"] == 1


def test_cache_key_is_canonical_so_arg_order_irrelevant():
    cache = make_cache_handler(wraps={":llm/call"})
    calls = {"n": 0}

    def raw(args, k, ctx):
        calls["n"] += 1
        return {"r": calls["n"]}

    rt = Runtime([
        Handler(name="raw", wraps={":llm/call"}, clauses={":llm/call": raw}),
        cache,
    ])
    with with_runtime(rt):
        # Pass args in different orders — canonical JSON sort_keys must dedupe.
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "x"}])
        perform(":llm/call", messages=[{"role": "user", "content": "x"}], model="m")
    assert calls["n"] == 1


def test_cache_differentiates_by_args():
    cache = make_cache_handler(wraps={":llm/call"})
    calls = {"n": 0}

    def raw(args, k, ctx):
        calls["n"] += 1
        return {"r": calls["n"]}

    rt = Runtime([
        Handler(name="raw", wraps={":llm/call"}, clauses={":llm/call": raw}),
        cache,
    ])
    with with_runtime(rt):
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "a"}])
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "b"}])
    assert calls["n"] == 2


def test_cache_store_is_per_instance_no_globals():
    """Two cache handlers must not share state (spec §9 — no hidden globals)."""
    cache_a = make_cache_handler(wraps={":llm/call"})
    cache_b = make_cache_handler(wraps={":llm/call"})
    assert cache_a.ctx["store"] is not cache_b.ctx["store"]
