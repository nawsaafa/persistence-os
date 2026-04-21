"""`:persistence.plan/node` vector form (ARIS Round 3 P-plan-node).

Per ``docs/agent2-plan-spec.md`` §1 + §8 every plan node is an EDN vector:

    [:node-type {attrs} & children]

where ``:node-type`` is a keyword tag, ``{attrs}`` is a keyword-keyed
attribute map (always containing ``:id``), and the remaining elements are
recursive children (each itself a ``:persistence.plan/node``).

Before the fix, :persistence.plan/node was registered as the map form
``{:node/id … :node/kind …}`` via ``keys(...)``, which contradicted the
paper §4.7 methodology contribution. This test conforms a representative
multi-level plan AST (the Adaptive Trader v2 track plan from the doc
§5) against the new vector-shaped spec and exercises each of the
required node-type variants.
"""
from __future__ import annotations

import pytest

from persistence import spec as S


# ---------------------------------------------------------------------------
# Minimal synthetic plan from agent2-plan-spec.md §5 (AT-v2 track)
# ---------------------------------------------------------------------------


def _at_v2_plan():
    """Three-level vector plan matching §5 of the doc. IDs are hex so
    they satisfy :persistence.spec/sha256 (``sha256:<hex>``)."""
    return [
        ":seq", {":id": "sha256:deadbeef0001"},
        [":call-skill", {":id": "sha256:deadbeef0002",
                         ":skill": ":skill/aris-phase0-scope@v1"}],
        [":par", {":id": "sha256:deadbeef0003", ":join": ":all"},
            [":call-skill", {":id": "sha256:deadbeef0004",
                             ":skill": ":skill/worker-memory-layer@v1"}],
            [":call-skill", {":id": "sha256:deadbeef0005",
                             ":skill": ":skill/worker-market-intel@v1"}],
            [":call-skill", {":id": "sha256:deadbeef0006",
                             ":skill": ":skill/worker-playbook-engine@v1"}]],
        [":checkpoint", {":id": "sha256:deadbeef0007", ":tier": ":L1"}],
        [":verify", {":id": "sha256:deadbeef0008", ":prover": ":aris",
                     ":gate": ":round-4", ":min-score": 9.0}],
        [":reflect", {":id": "sha256:deadbeef0009",
                      ":criteria": ["scope-drift", "coverage"]}],
    ]


class TestPlanNodeVectorForm:
    """The spec must conform the vector form, and reject the map form."""

    def test_seq_with_children_ok(self):
        node = [":seq", {":id": "sha256:a1"}]
        assert S.conform(":persistence.plan/node", node).is_ok

    def test_llm_call_leaf_ok(self):
        node = [":llm-call", {":id": "sha256:b1",
                              ":signature": "in->out",
                              ":prompt": "hello",
                              ":model": ":opus-4.7"}]
        assert S.conform(":persistence.plan/node", node).is_ok

    def test_tool_call_leaf_ok(self):
        node = [":tool-call", {":id": "sha256:c1",
                               ":tool": ":http/get"}]
        assert S.conform(":persistence.plan/node", node).is_ok

    def test_branch_with_plan_variants_ok(self):
        node = [":branch", {":id": "sha256:d1",
                            ":strategy": ":beam", ":k": 3},
                [":seq", {":id": "sha256:d1a0d1a0"}]]
        assert S.conform(":persistence.plan/node", node).is_ok

    def test_policy_gate_via_verify_ok(self):
        # :verify is the proof-of-thought gate node per doc §1 cognitive ops
        node = [":verify", {":id": "sha256:e1", ":prover": ":z3"}]
        assert S.conform(":persistence.plan/node", node).is_ok

    def test_recursive_three_level_ast_ok(self):
        plan = _at_v2_plan()
        result = S.conform(":persistence.plan/node", plan)
        assert result.is_ok, (
            "Adaptive Trader v2 plan failed to conform to "
            f":persistence.plan/node: {result}"
        )

    def test_round_trip_preserves_structure(self):
        plan = _at_v2_plan()
        result = S.conform(":persistence.plan/node", plan)
        assert result.is_ok
        # Round-trip: conformed value must be structurally equal to input
        # (list/tuple symmetry accepted — the spec normalizes to tuple).
        roundtripped = list(result.value) if isinstance(result.value, tuple) else result.value
        assert roundtripped[0] == plan[0]
        assert roundtripped[1] == plan[1]

    def test_map_form_rejected(self):
        """The OLD map form must no longer conform."""
        old_map = {":node/id": "sha256:aa",
                   ":node/kind": ":seq",
                   ":node/children": []}
        assert not S.conform(":persistence.plan/node", old_map).is_ok

    def test_empty_vector_rejected(self):
        assert not S.conform(":persistence.plan/node", []).is_ok

    def test_missing_attrs_map_rejected(self):
        # Missing the attrs map (index 1) — vector [:seq] alone is malformed
        assert not S.conform(":persistence.plan/node", [":seq"]).is_ok

    def test_non_keyword_tag_rejected(self):
        node = ["seq", {":id": "sha256:aa"}]
        assert not S.conform(":persistence.plan/node", node).is_ok

    def test_unknown_tag_rejected(self):
        node = [":not-a-real-node", {":id": "sha256:aa"}]
        assert not S.conform(":persistence.plan/node", node).is_ok

    def test_attrs_missing_id_rejected(self):
        node = [":seq", {}]
        assert not S.conform(":persistence.plan/node", node).is_ok

    def test_non_dict_attrs_rejected(self):
        node = [":seq", "not-a-map"]
        assert not S.conform(":persistence.plan/node", node).is_ok


# ---------------------------------------------------------------------------
# Node-type variants — at least 5 per agent2 §8 examples
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("node", [
    [":seq", {":id": "sha256:aa01"}],
    [":par", {":id": "sha256:aa02", ":join": ":all"}],
    [":tool-call", {":id": "sha256:aa03", ":tool": ":http/get"}],
    [":llm-call", {":id": "sha256:aa04", ":model": ":opus-4.7"}],
    [":choice", {":id": "sha256:aa05", ":selector": "regime"}],
    [":loop", {":id": "sha256:aa06", ":while": "pred", ":max-iter": 10}],
    [":race", {":id": "sha256:aa07", ":timeout-ms": 30000}],
    [":code", {":id": "sha256:aa08", ":lang": ":python",
               ":sandbox": ":e2b", ":body": "pass"}],
    [":reflect", {":id": "sha256:aa09", ":criteria": ["correctness"]}],
    [":checkpoint", {":id": "sha256:aa10", ":tier": ":L1"}],
    [":branch", {":id": "sha256:aa11", ":strategy": ":beam", ":k": 3}],
    [":verify", {":id": "sha256:aa12", ":prover": ":z3"}],
    [":call-skill", {":id": "sha256:aa13",
                     ":skill": ":skill/draft@v3"}],
    [":let", {":id": "sha256:aa14", ":bindings": {":q": 1}}],
])
def test_every_node_kind_variant_conforms(node):
    assert S.conform(":persistence.plan/node", node).is_ok
