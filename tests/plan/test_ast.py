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


class TestAttrKeyShape:
    """R1 m2: attr keys must be plain strings without leading colon.

    The internal convention is that attrs keys are the attribute names AFTER
    keyword-stripping: ``"prompt"`` not ``":prompt"``. Two Nodes constructed
    with ``{":prompt": "hi"}`` vs ``{"prompt": "hi"}`` would hash differently,
    so forbidding the ambiguous leading-colon form at construction time is
    the only way to preserve the Merkle-DAG claim.

    Non-string keys (int, None) are rejected for the same reason: canonical
    JSON would serialize them through str(), but content-equal Nodes written
    as {1: "x"} vs {"1": "x"} would hash-collide — ambiguity the content-
    addressing contract cannot tolerate.
    """

    def test_str_attr_keys_accepted(self):
        n = Node(tag=":llm-call", attrs={"prompt": "hi", "model": ":opus"}, children=())
        assert n.id  # does not raise

    def test_int_attr_key_rejects(self):
        with pytest.raises(ValueError, match="plain strings"):
            Node(tag=":x", attrs={1: "bad"}, children=())

    def test_none_attr_key_rejects(self):
        with pytest.raises(ValueError, match="plain strings"):
            Node(tag=":x", attrs={None: "bad"}, children=())

    def test_bytes_attr_key_rejects(self):
        with pytest.raises(ValueError, match="plain strings"):
            Node(tag=":x", attrs={b"prompt": "bad"}, children=())

    def test_colon_prefixed_attr_key_rejects(self):
        """`:foo` key is the EDN keyword form — must be stripped to "foo" before
        construction. Accepting both would yield two distinct canonical hashes
        for the same logical attribute."""
        with pytest.raises(ValueError, match="leading colon"):
            Node(tag=":x", attrs={":foo": "bad"}, children=())

    def test_empty_string_attr_key_rejects(self):
        """Empty string is a degenerate case that canonical-form can't round-trip."""
        with pytest.raises(ValueError, match="plain strings"):
            Node(tag=":x", attrs={"": "bad"}, children=())


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
    def test_id_is_32_hex_chars(self):
        """128-bit truncation of sha256.

        Birthday-collision probability of 1% reached at ~2×10^18 plans;
        widened from 64-bit (16 hex) in R2 because the smaller width put
        content-addressing at ~6×10^8 plans — too narrow to back the
        Merkle-DAG paper claim against adversarial inputs.
        """
        n = Node(tag=":seq", attrs={}, children=())
        assert len(n.id) == 32
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


class TestNonFiniteFloatsRejected:
    """Content-addressing requires deterministic equality.

    NaN ≠ NaN in Python, so two "identical" Nodes containing NaN would
    hash-collide but compare non-equal — invalidating Claim 1 (Merkle
    DAG). Infinity serializes as JSON ``Infinity`` which is not strict
    JSON and cannot round-trip either. Reject both at ``Node.id`` time.
    """

    def test_nan_in_attrs_rejects(self):
        n = Node(tag=":llm-call", attrs={"v": float("nan")}, children=())
        with pytest.raises(ValueError, match="non-finite"):
            n.id

    def test_positive_inf_in_attrs_rejects(self):
        n = Node(tag=":llm-call", attrs={"v": float("inf")}, children=())
        with pytest.raises(ValueError, match="non-finite"):
            n.id

    def test_negative_inf_in_attrs_rejects(self):
        n = Node(tag=":llm-call", attrs={"v": float("-inf")}, children=())
        with pytest.raises(ValueError, match="non-finite"):
            n.id

    def test_nan_in_nested_attrs_rejects(self):
        """Non-finite floats anywhere in the nested attr structure fail."""
        n = Node(
            tag=":tool-call",
            attrs={"args": {"timeout": float("nan")}},
            children=(),
        )
        with pytest.raises(ValueError, match="non-finite"):
            n.id

    def test_nan_in_child_attrs_rejects(self):
        """Recursion propagates non-finite rejection."""
        bad_child = Node(tag=":llm-call", attrs={"v": float("inf")}, children=())
        parent = Node(tag=":seq", attrs={}, children=(bad_child,))
        with pytest.raises(ValueError, match="non-finite"):
            parent.id


import subprocess
import sys


class TestIdDeterminism:
    def test_descendant_change_propagates_to_all_ancestors(self):
        """R1 m1: Merkle-DAG invariant — mutating any leaf propagates to EVERY ancestor.

        Build a 4-level tree, rebuild with the deepest leaf mutated, assert that
        every ancestor id (including the root) is different. Two consecutive
        levels with the same id under mutation would signal that the hashing
        isn't recursive.
        """
        leaf_a = Node(tag=":llm-call", attrs={"prompt": "original"}, children=())
        leaf_b = Node(tag=":llm-call", attrs={"prompt": "mutated"}, children=())

        def build_tree(leaf: Node) -> tuple[Node, Node, Node, Node]:
            """Return (root, l1, l2, leaf) 4-level deep chain."""
            l2 = Node(tag=":seq", attrs={}, children=(leaf,))
            l1 = Node(tag=":seq", attrs={}, children=(l2,))
            root = Node(tag=":seq", attrs={}, children=(l1,))
            return root, l1, l2, leaf

        root_a, l1_a, l2_a, _ = build_tree(leaf_a)
        root_b, l1_b, l2_b, _ = build_tree(leaf_b)

        assert leaf_a.id != leaf_b.id, "sanity: mutated leaf must differ"
        assert l2_a.id != l2_b.id, "l2 (leaf's parent) must differ"
        assert l1_a.id != l1_b.id, "l1 (l2's parent) must differ"
        assert root_a.id != root_b.id, "root must differ"

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
        assert len(id_a) == 32
