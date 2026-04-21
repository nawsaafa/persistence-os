"""Audit handler — hash-chained Merkle log; maps to Fact 8-tuple datoms.

Design notes tied to spec §9 anti-patterns:

- ``audit`` does **not** write to disk synchronously. When constructed with
  ``sink_name=<handler-name>``, it performs ``:audit/emit`` (*masked* from
  itself to prevent re-entry) so a separate archive handler receives each
  entry and may persist asynchronously.
- ``audit`` does **not** call ``time.time()``. It reads ``:clock/now`` under
  mask, so replay mode (mock clock) makes timestamps deterministic.
- Entry ``id`` is a SHA-256 hash of the entry's canonical content — so the
  Merkle chain is cryptographic, not reliant on external uniqueness.
- Both ``verdict='ok'`` (success) and ``verdict='error'`` (failure) entries
  are produced; regulators need the attempted-but-denied trail too (see §3).

The 8-tuple datom schema from agent1-fact-spec.md §1 is reproduced by
:func:`audit_entry_to_datom`; :func:`datom_to_audit_entry` is the inverse.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Iterable

from persistence.effect.canonical import canonical_dumps, canonical_hash
from persistence.effect.runtime import Handler, mask, named, perform


# ---------------------------------------------------------------------------
# AuditEntry — the in-memory record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditEntry:
    """One row in the append-only audit log.

    ``id`` is a content-hash of the entry (excluding ``id`` and
    ``prev_hash`` themselves); the log thus forms a Merkle chain where
    tampering is detectable.
    """

    id: str
    prev_hash: str | None
    op: str
    args_hash: str
    verdict: str  # "ok" | "error" | "deny" | "deny-silently" | "require-approval"
    latency_ms: int
    recorded_at: float
    result_hash: str | None = None
    error: str | None = None
    policy_id: str | None = None
    handler_chain: tuple[str, ...] = field(default_factory=tuple)
    principal: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    parent: str | None = None

    # ---- helpers ----

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def with_fields(self, **changes: Any) -> AuditEntry:
        return dataclasses.replace(self, **changes)


# ---------------------------------------------------------------------------
# ID / chain computation
# ---------------------------------------------------------------------------


def _content_hash(entry_content: dict[str, Any]) -> str:
    """sha256:... of the canonical JSON of the *content* fields (excluding id)."""
    return canonical_hash(entry_content)


def verify_chain(entries: Iterable[AuditEntry]) -> bool:
    """True iff every entry's id is the content hash of its fields AND
    prev_hash references the previous entry's id.
    """
    prev: str | None = None
    for entry in entries:
        # Recompute content hash.
        d = entry.to_dict()
        d.pop("id")
        expected_id = _content_hash(d)
        if entry.id != expected_id:
            return False
        if entry.prev_hash != prev:
            return False
        prev = entry.id
    return True


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_audit_handler(
    entries: list[AuditEntry],
    *,
    wraps: Iterable[str] = (":llm/call",),
    sink_name: str | None = None,
    run_id: str | None = None,
    principal: dict[str, Any] | None = None,
    policy_id: str | None = None,
) -> Handler:
    """Return an audit handler that appends to ``entries``.

    Parameters
    ----------
    entries
        The caller-owned log. Tests pass a fresh list; production code
        passes a list backed by a Ring buffer or file writer.
    wraps
        Ops this handler audits. Typically the stateful ones: :llm/call,
        :tool/call, :decide, :mem/write, :emit-artifact, :net/fetch.
    sink_name
        If given, each entry is also forwarded to the named handler via
        ``:audit/emit`` (masked). Spec §9 anti-pattern avoidance.
    run_id, principal, policy_id
        Fields copied into every entry.
    """
    audit_name = "audit"

    def clause(args: dict[str, Any], k, ctx) -> Any:
        # --- pre-call metadata
        args_hash = canonical_hash(args)
        # Masked so clock/now doesn't re-enter the audit handler (which might
        # itself wrap :clock/now if the caller puts it in wraps).
        with mask(audit_name):
            t0 = perform(":clock/now")["ts"]

        op_name = ctx["_current_op"]  # set per-dispatch below
        result_hash: str | None = None
        error_str: str | None = None
        verdict = "ok"
        raised: BaseException | None = None
        try:
            result = k(args)
            if result is not None:
                result_hash = canonical_hash(result)
            return result
        except BaseException as exc:  # noqa: BLE001
            verdict = "error"
            error_str = f"{type(exc).__name__}: {exc}"
            raised = exc
            raise
        finally:
            with mask(audit_name):
                t1 = perform(":clock/now")["ts"]
            latency_ms = max(0, int((t1 - t0) * 1000))
            prev_hash = ctx["entries"][-1].id if ctx["entries"] else None
            content: dict[str, Any] = {
                "prev_hash": prev_hash,
                "op": op_name,
                "args_hash": args_hash,
                "verdict": verdict,
                "latency_ms": latency_ms,
                "recorded_at": t1,
                "result_hash": result_hash,
                "error": error_str,
                "policy_id": ctx.get("policy_id"),
                "handler_chain": tuple(ctx.get("handler_chain", ())),
                "principal": dict(ctx.get("principal", {})),
                "run_id": ctx.get("run_id"),
                "parent": prev_hash,
            }
            entry_id = _content_hash(content)
            entry = AuditEntry(id=entry_id, **content)
            ctx["entries"].append(entry)
            # Drain to named sink if configured.
            sink = ctx.get("sink_name")
            if sink:
                with mask(audit_name):
                    named(sink, ":audit/emit", kind="audit-entry", payload=entry.to_dict())
            # Swallow raised? No — re-raise path handled above.

    # Dispatch shim: we need op name inside the clause but the clause
    # signature is (args, k, ctx). We capture op by wrapping per-op.
    clauses: dict[str, Any] = {}

    def make_op_clause(op_name: str):
        def op_clause(args, k, ctx):
            ctx["_current_op"] = op_name
            return clause(args, k, ctx)

        return op_clause

    for op_name in wraps:
        clauses[op_name] = make_op_clause(op_name)

    return Handler(
        name=audit_name,
        wraps=set(wraps),
        clauses=clauses,
        ctx={
            "entries": entries,
            "sink_name": sink_name,
            "run_id": run_id,
            "principal": principal or {},
            "policy_id": policy_id,
            "handler_chain": (),
        },
    )


# ---------------------------------------------------------------------------
# Datom conversion — Fact spec §1, 8-tuple
# ---------------------------------------------------------------------------


def audit_entry_to_datom(entry: AuditEntry) -> dict[str, Any]:
    """Convert an AuditEntry to the 8-tuple datom shape from agent1-fact-spec §1.

    Mapping:
    - ``:datom/e``           → run-id (the causal root, a uuid-ish string)
    - ``:datom/a``           → ``:audit/<op>`` (e.g. ``:audit/llm/call``)
    - ``:datom/v``           → dict with verdict, args_hash, result_hash,
                               latency_ms, error
    - ``:datom/tx``          → entry.id (content hash; monotonic-in-chain)
    - ``:datom/tx-time``     → recorded_at
    - ``:datom/valid-from``  → recorded_at (we learn the fact at that instant)
    - ``:datom/valid-to``    → None (open interval; audit entries never
                               become invalid)
    - ``:datom/op``          → ``"assert"``
    - ``:datom/provenance``  → principal/policy-id/prev-hash
    - ``:datom/invalidated-by`` → None (audit is append-only)
    """
    provenance = {
        "source": "persistence.effect.audit",
        "model": None,
        "prompt_hash": None,
        "confidence": 1.0,
        "signature": entry.id,
        "episode": entry.run_id,
        "prev_hash": entry.prev_hash,
        "handler_chain": list(entry.handler_chain),
        "policy_id": entry.policy_id,
        "principal": dict(entry.principal),
    }
    value = {
        "verdict": entry.verdict,
        "args_hash": entry.args_hash,
        "result_hash": entry.result_hash,
        "latency_ms": entry.latency_ms,
        "error": entry.error,
    }
    return {
        "datom/e": entry.run_id or entry.id,
        "datom/a": f"audit/{entry.op}",
        "datom/v": value,
        "datom/tx": entry.id,
        "datom/tx-time": entry.recorded_at,
        "datom/valid-from": entry.recorded_at,
        "datom/valid-to": None,
        "datom/op": "assert",
        "datom/provenance": provenance,
        "datom/invalidated-by": None,
    }


def datom_to_audit_entry(datom: dict[str, Any]) -> AuditEntry:
    """Inverse of :func:`audit_entry_to_datom`. Loses nothing the schema
    round-trips (principal, run-id, policy-id, handler-chain, etc.).
    """
    provenance = datom["datom/provenance"]
    value = datom["datom/v"]
    a = datom["datom/a"]
    assert a.startswith("audit/"), f"not an audit datom: {a}"
    op = a[len("audit/") :]
    return AuditEntry(
        id=datom["datom/tx"],
        prev_hash=provenance.get("prev_hash"),
        op=op,
        args_hash=value["args_hash"],
        verdict=value["verdict"],
        latency_ms=value["latency_ms"],
        recorded_at=datom["datom/tx-time"],
        result_hash=value.get("result_hash"),
        error=value.get("error"),
        policy_id=provenance.get("policy_id"),
        handler_chain=tuple(provenance.get("handler_chain", ())),
        principal=dict(provenance.get("principal", {})),
        run_id=provenance.get("episode"),
        parent=provenance.get("prev_hash"),
    )


# Re-export canonical_dumps so callers importing this module don't need
# to reach into the canonical helper.
__all__ = [
    "AuditEntry",
    "audit_entry_to_datom",
    "canonical_dumps",
    "datom_to_audit_entry",
    "make_audit_handler",
    "verify_chain",
]
