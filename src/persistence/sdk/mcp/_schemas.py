"""Tool ``inputSchema`` / ``outputSchema`` — Schema Profile v0.8 (draft-07).

Per ADR-15 § 5.1.1 (Schema Profile v0.8) and G13:

- All schemas use ``$schema: "http://json-schema.org/draft-07/schema#"``.
- Allowed keywords (per § 5.1.1 closed list): ``type``, ``properties``,
  ``required``, ``additionalProperties`` (always ``false`` at object root),
  ``items``, ``minItems``, ``maxItems``, ``minLength``, ``maxLength``,
  ``minimum``, ``maximum``, ``enum``, ``const``, ``pattern``,
  ``description``, ``default``, ``examples``, ``$schema`` (root only),
  and ``format`` (``uuid`` / ``date-time`` / ``uri`` only).
- Disallowed: ``$ref`` / ``$defs`` / ``$dynamicRef`` / ``$dynamicAnchor``
  / ``if`` / ``then`` / ``else`` / ``dependentSchemas`` /
  ``unevaluatedProperties`` / ``unevaluatedItems`` / ``not`` / ``oneOf``
  / ``anyOf`` / ``allOf`` / ``prefixItems`` / ``title``.

Per R3 SHOULD-FIX 1: instead of pydantic-emitted schemas (which require
a ``$ref`` inliner to be profile-conformant), v0.8 hand-authors the
schemas as plain Python dicts. This is the cleanest path: no inliner,
no pydantic dep, no ``$defs`` ever emitted, every schema is one-pass
readable per the design's "thin shim" claim.

The :func:`canonical_schema_sha256` helper feeds the schema lockfile
(G13c). The :func:`validate_profile` helper enforces the closed
allowed-keywords set at every node (G13a).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from persistence.sdk.mcp._names import _NAMES, _TOOL_ORDER


# ---------------------------------------------------------------------------
# Schema Profile v0.8 — closed allowed-keywords sets
# ---------------------------------------------------------------------------
# Allowed at any node depth.
_PROFILE_ALLOWED_KEYWORDS: frozenset[str] = frozenset({
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "enum",
    "const",
    "pattern",
    "description",
    "default",
    "examples",
    "format",
})

# Allowed only at the schema root.
_PROFILE_ROOT_ONLY: frozenset[str] = frozenset({"$schema"})

# Allowed ``format`` values (ADR-15 § 5.1.1).
_PROFILE_ALLOWED_FORMATS: frozenset[str] = frozenset({
    "uuid",
    "date-time",
    "uri",
})

# Draft-07 metaschema URI.
_DRAFT_07_URI: str = "http://json-schema.org/draft-07/schema#"


class SchemaProfileViolation(ValueError):
    """Raised by :func:`validate_profile` on any disallowed keyword."""


def validate_profile(schema: dict[str, Any], path: str = "$") -> None:
    """Walk ``schema``; raise :class:`SchemaProfileViolation` on any
    keyword outside the v0.8 profile.

    ``path`` is a ``$.foo.bar``-style breadcrumb threaded through the
    recursion so the error message points at the offending node.
    """
    if not isinstance(schema, dict):
        # Plain values (e.g. ``items: {type: 'string'}``) — only the
        # outer dict shape is profile-validated. Bare lists / strings
        # under ``enum``/``const``/``examples`` are user data, not
        # schema keywords; those are validated at the keyword above.
        return

    is_root = path == "$"
    for key, value in schema.items():
        if key in _PROFILE_ALLOWED_KEYWORDS:
            pass
        elif key in _PROFILE_ROOT_ONLY and is_root:
            pass
        else:
            raise SchemaProfileViolation(
                f"disallowed keyword {key!r} at {path}; "
                f"allowed at this depth: "
                f"{sorted(_PROFILE_ALLOWED_KEYWORDS) + (sorted(_PROFILE_ROOT_ONLY) if is_root else [])}"
            )

        # Recurse into known-shape sub-schemas.
        if key == "properties" and isinstance(value, dict):
            for prop_name, prop_schema in value.items():
                validate_profile(prop_schema, f"{path}.properties.{prop_name}")
        elif key == "items" and isinstance(value, dict):
            validate_profile(value, f"{path}.items")
        elif key == "additionalProperties" and isinstance(value, dict):
            validate_profile(value, f"{path}.additionalProperties")
        elif key == "format":
            if value not in _PROFILE_ALLOWED_FORMATS:
                raise SchemaProfileViolation(
                    f"disallowed format value {value!r} at {path}; "
                    f"allowed: {sorted(_PROFILE_ALLOWED_FORMATS)}"
                )

    # Object-typed schemas at non-leaf nodes must declare
    # ``additionalProperties: false`` per the profile.
    if (
        schema.get("type") == "object"
        and "additionalProperties" not in schema
    ):
        raise SchemaProfileViolation(
            f"object-typed schema at {path} must declare "
            f"`additionalProperties: false` per Schema Profile v0.8"
        )


# ---------------------------------------------------------------------------
# Tool schemas — hand-authored per § 5.1.1 (no pydantic, no $ref inliner)
# ---------------------------------------------------------------------------
# remember
_INPUT_REMEMBER: dict[str, Any] = {
    "$schema": _DRAFT_07_URI,
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "content": {
            "type": "string",
            "minLength": 1,
            "maxLength": 16384,
            "description": "The fact to remember (1..16384 chars).",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 64},
            "maxItems": 32,
            "description": "Optional tags for retrieval.",
        },
    },
    "required": ["content"],
}

_OUTPUT_REMEMBER: dict[str, Any] = {
    "$schema": _DRAFT_07_URI,
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "eid": {
            "type": "string",
            "format": "uuid",
            "description": "Stored entity id (UUID).",
        },
        "tx": {
            "type": "integer",
            "minimum": 0,
            "description": "Transaction id of the assertion.",
        },
        "valid_from": {
            "type": "string",
            "format": "date-time",
            "description": "ISO-8601 valid_from timestamp.",
        },
    },
    "required": ["eid", "tx", "valid_from"],
}

# recall
_INPUT_RECALL: dict[str, Any] = {
    "$schema": _DRAFT_07_URI,
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 1024,
            "description": "Substring query (case-insensitive).",
        },
        "k": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "default": 5,
            "description": "Max number of hits to return.",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 64},
            "maxItems": 32,
            "description": "Optional tag filter (AND semantics).",
        },
        "cursor": {
            "type": "string",
            "minLength": 1,
            "maxLength": 256,
            "description": "Opaque cursor from a prior recall reply.",
        },
    },
    "required": ["query"],
}

_OUTPUT_RECALL: dict[str, Any] = {
    "$schema": _DRAFT_07_URI,
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hits": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "eid": {"type": "string", "format": "uuid"},
                    "content": {"type": "string"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "valid_from": {
                        "type": "string",
                        "format": "date-time",
                    },
                },
                "required": ["eid", "content", "tags", "valid_from"],
            },
        },
        "next_cursor": {
            "type": "string",
            "description": "Opaque cursor; absent when no further pages.",
        },
    },
    "required": ["hits"],
}

# forget
_INPUT_FORGET: dict[str, Any] = {
    "$schema": _DRAFT_07_URI,
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "eid": {
            "type": "string",
            "format": "uuid",
            "description": "Entity id to retract.",
        },
    },
    "required": ["eid"],
}

_OUTPUT_FORGET: dict[str, Any] = {
    "$schema": _DRAFT_07_URI,
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "eid": {"type": "string", "format": "uuid"},
        "valid_to": {"type": "string", "format": "date-time"},
        "retracted": {"type": "boolean"},
    },
    "required": ["eid", "valid_to", "retracted"],
}

# audit_window
_INPUT_AUDIT_WINDOW: dict[str, Any] = {
    "$schema": _DRAFT_07_URI,
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "from_tx": {
            "type": "integer",
            "minimum": 0,
            "description": "Lower bound (inclusive).",
        },
        "to_tx": {
            "type": "integer",
            "minimum": 0,
            "description": "Upper bound (inclusive); omit for +inf.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1000,
            "default": 100,
            "description": "Max entries to return.",
        },
        "cursor": {
            "type": "string",
            "minLength": 1,
            "maxLength": 256,
            "description": "Opaque cursor from a prior audit_window reply.",
        },
    },
    "required": ["from_tx"],
}

_OUTPUT_AUDIT_WINDOW: dict[str, Any] = {
    "$schema": _DRAFT_07_URI,
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "op": {"type": "string"},
                    "args_hash": {"type": "string"},
                    "result_hash": {"type": "string"},
                    "prev_hash": {"type": "string"},
                    "tx": {"type": "integer"},
                },
                "required": ["op", "tx"],
            },
        },
        "next_cursor": {"type": "string"},
        "head_hash": {"type": "string"},
    },
    "required": ["entries", "head_hash"],
}

# replay_check
_INPUT_REPLAY_CHECK: dict[str, Any] = {
    "$schema": _DRAFT_07_URI,
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tx": {
            "type": "integer",
            "minimum": 0,
            "description": "Transaction id at the center of the window.",
        },
        "window": {
            "type": "integer",
            "minimum": 1,
            "maximum": 256,
            "default": 32,
            "description": "Window size (entries on each side).",
        },
    },
    "required": ["tx"],
}

_OUTPUT_REPLAY_CHECK: dict[str, Any] = {
    "$schema": _DRAFT_07_URI,
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ok": {"type": "boolean"},
        "reason_code": {
            "type": "string",
            "enum": [
                "ok",
                "mismatch_user_log",
                "mismatch_audit_chain",
                "window_too_large",
                "replay_aborted_budget",
                "tx_not_found",
            ],
        },
        "window_actual": {"type": "integer", "minimum": 0},
        "head_hash": {"type": "string"},
    },
    "required": ["ok", "reason_code", "window_actual", "head_hash"],
}

# view_at
_INPUT_VIEW_AT: dict[str, Any] = {
    "$schema": _DRAFT_07_URI,
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tx": {
            "type": "integer",
            "minimum": 0,
            "description": "Transaction id to anchor the cursor at.",
        },
        "label": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "description": "Optional human-readable label.",
        },
    },
    "required": ["tx"],
}

_OUTPUT_VIEW_AT: dict[str, Any] = {
    "$schema": _DRAFT_07_URI,
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "cursor_id": {"type": "string", "format": "uuid"},
        "view_cursor_tx_time_iso": {
            "type": "string",
            "format": "date-time",
        },
        "parent_chain_depth": {"type": "integer", "minimum": 0},
        "label": {"type": "string"},
    },
    "required": [
        "cursor_id",
        "view_cursor_tx_time_iso",
        "parent_chain_depth",
    ],
}


# ---------------------------------------------------------------------------
# Aggregated registry
# ---------------------------------------------------------------------------
INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "remember": _INPUT_REMEMBER,
    "recall": _INPUT_RECALL,
    "forget": _INPUT_FORGET,
    "audit_window": _INPUT_AUDIT_WINDOW,
    "replay_check": _INPUT_REPLAY_CHECK,
    "view_at": _INPUT_VIEW_AT,
}

OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "remember": _OUTPUT_REMEMBER,
    "recall": _OUTPUT_RECALL,
    "forget": _OUTPUT_FORGET,
    "audit_window": _OUTPUT_AUDIT_WINDOW,
    "replay_check": _OUTPUT_REPLAY_CHECK,
    "view_at": _OUTPUT_VIEW_AT,
}


# Profile-validate every schema at import time. The closed registry
# means a future contributor who adds a tool gets an immediate fail at
# package-import time if their schema strays from the profile (loud,
# not silent at first wire dispatch).
for _verb in _TOOL_ORDER:
    validate_profile(INPUT_SCHEMAS[_verb])
    validate_profile(OUTPUT_SCHEMAS[_verb])


def canonical_schema_sha256(schema: dict[str, Any]) -> str:
    """Compute SHA-256 of ``schema`` as canonical JSON.

    Canonical JSON = ``json.dumps(d, sort_keys=True, separators=(",", ":"))``.
    The hex digest feeds the schema lockfile (G13c).
    """
    payload = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def all_tool_schema_shas() -> dict[str, str]:
    """Return ``{tool_wire_name: sha256(canonical(input)+canonical(output))}``.

    The combined input+output SHA is what the lockfile records: a single
    string per tool. Drift in either schema flips the SHA. Format:
    ``sha256(input_canonical || "|" || output_canonical)``.
    """
    out: dict[str, str] = {}
    for verb in _TOOL_ORDER:
        wire = _NAMES[verb]["wire"]
        input_canonical = json.dumps(
            INPUT_SCHEMAS[verb], sort_keys=True, separators=(",", ":")
        )
        output_canonical = json.dumps(
            OUTPUT_SCHEMAS[verb], sort_keys=True, separators=(",", ":")
        )
        combined = (input_canonical + "|" + output_canonical).encode("utf-8")
        out[wire] = hashlib.sha256(combined).hexdigest()
    return out


__all__ = [
    "INPUT_SCHEMAS",
    "OUTPUT_SCHEMAS",
    "SchemaProfileViolation",
    "all_tool_schema_shas",
    "canonical_schema_sha256",
    "validate_profile",
]
