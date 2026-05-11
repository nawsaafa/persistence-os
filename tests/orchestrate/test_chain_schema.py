"""G2 — LD-3 falsifier: EDN chain schema parses to AST and roundtrips.

R0-fold B2: v0 is EDN-only. YAML input must raise ChainSchemaError.
"""
from __future__ import annotations

import pytest

from persistence.orchestrate._schema import (
    Chain,
    ChainSchemaError,
    Step,
    StepCapability,
    parse_chain_edn,
    serialize_chain_edn,
)


VALID_CHAIN_EDN = """(:chain
  :name "demo"
  :description "G2 demo chain"
  :steps [(:step :id 1
                :op :fs/read
                :args {:path "input.md"}
                :capability (:Capability :op "coder" :qualifier "read"))])"""


def test_chain_parses_to_edn_ast_and_roundtrips() -> None:
    chain = parse_chain_edn(VALID_CHAIN_EDN)
    assert chain.name == "demo"
    assert chain.description == "G2 demo chain"
    assert len(chain.steps) == 1
    assert chain.steps[0].id == 1
    assert chain.steps[0].op == ":fs/read"
    assert chain.steps[0].args == {":path": "input.md"}
    assert chain.steps[0].capability == StepCapability(op="coder", qualifier="read")

    # Roundtrip: serialize → parse equals original
    reserialized = serialize_chain_edn(chain)
    assert parse_chain_edn(reserialized) == chain


def test_yaml_input_raises_v0_edn_only_error() -> None:
    yaml_src = """name: demo
description: should fail
steps:
  - id: 1
    op: ":fs/read"
"""
    with pytest.raises(ChainSchemaError, match="v0 is EDN-only"):
        parse_chain_edn(yaml_src)


def test_missing_required_field_raises() -> None:
    src = """(:chain :description "no name field" :steps [])"""
    with pytest.raises(ChainSchemaError, match="missing required field: :name"):
        parse_chain_edn(src)
