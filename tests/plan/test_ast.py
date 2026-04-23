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


class TestNodeId:
    def test_id_is_16_hex_chars(self):
        n = Node(tag=":seq", attrs={}, children=())
        assert len(n.id) == 16
        assert all(c in "0123456789abcdef" for c in n.id)

    def test_identical_nodes_have_identical_id(self):
        a = Node(tag=":llm-call", attrs={"prompt": "hi"}, children=())
        b = Node(tag=":llm-call", attrs={"prompt": "hi"}, children=())
        assert a.id == b.id

    def test_different_tag_different_id(self):
        a = Node(tag=":seq", attrs={}, children=())
        b = Node(tag=":par", attrs={}, children=())
        assert a.id != b.id

    def test_different_attrs_different_id(self):
        a = Node(tag=":llm-call", attrs={"prompt": "hi"}, children=())
        b = Node(tag=":llm-call", attrs={"prompt": "bye"}, children=())
        assert a.id != b.id

    def test_different_children_different_id(self):
        child = Node(tag=":llm-call", attrs={"prompt": "hi"}, children=())
        a = Node(tag=":seq", attrs={}, children=())
        b = Node(tag=":seq", attrs={}, children=(child,))
        assert a.id != b.id

    def test_attrs_key_order_does_not_affect_id(self):
        """Canonical form sorts attrs keys — key-insertion order is irrelevant."""
        a = Node(tag=":llm-call", attrs={"a": 1, "z": 2}, children=())
        b = Node(tag=":llm-call", attrs={"z": 2, "a": 1}, children=())
        assert a.id == b.id

    def test_child_order_DOES_affect_id(self):
        """:seq is ordered — child order is semantic."""
        c1 = Node(tag=":llm-call", attrs={"prompt": "a"}, children=())
        c2 = Node(tag=":llm-call", attrs={"prompt": "b"}, children=())
        a = Node(tag=":seq", attrs={}, children=(c1, c2))
        b = Node(tag=":seq", attrs={}, children=(c2, c1))
        assert a.id != b.id


import subprocess
import sys


class TestIdDeterminism:
    def test_id_is_deterministic_across_processes(self, tmp_path):
        """Same Node constructed in a fresh Python process → identical :id.

        This is the content-addressing contract: two agents independently
        deriving the same plan fragment MUST hash-collide.
        """
        script = tmp_path / "print_id.py"
        script.write_text(
            "import sys; sys.path.insert(0, 'src')\n"
            "from persistence.plan import Node\n"
            "n = Node(tag=':llm-call', attrs={'prompt': 'hello', 'model': ':opus-4.7'}, children=())\n"
            "print(n.id)\n"
        )

        def run_in_subprocess() -> str:
            result = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                cwd="/Users/nawfalsaadi/Projects/persistence-os",
            )
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()

        id_a = run_in_subprocess()
        id_b = run_in_subprocess()
        assert id_a == id_b
        assert len(id_a) == 16
