"""Handler-per-tag dispatch over the existing plan AST walker.

The walker (`_walk.walk`) traverses the tree and returns a trace of node ids.
The dispatcher layers a handler registry on top: register a callable per tag,
and `dispatch(node, env)` calls registered handlers in walk order.

Designed to be the substrate seam for `:llm-call`, `:tool-call`, `:transact`
etc. — effect runtime registers handlers; the substrate doesn't know who is
"thinking." `:code` and `:branch` continue to raise UnimplementedNodeKindError
in the walker (post-NeSy roadmap; v0.5 / Phase 3).
"""
from __future__ import annotations

from typing import Any, Callable

from persistence.plan._ast import Node
from persistence.plan._walk import walk

#: A dispatch handler: receives the node and an environment, returns a result.
#: Result type is open — the substrate doesn't constrain it.
Handler = Callable[[Node, dict], Any]


class Dispatcher:
    """Handler-per-tag dispatch over the plan AST.

    Usage::

        d = Dispatcher()
        d.register(":llm-call", my_llm_handler)
        result = d.dispatch(plan_root, env={"trace_id": "abc"})

    Internally uses :func:`persistence.plan._walk.walk` for traversal — does
    NOT re-implement DFS. Walk order = parent before children, depth-first.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(
        self,
        tag: str,
        handler: Handler,
        *,
        replace: bool = False,
    ) -> None:
        """Register a handler for ``tag``.

        Re-registering an already-registered tag raises ValueError unless
        ``replace=True`` is set (test-only convenience).
        """
        if tag in self._handlers and not replace:
            raise ValueError(
                f"Dispatcher: handler for {tag!r} already registered. "
                f"Pass replace=True to override."
            )
        self._handlers[tag] = handler

    def has_handler(self, tag: str) -> bool:
        """True if a handler is registered for ``tag``."""
        return tag in self._handlers

    def dispatch(self, node: Node, env: dict) -> list[Any]:
        """Walk ``node`` in DFS order; for each node with a registered
        handler, call it with ``(node, env)``; return results in walk order.

        Nodes without a registered handler are skipped (no error). The walker's
        existing semantics are preserved — :code/:branch still raise
        UnimplementedNodeKindError if encountered as leaves.

        ``env`` is passed by reference; handler mutations to ``env`` are
        visible to later handlers in the same walk. This is the implicit
        shared-state thread between handlers — by design, not by accident.

        Returns:
            List of handler return values in walk order. Length equals the
            number of nodes whose tag had a registered handler.
        """
        results: list[Any] = []

        def visitor(n: Node, _path: tuple[str, ...]) -> None:
            handler = self._handlers.get(n.tag)
            if handler is not None:
                results.append(handler(n, env))

        walk(node, visitor=visitor)
        return results


__all__ = ["Dispatcher", "Handler"]
