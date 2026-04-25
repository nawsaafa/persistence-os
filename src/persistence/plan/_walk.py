"""Depth-first walker for persistence.plan AST. No executors in v0.1."""
from __future__ import annotations

from typing import Callable

from persistence.plan._ast import Node
from persistence.plan._errors import UnimplementedNodeKindError

#: Node kinds that raise UnimplementedNodeKindError when walked in leaf
#: position. :code needs a sandbox (v0.2); :branch needs MCTS (Phase 3).
_UNIMPLEMENTED_KINDS = frozenset({":code", ":branch"})

_UPGRADE_MESSAGES = {
    ":code": ":code execution lands in v0.2 with e2b/docker sandbox harness",
    ":branch": ":branch speculative search lands in Phase 3 with MCTS outer loop",
}


def walk(
    node: Node,
    visitor: Callable[[Node, tuple[str, ...]], None] | None = None,
) -> list[str]:
    """Depth-first traversal. Returns ordered list of :ids visited.

    Args:
        node: root Node to walk.
        visitor: optional callback(node, path) called per node. ``path`` is the
            breadcrumb of tags from root. No side effects in v0.1.

    Returns:
        List of ``:id`` strings in depth-first, parent-before-children order.

    Raises:
        UnimplementedNodeKindError: walker encountered :code or :branch in
            leaf position. Message names the v0.x that ships real support.
    """
    trace: list[str] = []
    _walk_recursive(node, (), visitor, trace)
    return trace


def _walk_recursive(
    node: Node,
    path: tuple[str, ...],
    visitor: Callable[[Node, tuple[str, ...]], None] | None,
    trace: list[str],
) -> None:
    # Raise BEFORE recording so :id trace does not include unimplemented nodes.
    if node.tag in _UNIMPLEMENTED_KINDS and not node.children:
        raise UnimplementedNodeKindError(
            f"{node.tag} is not supported in persistence.plan v0.1. "
            f"{_UPGRADE_MESSAGES[node.tag]}"
        )

    current_path = path + (node.tag,)
    trace.append(node.id)
    if visitor is not None:
        visitor(node, current_path)

    for child in node.children:
        _walk_recursive(child, current_path, visitor, trace)
