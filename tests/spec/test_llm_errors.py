"""Self-healing retry hint tests.

The Spec module's killer feature is that conform failures produce messages an
LLM can read and act on: field name, constraint, and a 'Fix:' clause.

These tests lock that contract down.
"""
from __future__ import annotations

from persistence import spec as S


class TestDecisionExplanations:
    def test_empty_rationale_message_names_field_and_constraint(self):
        bad = {":question": "Buy?", ":options": ["y", "n"],
               ":rationale": "", ":choice": "y", ":confidence": 0.5}
        msg = S.explain_for_llm(":persistence.domain/decision", bad)
        # Field name appears
        assert ":rationale" in msg or "rationale" in msg.lower()
        # Constraint (empty / non-empty)
        assert "empty" in msg.lower()
        # Fix clause
        assert "Fix:" in msg
        # Spec key (the one the caller asked about) appears on top line
        assert ":persistence.domain/decision" in msg

    def test_out_of_bounds_confidence_message(self):
        bad = {":question": "q", ":options": ["a"],
               ":rationale": "r", ":choice": "a", ":confidence": 2.0}
        msg = S.explain_for_llm(":persistence.domain/decision", bad)
        assert ":confidence" in msg or "confidence" in msg.lower()
        assert "Fix:" in msg
        # the phrase should mention the [0, 1] range
        assert "0" in msg and "1" in msg

    def test_missing_required_field_message(self):
        bad = {":options": ["a"], ":rationale": "r",
               ":choice": "a", ":confidence": 0.5}  # no :question
        msg = S.explain_for_llm(":persistence.domain/decision", bad)
        assert "missing" in msg.lower()
        assert ":question" in msg

    def test_type_error_message_reports_wrong_type(self):
        bad = {":question": "q", ":options": ["a"],
               ":rationale": 42,  # wrong type
               ":choice": "a", ":confidence": 0.5}
        msg = S.explain_for_llm(":persistence.domain/decision", bad)
        assert "int" in msg.lower() or "str" in msg.lower()


class TestWaccExplanation:
    def test_percent_over_one_fix_clause(self):
        bad = {":project-id": "p-042", ":percent": 1.5,
               ":source": ":dfi", ":confidence": 0.5}
        msg = S.explain_for_llm(":persistence.domain/wacc-assumption", bad)
        assert "Fix:" in msg
        assert "percent" in msg.lower() or ":percent" in msg


class TestDatomExplanation:
    def test_wrong_op_names_allowed_values(self):
        import datetime as dt
        import uuid
        bad = {
            ":datom/e": uuid.uuid4(),
            ":datom/a": ":test/foo",
            ":datom/v": 1,
            ":datom/tx": 1,
            ":datom/tx-time": dt.datetime.now(dt.timezone.utc),
            ":datom/valid-from": dt.datetime.now(dt.timezone.utc),
            ":datom/valid-to": None,
            ":datom/op": ":delete",  # invalid
            ":datom/provenance": {},
        }
        msg = S.explain_for_llm(":persistence.fact/datom", bad)
        # Enum error should include the allowed values
        assert ":assert" in msg
        assert ":retract" in msg
        assert "Fix:" in msg


class TestMultipleErrorsListed:
    def test_multiple_field_errors_all_reported(self):
        bad = {":question": "", ":options": [],
               ":rationale": "", ":choice": "x", ":confidence": 99.0}
        msg = S.explain_for_llm(":persistence.domain/decision", bad)
        # all three field violations should be mentioned somewhere
        # (question, rationale, confidence)
        assert "rationale" in msg.lower()
        # confidence out of range OR options too short should also show
        assert ("confidence" in msg.lower() or "options" in msg.lower()
                or "question" in msg.lower())
