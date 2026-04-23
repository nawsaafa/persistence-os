# tests/plan/test_ast.py
"""Node AST tests — construction, canonical form, :id."""
from __future__ import annotations

import pytest

from persistence.plan import Node


class TestNodeConstruction:
    def test_node_is_frozen_dataclass_with_tag_attrs_children(self):
        """Node(tag, attrs, children) holds immutable tag + attrs + tuple of children."""
        n = Node(tag=":seq", attrs={}, children=())
        assert n.tag == ":seq"
        assert n.attrs == {}
        assert n.children == ()

    def test_node_is_frozen_cannot_mutate(self):
        """Node is immutable — attribute assignment raises."""
        n = Node(tag=":seq", attrs={}, children=())
        with pytest.raises((AttributeError, TypeError)):
            n.tag = ":par"  # type: ignore[misc]

    def test_node_children_must_be_tuple_of_nodes_or_empty(self):
        """children accepts tuple of Node (possibly empty)."""
        child = Node(tag=":llm-call", attrs={"prompt": "hi"}, children=())
        parent = Node(tag=":seq", attrs={}, children=(child,))
        assert parent.children == (child,)
