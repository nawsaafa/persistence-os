# tests/plan/test_misc.py
"""Edge cases — unicode, empty trees, deep nesting, determinism."""
from __future__ import annotations

from persistence.plan import Node, parse, unparse, walk


class TestEdgeCases:
    def test_unicode_in_prompt_preserved(self):
        edn = '[:llm-call {:prompt "Hello 世界 🌍"}]'
        n = parse(edn, strict=False)
        assert n.attrs["prompt"] == "Hello 世界 🌍"
        assert unparse(n) == edn

    def test_empty_seq_children(self):
        n = Node(tag=":seq", attrs={}, children=())
        assert walk(n) == [n.id]
        assert unparse(n) == "[:seq {}]"

    def test_deeply_nested_parses_and_walks(self):
        """100-level deep :seq nesting — no arbitrary depth limit in v0.1."""
        edn_open = "[:seq {} " * 100
        edn_close = "]" * 100
        leaf = '[:llm-call {:prompt "deep"}]'
        edn = edn_open + leaf + edn_close
        n = parse(edn, strict=False)
        ids = walk(n)
        assert len(ids) == 101  # 100 :seq + 1 :llm-call

    def test_numeric_attr_value(self):
        n = Node(tag=":loop", attrs={"max-iter": 42}, children=())
        assert unparse(n) == "[:loop {:max-iter 42}]"
        round_trip = parse(unparse(n), strict=False)
        assert round_trip.attrs["max-iter"] == 42

    def test_boolean_attr_value(self):
        n = Node(tag=":checkpoint", attrs={"persist": True}, children=())
        assert unparse(n) == "[:checkpoint {:persist true}]"

    def test_nested_map_value(self):
        n = Node(
            tag=":tool-call",
            attrs={"args": {"headers": {"X-Key": "abc"}, "method": "POST"}},
            children=(),
        )
        emitted = unparse(n)
        # Keys sorted at every nesting level
        assert emitted == '[:tool-call {:args {:headers {:X-Key "abc"} :method "POST"}}]'

    def test_round_trip_preserves_id(self):
        """:id is computed from canonical form — parse(unparse(n)).id == n.id."""
        n = Node(
            tag=":seq",
            attrs={"name": "test"},
            children=(Node(tag=":llm-call", attrs={"prompt": "x"}, children=()),),
        )
        re_parsed = parse(unparse(n), strict=False)
        assert re_parsed.id == n.id
