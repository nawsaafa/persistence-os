""":repl/op AuditEntry emission + persistence (D7).

See design doc ``docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md``
sections ADR-4 (op auditing — every op emits one ``:repl/op``
AuditEntry through the canonical Merkle chain), 5.1 (inspect
``kind="audit-window"``), 8.2 (byte-identity defense), and 10 (D7).

The substrate's :class:`persistence.effect.handlers.audit.AuditEntry`
has a fixed canonical field set; :func:`verify_chain` recomputes
``_content_hash(entry.to_dict())`` against exactly those fields.
**REPL-specific fields (`op_kind`, `view_cursor_*`) MUST ride on
``principal``** — a canonical dataclass slot of type ``dict[str, Any]``
— so the emitted entry's ``id`` matches what ``verify_chain`` would
recompute. Adding a flat top-level field would break Merkle continuity
across every ``:repl/op`` entry. This module is the load-bearing
W2.A / W2.B / W2.C closure point for Stream D.

Two-tier audit storage (W2.C): the in-memory ring on
``WSServer._audit_entries`` is the **hot cache** for the WS-tail
subscription; the **fact store** is the durable source of truth.
:func:`persist_repl_audit` writes one ``audit/repl.op`` datom per
entry (the standard wire shape produced by
:func:`persistence.effect.handlers.audit.audit_entry_to_datom`,
reshaped to the fact-store ``Datom`` slot conventions). After server
restart the ring is empty but the durable datoms remain, so
``inspect kind="audit-window"`` (via :func:`_audit_window_query`) can
backfill from the fact store.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from persistence.effect.canonical import canonical_hash
from persistence.effect.handlers.audit import (
    AuditEntry,
    _canonicalise_content,
    _content_hash,
    audit_entry_to_datom,
    datom_to_audit_entry,
)
from persistence.fact.datom import Datom


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
#: The bare-form ``Datom.a`` slot for a ``:repl/op`` audit datom.
#:
#: ``audit_entry_to_datom`` encodes the inner ``/`` as a ``.`` so the
#: full keyword stays single-namespaced (``:audit/repl.op``). At the
#: store level the leading ``:`` is dropped (``Datom.a`` is bare-string
#: per ``datom.py:114``), giving us ``"audit/repl.op"``. Filtering the
#: log on this exact string lets :func:`_audit_window_query` skip every
#: non-REPL audit datom (``audit/llm.call``, ``audit/tool.call``, etc.)
#: in O(N_log) without parsing.
_REPL_OP_ATTR: str = "audit/repl.op"


# ---------------------------------------------------------------------------
# emit_repl_op_audit (W2.A — REPL fields ride on principal)
# ---------------------------------------------------------------------------
def emit_repl_op_audit(
    entries: list[AuditEntry],
    *,
    session: Any,  # ``Session`` — typed as Any to avoid circular import
    op_kind: str,  # "inspect" | "edit" | "rewind" | "branch" | "auth"
    args: dict,
    verdict: str,  # "ok" | "error" | "deny" | "deny-silently" | "require-approval"
    latency_ms: int,
    result_hash: str | None = None,
    error: str | None = None,
    view_cursor_tx_time_iso: str | None = None,
    view_cursor_vt_iso: str | None = None,
) -> AuditEntry:
    """Build one ``:repl/op`` AuditEntry, append to ``entries``, return it.

    Per ADR-4 (W2.A): REPL-specific fields (``op_kind``,
    ``view_cursor_tx_time_iso``, ``view_cursor_vt_iso``) ride on
    ``principal`` so the canonical AuditEntry field set is intact and
    ``verify_chain`` returns ``True``. The hash is computed over
    ``_canonicalise_content`` so the dataclass-side
    ``__post_init__`` canonicalisation is mirrored exactly — closes the
    same R5 N1 MAJOR class as ARIS R6's audit-handler factory fix.

    ``txn_commit`` is intentionally absent from the content dict so
    ``AuditEntry`` defaults it to ``None``; ``to_dict()`` strips
    ``None`` ``txn_commit`` (audit.py:184), so the
    ``verify_chain`` recomputed-hash lines up byte-identically.

    Caller owns persistence (:func:`persist_repl_audit`); the in-memory
    ring is a hot cache for the WS-tail subscription.
    """
    prev_hash = entries[-1].id if entries else None
    principal: dict[str, Any] = {
        "token_id": session.token_id,
        "session_id": session.session_id,
        "op_kind": op_kind,
        "view_cursor_tx_time_iso": view_cursor_tx_time_iso,
        "view_cursor_vt_iso": view_cursor_vt_iso,
    }
    content: dict[str, Any] = {
        "prev_hash": prev_hash,
        "op": ":repl/op",
        "args_hash": canonical_hash(args),
        "verdict": verdict,
        "latency_ms": latency_ms,
        "recorded_at": session.clock().timestamp(),  # float epoch seconds
        "result_hash": result_hash,
        "error": error,
        "policy_id": None,
        "handler_chain": (),
        "principal": principal,
        "run_id": None,
        "parent": prev_hash,
        # Phase 2.3c.2 LD5 — :repl/op AuditEntries are never emitted from
        # a nested LLM/skill dispatch (the REPL op surface is the OUTER
        # boundary, not a child of any other call), so this is always
        # ``None``. The key MUST be present in the content dict so the
        # hash matches ``to_dict()``'s output (which always includes
        # ``parent_audit_entry_id``, unlike ``txn_commit`` which is
        # popped when None). Mirrors the audit.py:make_audit_handler
        # always-write rule introduced in T2.
        "parent_audit_entry_id": None,
    }
    # ``txn_commit`` intentionally OMITTED — see audit.py:174-186 for the
    # symmetry: ``to_dict()`` pops ``None`` txn_commit before hashing in
    # ``verify_chain``, so we must also omit it from the content dict
    # used to compute ``entry.id`` here.
    canonical_content = _canonicalise_content(content)
    content_hash_value = _content_hash(canonical_content)
    entry = AuditEntry(id=content_hash_value, **canonical_content)
    entries.append(entry)
    return entry


# ---------------------------------------------------------------------------
# persist_repl_audit (W2.C + PG3 ADR-13 — routes through transact_serializable)
# ---------------------------------------------------------------------------
def persist_repl_audit(db: Any, entry: AuditEntry) -> None:
    """Persist one ``:repl/op`` AuditEntry as one ``audit/repl.op`` datom.

    Per ADR-4 + W2.C + PG3 ADR-13: ``audit_entry_to_datom`` returns the
    wire-form dict ``{":datom/e": ..., ":datom/a": ":audit/repl.op",
    ...}``; we reshape into a :class:`Datom` (bare-string ``a``, ``int``
    ``tx``) and route through ``db.store.transact_serializable`` so the
    audit datom inherits the multi-process SSI guarantees the
    PostgresStore backbone offers — and, crucially, takes the
    ``audit_chain_lock`` row lock that orders the Merkle chain across
    concurrent writers.

    **Why ``transact_serializable`` and not ``db.transact``.** ``db.transact``
    re-stamps ``tx_time`` from the DB clock and would lose the audit
    handler's own ``recorded_at`` instant — exactly the constraint that
    drove the original ``store.append`` route in pre-PG3 code. ADR-13
    closes this by extending ``transact_serializable`` with an explicit
    ``tx_time=`` parameter; we pass the audit datom's
    ``:datom/tx-time`` (which is ``recorded_at`` in datetime form),
    preserving byte-identity for replay across the migration.

    **Why ``transact_serializable`` and not ``store.append``.** Pre-PG3
    code allocated tx via ``_allocate_next_tx`` (full ``db.log()`` scan
    + ``max+1``) and routed through ``store.append``. Under multi-
    process this races: two writers both compute ``max_tx+1=N``, both
    INSERT, and the chain head pointer in ``audit_chain_lock`` plus the
    in-Python audit chain state diverge. The PG3 migration routes
    through ``transact_serializable`` so the row-lock allocator + the
    audit-chain-lock primitive both fire — single atomic step, multi-
    process safe.
    """
    wire = audit_entry_to_datom(entry)
    # Re-shape wire-dict → Datom slots:
    # - ``:datom/a`` carries the leading ``":"``; ``Datom.a`` is bare.
    # - ``:datom/tx`` carries the audit entry's content hash (sha256
    #   string), but ``Datom.tx`` is ``int``. The hash is preserved in
    #   ``provenance[":signature"]`` (see ``audit_entry_to_datom``
    #   docstring), so the inverse can splice it back. ``tx`` here is
    #   set to a placeholder ``0`` because ``transact_serializable``'s
    #   contract is "tx field on input datoms is ignored — the
    #   allocator picks the real value" (see Store Protocol docstring
    #   for the additive method).
    # - ``:datom/op`` carries the leading ``":"``; ``Datom.op`` is bare.
    recorded_at = wire[":datom/tx-time"]
    datom = Datom(
        e=wire[":datom/e"],
        a=wire[":datom/a"].lstrip(":"),
        v=wire[":datom/v"],
        tx=0,  # ignored — transact_serializable picks the real tx-id
        tx_time=recorded_at,  # preserved as-is; passed via tx_time= kwarg too
        valid_from=wire[":datom/valid-from"],
        valid_to=wire[":datom/valid-to"],
        op="assert",
        provenance=wire[":datom/provenance"],
    )
    # Route through transact_serializable per ADR-13. The explicit
    # ``tx_time=recorded_at`` argument is load-bearing: it tells the
    # store-level primitive to override the datom's tx_time with the
    # audit handler's recorded instant, preserving replay byte-identity
    # for existing audit entries that bypass the DB clock. On
    # PostgresStore this also takes the ``audit_chain_lock`` row lock
    # because the datom's ``a`` starts with ``audit/``.
    db.store.transact_serializable([datom], tx_time=recorded_at)


# ---------------------------------------------------------------------------
# _audit_window_query — backfill path for inspect kind="audit-window"
# ---------------------------------------------------------------------------
def _datom_to_wire_for_audit(datom: Datom) -> dict[str, Any]:
    """Reconstruct the wire-form dict that ``datom_to_audit_entry`` expects.

    Mirror of ``persistence.plan._promotion._datom_to_wire_for_audit``
    (see that module's docstring for the full rationale). The audit
    entry's content hash lives in ``provenance[":signature"]`` because
    ``Datom.tx`` is ``int``; we splice it back into ``:datom/tx`` so
    the inverse reconstructs ``entry.id`` correctly.
    """
    provenance = dict(datom.provenance)
    if ":signature" not in provenance:
        raise ValueError(
            "audit Datom missing required provenance[':signature']; "
            "the datom was not produced by audit_entry_to_datom"
        )
    audit_id = provenance[":signature"]
    return {
        ":datom/e": datom.e,
        ":datom/a": ":" + datom.a,
        ":datom/v": datom.v,
        ":datom/tx": audit_id,
        ":datom/tx-time": datom.tx_time,
        ":datom/valid-from": datom.valid_from,
        ":datom/valid-to": datom.valid_to,
        ":datom/op": ":" + datom.op,
        ":datom/provenance": provenance,
        ":datom/invalidated-by": datom.invalidated_by,
    }


def _audit_window_query(
    db: Any,
    *,
    from_iso: str | None,
    to_iso: str | None,
    op_filter: str | None,
    limit: int,
) -> list[AuditEntry]:
    """Query persisted ``audit/...`` datoms in the window.

    Walks ``db.log()`` once; filters on attribute prefix ``audit/``,
    optionally narrowed by ``op_filter`` (which matches against the
    canonical ``:audit/<op>`` keyword form, e.g. ``":repl/op"``,
    ``":llm/call"``); reconstructs each matching datom into an
    ``AuditEntry`` via ``datom_to_audit_entry``.

    Returned in fact-store insertion order (which IS chain order
    because each ``:repl/op`` lands as one ``store.append`` call —
    see ``persist_repl_audit``). Truncated to ``limit``.

    For ``op_filter=":repl/op"`` we shortcut to the bare-form
    ``"audit/repl.op"`` constant for an O(1) per-datom check; for any
    other filter we apply it after attribute-prefix detection.
    """
    from_dt = _coerce_iso(from_iso)
    to_dt = _coerce_iso(to_iso)
    out: list[AuditEntry] = []
    for d in db.log():
        if not (isinstance(d.a, str) and d.a.startswith("audit/")):
            continue
        # ``op_filter`` narrowing — match against the bare-form
        # encoded attribute (``audit/repl.op`` for ``:repl/op``,
        # ``audit/llm.call`` for ``:llm/call``).
        if op_filter is not None:
            expected_a = "audit/" + op_filter.lstrip(":").replace("/", ".")
            if d.a != expected_a:
                continue
        if from_dt is not None and d.tx_time < from_dt:
            continue
        if to_dt is not None and d.tx_time > to_dt:
            continue
        try:
            wire = _datom_to_wire_for_audit(d)
            entry = datom_to_audit_entry(wire)
        except (ValueError, KeyError):
            # Defensive: hand-rolled audit datoms (without :signature
            # or with unexpected wire shape) are silently skipped — the
            # window query is best-effort, not a verification gate.
            continue
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def _coerce_iso(iso_str: str | None) -> datetime | None:
    """Parse an ISO-8601 string to a tz-aware datetime, or None.

    Defensive: ``inspect_op._coerce_iso`` already validates shape at
    the op boundary; this is a thin re-parse since the validated form
    is the string itself, not a ``datetime``.
    """
    if iso_str is None:
        return None
    try:
        return datetime.fromisoformat(iso_str)
    except ValueError:
        return None


def _audit_entry_to_summary(entry: AuditEntry) -> dict[str, Any]:
    """Project an AuditEntry to the JSON-friendly dict the wire returns.

    The ``inspect kind="audit-window"`` response carries summary dicts,
    not full ``AuditEntry`` objects (which are dataclasses with
    non-JSON ``frozenset`` / ``tuple`` fields). This helper produces
    the canonical projection — every audit-window response uses the
    same shape regardless of whether the entry is REPL-originated or
    programmatic.
    """
    return {
        "id": entry.id,
        "prev_hash": entry.prev_hash,
        "op": entry.op,
        "args_hash": entry.args_hash,
        "verdict": entry.verdict,
        "latency_ms": entry.latency_ms,
        "recorded_at": entry.recorded_at,
        "result_hash": entry.result_hash,
        "error": entry.error,
        "principal": dict(entry.principal),
    }


__all__ = [
    "_REPL_OP_ATTR",
    "_audit_entry_to_summary",
    "_audit_window_query",
    "emit_repl_op_audit",
    "persist_repl_audit",
]
