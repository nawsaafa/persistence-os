"""Phase 2.1b — Coder._decide body coverage (design § 3.4 + § 5 + G2.1b-a/b).

Uses make_callable_llm_handler with fixture-controlled call_fn to inject
all tier-1/tier-2/tier-3 paths deterministically. Asserts datom
emissions, parsed-via attribution, source-call FK linkage. No real
LLM calls — pure unit.

API note: tests filter datoms via ``s._db.log()`` (raw DB iterator over
all datoms ever transacted). The curated ``s.fact.history(e, a)``
requires both entity AND attribute and is keyed by entity, not suitable
for "all :llm/* datoms" sweep. ``s.escape.fact.log()`` would emit a
``:sdk/escape-hatch-access`` audit entry on first access; for test
helpers we bypass that telemetry by reaching ``_db`` directly. The test
contract — datom counts, parsed_via attribution, FK linkage — is preserved.

``Datom.a`` strips leading ``:`` at construction (datom.py:174-177), so
filters use the bare ``"llm/messages"`` / ``"llm/decision"`` form.
"""
from __future__ import annotations

import json

import pytest
from hypothesis import given, settings, strategies as st

from persistence.coder import Coder
from persistence.coder._types import LLMDecision, Observation
from persistence.effect.handlers import make_callable_llm_handler
from persistence.sdk import Substrate


# ---------- helpers ----------


def _open_substrate_with_call_fn(call_fn):
    """Open an in-memory substrate, install a callable handler at bottom,
    return (substrate, contextmanager-enter-result)."""
    s_cm = Substrate.open("memory")
    s = s_cm.__enter__()
    s.effect.install_handler(make_callable_llm_handler(call_fn=call_fn), position="bottom")
    return s_cm, s


def _llm_messages_datoms(s):
    return [d for d in s._db.log() if d.a == "llm/messages"]


def _llm_decision_datoms(s):
    return [d for d in s._db.log() if d.a == "llm/decision"]


# ---------- Tier 1 — tool-use path ----------


def test_decide_tier1_tool_use_returns_act_decision():
    def call_fn(model, messages, tools=None, **_):
        return {
            "text": "",
            "tool_calls": [{
                "id": "tu_001", "name": "emit_decision",
                "input": {"kind": "act", "confidence": 0.9,
                          "payload": {"tool": "fs/write", "path": "x.py"}},
            }],
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "fingerprint": "fp",
        }

    s_cm, s = _open_substrate_with_call_fn(call_fn)
    try:
        coder = Coder(task="test", substrate=s)
        decision = coder._decide(Observation())
        assert decision.kind == "act"
        assert decision.confidence == 0.9
        assert decision.payload == {"tool": "fs/write", "path": "x.py"}
    finally:
        s_cm.__exit__(None, None, None)


def test_decide_tier1_emits_messages_datom_before_call_and_decision_datom_after():
    """G2.1b-b: :llm/messages transacted BEFORE perform; :llm/decision AFTER."""
    def call_fn(model, messages, tools=None, **_):
        return {"text": "", "tool_calls": [{
            "id": "x", "name": "emit_decision",
            "input": {"kind": "plan", "confidence": 0.8, "payload": {"steps": []}},
        }], "usage": {}, "fingerprint": ""}

    s_cm, s = _open_substrate_with_call_fn(call_fn)
    try:
        Coder(task="t", substrate=s)._decide(Observation())
        msgs = _llm_messages_datoms(s)
        decs = _llm_decision_datoms(s)
        assert len(msgs) == 1
        assert len(decs) == 1
        # FK: decision's source_call must equal the messages datom's entity-id
        decision_value = json.loads(decs[0].v)
        assert decision_value["source_call"] == msgs[0].e
    finally:
        s_cm.__exit__(None, None, None)


def test_decide_tier1_decision_value_canonical_json_round_trips():
    def call_fn(model, messages, tools=None, **_):
        return {"text": "", "tool_calls": [{
            "id": "x", "name": "emit_decision",
            "input": {"kind": "act", "confidence": 0.7,
                      "payload": {"tool": "shell/run", "argv": ["ls", "-la"]}},
        }], "usage": {}, "fingerprint": ""}

    s_cm, s = _open_substrate_with_call_fn(call_fn)
    try:
        Coder(task="t", substrate=s)._decide(Observation())
        decs = _llm_decision_datoms(s)
        v = json.loads(decs[0].v)
        assert v["kind"] == "act"
        assert v["confidence"] == 0.7
        assert v["payload"]["tool"] == "shell/run"
        assert v["parsed_via"] == "tool_use"
    finally:
        s_cm.__exit__(None, None, None)


# ---------- Tier 2 — text fallback path ----------


def test_decide_tier2_text_fallback_returns_correct_decision():
    def call_fn(model, messages, tools=None, **_):
        return {
            "text": '<decision>{"kind":"branch","confidence":0.4,"payload":{}}</decision>',
            "tool_calls": [],
            "usage": {}, "fingerprint": "",
        }

    s_cm, s = _open_substrate_with_call_fn(call_fn)
    try:
        decision = Coder(task="t", substrate=s)._decide(Observation())
        assert decision.kind == "branch"
        assert decision.confidence == 0.4
        decs = _llm_decision_datoms(s)
        assert json.loads(decs[0].v)["parsed_via"] == "text_fallback"
    finally:
        s_cm.__exit__(None, None, None)


# ---------- Tier 3 — missing-confidence default ----------


def test_decide_tier3_missing_default_when_no_tool_call_and_no_envelope():
    def call_fn(model, messages, tools=None, **_):
        return {"text": "I don't know", "tool_calls": [], "usage": {}, "fingerprint": ""}

    s_cm, s = _open_substrate_with_call_fn(call_fn)
    try:
        coder = Coder(task="t", substrate=s)
        decision = coder._decide(Observation())
        assert decision.kind == "act"
        assert decision.confidence == coder.missing_confidence_default
        assert decision.confidence < coder.confidence_threshold  # forces branch escalation
        assert decision.payload["raw_text"] == "I don't know"
        decs = _llm_decision_datoms(s)
        assert json.loads(decs[0].v)["parsed_via"] == "missing_default"
    finally:
        s_cm.__exit__(None, None, None)


# ---------- Tier 1 fall-through to tier 2 (malformed tool_call) ----------


def test_decide_malformed_tool_call_falls_through_to_text_fallback():
    """If tier-1 tool_call is malformed (missing kind), parser falls
    through to tier-2 text-fallback."""
    def call_fn(model, messages, tools=None, **_):
        return {
            "text": '<decision>{"kind":"plan","confidence":0.7,"payload":{}}</decision>',
            "tool_calls": [{"id": "x", "name": "emit_decision",
                            "input": {"confidence": 0.9, "payload": {}}}],  # missing kind
            "usage": {}, "fingerprint": "",
        }

    s_cm, s = _open_substrate_with_call_fn(call_fn)
    try:
        decision = Coder(task="t", substrate=s)._decide(Observation())
        assert decision.kind == "plan"
        assert decision.confidence == 0.7
        decs = _llm_decision_datoms(s)
        assert json.loads(decs[0].v)["parsed_via"] == "text_fallback"
    finally:
        s_cm.__exit__(None, None, None)


# ---------- G2.1b-b: provenance even on call failure ----------


def test_decide_messages_datom_persists_when_perform_raises():
    """If the LLM provider raises, :llm/messages must still be in the
    substrate (was transacted BEFORE perform); :llm/decision must NOT
    be (transacted only AFTER successful parse)."""
    def call_fn(model, messages, tools=None, **_):
        raise RuntimeError("simulated vendor 503")

    s_cm, s = _open_substrate_with_call_fn(call_fn)
    try:
        with pytest.raises(RuntimeError, match="simulated vendor 503"):
            Coder(task="t", substrate=s)._decide(Observation())
        assert len(_llm_messages_datoms(s)) == 1
        assert len(_llm_decision_datoms(s)) == 0
    finally:
        s_cm.__exit__(None, None, None)


# ---------- G6: decision/action split (no direct effect callsites) ----------


def test_decide_does_not_invoke_effect_ops_other_than_llm_call():
    """G6: AST-walk through _session.py — _decide method body contains
    exactly one s.effect.perform call, and its op string is ':llm/call'."""
    import ast
    import inspect
    from persistence.coder import _session

    src = inspect.getsource(_session)
    tree = ast.parse(src)

    # Locate Coder._decide
    decide_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Coder":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "_decide":
                    decide_fn = item
                    break
    assert decide_fn is not None, "_decide method not found"

    # Collect all string literals passed as the FIRST positional arg to
    # any s.effect.perform / substrate.effect.perform call inside _decide.
    perform_ops: list[str] = []
    for node in ast.walk(decide_fn):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "perform"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            perform_ops.append(node.args[0].value)

    assert perform_ops == [":llm/call"], (
        f"_decide must perform exactly :llm/call; got {perform_ops!r}"
    )


# ---------- G2.1b-a: parse_decision is total (Hypothesis) ----------


_RESULT_STRATEGY = st.fixed_dictionaries({
    "text": st.one_of(st.text(max_size=200), st.just("")),
    "tool_calls": st.one_of(
        st.just([]),
        st.lists(st.fixed_dictionaries({
            "id": st.text(min_size=1, max_size=10),
            "name": st.text(min_size=1, max_size=20),
            "input": st.dictionaries(
                st.text(min_size=1, max_size=10),
                st.one_of(st.text(max_size=20), st.integers(), st.floats(allow_nan=False, allow_infinity=False)),
                max_size=5,
            ),
        }), max_size=2),
    ),
    "usage": st.dictionaries(st.text(min_size=1, max_size=10), st.integers(), max_size=3),
    "fingerprint": st.text(max_size=20),
})


@given(result=_RESULT_STRATEGY)
@settings(max_examples=200)
def test_parse_decision_is_total_function(result):
    """G2.1b-a: _parse_decision returns exactly one of three parsed_via
    outcomes; never raises; LLMDecision invariants always hold."""
    s_cm = Substrate.open("memory")
    s = s_cm.__enter__()
    try:
        coder = Coder(task="t", substrate=s)
        decision, parsed_via = coder._parse_decision(result)
        assert isinstance(decision, LLMDecision)
        assert decision.kind in {"act", "plan", "branch"}
        assert 0.0 <= decision.confidence <= 1.0
        assert parsed_via in {"tool_use", "text_fallback", "missing_default"}
    finally:
        s_cm.__exit__(None, None, None)


# ---------- R5 invariant: missing_confidence_default < confidence_threshold ----------


def test_missing_confidence_default_is_below_threshold():
    """R5: a future change to either constant must preserve the
    branch-escalation guarantee — tier 3's confidence MUST be below
    the threshold so missing-decision triggers branch."""
    s_cm = Substrate.open("memory")
    s = s_cm.__enter__()
    try:
        coder = Coder(task="t", substrate=s)
        assert coder.missing_confidence_default < coder.confidence_threshold
    finally:
        s_cm.__exit__(None, None, None)


# ---------- Coder.model field (CLI override) ----------


def test_coder_model_default_is_claude_opus_4_7():
    s_cm = Substrate.open("memory")
    s = s_cm.__enter__()
    try:
        coder = Coder(task="t", substrate=s)
        assert coder.model == "claude-opus-4-7"
    finally:
        s_cm.__exit__(None, None, None)


def test_coder_model_can_be_overridden():
    s_cm = Substrate.open("memory")
    s = s_cm.__enter__()
    try:
        coder = Coder(task="t", substrate=s, model="claude-haiku-4-5-20251001")
        assert coder.model == "claude-haiku-4-5-20251001"
    finally:
        s_cm.__exit__(None, None, None)
