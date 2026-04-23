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


class TestParseNested:
    def test_parse_seq_with_single_child(self):
        n = parse('[:seq {} [:llm-call {:prompt "hi"}]]')
        assert n.tag == ":seq"
        assert len(n.children) == 1
        assert n.children[0].tag == ":llm-call"

    def test_parse_seq_with_multiple_children(self):
        edn = '[:seq {} [:tool-call {:tool :a :args {}}] [:tool-call {:tool :b :args {}}]]'
        n = parse(edn)
        assert len(n.children) == 2
        assert n.children[0].attrs["tool"] == ":a"
        assert n.children[1].attrs["tool"] == ":b"

    def test_parse_deeply_nested(self):
        edn = '[:seq {} [:seq {} [:seq {} [:llm-call {:prompt "deep"}]]]]'
        n = parse(edn)
        assert n.tag == ":seq"
        assert n.children[0].tag == ":seq"
        assert n.children[0].children[0].tag == ":seq"
        assert n.children[0].children[0].children[0].tag == ":llm-call"

    def test_parse_par_with_mixed_leaf_types(self):
        edn = '[:par {:join :all} [:llm-call {:prompt "x"}] [:tool-call {:tool :y :args {}}]]'
        n = parse(edn)
        assert n.tag == ":par"
        assert dict(n.attrs) == {"join": ":all"}
        assert n.children[0].tag == ":llm-call"
        assert n.children[1].tag == ":tool-call"
