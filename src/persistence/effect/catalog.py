"""15-op effect catalog — typed signatures per spec §1.

Each op declares ``args`` and ``returns`` as a dict of
``{field_name: (python_type_or_tuple, required)}``. ``validate_args`` raises
on missing required fields or wrong types. Extra fields are tolerated so
handlers can attach metadata (e.g. a tenant id injected by ``tenant-isolate``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Field spec helpers
# ---------------------------------------------------------------------------


# Each field: (types, required). ``types`` may be a single type or a tuple.
FieldSpec = tuple[Any, bool]


def _req(t: Any) -> FieldSpec:
    return (t, True)


def _opt(t: Any) -> FieldSpec:
    return (t, False)


# ---------------------------------------------------------------------------
# OpSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpSpec:
    name: str
    args: dict[str, FieldSpec]
    returns: dict[str, FieldSpec] | None
    doc: str = ""


# ---------------------------------------------------------------------------
# The 15-op catalog. Follows spec §1 exactly.
# ---------------------------------------------------------------------------


CATALOG: dict[str, OpSpec] = {
    ":llm/call": OpSpec(
        name=":llm/call",
        args={
            "model": _req(str),
            "messages": _req(list),
            "tools": _opt(list),
            "temperature": _opt((int, float)),
            "max_tokens": _opt(int),
        },
        returns={
            "text": _opt(str),
            "tool_calls": _opt(list),
            "usage": _opt(dict),
            "fingerprint": _opt(str),
        },
        doc="Call an LLM. Non-deterministic — must be audited and cache-keyable.",
    ),
    ":tool/call": OpSpec(
        name=":tool/call",
        args={
            "name": _req(str),
            "input": _req(dict),
            "tenant_id": _opt(str),
        },
        returns={
            "result": _opt(object),
            "error": _opt(object),
        },
        doc="Call a named tool. Side-effect, must be policy-gated.",
    ),
    ":mem/read": OpSpec(
        name=":mem/read",
        args={"tier": _req(str), "query": _req(str), "scope": _opt(dict)},
        returns={"hits": _req(list)},
        doc="Query the fact store at a given tier.",
    ),
    ":mem/write": OpSpec(
        name=":mem/write",
        args={
            "tier": _req(str),
            "fact": _req(dict),
            "valid_from": _opt((str, int, float)),  # inst or epoch
            "recorded_at": _opt((str, int, float)),
        },
        returns={"id": _req(str)},
        doc="Write a fact to the store.",
    ),
    ":decide": OpSpec(
        name=":decide",
        args={
            "question": _req(str),
            "options": _req(list),
            "rationale": _opt(str),
            "tags": _opt(list),
        },
        returns={"choice": _req(object), "confidence": _opt((int, float))},
        doc="Record an explicit decision. Policy-gated: rationale required.",
    ),
    ":ask-user": OpSpec(
        name=":ask-user",
        args={"prompt": _req(str), "options": _opt(list), "timeout_ms": _opt(int)},
        returns={"answer": _req(object)},
        doc="Prompt the human. Bounded by timeout_ms.",
    ),
    ":emit-artifact": OpSpec(
        name=":emit-artifact",
        args={
            "kind": _req(str),
            "path": _req(str),
            "bytes": _opt((bytes, bytearray)),
            "meta": _opt(dict),
        },
        returns={"uri": _req(str)},
        doc="Emit a produced artifact (xlsx, pdf, json).",
    ),
    ":sleep": OpSpec(
        name=":sleep",
        args={"ms": _req(int)},
        returns=None,
        doc="Sleep. Routed through a handler so replay can skip.",
    ),
    ":random": OpSpec(
        name=":random",
        args={"kind": _req(str), "params": _opt(dict)},
        returns={"value": _req(object)},
        doc="Sample a random value. Handler records for replay.",
    ),
    ":env/read": OpSpec(
        name=":env/read",
        args={"key": _req(str)},
        returns={"value": _opt(str), "source": _opt(str)},
        doc="Read an env var. Source lets audit catch silent overrides.",
    ),
    ":net/fetch": OpSpec(
        name=":net/fetch",
        args={"url": _req(str), "method": _opt(str), "headers": _opt(dict), "body": _opt(object)},
        returns={"status": _req(int), "body": _opt(object)},
        doc="Fetch a URL. PII-redact above; cache below.",
    ),
    ":secret/use": OpSpec(
        name=":secret/use",
        args={"name": _req(str), "purpose": _req(str)},
        returns={"handle": _req(object)},
        doc="Use a secret by handle. Never returns raw material.",
    ),
    ":cost/charge": OpSpec(
        name=":cost/charge",
        args={"units": _req((int, float)), "currency": _req(str), "category": _req(str)},
        returns={"remaining": _req((int, float))},
        doc="Charge against the budget.",
    ),
    ":clock/now": OpSpec(
        name=":clock/now",
        args={},
        returns={"ts": _req((int, float, str))},
        doc="Read the clock. Handler returns recorded ts in replay.",
    ),
    ":audit/emit": OpSpec(
        name=":audit/emit",
        args={"kind": _req(str), "payload": _req(dict)},
        returns=None,
        doc="Emit an audit record. Usually masked inside audit handler body.",
    ),
}


OP_NAMES: frozenset[str] = frozenset(CATALOG.keys())


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_args(op: str, args: dict[str, Any]) -> None:
    """Raise KeyError if ``op`` is unknown; ValueError if a required field is
    missing; TypeError if a field has the wrong type.
    """
    if op not in CATALOG:
        raise KeyError(f"unknown op {op!r}; known: {sorted(CATALOG)!r}")
    spec = CATALOG[op]
    # Required fields present?
    for field, (types, required) in spec.args.items():
        if required and field not in args:
            raise ValueError(f"{op}: missing required arg {field!r}")
    # Types match?
    for field, value in args.items():
        if field not in spec.args:
            continue  # extra fields tolerated
        types, _ = spec.args[field]
        if not isinstance(value, types):
            raise TypeError(
                f"{op}: arg {field!r} expected {types}, got {type(value).__name__}"
            )
