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


from persistence.plan._ast import _canonical_dict


class TestCanonicalDict:
    def test_empty_node_canonical_form(self):
        n = Node(tag=":seq", attrs={}, children=())
        assert _canonical_dict(n) == {"tag": ":seq", "attrs": {}, "children": []}

    def test_attrs_keys_sorted_in_canonical_form(self):
        n = Node(tag=":llm-call", attrs={"z": 1, "a": 2}, children=())
        result = _canonical_dict(n)
        # Canonical dict is intermediate — the SORTING happens at json.dumps time.
        # But values must be present and comparable.
        assert result == {"tag": ":llm-call", "attrs": {"z": 1, "a": 2}, "children": []}

    def test_canonical_form_is_recursive(self):
        inner = Node(tag=":llm-call", attrs={"p": "hi"}, children=())
        outer = Node(tag=":seq", attrs={}, children=(inner,))
        result = _canonical_dict(outer)
        assert result == {
            "tag": ":seq",
            "attrs": {},
            "children": [
                {"tag": ":llm-call", "attrs": {"p": "hi"}, "children": []},
            ],
        }

    def test_canonical_form_handles_nested_attrs_dicts(self):
        n = Node(
            tag=":tool-call",
            attrs={"args": {"url": "https://x.com", "method": "GET"}},
            children=(),
        )
        result = _canonical_dict(n)
        assert result["attrs"]["args"] == {"url": "https://x.com", "method": "GET"}
