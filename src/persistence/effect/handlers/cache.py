"""Cache handler — canonical-JSON args hash.

Only pure-ish ops should be cached: `:llm/call`, `:net/fetch`, `:tool/call`
when the tool is declared idempotent. Hits bypass everything below this
handler (per the stack ordering described in spec §3: cache is above
retry + rate-limit so a hit never burns quota).

Store lives in this handler's ctx — no module-level dict. Callers can
swap the store for a bounded LRU without touching the handler.
"""
from __future__ import annotations

from typing import Any, Iterable, MutableMapping

from persistence.effect.canonical import canonical_hash
from persistence.effect.runtime import Handler


def make_cache_handler(
    *,
    wraps: Iterable[str] = ("llm/call", "net/fetch"),
    store: MutableMapping[str, Any] | None = None,
) -> Handler:
    """Return a cache handler.

    Cache key: sha256 of canonical JSON of ``(op, args)``. On hit the handler
    returns the stored value without delegating. On miss, it calls ``k`` and
    stores the result.
    """
    if store is None:
        store = {}

    def make_op_clause(op_name: str):
        def clause(args, k, ctx):
            key = canonical_hash({"op": op_name, "args": args})
            if key in ctx["store"]:
                ctx["stats"]["hits"] += 1
                return ctx["store"][key]
            ctx["stats"]["misses"] += 1
            result = k(args)
            ctx["store"][key] = result
            return result

        return clause

    clauses = {op: make_op_clause(op) for op in wraps}
    return Handler(
        name="cache",
        wraps=set(wraps),
        clauses=clauses,
        ctx={
            "store": store,
            "stats": {"hits": 0, "misses": 0},
        },
    )
