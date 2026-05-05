"""Phase 2.1b — _prompt.py shape + text-parser unit tests (design § 5).

R1 fix-pass F7: NO jsonschema dep. Manual structural assertions only —
the schema is short enough that a hand-rolled checker is cheaper than
adding a dep.
"""
from __future__ import annotations

import pytest


def test_emit_decision_tool_schema_top_level_shape():
    from persistence.coder._prompt import EMIT_DECISION_TOOL_SCHEMA

    assert EMIT_DECISION_TOOL_SCHEMA["name"] == "emit_decision"
    assert isinstance(EMIT_DECISION_TOOL_SCHEMA["description"], str)
    schema = EMIT_DECISION_TOOL_SCHEMA["input_schema"]
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"kind", "confidence", "payload"}
    assert schema["additionalProperties"] is False


def test_emit_decision_kind_enum_is_three_choices():
    from persistence.coder._prompt import EMIT_DECISION_TOOL_SCHEMA

    kind_prop = EMIT_DECISION_TOOL_SCHEMA["input_schema"]["properties"]["kind"]
    assert kind_prop["type"] == "string"
    assert set(kind_prop["enum"]) == {"act", "plan", "branch"}


def test_emit_decision_confidence_is_bounded_number():
    from persistence.coder._prompt import EMIT_DECISION_TOOL_SCHEMA

    conf = EMIT_DECISION_TOOL_SCHEMA["input_schema"]["properties"]["confidence"]
    assert conf["type"] == "number"
    assert conf["minimum"] == 0.0
    assert conf["maximum"] == 1.0


def test_emit_decision_payload_is_object():
    from persistence.coder._prompt import EMIT_DECISION_TOOL_SCHEMA

    payload = EMIT_DECISION_TOOL_SCHEMA["input_schema"]["properties"]["payload"]
    assert payload["type"] == "object"


def test_build_messages_includes_task_and_emit_decision_pointer():
    from persistence.coder._prompt import build_messages
    from persistence.coder._types import Observation

    messages = build_messages("implement parse_csv_row", Observation())
    assert isinstance(messages, list) and len(messages) >= 1
    user_msg = messages[0]
    assert user_msg["role"] == "user"
    assert "implement parse_csv_row" in user_msg["content"]
    assert "emit_decision" in user_msg["content"]


@pytest.mark.parametrize("text,expected", [
    # Valid envelope, all fields well-formed
    (
        '<decision>{"kind":"act","confidence":0.9,"payload":{"tool":"fs/write"}}</decision>',
        {"kind": "act", "confidence": 0.9, "payload": {"tool": "fs/write"}},
    ),
    # Valid envelope, kind=branch, empty payload
    (
        '<decision>{"kind":"branch","confidence":0.4,"payload":{}}</decision>',
        {"kind": "branch", "confidence": 0.4, "payload": {}},
    ),
    # Whitespace inside envelope tolerated
    (
        '<decision>\n  {"kind":"plan","confidence":0.7,"payload":{}}\n</decision>',
        {"kind": "plan", "confidence": 0.7, "payload": {}},
    ),
])
def test_parse_text_decision_valid(text, expected):
    from persistence.coder._prompt import parse_text_decision

    assert parse_text_decision(text) == expected


@pytest.mark.parametrize("text", [
    "",                                                                    # empty
    "no envelope here",                                                    # missing tag
    '<decision>not json</decision>',                                       # invalid JSON
    '<decision>{"kind":"unknown","confidence":0.9,"payload":{}}</decision>',  # bad kind
    '<decision>{"kind":"act","confidence":2.0,"payload":{}}</decision>',     # OOB confidence
    '<decision>{"kind":"act","payload":{}}</decision>',                     # missing confidence
    '<decision>{"kind":"act","confidence":"high","payload":{}}</decision>',  # confidence not numeric
    '<decision>{"kind":"act","confidence":0.9,"payload":"oops"}</decision>',  # payload not object
    '<decision>[1,2,3]</decision>',                                        # JSON array, not object
])
def test_parse_text_decision_invalid_returns_none(text):
    from persistence.coder._prompt import parse_text_decision

    assert parse_text_decision(text) is None


def test_build_messages_includes_recent_history_when_present():
    from persistence.coder._prompt import build_messages
    from persistence.coder._types import Observation

    obs = Observation(
        iter_count=2,
        recent_decisions=({"kind": "act", "confidence": 0.9},),
        recent_actions=({"op": ":fs/read", "result_summary": {"size": 12}},),
    )
    msgs = build_messages("write README", obs)
    body = msgs[0]["content"]
    assert "Recent loop history" in body
    assert "iter 2" in body or "iteration 2" in body.lower()
    assert ":fs/read" in body


def test_build_messages_omits_history_section_when_empty():
    from persistence.coder._prompt import build_messages
    from persistence.coder._types import Observation

    obs = Observation(iter_count=0, recent_decisions=(), recent_actions=())
    msgs = build_messages("t", obs)
    body = msgs[0]["content"]
    assert "Recent loop history" not in body
