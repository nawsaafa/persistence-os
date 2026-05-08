"""Phase 2.3c.2 G3 — AuditEntry.parent_audit_entry_id structural assertions.

Covers test gate G3 from
``docs/plans/2026-05-08-phase-2.3c.2-recursion-composition-design.md`` §4
under the W3 rescope (commit ``98df051``): G3 is reformulated as
STRUCTURAL-ONLY. The active wiring of non-None ``parent_audit_entry_id``
through the audit middleware is rescoped to v0.9.x (see § 8 for the
falsifiable acceptance signal).

5 assertion classes (a-e) per the reformulated G3 gate:

  * G3.a — ``parent_audit_entry_id`` is part of the canonical content
    hash: two entries with identical ``(prev_hash, op, args_hash, ...)``
    but different ``parent_audit_entry_id`` (None vs sha256:abc...) have
    DIFFERENT ``id`` values.
  * G3.b — Field validation: non-None values must be ``"sha256:"``-prefixed
    or ValueError; None and well-formed sha256:-prefixed strings accepted.
  * G3.c — ``to_edn`` / ``from_edn`` round-trip preserves the field
    (omit-when-None on the wire, present when non-None).
  * G3.d — ``audit_entry_to_datom`` / ``datom_to_audit_entry`` round-trip
    preserves the field via the ``:parent-audit-entry-id`` provenance slot.
  * G3.e — Middleware-layer behavior under 2.3c.2 W3 rescope: a ``:llm/call``
    audit entry produced through ``canonical_audit_stack`` always has
    ``parent_audit_entry_id is None`` — proving the rescope is honored
    (active population deferred to v0.9.x).

T2 already shipped the field at the dataclass + chain-hash + wire form +
datom round-trip + spec layers (commit ``4dc3fb3``). T3 verifies these
structural invariants survive the dispatcher-binding middleware addition
and that G3.e (the rescope-honored invariant) holds.
"""
from __future__ import annotations

import pytest

from persistence.coder._recursion import (
    DispatcherContext,
    RecursionBudget,
    dispatcher_context,
)
from persistence.effect import canonical_audit_stack, perform, with_runtime
from persistence.effect.handlers.audit import (
    AuditEntry,
    audit_entry_to_datom,
    datom_to_audit_entry,
)
from persistence.effect.handlers.raw import make_echo_llm_handler


# ---------------------------------------------------------------------------
# Test data — canonical AuditEntry-shaped seed
# ---------------------------------------------------------------------------


_BASE_FIELDS = dict(
    prev_hash=None,
    op=":llm/call",
    args_hash="sha256:" + "a" * 64,
    verdict="ok",
    latency_ms=12,
    recorded_at=1715184000.0,
    result_hash="sha256:" + "b" * 64,
    error=None,
    policy_id=None,
    handler_chain=("audit",),
    principal={},
    run_id=None,
    parent=None,
)


def _make_entry(**overrides) -> AuditEntry:
    """Build an AuditEntry with placeholder ``id``; the dataclass does
    NOT validate that ``id`` matches the content hash (that's
    ``verify_chain``'s job), so a stub id is acceptable for
    structural-only tests."""
    fields = {**_BASE_FIELDS, **overrides}
    fields.setdefault("id", "sha256:" + "0" * 64)
    return AuditEntry(**fields)


# ---------------------------------------------------------------------------
# G3.a — parent_audit_entry_id participates in canonical content hash
# ---------------------------------------------------------------------------


def test_g3_a_parent_audit_entry_id_changes_content_hash() -> None:
    """Two entries with identical content except parent_audit_entry_id
    produce DIFFERENT canonical content hashes."""
    entry_none = _make_entry(parent_audit_entry_id=None)
    entry_set = _make_entry(parent_audit_entry_id="sha256:" + "c" * 64)

    d_none = entry_none.to_dict()
    d_none.pop("id")
    d_set = entry_set.to_dict()
    d_set.pop("id")

    # Sanity: every field except parent_audit_entry_id is identical
    for k in d_none:
        if k == "parent_audit_entry_id":
            continue
        assert d_none[k] == d_set[k], f"unexpected drift on field {k}"

    # Hashes differ -> field is part of the content hash
    from persistence.effect.handlers.audit import _content_hash, _canonicalise_content

    assert _content_hash(_canonicalise_content(d_none)) != _content_hash(
        _canonicalise_content(d_set)
    )


def test_g3_a_parent_audit_entry_id_none_default_part_of_hash() -> None:
    """Even with default None, the field is in to_dict (so chain-hash
    drift is locked in even for non-recursive entries)."""
    entry = _make_entry(parent_audit_entry_id=None)
    d = entry.to_dict()
    assert "parent_audit_entry_id" in d
    assert d["parent_audit_entry_id"] is None


# ---------------------------------------------------------------------------
# G3.b — Field validation
# ---------------------------------------------------------------------------


def test_g3_b_validation_accepts_none() -> None:
    """parent_audit_entry_id=None is accepted (default backward-compat)."""
    entry = _make_entry(parent_audit_entry_id=None)
    assert entry.parent_audit_entry_id is None


def test_g3_b_validation_accepts_sha256_prefix() -> None:
    """Well-formed sha256:-prefixed string is accepted."""
    paeid = "sha256:" + "f" * 64
    entry = _make_entry(parent_audit_entry_id=paeid)
    assert entry.parent_audit_entry_id == paeid


def test_g3_b_validation_rejects_non_sha256_prefix() -> None:
    """parent_audit_entry_id without ``sha256:`` prefix raises ValueError."""
    with pytest.raises(ValueError, match="sha256:"):
        _make_entry(parent_audit_entry_id="not-a-sha256-hash")


@pytest.mark.parametrize(
    "bogus",
    [
        "",
        "abcdef",
        "md5:" + "a" * 32,
        "SHA256:" + "a" * 64,  # wrong case
        ":sha256:abc",
    ],
)
def test_g3_b_validation_rejects_malformed(bogus: str) -> None:
    with pytest.raises(ValueError):
        _make_entry(parent_audit_entry_id=bogus)


def test_g3_b_validation_rejects_non_string() -> None:
    """Non-string non-None values raise ValueError."""
    with pytest.raises(ValueError):
        _make_entry(parent_audit_entry_id=123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# G3.c — to_edn / from_edn round-trip
# ---------------------------------------------------------------------------


def test_g3_c_to_edn_omits_when_none() -> None:
    """When parent_audit_entry_id is None, to_edn does NOT emit the key.

    Confirmed via design § LD5: emit-only-when-set rule (mirrors
    ``txn_commit`` pattern at audit.py:602).
    """
    entry = _make_entry(parent_audit_entry_id=None)
    edn = entry.to_edn()
    assert ":audit/parent-audit-entry-id" not in edn


def test_g3_c_to_edn_emits_when_set() -> None:
    """When parent_audit_entry_id is set, to_edn emits :audit/parent-audit-entry-id."""
    paeid = "sha256:" + "d" * 64
    entry = _make_entry(parent_audit_entry_id=paeid)
    edn = entry.to_edn()
    assert edn.get(":audit/parent-audit-entry-id") == paeid


def test_g3_c_from_edn_reads_when_present() -> None:
    """from_edn reads :audit/parent-audit-entry-id back into the field."""
    paeid = "sha256:" + "e" * 64
    entry = _make_entry(parent_audit_entry_id=paeid)
    edn = entry.to_edn()
    rebuilt = AuditEntry.from_edn(edn)
    assert rebuilt.parent_audit_entry_id == paeid


def test_g3_c_round_trip_preserves_field_when_none() -> None:
    """to_edn -> from_edn preserves None default."""
    entry = _make_entry(parent_audit_entry_id=None)
    edn = entry.to_edn()
    rebuilt = AuditEntry.from_edn(edn)
    assert rebuilt.parent_audit_entry_id is None


def test_g3_c_round_trip_preserves_field_when_set() -> None:
    """to_edn -> from_edn preserves set value."""
    paeid = "sha256:" + "1" * 64
    entry = _make_entry(parent_audit_entry_id=paeid)
    edn = entry.to_edn()
    rebuilt = AuditEntry.from_edn(edn)
    assert rebuilt.parent_audit_entry_id == paeid


# ---------------------------------------------------------------------------
# G3.d — datom round-trip via :parent-audit-entry-id slot
# ---------------------------------------------------------------------------


def test_g3_d_datom_round_trip_when_none() -> None:
    """audit_entry_to_datom -> datom_to_audit_entry preserves None default."""
    entry = _make_entry(parent_audit_entry_id=None)
    datom = audit_entry_to_datom(entry)
    rebuilt = datom_to_audit_entry(datom)
    assert rebuilt.parent_audit_entry_id is None


def test_g3_d_datom_round_trip_when_set() -> None:
    """Datom round-trip preserves non-None parent_audit_entry_id."""
    paeid = "sha256:" + "2" * 64
    entry = _make_entry(parent_audit_entry_id=paeid)
    datom = audit_entry_to_datom(entry)
    rebuilt = datom_to_audit_entry(datom)
    assert rebuilt.parent_audit_entry_id == paeid


def test_g3_d_datom_carries_provenance_slot_when_set() -> None:
    """Wire-side provenance dict surfaces :parent-audit-entry-id slot
    when the source field is non-None."""
    paeid = "sha256:" + "3" * 64
    entry = _make_entry(parent_audit_entry_id=paeid)
    datom = audit_entry_to_datom(entry)
    # Datom is a dict shape with provenance under ``:datom/provenance``.
    provenance = datom[":datom/provenance"]
    assert provenance.get(":parent-audit-entry-id") == paeid


def test_g3_d_datom_omits_provenance_slot_when_none() -> None:
    """Wire-side provenance dict OMITS :parent-audit-entry-id when None
    (mirrors to_edn omit-when-None)."""
    entry = _make_entry(parent_audit_entry_id=None)
    datom = audit_entry_to_datom(entry)
    provenance = datom[":datom/provenance"]
    assert ":parent-audit-entry-id" not in provenance


# ---------------------------------------------------------------------------
# G3.e — Middleware-layer behavior under 2.3c.2 W3 rescope
# ---------------------------------------------------------------------------


def test_g3_e_middleware_emits_entry_with_none_parent_audit_entry_id() -> None:
    """A :llm/call dispatched through canonical_audit_stack produces an
    AuditEntry with parent_audit_entry_id=None.

    This is the explicit invariant honoring the W3 rescope: 2.3c.2
    middleware does NOT populate parent_audit_entry_id with non-None
    values; that's deferred to v0.9.x. Test will need updating when the
    v0.9.x track lands.
    """
    entries: list[AuditEntry] = []
    rt = canonical_audit_stack(entries)
    rt.handlers.insert(0, make_echo_llm_handler())
    with with_runtime(rt):
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "hi"}])
    llm_entries = [e for e in entries if e.op == ":llm/call"]
    assert len(llm_entries) == 1
    assert llm_entries[0].parent_audit_entry_id is None


def test_g3_e_middleware_with_dispatcher_context_still_emits_none() -> None:
    """Even with a bound DispatcherContext, parent_audit_entry_id stays
    None at the middleware layer under 2.3c.2 W3 rescope.

    The dispatcher handler MAY thread DispatcherContext for budget + cycle
    purposes (T3 scope), but parent_audit_entry_id remains uninitialized
    at the audit handler's emit time. This test would FAIL once v0.9.x
    activates the parent-pointer wiring (intentional — the v0.9.x track's
    falsifiable acceptance signal flips this assertion).
    """
    entries: list[AuditEntry] = []
    rt = canonical_audit_stack(entries)
    rt.handlers.insert(0, make_echo_llm_handler())
    ctx = DispatcherContext()
    with with_runtime(rt), dispatcher_context(ctx):
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "hi"}])
    llm_entries = [e for e in entries if e.op == ":llm/call"]
    assert len(llm_entries) == 1
    assert llm_entries[0].parent_audit_entry_id is None


def test_g3_e_chain_integrity_unaffected_by_field_presence() -> None:
    """Linear prev_hash chain integrity holds across multiple calls,
    regardless of parent_audit_entry_id field presence (it's None for all
    middleware-emitted entries under 2.3c.2)."""
    entries: list[AuditEntry] = []
    rt = canonical_audit_stack(entries)
    rt.handlers.insert(0, make_echo_llm_handler())
    with with_runtime(rt):
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "a"}])
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "b"}])
        perform(":llm/call", model="m", messages=[{"role": "user", "content": "c"}])
    llm_entries = [e for e in entries if e.op == ":llm/call"]
    assert len(llm_entries) == 3
    for i in range(1, len(llm_entries)):
        assert llm_entries[i].prev_hash == llm_entries[i - 1].id
        assert llm_entries[i].parent_audit_entry_id is None
