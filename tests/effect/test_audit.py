"""Audit handler tests — Merkle chain, datom schema, no-sync-disk, re-entry mask."""
import pytest

from persistence.effect.handlers.audit import (
    AuditEntry,
    audit_entry_to_datom,
    datom_to_audit_entry,
    make_audit_handler,
)
from persistence.effect.handlers.raw import make_echo_llm_handler
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.runtime import Handler, Runtime, mask, perform, with_runtime


def _stack_with_audit(entries, clock_ts=1_712_000_000_000):
    """audit → raw-echo for :llm/call, with a fixed clock."""
    audit = make_audit_handler(entries, wraps={":llm/call", ":decide"})
    raw = make_echo_llm_handler()
    clock = make_fixed_clock_handler(ts=clock_ts)
    return Runtime([raw, clock, audit])


# ---------- basic capture ----------


def test_audit_captures_op_and_args_hash():
    entries: list[AuditEntry] = []
    rt = _stack_with_audit(entries)
    with with_runtime(rt):
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "hi"}])
    assert len(entries) == 1
    e = entries[0]
    assert e.op == ":llm/call"
    assert e.args_hash.startswith("sha256:")
    assert e.verdict == "ok"


def test_audit_latency_ms_is_non_negative():
    entries: list[AuditEntry] = []
    rt = _stack_with_audit(entries)
    with with_runtime(rt):
        perform(":llm/call", model="m", messages=[])
    assert entries[0].latency_ms >= 0


# ---------- Merkle / hash chain ----------


def test_prev_hash_of_first_entry_is_none():
    entries: list[AuditEntry] = []
    rt = _stack_with_audit(entries)
    with with_runtime(rt):
        perform(":llm/call", model="m", messages=[])
    assert entries[0].prev_hash is None


def test_prev_hash_of_nth_entry_is_id_of_prior():
    entries: list[AuditEntry] = []
    rt = _stack_with_audit(entries)
    with with_runtime(rt):
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "a"}])
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "b"}])
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "c"}])
    assert len(entries) == 3
    assert entries[1].prev_hash == entries[0].id
    assert entries[2].prev_hash == entries[1].id


def test_tampering_an_entry_breaks_the_chain():
    """Explicit integrity check: altering entries[1].args_hash must be detected."""
    entries: list[AuditEntry] = []
    rt = _stack_with_audit(entries)
    with with_runtime(rt):
        for i in range(3):
            perform(":llm/call", model="m", messages=[{"role": "user", "content": str(i)}])
    from persistence.effect.handlers.audit import verify_chain
    assert verify_chain(entries) is True
    # Tamper
    entries[1] = entries[1].with_fields(args_hash="sha256:deadbeef")
    assert verify_chain(entries) is False


# ---------- datom round-trip (Fact spec §1, 8-tuple) ----------


def test_audit_entry_to_datom_has_fact_schema_fields():
    entries: list[AuditEntry] = []
    rt = _stack_with_audit(entries)
    with with_runtime(rt):
        perform(":llm/call", model="m", messages=[])
    datom = audit_entry_to_datom(entries[0])
    # Required fields from agent1-fact-spec §1 (the 8-tuple).
    for key in (
        "datom/e",
        "datom/a",
        "datom/v",
        "datom/tx",
        "datom/tx-time",
        "datom/valid-from",
        "datom/valid-to",
        "datom/op",
        "datom/provenance",
        "datom/invalidated-by",
    ):
        assert key in datom, f"datom missing {key}"
    assert datom["datom/op"] in ("assert", "retract")
    prov = datom["datom/provenance"]
    assert "source" in prov and "signature" in prov


def test_datom_roundtrip_preserves_audit_entry():
    entries: list[AuditEntry] = []
    rt = _stack_with_audit(entries)
    with with_runtime(rt):
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "x"}])
    original = entries[0]
    datom = audit_entry_to_datom(original)
    restored = datom_to_audit_entry(datom)
    assert restored.id == original.id
    assert restored.op == original.op
    assert restored.args_hash == original.args_hash
    assert restored.verdict == original.verdict
    assert restored.prev_hash == original.prev_hash


# ---------- no synchronous disk writes ----------


def test_audit_routes_sink_through_named_handler_not_disk():
    """Spec §9 anti-pattern: audit must NOT write to disk synchronously.

    Instead it emits ``:audit/emit`` which a separate named handler drains.
    """
    entries: list[AuditEntry] = []
    sink_calls: list = []

    def sink_clause(args, k, ctx):
        sink_calls.append(args)
        return None

    audit = make_audit_handler(
        entries,
        wraps={":llm/call"},
        sink_name="archive",  # forward to named handler
    )
    raw = make_echo_llm_handler()
    clock = make_fixed_clock_handler(ts=1_712_000_000_000)
    sink = Handler(
        name="archive",
        wraps={":audit/emit"},
        clauses={":audit/emit": sink_clause},
    )
    rt = Runtime([raw, clock, sink, audit])
    with with_runtime(rt):
        perform(":llm/call", model="m", messages=[])
    assert len(sink_calls) == 1
    assert sink_calls[0]["kind"] == "audit-entry"
    assert sink_calls[0]["payload"]["op"] == ":llm/call"


# ---------- mask prevents re-entry ----------


def test_audit_masked_inside_body_prevents_re_entry():
    """Policy-style sub-handler can call LLM without re-triggering audit."""
    entries: list[AuditEntry] = []
    rt = _stack_with_audit(entries)
    with with_runtime(rt):
        with mask("audit"):
            perform(":llm/call", model="m", messages=[])
    assert entries == []  # masked body did not audit


# ---------- error path ----------


def test_audit_records_error_verdict():
    """Failed calls must still produce an audit entry (intent logging)."""
    entries: list[AuditEntry] = []
    audit = make_audit_handler(entries, wraps={":llm/call"})

    def failing(args, k, ctx):
        raise RuntimeError("vendor down")

    raw = Handler(name="raw", wraps={":llm/call"}, clauses={":llm/call": failing})
    clock = make_fixed_clock_handler(ts=1_712_000_000_000)
    rt = Runtime([raw, clock, audit])
    with with_runtime(rt):
        with pytest.raises(RuntimeError, match="vendor down"):
            perform(":llm/call", model="m", messages=[])
    assert len(entries) == 1
    assert entries[0].verdict == "error"
    assert "vendor down" in (entries[0].error or "")
