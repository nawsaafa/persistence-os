# tests/plan/test_parse.py
"""EDN parse / unparse tests — byte-identical round-trip + spec validation."""
from __future__ import annotations

import pytest

from persistence.plan import Node, parse, ParseError
from persistence.plan._parse import PlanSpecError
from persistence.spec import ConformError


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


class TestParseAllNodeKinds:
    """Each of the 16 spec-listed node kinds parses into a valid Node.

    :code and :branch parse at this layer (they are in the spec) but raise
    UnimplementedNodeKindError when walked (Task 21).
    """

    @pytest.mark.parametrize("edn,expected_tag", [
        # Control operators
        ('[:seq {:id "abc"} [:llm-call {:prompt "x"}]]', ":seq"),
        ('[:par {:join :all} [:llm-call {:prompt "x"}] [:llm-call {:prompt "y"}]]', ":par"),
        ('[:choice {:selector :regime} [:case {:match :bull} [:llm-call {:prompt "up"}]]]', ":choice"),
        ('[:loop {:while :retry :max-iter 3} [:llm-call {:prompt "try"}]]', ":loop"),
        ('[:race {:timeout-ms 5000} [:llm-call {:prompt "a"}] [:llm-call {:prompt "b"}]]', ":race"),
        ('[:let {:bindings {:x 1}} [:llm-call {:prompt "use-x"}]]', ":let"),
        # Case arm (used inside :choice) — uniform [tag attrs *children] shape per spec at
        # src/persistence/spec/_canonical.py:469 (attrs must be dict); pred lives in attrs as :match.
        ('[:case {:match :bull} [:llm-call {:prompt "up"}]]', ":case"),
        # Effect leaves (parse OK)
        ('[:tool-call {:tool :http/get :args {:url "x"}}]', ":tool-call"),
        ('[:llm-call {:signature :q->a :prompt "hi" :model :opus-4.7}]', ":llm-call"),
        ('[:code {:lang :python :body "pass"}]', ":code"),
        # Cognitive operators
        ('[:reflect {:criteria ["cost"]}]', ":reflect"),
        ('[:checkpoint {:persist :vault :tier :L1}]', ":checkpoint"),
        ('[:verify {:prover :heuristic :claim "non-empty"}]', ":verify"),
        ('[:call-skill {:skill :skill/boa@v3 :args {}}]', ":call-skill"),
        # Binding / dataflow
        ('[:ref {:symbol :q}]', ":ref"),
        # Speculative search (parse OK, walk raises)
        ('[:branch {:strategy :beam :k 3} [:llm-call {:prompt "variant"}]]', ":branch"),
    ])
    def test_each_kind_parses(self, edn: str, expected_tag: str):
        n = parse(edn, strict=False)  # strict=False — spec validation comes in Task 13
        assert n.tag == expected_tag


class TestParseErrors:
    def test_parse_raises_on_empty_string(self):
        with pytest.raises(ParseError):
            parse("")

    def test_parse_raises_on_garbage(self):
        with pytest.raises(ParseError):
            parse("this is not edn at all {")

    def test_parse_raises_on_top_level_non_vector(self):
        with pytest.raises(ParseError):
            parse('{:tag ":seq"}')  # map, not vector

    def test_parse_raises_on_empty_vector(self):
        with pytest.raises(ParseError, match="too short"):
            parse("[]")

    def test_parse_raises_on_missing_attrs_map(self):
        with pytest.raises(ParseError):
            parse("[:seq]")  # only tag, no attrs

    def test_parse_raises_on_attrs_not_a_map(self):
        with pytest.raises(ParseError, match="attrs must be map"):
            parse('[:seq "not-a-map"]')

    def test_parse_raises_on_tag_not_keyword(self):
        with pytest.raises(ParseError, match="tag must be keyword"):
            parse('["seq" {}]')  # string, not keyword


class TestSpecValidation:
    def test_parse_strict_rejects_unknown_tag(self):
        """:not-a-real-kind is not in :persistence.plan/node enum."""
        with pytest.raises(PlanSpecError) as excinfo:
            parse('[:not-a-real-kind {}]', strict=True)
        err = excinfo.value
        assert ":persistence.plan/node" in str(err.spec_key) or ":persistence.plan/node" in repr(err)

    def test_parse_strict_accepts_valid_seq(self):
        """Valid :seq passes spec validation — :id auto-injected from Node.id."""
        n = parse('[:seq {} [:llm-call {:prompt "hi"}]]', strict=True)
        assert n.tag == ":seq"

    def test_parse_non_strict_skips_validation(self):
        """strict=False bypasses spec check — used for testing."""
        n = parse('[:not-a-real-kind {}]', strict=False)
        assert n.tag == ":not-a-real-kind"

    def test_strict_validates_nested_children(self):
        """Spec validation is recursive — bad child → PlanSpecError."""
        with pytest.raises(PlanSpecError):
            parse('[:seq {} [:not-a-real-kind {}]]', strict=True)

    def test_id_auto_injected_at_validate_time_not_on_node(self):
        """Validation injects :id into vector form for spec check;
        Node.attrs itself is unchanged (:id is computed, not stored)."""
        n = parse('[:seq {} [:llm-call {:prompt "hi"}]]', strict=True)
        assert ":id" not in n.attrs
        assert "id" not in n.attrs
        # But Node.id is always available (computed)
        assert len(n.id) == 16


class TestSpecValidationMalformed:
    """Each node kind has malformed shapes spec should catch.

    v0.1 limitation: :persistence.plan/node (current form) validates
    TOP-LEVEL shape (tag enum + attrs dict + :id + keyword keys + recursive
    children) but does NOT enforce per-kind required attrs like :tool-call
    needing :tool. Tests that depend on per-kind tightness are marked xfail
    as v0.2 spec-tightening work. See §13 of the design doc (R2 flag).
    """

    def test_recursive_validation_catches_bad_child(self):
        """Child with unknown kind is rejected recursively."""
        with pytest.raises(PlanSpecError):
            parse('[:seq {} [:not-a-kind {}]]', strict=True)

    def test_tag_not_in_enum_rejected(self):
        """Unknown tag at top level rejected."""
        with pytest.raises(PlanSpecError):
            parse('[:not-a-real-kind {}]', strict=True)

    @pytest.mark.xfail(
        reason=(
            "v0.1 spec does not enforce per-kind required attrs — "
            ":tool-call without :tool, :llm-call without :prompt, etc. "
            "are valid at the shape level. Per-kind tightening is a v0.2 "
            "spec extension (R2 flag in design doc §13)."
        ),
        strict=False,
    )
    @pytest.mark.parametrize("edn,reason", [
        ('[:tool-call {:args {}}]', "tool-call missing :tool"),
        ('[:llm-call {:model :opus-4.7}]', "llm-call missing :prompt"),
        ('[:checkpoint {}]', "checkpoint missing :tier"),
        ('[:verify {:claim "x"}]', "verify missing :prover"),
        ('[:call-skill {:args {}}]', "call-skill missing :skill"),
        ('[:loop {} [:llm-call {:prompt "x"}]]', "loop missing :max-iter"),
        ('[:choice {:selector :x}]', "choice needs at least one case arm"),
    ])
    def test_per_kind_required_attrs_v02(self, edn: str, reason: str):
        """Will xfail until v0.2 tightens :persistence.plan/node per-kind."""
        with pytest.raises(PlanSpecError):
            parse(edn, strict=True)
