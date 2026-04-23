# tests/plan/test_parse.py
"""EDN parse / unparse tests — byte-identical round-trip + spec validation."""
from __future__ import annotations

import pytest

from persistence.plan import Node, parse, ParseError


class TestParseLeaf:
    def test_parse_seq_empty(self):
        n = parse("[:seq {}]")
        assert isinstance(n, Node)
        assert n.tag == ":seq"
        assert dict(n.attrs) == {}
        assert n.children == ()

    def test_parse_llm_call_with_attrs(self):
        n = parse('[:llm-call {:prompt "hello" :model :opus-4.7}]')
        assert n.tag == ":llm-call"
        assert dict(n.attrs) == {"prompt": "hello", "model": ":opus-4.7"}
        assert n.children == ()

    def test_parse_tool_call_with_args_map(self):
        n = parse('[:tool-call {:tool :http/get :args {:url "https://x.com"}}]')
        assert n.tag == ":tool-call"
        assert n.attrs["tool"] == ":http/get"
        assert n.attrs["args"] == {"url": "https://x.com"}
