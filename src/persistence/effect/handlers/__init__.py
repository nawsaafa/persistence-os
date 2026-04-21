"""persistence.effect.handlers — the handler library.

Each handler is a factory returning a :class:`persistence.effect.runtime.Handler`.
Per spec §9, handlers carry their own ctx (no hidden globals) and route all
non-determinism through effects (:clock/now, :random).
"""
