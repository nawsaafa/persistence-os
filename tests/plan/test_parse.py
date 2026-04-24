# tests/plan/test_parse.py
"""EDN parse / unparse tests — byte-identical round-trip + spec validation."""
from __future__ import annotations

import pytest

from persistence.plan import Node, parse, ParseError
from persistence.plan._parse import PlanSpecError


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
        assert len(n.id) == 32


class TestPlanSpecErrorSharedBase:
    """R3-M2: PlanSpecError inherits from SpecError so downstream catchers
    that want to handle 'any persistence-substrate spec-validation failure'
    can import one symbol, not two.
    """

    def test_plan_spec_error_is_spec_error(self):
        """PlanSpecError inherits from SpecError so downstream catchers work uniformly."""
        from persistence.plan._parse import PlanSpecError
        from persistence.spec._registry import SpecError
        assert issubclass(PlanSpecError, SpecError)

    def test_plan_spec_error_caught_as_spec_error(self):
        """A real plan validation failure is catchable as SpecError and
        carries both `conform_error` (back-compat) and `error` (from SpecError)."""
        from persistence.plan import parse
        from persistence.spec._registry import SpecError
        # A vector that triggers spec failure (unknown kind).
        bad = "[:totally-not-a-real-kind {}]"
        try:
            parse(bad, strict=True)
        except SpecError as e:
            assert hasattr(e, "conform_error")  # back-compat with PlanSpecError pre-R3
            assert hasattr(e, "error")  # from SpecError parent
        else:
            raise AssertionError("parse(..., strict=True) should have raised")


class TestUserSuppliedIdStripped:
    """User-supplied :id in EDN must never enter the canonical form.

    Two failure modes both land on a malicious user poisoning the hash:
      1. :id enters attrs → _canonical_dict includes it → Node.id depends
         on user input, breaking content-addressing (Claim 1).
      2. _to_vector_form writes computed :id then loops node.attrs, so
         a user-supplied id key clobbers the computed one at spec-check
         time → spec validates the attacker's hash, not ours.

    Fix: drop both "id" and ":id" from attrs_raw at _python_to_node().
    """

    def test_user_supplied_id_is_stripped_from_attrs(self):
        edn = '[:seq {:id "malicious"} [:llm-call {:prompt "hi"}]]'
        n = parse(edn, strict=False)
        assert "id" not in n.attrs
        assert ":id" not in n.attrs

    def test_user_supplied_id_does_not_affect_computed_id(self):
        """Parsing with and without the injected :id yields the same Node.id."""
        with_id = parse('[:seq {:id "malicious"} [:llm-call {:prompt "hi"}]]', strict=False)
        without_id = parse('[:seq {} [:llm-call {:prompt "hi"}]]', strict=False)
        assert with_id.id == without_id.id

    def test_user_supplied_id_does_not_clobber_spec_validation(self):
        """Strict parse must succeed and the :id in the vector form
        must be the computed Node.id, not the attacker-supplied one."""
        from persistence.plan._parse import _to_vector_form

        n = parse('[:seq {:id "malicious"} [:llm-call {:prompt "hi"}]]', strict=True)
        vec = _to_vector_form(n)
        # vector form: [tag, attrs_dict, *children]
        attrs = vec[1]
        assert attrs[":id"] == f"sha256:{n.id}"
        assert "malicious" not in attrs[":id"]

    def test_user_supplied_id_both_keyword_and_string_form_stripped(self):
        """Parser sees ':id' as the key after keyword-stripping. A raw string
        "id" key in Python-constructed input (via lower_aliases reprocessing,
        etc.) must also be stripped if it sneaks in through _edn_to_python."""
        # Only :id is reachable via EDN, but be defensive.
        edn = '[:seq {:id "x" :name "n"} [:llm-call {:prompt "hi"}]]'
        n = parse(edn, strict=False)
        assert "id" not in n.attrs
        assert ":id" not in n.attrs
        assert n.attrs.get("name") == "n"

    def test_payload_id_in_args_survives(self):
        """Payload :id nested in attrs (e.g. a tool arg) is data, not Node identity.

        Contract: only the TOP-LEVEL :id on a Node is the content-address handle
        (stripped at parse time and recomputed). Nested maps inside attr values
        are opaque payload — a `:id` key there survives untouched.

        Regression pin: the :id-stripping in `_python_to_node` must NOT recurse
        into attr values. If it did, a tool call that legitimately needed to
        carry an external reference id would silently lose it.
        """
        edn = '[:tool-call {:tool :http/get :args {:id "ref-123" :limit 5}}]'
        node = parse(edn, strict=False)
        # Top-level :id was never in the source, so the strip is a no-op at
        # the Node level; the load-bearing assertion is about nested payload.
        assert "id" not in node.attrs
        assert ":id" not in node.attrs
        # The nested :id survives as opaque data inside the args map.
        assert node.attrs["args"] == {"id": "ref-123", "limit": 5}


class TestParseAllNodeKindsMalformed:
    """Per-kind malformed coverage — every known kind has a shape the
    registered :persistence.plan/node spec rejects at strict=True.

    R2 M6 asked for per-kind negative coverage. v0.1 spec enforces
    SHAPE invariants (tag enum + attrs dict + keyword attr keys +
    recursive child validation) but not per-kind required attrs
    (e.g., :tool-call needing :tool). We pick shape-level malformations
    each kind must reject:

      (a) unknown child tag — recursive child validation fails.
      (b) malformed attrs (list instead of dict) — NOTE: bare-shorthand
          fix in R2 C4 makes ``[:tag [child]]`` legal; so (b) needs a
          shape that can't be confused with shorthand — a string at
          position 1.
      (c) non-keyword-form children — bare keyword at child position.

    We use shape (a): every kind is tested with a malformed :not-a-real-kind
    child. The parent tag drives the parametrize so each kind exercises
    the spec independently.
    """

    PLAN_KINDS_WITH_CHILDREN = [
        ":seq", ":par", ":choice", ":loop", ":race", ":let", ":branch", ":case",
    ]

    @pytest.mark.parametrize("parent_tag", PLAN_KINDS_WITH_CHILDREN)
    def test_kind_with_malformed_child_rejected(self, parent_tag: str):
        """Every kind that accepts children recursively validates them."""
        edn = f"[{parent_tag} {{}} [:not-a-real-kind {{}}]]"
        with pytest.raises(PlanSpecError):
            parse(edn, strict=True)

    PLAN_LEAF_KINDS = [
        ":tool-call", ":llm-call", ":code", ":checkpoint",
        ":reflect", ":verify", ":call-skill", ":ref",
    ]

    @pytest.mark.parametrize("leaf_tag", PLAN_LEAF_KINDS)
    def test_leaf_kind_rejects_non_dict_attrs(self, leaf_tag: str):
        """Every leaf kind rejects a string at position 1 (cannot be bare
        shorthand because shorthand requires a list, not a string)."""
        edn = f'[{leaf_tag} "not-a-map"]'
        with pytest.raises(ParseError, match="attrs must be map"):
            parse(edn, strict=True)

    @pytest.mark.parametrize("tag", [
        ":seq", ":par", ":choice", ":loop", ":race", ":let", ":branch", ":case",
        ":tool-call", ":llm-call", ":code", ":checkpoint",
        ":reflect", ":verify", ":call-skill", ":ref",
    ])
    def test_every_kind_wrong_tag_case_rejected(self, tag: str):
        """Tag case-sensitivity: uppercase tag variants not in the enum."""
        uppercased = tag.upper()
        edn = f"[{uppercased} {{}}]"
        with pytest.raises(PlanSpecError):
            parse(edn, strict=True)


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
    def test_per_kind_required_attrs_v02(self, edn: str, reason: str):  # noqa: ARG002 — reason doc only
        """Will xfail until v0.2 tightens :persistence.plan/node per-kind."""
        with pytest.raises(PlanSpecError):
            parse(edn, strict=True)


from persistence.plan import unparse


class TestUnparse:
    def test_unparse_empty_seq(self):
        n = Node(tag=":seq", attrs={}, children=())
        assert unparse(n) == "[:seq {}]"

    def test_unparse_llm_call_with_attrs(self):
        n = Node(tag=":llm-call", attrs={"prompt": "hi"}, children=())
        assert unparse(n) == '[:llm-call {:prompt "hi"}]'

    def test_unparse_nested(self):
        inner = Node(tag=":llm-call", attrs={"prompt": "deep"}, children=())
        outer = Node(tag=":seq", attrs={}, children=(inner,))
        assert unparse(outer) == '[:seq {} [:llm-call {:prompt "deep"}]]'

    def test_unparse_sorts_attrs_keys(self):
        """Canonical form sorts attrs keys alphabetically."""
        n = Node(tag=":llm-call", attrs={"z": 1, "a": "x"}, children=())
        assert unparse(n) == '[:llm-call {:a "x" :z 1}]'

    def test_unparse_handles_nested_attrs_maps(self):
        n = Node(tag=":tool-call", attrs={"args": {"url": "x"}}, children=())
        assert unparse(n) == '[:tool-call {:args {:url "x"}}]'


class TestRoundTrip:
    """unparse(parse(x)) == x byte-identical for canonical inputs.

    Non-canonical inputs (extra whitespace, different attr order) are explicitly
    NOT round-trip preserved — canonicalisation is the whole point.
    """

    CANONICAL_SHAPES = [
        "[:seq {}]",
        '[:llm-call {:prompt "hi"}]',
        '[:seq {} [:llm-call {:prompt "a"}] [:llm-call {:prompt "b"}]]',
        '[:tool-call {:args {:url "x"} :tool :http/get}]',
        '[:par {:join :all} [:llm-call {:prompt "x"}]]',
    ]

    @pytest.mark.parametrize("canonical", CANONICAL_SHAPES)
    def test_round_trip_byte_identical(self, canonical: str):
        assert unparse(parse(canonical, strict=False)) == canonical

    def test_non_canonical_input_normalises(self):
        """Unsorted attrs in input → sorted attrs in unparse output."""
        non_canonical = '[:llm-call {:z 1 :a "x"}]'
        canonical = '[:llm-call {:a "x" :z 1}]'
        assert unparse(parse(non_canonical, strict=False)) == canonical

    def test_round_trip_idempotent(self):
        """unparse(parse(unparse(parse(x)))) == unparse(parse(x)) for any x."""
        input_edn = '[:llm-call {:z 1 :a 2}]'
        once = unparse(parse(input_edn, strict=False))
        twice = unparse(parse(once, strict=False))
        assert once == twice


class TestAliasLowering:
    def test_phase_lowered_to_seq(self):
        edn = '[:phase {:id "p1" :name "Bootstrap"} [:llm-call {:prompt "x"}]]'
        n = parse(edn, lower_aliases={":phase": ":seq"}, strict=False)
        assert n.tag == ":seq"  # lowered
        assert n.attrs["name"] == "Bootstrap"  # attrs preserved
        assert n.children[0].tag == ":llm-call"

    def test_workstream_lowered_to_seq(self):
        edn = '[:workstream {:id :ws/fact :owner :team/fact} [:llm-call {:prompt "x"}]]'
        n = parse(edn, lower_aliases={":workstream": ":seq"}, strict=False)
        assert n.tag == ":seq"

    def test_multiple_aliases_lowered_recursively(self):
        edn = '[:phase {} [:workstream {} [:llm-call {:prompt "deep"}]]]'
        n = parse(
            edn,
            lower_aliases={":phase": ":seq", ":workstream": ":seq"},
            strict=False,
        )
        assert n.tag == ":seq"
        assert n.children[0].tag == ":seq"
        assert n.children[0].children[0].tag == ":llm-call"

    def test_alias_not_round_trip_preserved(self):
        """Aliased inputs do NOT round-trip — documented behavior."""
        original = '[:phase {} [:llm-call {:prompt "x"}]]'
        n = parse(original, lower_aliases={":phase": ":seq"}, strict=False)
        emitted = unparse(n)
        assert emitted != original  # explicit non-invariant
        assert emitted.startswith("[:seq")

    def test_no_aliases_kwarg_no_lowering(self):
        """Without lower_aliases, unknown tags passed through (strict=False)."""
        edn = '[:phase {} [:llm-call {:prompt "x"}]]'
        n = parse(edn, strict=False)
        assert n.tag == ":phase"  # unchanged
