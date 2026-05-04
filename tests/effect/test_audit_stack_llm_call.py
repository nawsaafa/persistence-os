"""Phase 2.1b — :llm/call audit coverage tests (design § 3.0.2)."""
from __future__ import annotations

from persistence.effect import canonical_audit_stack, with_runtime
from persistence.effect._audit_stack import (
    CANONICAL_AUDIT_OPS,
    CANONICAL_AUDIT_RAW_OPS,
    CANONICAL_AUDIT_WRAPPED_OPS,
    _make_canonical_raw_terminator,
)
from persistence.effect.handlers.audit import AuditEntry
from persistence.effect.handlers.raw import make_echo_llm_handler


def test_wrapped_ops_includes_llm_call():
    assert ":llm/call" in CANONICAL_AUDIT_WRAPPED_OPS


def test_raw_ops_excludes_llm_call():
    assert ":llm/call" not in CANONICAL_AUDIT_RAW_OPS


def test_backwards_compat_alias_equals_wrapped():
    assert CANONICAL_AUDIT_OPS == CANONICAL_AUDIT_WRAPPED_OPS


def test_raw_terminator_does_not_handle_llm_call():
    raw = _make_canonical_raw_terminator()
    assert ":llm/call" not in raw.clauses
    assert ":llm/call" not in raw.wraps


def test_llm_call_emits_audit_entry_with_real_provider_underneath():
    """End-to-end: install canonical audit stack + echo handler at bottom,
    perform :llm/call, assert audit entry emitted AND result came from echo
    (not from the raw terminator returning None)."""
    entries: list[AuditEntry] = []
    rt = canonical_audit_stack(entries)
    rt.handlers.insert(0, make_echo_llm_handler())  # bottom = position 0
    with with_runtime(rt):
        from persistence.effect import perform

        result = perform(":llm/call", model="test-model", messages=[{"role": "user", "content": "hello"}])
    assert result["text"] == "echo:hello"
    llm_entries = [e for e in entries if e.op == ":llm/call"]
    assert len(llm_entries) == 1


def test_llm_call_audit_entry_extends_chain_hash():
    """Two :llm/call performs produce two entries whose prev_hash chain links.

    Note: The AuditEntry content-hash field is named ``.id`` (the entry's
    sha256 content hash); ``.prev_hash`` references the previous entry's id.
    """
    entries: list[AuditEntry] = []
    rt = canonical_audit_stack(entries)
    rt.handlers.insert(0, make_echo_llm_handler())
    with with_runtime(rt):
        from persistence.effect import perform

        perform(":llm/call", model="m", messages=[{"role": "user", "content": "a"}])
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "b"}])
    assert len(entries) == 2
    # Second entry's prev_hash must equal the first entry's id (content hash).
    assert entries[1].prev_hash == entries[0].id
