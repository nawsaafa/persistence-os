"""ARIS Round 5 W-polish3 W6-factory-canonicalize — closes R5 N1 MAJOR.

``make_audit_handler`` used to compute ``_content_hash(content)`` on the
*pre-canonical* dict and then construct the ``AuditEntry`` (which, since
W-polish2's ``__post_init__``, canonicalises ``policy_id`` /
``handler_chain`` / ``principal``). The stored ``entry.id`` thus hashed
one shape while ``verify_chain`` rehashed the canonicalised
``entry.to_dict()`` — mismatch, so every production chain with a
non-already-keyworded ``policy_id`` failed ``verify_chain``.

These tests exercise the factory path end-to-end (the exact shape the
production runtime takes) and assert the Merkle chain verifies.
"""
from __future__ import annotations

from persistence.effect.handlers.audit import (
    AuditEntry,
    make_audit_handler,
    verify_chain,
)
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.handlers.raw import make_echo_llm_handler
from persistence.effect.runtime import Runtime, perform, with_runtime


def test_verify_chain_on_factory_with_bare_policy_id() -> None:
    """Production shape: ``policy_id="bankability-v3"`` (bare string).

    The R5 N1 MAJOR reproducer from ``docs/aris-round-5/R1-correctness.md``.
    Before the W6 fix, ``verify_chain`` returned ``False`` because the
    factory hashed the pre-canonical content but ``AuditEntry.to_dict``
    reflected the canonical form.
    """
    entries: list[AuditEntry] = []
    audit = make_audit_handler(
        entries,
        policy_id="bankability-v3",  # bare — production shape
    )
    raw = make_echo_llm_handler()
    clock = make_fixed_clock_handler(ts=1_712_000_000)
    rt = Runtime([raw, clock, audit])
    with with_runtime(rt):
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "x"}])
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "y"}])

    assert len(entries) == 2
    # Every stored entry must reflect the canonical form.
    for e in entries:
        assert e.policy_id == ":bankability-v3"
    # Merkle chain must verify — this is the MAJOR check.
    assert verify_chain(entries) is True


def test_verify_chain_on_factory_with_bare_string_handler_chain() -> None:
    """Factory chain contains pre-keyworded handler names in ctx.

    W-polish2's ``__post_init__`` strips leading colons from
    ``handler_chain`` entries (canonical internal form = bare). If a
    caller seeds ``ctx["handler_chain"]`` with colon-prefixed entries
    (e.g. pulled from a wire payload), the factory must hash the
    canonical (bare) form so ``verify_chain`` still holds.
    """
    entries: list[AuditEntry] = []
    audit = make_audit_handler(
        entries,
        policy_id=":already-keyworded",  # take policy_id off the table
    )
    # Simulate a caller-provided handler_chain with colon-prefixed
    # (pre-keyworded) entries. The dataclass canonicalises these to
    # bare form; the factory must hash the canonical form.
    audit.ctx["handler_chain"] = (":audit", ":llm", ":tool")
    raw = make_echo_llm_handler()
    clock = make_fixed_clock_handler(ts=1_712_000_000)
    rt = Runtime([raw, clock, audit])
    with with_runtime(rt):
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "hello"}])
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "world"}])

    assert len(entries) == 2
    # handler_chain must be canonicalised to bare form (lstrip colons).
    for e in entries:
        assert e.handler_chain == ("audit", "llm", "tool")
    assert verify_chain(entries) is True


def test_verify_chain_on_factory_with_mixed_principal_keys() -> None:
    """principal dict with mixed bare + keyworded keys.

    Real callers may assemble a principal from multiple sources — some
    already-keyworded, some bare. The dataclass normalises all keys to
    bare form; the factory must hash the canonical form so the Merkle
    chain survives.
    """
    entries: list[AuditEntry] = []
    audit = make_audit_handler(
        entries,
        policy_id=":already-keyworded",
        principal={":user": "a", "session": "b"},  # mixed
    )
    raw = make_echo_llm_handler()
    clock = make_fixed_clock_handler(ts=1_712_000_000)
    rt = Runtime([raw, clock, audit])
    with with_runtime(rt):
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "mixed"}])
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "keys"}])

    assert len(entries) == 2
    for e in entries:
        # Both keys stripped to bare form.
        assert e.principal == {"user": "a", "session": "b"}
    assert verify_chain(entries) is True
