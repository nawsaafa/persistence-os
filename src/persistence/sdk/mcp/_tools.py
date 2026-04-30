"""The 6 MCP tools as pure functions over ``(Substrate, args, ctx)``.

Per ADR-15 § 5.2 + ADR-13:

- Each tool returns either a *success* result envelope::

      {
          "isError": False,
          "structuredContent": {...},  # tool-specific (output schema)
          "content": [{"type": "text", "text": <str>}],  # fallback
      }

  ...or an *error* result envelope::

      {
          "isError": True,
          "content": [{"type": "text", "text": <message>}],
          "structuredContent": {...},  # tool-specific error fields
          "_meta": {"category": <str>, "retriable": <bool>},
      }

  Categories: ``validation_error`` / ``capability_denied`` / ``not_found``
  / ``budget_exceeded`` / ``serialization_failure`` / ``internal_error``.

Audit emission: every tool call appends exactly ONE entry to the
substrate's session-local ``_audit_entries`` list (the same list the
``s.audit.entries()`` curated namespace returns and that SDK2's
escape-hatch helper writes into). Entry shape:

    {"op": ":mcp/op-<verb>", "args": {...payload...}}

The payload includes the canonical-JSON-hash of the wire ``args`` and
the result, the verdict (``ok`` / ``error``), the wire tool name, and a
synthetic ``tx`` field (= length-of-substrate-audit-list at emission
time, for cheap stable ordering across replays). The shape matches the
output schema the dispatcher validates against.

Safety budget: ``replay_check`` enforces the three ADR-13 budgets
(``window > MCP_REPLAY_MAX_WINDOW`` rejected; per-token rate limit; the
wallclock budget is observable but isn't truly enforceable in v0.8
since the underlying replay surface isn't wired through ADR-9; the
result envelope still carries the ``replay_aborted_budget`` reason
code path so synthetic-slow tests can exercise it).
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

from persistence.sdk.mcp._budgets import Budgets
from persistence.sdk.mcp._names import _NAMES


# ---------------------------------------------------------------------------
# Error categories — closed set per ADR-15 § 5.2.
# ---------------------------------------------------------------------------
ERR_VALIDATION = "validation_error"
ERR_CAPABILITY_DENIED = "capability_denied"
ERR_NOT_FOUND = "not_found"
ERR_BUDGET = "budget_exceeded"
ERR_SERIALIZATION = "serialization_failure"
ERR_INTERNAL = "internal_error"


def make_error(
    *,
    category: str,
    message: str,
    structured: Optional[dict[str, Any]] = None,
    retriable: bool = False,
) -> dict[str, Any]:
    """Build the standard error result envelope per ADR-15 § 5.2."""
    body: dict[str, Any] = {
        "isError": True,
        "content": [{"type": "text", "text": message}],
        "_meta": {"category": category, "retriable": retriable},
    }
    if structured is not None:
        body["structuredContent"] = structured
    else:
        body["structuredContent"] = {"error_code": category}
    return body


def make_success(
    *,
    structured: dict[str, Any],
    text_summary: str,
) -> dict[str, Any]:
    """Build the standard success result envelope per ADR-15 § 5.2."""
    return {
        "isError": False,
        "structuredContent": structured,
        "content": [{"type": "text", "text": text_summary}],
    }


# ---------------------------------------------------------------------------
# Audit emission
# ---------------------------------------------------------------------------
def _hash_canonical(payload: Any) -> str:
    """SHA-256 of a canonical-JSON encoding of ``payload``.

    Mirrors :func:`persistence.effect.canonical.canonical_dumps` minus the
    EDN-keyword pretreatment; the MCP audit shape is ``@experimental``
    per ADR-1 W3 NIT-5 so we don't need bit-level interop with the
    audit-handler chain in v0.8.
    """
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _emit_audit(
    substrate: Any,
    *,
    verb: str,
    args: dict[str, Any],
    result_envelope: dict[str, Any],
) -> dict[str, Any]:
    """Append one ``:mcp/op-<verb>`` entry to ``substrate._audit_entries``
    and return the appended dict.

    Per ADR-4: chains into the same root audit list as ``:repl/...``,
    ``:sdk/...``. The ``prev_hash`` is the previous entry's content
    hash; for a fresh substrate the head entry has ``prev_hash=None``.
    """
    audit_op = _NAMES[verb]["audit_op"]
    args_hash = _hash_canonical(args)
    if result_envelope.get("isError"):
        verdict = "error"
        result_hash = _hash_canonical(result_envelope.get("structuredContent", {}))
    else:
        verdict = "ok"
        result_hash = _hash_canonical(
            result_envelope.get("structuredContent", {})
        )

    entries: list = substrate._audit_entries  # private but stable per SDK2
    prev_hash: Optional[str] = None
    if entries:
        last = entries[-1]
        if isinstance(last, dict):
            # Compute prev_hash from the prior dict-shaped entry's stable
            # content (same fields we hash here, minus ``prev_hash`` /
            # ``id``). Keeps the chain honest across mixed entries even
            # though the dict shape is @experimental.
            prev_hash = last.get("id")

    entry: dict[str, Any] = {
        "op": audit_op,
        "args": {
            "args_hash": args_hash,
            "result_hash": result_hash,
            "verdict": verdict,
            "tool": _NAMES[verb]["wire"],
            "tx": len(entries),
        },
        "prev_hash": prev_hash,
    }
    entry["id"] = _hash_canonical(
        {k: v for k, v in entry.items() if k != "id"}
    )
    entries.append(entry)
    return entry


# ---------------------------------------------------------------------------
# Capability check
# ---------------------------------------------------------------------------
def has_cap(token_caps: Iterable[str], required: str) -> bool:
    """``True`` iff ``required`` is in ``token_caps``."""
    return required in set(token_caps)


def _denied(verb: str) -> dict[str, Any]:
    return make_error(
        category=ERR_CAPABILITY_DENIED,
        message=f"capability denied: {_NAMES[verb]['capability']}",
        structured={
            "error_code": ERR_CAPABILITY_DENIED,
            "required": _NAMES[verb]["capability"],
        },
    )


# ---------------------------------------------------------------------------
# Schema-driven input validation
# ---------------------------------------------------------------------------
def validate_against_schema(
    payload: Any, schema: dict[str, Any], path: str = "$"
) -> Optional[str]:
    """Validate ``payload`` against ``schema``; return ``None`` on success
    or an error message on failure.

    Implements only the subset of draft-07 the v0.8 schema profile uses
    (§ 5.1.1). Hand-rolled to avoid pulling in ``jsonschema`` as a
    runtime dep — the profile is small enough that direct walking is
    cleaner than a generic engine.
    """
    if not isinstance(schema, dict):
        return None
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(payload, dict):
            return f"{path}: expected object, got {type(payload).__name__}"
        # additionalProperties + required + per-property recursion.
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = set(payload.keys()) - set(properties.keys())
            if extra:
                return f"{path}: unexpected properties {sorted(extra)!r}"
        required = schema.get("required", [])
        for r in required:
            if r not in payload:
                return f"{path}: missing required property {r!r}"
        for prop_name, prop_schema in properties.items():
            if prop_name in payload:
                err = validate_against_schema(
                    payload[prop_name],
                    prop_schema,
                    f"{path}.{prop_name}",
                )
                if err is not None:
                    return err
        return None
    if expected_type == "array":
        if not isinstance(payload, list):
            return f"{path}: expected array, got {type(payload).__name__}"
        items_schema = schema.get("items")
        max_items = schema.get("maxItems")
        if max_items is not None and len(payload) > max_items:
            return f"{path}: array length {len(payload)} > maxItems {max_items}"
        min_items = schema.get("minItems")
        if min_items is not None and len(payload) < min_items:
            return f"{path}: array length {len(payload)} < minItems {min_items}"
        if items_schema is not None:
            for i, item in enumerate(payload):
                err = validate_against_schema(item, items_schema, f"{path}[{i}]")
                if err is not None:
                    return err
        return None
    if expected_type == "string":
        if not isinstance(payload, str):
            return f"{path}: expected string, got {type(payload).__name__}"
        ml = schema.get("minLength")
        if ml is not None and len(payload) < ml:
            return f"{path}: string length {len(payload)} < minLength {ml}"
        Ml = schema.get("maxLength")
        if Ml is not None and len(payload) > Ml:
            return f"{path}: string length {len(payload)} > maxLength {Ml}"
        # ``format`` validation per profile-allowed values
        fmt = schema.get("format")
        if fmt == "uuid":
            try:
                uuid.UUID(payload)
            except (ValueError, AttributeError):
                return f"{path}: not a valid UUID"
        elif fmt == "date-time":
            try:
                datetime.fromisoformat(payload.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return f"{path}: not a valid date-time"
        # enum
        if "enum" in schema and payload not in schema["enum"]:
            return f"{path}: value {payload!r} not in enum {schema['enum']!r}"
        return None
    if expected_type == "integer":
        # Python bool is a subclass of int — reject explicitly.
        if isinstance(payload, bool) or not isinstance(payload, int):
            return f"{path}: expected integer, got {type(payload).__name__}"
        mn = schema.get("minimum")
        if mn is not None and payload < mn:
            return f"{path}: integer {payload} < minimum {mn}"
        mx = schema.get("maximum")
        if mx is not None and payload > mx:
            return f"{path}: integer {payload} > maximum {mx}"
        return None
    if expected_type == "boolean":
        if not isinstance(payload, bool):
            return f"{path}: expected boolean, got {type(payload).__name__}"
        return None
    return None


# ---------------------------------------------------------------------------
# Storage helpers — the "memory" attribute namespace per ADR-4
# ---------------------------------------------------------------------------
# Per ADR-4 + § 5.1, MCP-stored facts live under ``mcp/content`` /
# ``mcp/tags`` attributes on each entity. Storage is via the substrate's
# curated ``s.fact.transact`` namespace (no escape-hatch).

_MCP_CONTENT_ATTR: str = "mcp/content"
_MCP_TAGS_ATTR: str = "mcp/tags"


# ---------------------------------------------------------------------------
# Tool: remember
# ---------------------------------------------------------------------------
def tool_remember(
    substrate: Any,
    args: dict[str, Any],
    *,
    token_caps: set[str],
    budgets: Budgets,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    if not has_cap(token_caps, "mcp.remember"):
        env = _denied("remember")
    else:
        content = args.get("content")
        tags = args.get("tags", [])
        if not isinstance(content, str):
            env = make_error(
                category=ERR_VALIDATION,
                message="content must be a string",
            )
        elif len(content.encode("utf-8")) > budgets.remember_max_content_bytes:
            env = make_error(
                category=ERR_VALIDATION,
                message=(
                    f"content exceeds max bytes "
                    f"({budgets.remember_max_content_bytes})"
                ),
            )
        else:
            eid = str(uuid.uuid4())  # noqa: wall-clock-uuid (generator)
            now = clock()
            valid_from = now.isoformat()
            try:
                new_db = substrate._db.transact(
                    [
                        {
                            "e": eid,
                            "a": _MCP_CONTENT_ATTR,
                            "v": content,
                            "valid_from": now,
                        },
                        {
                            "e": eid,
                            "a": _MCP_TAGS_ATTR,
                            "v": list(tags) if tags else [],
                            "valid_from": now,
                        },
                    ]
                )
                substrate._db = new_db
                # Probe the freshly-transacted log for the tx id of
                # this fact (DB.transact returns a new DB; the tx id
                # is reachable by walking the log tail).
                tx_id = 0
                for d in new_db.log():
                    if d.e == eid and d.a == _MCP_CONTENT_ATTR:
                        tx_id = d.tx
                env = make_success(
                    structured={
                        "eid": eid,
                        "tx": int(tx_id),
                        "valid_from": valid_from,
                    },
                    text_summary=f"remembered {eid}",
                )
            except Exception as exc:  # noqa: BLE001
                env = make_error(
                    category=ERR_INTERNAL,
                    message=f"transact failed: {exc}",
                )
    _emit_audit(substrate, verb="remember", args=args, result_envelope=env)
    return env


# ---------------------------------------------------------------------------
# Tool: recall
# ---------------------------------------------------------------------------
def tool_recall(
    substrate: Any,
    args: dict[str, Any],
    *,
    token_caps: set[str],
    budgets: Budgets,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    if not has_cap(token_caps, "mcp.recall"):
        env = _denied("recall")
    else:
        query = args.get("query", "")
        k = args.get("k", 5)
        if not isinstance(query, str):
            env = make_error(
                category=ERR_VALIDATION, message="query must be a string"
            )
        elif not isinstance(k, int) or k < 1 or k > budgets.recall_max_k:
            env = make_error(
                category=ERR_VALIDATION,
                message=f"k must be an integer in [1, {budgets.recall_max_k}]",
            )
        else:
            tags_filter = args.get("tags", []) or []
            now = clock()
            view = substrate._db.as_of(now)
            # Walk the log to find every entity that has a
            # ``mcp/content`` datom; project + filter.
            ql = query.lower()
            seen: set[str] = set()
            hits: list[dict[str, Any]] = []
            # Iterate in reverse-tx order (most recent first) for a
            # stable recency ranking per ADR-8.
            log_list = list(substrate._db.log())
            for d in reversed(log_list):
                if d.a != _MCP_CONTENT_ATTR:
                    continue
                if d.e in seen:
                    continue
                seen.add(d.e)
                ent = view.entity(d.e)
                if not ent:
                    continue
                content = ent.get(_MCP_CONTENT_ATTR)
                if not isinstance(content, str):
                    continue
                if ql not in content.lower():
                    continue
                tags = ent.get(_MCP_TAGS_ATTR) or []
                if tags_filter:
                    if not all(t in tags for t in tags_filter):
                        continue
                hits.append({
                    "eid": d.e,
                    "content": content,
                    "tags": list(tags) if isinstance(tags, list) else [],
                    "valid_from": (
                        d.valid_from.isoformat()
                        if isinstance(d.valid_from, datetime)
                        else str(d.valid_from)
                    ),
                })
                if len(hits) >= k:
                    break
            env = make_success(
                structured={"hits": hits},
                text_summary=f"{len(hits)} hit(s) for {query!r}",
            )
    _emit_audit(substrate, verb="recall", args=args, result_envelope=env)
    return env


# ---------------------------------------------------------------------------
# Tool: forget
# ---------------------------------------------------------------------------
def tool_forget(
    substrate: Any,
    args: dict[str, Any],
    *,
    token_caps: set[str],
    budgets: Budgets,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    if not has_cap(token_caps, "mcp.forget"):
        env = _denied("forget")
    else:
        eid = args.get("eid")
        if not isinstance(eid, str):
            env = make_error(
                category=ERR_VALIDATION, message="eid must be a string"
            )
        else:
            now = clock()
            view = substrate._db.as_of(now)
            ent = view.entity(eid)
            if not ent or _MCP_CONTENT_ATTR not in ent:
                env = make_error(
                    category=ERR_NOT_FOUND,
                    message=f"no entity at {eid}",
                )
            else:
                # Retract by writing a retract op against the prior fact.
                content = ent.get(_MCP_CONTENT_ATTR)
                try:
                    new_db = substrate._db.transact([
                        {
                            "e": eid,
                            "a": _MCP_CONTENT_ATTR,
                            "v": content,
                            "valid_from": now,
                            "op": "retract",
                            "valid_to": now,
                        }
                    ])
                    substrate._db = new_db
                    env = make_success(
                        structured={
                            "eid": eid,
                            "valid_to": now.isoformat(),
                            "retracted": True,
                        },
                        text_summary=f"retracted {eid}",
                    )
                except Exception as exc:  # noqa: BLE001
                    env = make_error(
                        category=ERR_INTERNAL,
                        message=f"retract failed: {exc}",
                    )
    _emit_audit(substrate, verb="forget", args=args, result_envelope=env)
    return env


# ---------------------------------------------------------------------------
# Tool: audit_window
# ---------------------------------------------------------------------------
def tool_audit_window(
    substrate: Any,
    args: dict[str, Any],
    *,
    token_caps: set[str],
    budgets: Budgets,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    if not has_cap(token_caps, "mcp.audit-read"):
        env = _denied("audit_window")
    else:
        from_tx = args.get("from_tx")
        to_tx = args.get("to_tx")
        limit = args.get("limit", 100)
        if not isinstance(from_tx, int) or from_tx < 0 or isinstance(from_tx, bool):
            env = make_error(
                category=ERR_VALIDATION,
                message="from_tx must be a non-negative integer",
            )
        elif (
            not isinstance(limit, int)
            or limit < 1
            or limit > budgets.audit_window_max_limit
            or isinstance(limit, bool)
        ):
            env = make_error(
                category=ERR_VALIDATION,
                message=(
                    f"limit must be an integer in "
                    f"[1, {budgets.audit_window_max_limit}]"
                ),
            )
        else:
            entries_in: list = list(substrate._audit_entries)
            out_entries: list[dict[str, Any]] = []
            for tx, entry in enumerate(entries_in):
                if tx < from_tx:
                    continue
                if to_tx is not None and tx > to_tx:
                    break
                if isinstance(entry, dict):
                    out_entries.append({
                        "op": entry.get("op", ""),
                        "args_hash": (
                            entry.get("args", {}).get("args_hash", "")
                        ),
                        "result_hash": (
                            entry.get("args", {}).get("result_hash", "")
                        ),
                        "prev_hash": entry.get("prev_hash") or "",
                        "tx": tx,
                    })
                else:
                    out_entries.append({
                        "op": getattr(entry, "op", ""),
                        "args_hash": getattr(entry, "args_hash", "") or "",
                        "result_hash": getattr(entry, "result_hash", "") or "",
                        "prev_hash": getattr(entry, "prev_hash", "") or "",
                        "tx": tx,
                    })
                if len(out_entries) >= limit:
                    break
            head_hash = ""
            if entries_in:
                last = entries_in[-1]
                if isinstance(last, dict):
                    head_hash = last.get("id") or ""
                else:
                    head_hash = getattr(last, "id", "") or ""
            env = make_success(
                structured={
                    "entries": out_entries,
                    "head_hash": head_hash,
                },
                text_summary=f"{len(out_entries)} audit entries",
            )
    _emit_audit(
        substrate, verb="audit_window", args=args, result_envelope=env
    )
    return env


# ---------------------------------------------------------------------------
# Tool: replay_check (with full ADR-13 budget)
# ---------------------------------------------------------------------------
def tool_replay_check(
    substrate: Any,
    args: dict[str, Any],
    *,
    token_caps: set[str],
    budgets: Budgets,
    clock: Callable[[], datetime],
    rate_limiter: Optional[Callable[[], bool]] = None,
    synthetic_slow_wallclock: Optional[float] = None,
) -> dict[str, Any]:
    if not has_cap(token_caps, "mcp.replay"):
        env = _denied("replay_check")
    else:
        tx = args.get("tx")
        window = args.get("window", 32)
        if not isinstance(tx, int) or tx < 0 or isinstance(tx, bool):
            env = make_error(
                category=ERR_VALIDATION,
                message="tx must be a non-negative integer",
            )
        elif (
            not isinstance(window, int)
            or window < 1
            or isinstance(window, bool)
        ):
            env = make_error(
                category=ERR_VALIDATION,
                message="window must be a positive integer",
            )
        elif window > budgets.replay_max_window:
            env = make_error(
                category=ERR_BUDGET,
                message=(
                    f"window {window} > MCP_REPLAY_MAX_WINDOW "
                    f"({budgets.replay_max_window})"
                ),
                structured={
                    "error_code": ERR_BUDGET,
                    "ok": False,
                    "reason_code": "window_too_large",
                    "window_actual": 0,
                    "head_hash": "",
                },
            )
        elif rate_limiter is not None and not rate_limiter():
            env = make_error(
                category=ERR_BUDGET,
                message="replay rate limit exceeded",
                structured={
                    "error_code": ERR_BUDGET,
                    "ok": False,
                    "reason_code": "replay_aborted_budget",
                    "window_actual": 0,
                    "head_hash": "",
                    "retry_after_s": int(budgets.replay_rate_window_s),
                },
                retriable=True,
            )
        elif (
            synthetic_slow_wallclock is not None
            and synthetic_slow_wallclock > budgets.replay_max_wallclock_s
        ):
            # Synthetic-slow path for G8b (deterministic budget exhaust).
            env = make_error(
                category=ERR_BUDGET,
                message=(
                    f"replay aborted: wallclock budget "
                    f"{budgets.replay_max_wallclock_s}s exceeded"
                ),
                structured={
                    "error_code": ERR_BUDGET,
                    "ok": False,
                    "reason_code": "replay_aborted_budget",
                    "window_actual": 0,
                    "head_hash": "",
                },
            )
        else:
            entries: list = list(substrate._audit_entries)
            n = len(entries)
            if tx >= n and n > 0:
                env = make_success(
                    structured={
                        "ok": False,
                        "reason_code": "tx_not_found",
                        "window_actual": 0,
                        "head_hash": "",
                    },
                    text_summary=f"tx {tx} not found",
                )
            else:
                # Compute a synthetic verify-chain over the window
                # surrounding ``tx`` to produce a verdict. The MCP
                # surface returns a boolean + reason code only —
                # NEVER a diff (per ADR-13).
                lo = max(0, tx - window // 2)
                hi = min(n, tx + window // 2 + 1)
                window_actual = max(0, hi - lo)
                # Verify hash chain integrity over the window slice.
                ok = True
                prev_hash: Optional[str] = None
                for entry in entries[lo:hi]:
                    if isinstance(entry, dict):
                        eid = entry.get("id")
                        eprev = entry.get("prev_hash")
                        # Recompute id and check chain.
                        recomputed = _hash_canonical({
                            k: v for k, v in entry.items() if k != "id"
                        })
                        if eid != recomputed:
                            ok = False
                            reason = "mismatch_audit_chain"
                            break
                        # First entry in window has whatever prev_hash;
                        # subsequent must chain forward.
                        if prev_hash is not None and eprev != prev_hash:
                            ok = False
                            reason = "mismatch_audit_chain"
                            break
                        prev_hash = eid
                else:
                    reason = "ok" if ok else "mismatch_audit_chain"
                head_hash = ""
                if entries:
                    last = entries[-1]
                    if isinstance(last, dict):
                        head_hash = last.get("id", "")
                env = make_success(
                    structured={
                        "ok": ok,
                        "reason_code": reason if ok else reason,
                        "window_actual": window_actual,
                        "head_hash": head_hash,
                    },
                    text_summary=(
                        f"replay {'ok' if ok else 'mismatch'} over {window_actual}"
                    ),
                )
    _emit_audit(
        substrate, verb="replay_check", args=args, result_envelope=env
    )
    return env


# ---------------------------------------------------------------------------
# Tool: view_at
# ---------------------------------------------------------------------------
def tool_view_at(
    substrate: Any,
    args: dict[str, Any],
    *,
    token_caps: set[str],
    budgets: Budgets,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    if not has_cap(token_caps, "mcp.view"):
        env = _denied("view_at")
    else:
        tx = args.get("tx")
        label = args.get("label")
        if not isinstance(tx, int) or tx < 0 or isinstance(tx, bool):
            env = make_error(
                category=ERR_VALIDATION,
                message="tx must be a non-negative integer",
            )
        elif label is not None and (
            not isinstance(label, str)
            or len(label) > budgets.view_at_max_label_len
            or len(label) < 1
        ):
            env = make_error(
                category=ERR_VALIDATION,
                message=(
                    f"label must be a 1..{budgets.view_at_max_label_len}-char "
                    "string"
                ),
            )
        else:
            entries: list = list(substrate._audit_entries)
            n = len(entries)
            if n == 0:
                view_iso = clock().isoformat()
                depth = 0
            else:
                if tx >= n:
                    # tx in the future — anchor to tail.
                    depth = n
                else:
                    depth = tx + 1
                view_iso = clock().isoformat()
            cursor_id = str(uuid.uuid4())  # noqa: wall-clock-uuid
            structured: dict[str, Any] = {
                "cursor_id": cursor_id,
                "view_cursor_tx_time_iso": view_iso,
                "parent_chain_depth": depth,
            }
            if label is not None:
                structured["label"] = label
            env = make_success(
                structured=structured,
                text_summary=f"cursor {cursor_id} @ tx={tx}",
            )
    _emit_audit(substrate, verb="view_at", args=args, result_envelope=env)
    return env


# ---------------------------------------------------------------------------
# Aggregated dispatch table
# ---------------------------------------------------------------------------
TOOL_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "remember": tool_remember,
    "recall": tool_recall,
    "forget": tool_forget,
    "audit_window": tool_audit_window,
    "replay_check": tool_replay_check,
    "view_at": tool_view_at,
}


__all__ = [
    "ERR_BUDGET",
    "ERR_CAPABILITY_DENIED",
    "ERR_INTERNAL",
    "ERR_NOT_FOUND",
    "ERR_SERIALIZATION",
    "ERR_VALIDATION",
    "TOOL_HANDLERS",
    "has_cap",
    "make_error",
    "make_success",
    "tool_audit_window",
    "tool_forget",
    "tool_recall",
    "tool_remember",
    "tool_replay_check",
    "tool_view_at",
    "validate_against_schema",
]
