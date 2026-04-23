# tests/plan/test_interpret.py
"""Walker tests — depth-first :id trace, no executors in v0.1."""
from __future__ import annotations

import pytest

from persistence.plan import Node, UnimplementedNodeKindError, parse, walk


class TestWalkBasic:
    def test_walk_leaf_emits_single_id(self):
        n = Node(tag=":llm-call", attrs={"prompt": "hi"}, children=())
        ids = walk(n)
        assert ids == [n.id]

    def test_walk_seq_parent_before_children(self):
        c1 = Node(tag=":llm-call", attrs={"prompt": "a"}, children=())
        c2 = Node(tag=":llm-call", attrs={"prompt": "b"}, children=())
        root = Node(tag=":seq", attrs={}, children=(c1, c2))
        ids = walk(root)
        assert ids == [root.id, c1.id, c2.id]

    def test_walk_depth_first(self):
        inner = Node(tag=":llm-call", attrs={"prompt": "inner"}, children=())
        mid = Node(tag=":seq", attrs={}, children=(inner,))
        sibling = Node(tag=":llm-call", attrs={"prompt": "sib"}, children=())
        root = Node(tag=":seq", attrs={}, children=(mid, sibling))
        ids = walk(root)
        assert ids == [root.id, mid.id, inner.id, sibling.id]


class TestWalkVisitor:
    def test_visitor_called_per_node(self):
        c = Node(tag=":llm-call", attrs={"prompt": "x"}, children=())
        root = Node(tag=":seq", attrs={}, children=(c,))

        visited: list[tuple[str, tuple[str, ...]]] = []

        def visitor(node: Node, path: tuple[str, ...]) -> None:
            visited.append((node.tag, path))

        walk(root, visitor=visitor)
        assert visited == [
            (":seq", (":seq",)),
            (":llm-call", (":seq", ":llm-call")),
        ]

    def test_visitor_receives_deep_path(self):
        inner = Node(tag=":llm-call", attrs={"prompt": "deep"}, children=())
        mid = Node(tag=":loop", attrs={"max-iter": 3}, children=(inner,))
        root = Node(tag=":seq", attrs={}, children=(mid,))

        deepest_path = []

        def visitor(node: Node, path: tuple[str, ...]) -> None:
            if node.tag == ":llm-call":
                deepest_path.append(path)

        walk(root, visitor=visitor)
        assert deepest_path == [(":seq", ":loop", ":llm-call")]
