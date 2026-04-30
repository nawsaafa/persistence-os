"""SDK3 — Schema Profile v0.8 enforcement (G13a/b/c/d)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from persistence.sdk.mcp import (
    INPUT_SCHEMAS,
    OUTPUT_SCHEMAS,
    SCHEMA_LOCKFILE_PATH,
    SchemaProfileViolation,
    all_tool_schema_shas,
    canonical_schema_sha256,
    validate_profile,
)
from persistence.sdk.mcp._names import _NAMES, _TOOL_ORDER


def test_g13a_every_input_schema_is_profile_conformant():
    """G13a: every input schema validates against the v0.8 profile."""
    for verb, schema in INPUT_SCHEMAS.items():
        validate_profile(schema)  # raises on violation


def test_g13a_every_output_schema_is_profile_conformant():
    for verb, schema in OUTPUT_SCHEMAS.items():
        validate_profile(schema)


def test_g13b_every_schema_uses_draft_07_metaschema():
    """G13b: every schema's $schema is draft-07."""
    expected = "http://json-schema.org/draft-07/schema#"
    for schema in INPUT_SCHEMAS.values():
        assert schema["$schema"] == expected
    for schema in OUTPUT_SCHEMAS.values():
        assert schema["$schema"] == expected


def test_g13b_no_draft_2020_12_keywords_present():
    """G13: ZERO draft-2020-12-only keywords in any schema."""
    forbidden = {
        "$ref",
        "$defs",
        "$dynamicRef",
        "$dynamicAnchor",
        "if",
        "then",
        "else",
        "dependentSchemas",
        "unevaluatedProperties",
        "unevaluatedItems",
        "not",
        "oneOf",
        "anyOf",
        "allOf",
        "prefixItems",
        "title",
    }

    def walk(schema):
        if isinstance(schema, dict):
            for k in schema.keys():
                if k in forbidden:
                    pytest.fail(f"forbidden keyword {k!r}")
            for v in schema.values():
                walk(v)
        elif isinstance(schema, list):
            for item in schema:
                walk(item)

    for schema in INPUT_SCHEMAS.values():
        walk(schema)
    for schema in OUTPUT_SCHEMAS.values():
        walk(schema)


def test_g13c_lockfile_matches_recomputed_shas():
    """G13c: the committed schema_lockfile.json SHAs match recomputation
    from the running schemas. CI gate against drift.
    """
    raw = SCHEMA_LOCKFILE_PATH.read_text()
    locked = json.loads(raw)
    recomputed = all_tool_schema_shas()
    assert locked["tools"] == recomputed


def test_g13c_lockfile_has_six_tools():
    locked = json.loads(SCHEMA_LOCKFILE_PATH.read_text())
    assert len(locked["tools"]) == 6
    expected_wires = {names["wire"] for names in _NAMES.values()}
    assert set(locked["tools"].keys()) == expected_wires


def test_g13c_lockfile_records_draft_07_dialect():
    locked = json.loads(SCHEMA_LOCKFILE_PATH.read_text())
    assert locked["schema_dialect"] == "http://json-schema.org/draft-07/schema#"
    assert locked["profile_version"] == "v0.8"


def test_canonical_sha_is_deterministic():
    schema = INPUT_SCHEMAS["remember"]
    sha1 = canonical_schema_sha256(schema)
    sha2 = canonical_schema_sha256(schema)
    assert sha1 == sha2
    assert len(sha1) == 64  # hex sha256


def test_validate_profile_rejects_disallowed_keyword():
    bad = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": False,
        "$defs": {"foo": {"type": "string"}},
    }
    with pytest.raises(SchemaProfileViolation):
        validate_profile(bad)


def test_validate_profile_rejects_oneof():
    bad = {
        "type": "object",
        "additionalProperties": False,
        "oneOf": [{"type": "string"}],
    }
    with pytest.raises(SchemaProfileViolation):
        validate_profile(bad)


def test_validate_profile_rejects_disallowed_format():
    bad = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "foo": {"type": "string", "format": "ipv4"},
        },
    }
    with pytest.raises(SchemaProfileViolation):
        validate_profile(bad)


def test_validate_profile_object_must_have_additional_properties_false():
    bad = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {},
    }
    with pytest.raises(SchemaProfileViolation):
        validate_profile(bad)


def test_validate_profile_accepts_allowed_format_uuid():
    ok = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "eid": {"type": "string", "format": "uuid"},
        },
    }
    validate_profile(ok)  # must not raise


def test_input_schemas_have_object_type_at_root():
    for verb, schema in INPUT_SCHEMAS.items():
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


def test_output_schemas_have_object_type_at_root():
    for verb, schema in OUTPUT_SCHEMAS.items():
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
