"""PII redact handler — pluggable schema-based field redaction.

The schema shape is:

    {
      "fields": {"guest_name", "phone", "email", "authorization", ...},
      "paths":  {"meta.email", "user.ssn", ...}    # dotted-path nested fields
    }

On each wrapped op, args are deep-copied, the matched fields replaced with
the :data:`REDACTED` sentinel, and the cleaned args passed down. The original
caller dict is never mutated — important when redaction sits between
the agent and the network.
"""
from __future__ import annotations

import copy
from typing import Any, Iterable

from persistence.effect.runtime import Handler


REDACTED = "<REDACTED>"


def _redact_in_place(value: Any, fields: set[str], paths: set[str], prefix: str = "") -> None:
    """Mutate a (deep-copied) nested dict structure to redact PII in place.

    Only dicts are traversed; lists of dicts are also handled.
    """
    if isinstance(value, dict):
        for key, sub in list(value.items()):
            full = f"{prefix}.{key}" if prefix else key
            if key in fields or full in paths:
                value[key] = REDACTED
            else:
                _redact_in_place(sub, fields, paths, prefix=full)
    elif isinstance(value, list):
        for item in value:
            _redact_in_place(item, fields, paths, prefix=prefix)
    # Primitives: nothing to do.


def make_pii_redact_handler(
    *,
    schema: dict[str, set[str]],
    wraps: Iterable[str] = ("net/fetch", "tool/call", "emit-artifact"),
) -> Handler:
    """Return a PII-redact handler."""
    fields = set(schema.get("fields", set()))
    paths = set(schema.get("paths", set()))

    def make_op_clause(op_name: str):
        def clause(args, k, ctx):
            # Deep-copy to protect the caller's dict.
            clean = copy.deepcopy(args)
            _redact_in_place(clean, ctx["fields"], ctx["paths"])
            return k(clean)

        return clause

    clauses = {op: make_op_clause(op) for op in wraps}
    return Handler(
        name="pii-redact",
        wraps=set(wraps),
        clauses=clauses,
        ctx={"fields": fields, "paths": paths},
    )
