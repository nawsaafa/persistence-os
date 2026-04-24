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


class TestWalkOrderByKind:
    def test_par_children_document_order(self):
        """v0.1 walks :par children in document order — not actual parallelism."""
        c1 = Node(tag=":llm-call", attrs={"prompt": "1"}, children=())
        c2 = Node(tag=":llm-call", attrs={"prompt": "2"}, children=())
        root = Node(tag=":par", attrs={"join": ":all"}, children=(c1, c2))
        ids = walk(root)
        assert ids == [root.id, c1.id, c2.id]

    def test_choice_walks_all_case_arms(self):
        """v0.1 walks ALL :case branches — this is pre-execution structural
        analysis, not runtime selector dispatch."""
        arm_a = Node(
            tag=":case",
            attrs={"match": ":bull"},
            children=(Node(tag=":llm-call", attrs={"prompt": "up"}, children=()),),
        )
        arm_b = Node(
            tag=":case",
            attrs={"match": ":bear"},
            children=(Node(tag=":llm-call", attrs={"prompt": "down"}, children=()),),
        )
        root = Node(tag=":choice", attrs={"selector": ":regime"}, children=(arm_a, arm_b))
        ids = walk(root)
        # root + arm_a + arm_a.child + arm_b + arm_b.child = 5 ids
        assert len(ids) == 5

    def test_loop_body_walked_once(self):
        """v0.1 walks :loop body ONCE — unrolling is executor concern."""
        body = Node(tag=":llm-call", attrs={"prompt": "retry"}, children=())
        root = Node(tag=":loop", attrs={"max-iter": 3}, children=(body,))
        ids = walk(root)
        assert ids == [root.id, body.id]  # exactly one body visit

    def test_race_children_walked_once_each(self):
        c1 = Node(tag=":llm-call", attrs={"prompt": "a"}, children=())
        c2 = Node(tag=":llm-call", attrs={"prompt": "b"}, children=())
        root = Node(tag=":race", attrs={"timeout-ms": 1000}, children=(c1, c2))
        ids = walk(root)
        assert ids == [root.id, c1.id, c2.id]

    def test_let_body_walked_normally(self):
        body = Node(tag=":llm-call", attrs={"prompt": "use-x"}, children=())
        root = Node(tag=":let", attrs={"bindings": {"x": 1}}, children=(body,))
        ids = walk(root)
        assert ids == [root.id, body.id]


class TestUnimplemented:
    def test_code_leaf_raises(self):
        n = Node(tag=":code", attrs={"lang": ":python", "body": "pass"}, children=())
        with pytest.raises(UnimplementedNodeKindError, match="v0.2"):
            walk(n)

    def test_branch_leaf_raises(self):
        n = Node(tag=":branch", attrs={"strategy": ":beam", "k": 3}, children=())
        with pytest.raises(UnimplementedNodeKindError, match="Phase 3"):
            walk(n)

    def test_code_with_children_does_not_raise(self):
        """Edge case: :code with children is a spec-malformed shape,
        but walker only raises for leaf :code. Spec validation is the gate
        for no-children-allowed check."""
        child = Node(tag=":llm-call", attrs={"prompt": "x"}, children=())
        n = Node(tag=":code", attrs={"lang": ":python", "body": "pass"}, children=(child,))
        # Walker walks it normally; spec layer would reject this shape.
        ids = walk(n)
        assert ids == [n.id, child.id]

    def test_error_message_names_upgrade_version(self):
        n = Node(tag=":code", attrs={"lang": ":python", "body": "pass"}, children=())
        with pytest.raises(UnimplementedNodeKindError) as excinfo:
            walk(n)
        assert "v0.2" in str(excinfo.value)
        assert "sandbox" in str(excinfo.value).lower()

    def test_error_raised_before_id_added_to_trace(self):
        """When walker hits unimplemented, exception propagates out of walk()."""
        ok = Node(tag=":llm-call", attrs={"prompt": "first"}, children=())
        bad = Node(tag=":code", attrs={"lang": ":python", "body": "pass"}, children=())
        root = Node(tag=":seq", attrs={}, children=(ok, bad))
        with pytest.raises(UnimplementedNodeKindError):
            walk(root)
