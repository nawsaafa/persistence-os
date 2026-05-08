"""Unit tests for ``rebind_audit_datom_prev_hash`` (PG-W1, ADR-17).

Per ARIS R2 Dim 4 closure: the helper rebinds an audit datom's
``:prev-hash`` to the actually-locked cross-process head. These tests
pin the contract: no-op when prev_hash already matches; recomputed
``:signature`` matches the canonical content hash; ``:datom/e`` and
``:datom/tx`` follow the run_id-or-id rule; ``verify_chain`` accepts
``[entry, rebound_entry]`` when chained correctly.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from persistence.effect.handlers.audit import (
    AuditEntry,
    _canonicalise_content,
    _content_hash,
    audit_entry_to_datom,
    datom_to_audit_entry,
    rebind_audit_datom_prev_hash,
    verify_chain,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utc(year: int, month: int, day: int, hour: int = 12) -> _dt.datetime:
    return _dt.datetime(year, month, day, hour, 0, 0, tzinfo=_dt.timezone.utc)


def _build_entry(
    *,
    prev_hash: str | None = None,
    op: str = ":repl/op",
    args_hash: str = "h",
    run_id: str | None = None,
    recorded_at: float | None = None,
) -> AuditEntry:
    """Build a content-hashed ``AuditEntry`` mirroring production paths."""
    if recorded_at is None:
        recorded_at = _utc(2026, 4, 30).timestamp()
    content: dict[str, Any] = {
        "prev_hash": prev_hash,
        "op": op,
        "args_hash": args_hash,
        "verdict": "ok",
        "latency_ms": 1,
        "recorded_at": recorded_at,
        "result_hash": None,
        "error": None,
        "policy_id": None,
        "handler_chain": (),
        "principal": {"token_id": "tok", "session_id": "ses"},
        "run_id": run_id,
        "parent": prev_hash,
        # Phase 2.3c.2 LD5 — Re-pinned 2026-05-08 for parent_audit_entry_id
        # field add. Production ``make_audit_handler`` always writes the
        # key (None for non-nested entries) and ``to_dict()`` keeps it,
        # so the helper input shape mirrors that. Otherwise the
        # helper-side content hash (used to assign ``entry.id``) would
        # diverge from ``verify_chain``'s recomputed hash via
        # ``to_dict()``, breaking the rebind round-trip.
        "parent_audit_entry_id": None,
    }
    canonical = _canonicalise_content(content)
    return AuditEntry(id=_content_hash(canonical), **canonical)


# ---------------------------------------------------------------------------
# (a) No-op when prev_hash unchanged.
# ---------------------------------------------------------------------------
def test_rebind_is_noop_when_prev_hash_already_matches() -> None:
    """Fast-path: input dict returned unchanged when prev_hash matches."""
    entry = _build_entry(prev_hash="sha256:abcd")
    datom = audit_entry_to_datom(entry)
    rebound = rebind_audit_datom_prev_hash(datom, "sha256:abcd")
    # Returned object IS the input — fast-path skipped the decode.
    assert rebound is datom


def test_rebind_noop_when_both_none() -> None:
    """Genesis entry: prev=None and new_prev_hash=None → no-op."""
    entry = _build_entry(prev_hash=None)
    datom = audit_entry_to_datom(entry)
    rebound = rebind_audit_datom_prev_hash(datom, None)
    assert rebound is datom


# ---------------------------------------------------------------------------
# (b) :prev-hash + parent_provenance_hash both updated.
# ---------------------------------------------------------------------------
def test_rebind_updates_both_dual_namespace_keys() -> None:
    """Both ``:prev-hash`` (audit reader) and ``parent_provenance_hash``
    (typed Provenance reader) carry the new value."""
    entry = _build_entry(prev_hash="sha256:OLD")
    datom = audit_entry_to_datom(entry)
    rebound = rebind_audit_datom_prev_hash(datom, "sha256:NEW")
    prov = rebound[":datom/provenance"]
    assert prov[":prev-hash"] == "sha256:NEW"
    assert prov["parent_provenance_hash"] == "sha256:NEW"


def test_rebind_to_none_clears_both_keys() -> None:
    """Rebinding to None (chain head reset) clears both dual-namespace keys."""
    entry = _build_entry(prev_hash="sha256:OLD")
    datom = audit_entry_to_datom(entry)
    rebound = rebind_audit_datom_prev_hash(datom, None)
    prov = rebound[":datom/provenance"]
    assert prov[":prev-hash"] is None
    assert prov["parent_provenance_hash"] is None


# ---------------------------------------------------------------------------
# (c) :signature + :datom/tx both updated to recomputed entry id.
# ---------------------------------------------------------------------------
def test_rebind_updates_signature_and_datom_tx() -> None:
    """``:signature`` and ``:datom/tx`` both carry the recomputed
    ``entry.id`` after rebind — ``audit_entry_to_datom`` writes both."""
    entry = _build_entry(prev_hash="sha256:OLD")
    datom = audit_entry_to_datom(entry)
    original_id = entry.id

    rebound = rebind_audit_datom_prev_hash(datom, "sha256:NEW")
    new_signature = rebound[":datom/provenance"][":signature"]
    new_tx = rebound[":datom/tx"]

    # Signature must change because prev_hash is part of the canonical
    # content hash.
    assert new_signature != original_id
    # ``:datom/tx`` mirrors the new signature (audit datoms encode
    # entry.id in :datom/tx — see audit_entry_to_datom).
    assert new_tx == new_signature


# ---------------------------------------------------------------------------
# (d) :datom/e updated when no run_id (uses entry.id).
# ---------------------------------------------------------------------------
def test_rebind_updates_datom_e_when_no_run_id() -> None:
    """When ``run_id`` is absent, ``:datom/e`` == ``entry.id``; rebind
    must update both consistently."""
    entry = _build_entry(prev_hash="sha256:OLD", run_id=None)
    datom = audit_entry_to_datom(entry)
    # Pre-condition: :datom/e equals entry.id when no run_id.
    assert datom[":datom/e"] == entry.id

    rebound = rebind_audit_datom_prev_hash(datom, "sha256:NEW")
    new_signature = rebound[":datom/provenance"][":signature"]

    # :datom/e tracks the new signature too.
    assert rebound[":datom/e"] == new_signature
    assert rebound[":datom/e"] != datom[":datom/e"]


# ---------------------------------------------------------------------------
# (e) :datom/e preserved when run_id is set.
# ---------------------------------------------------------------------------
def test_rebind_preserves_datom_e_when_run_id_set() -> None:
    """``run_id`` wins for ``:datom/e``; rebind must NOT clobber it."""
    run_id = "11111111-2222-3333-4444-555555555555"
    entry = _build_entry(prev_hash="sha256:OLD", run_id=run_id)
    datom = audit_entry_to_datom(entry)
    # Pre-condition: :datom/e equals run_id when set.
    assert datom[":datom/e"] == run_id

    rebound = rebind_audit_datom_prev_hash(datom, "sha256:NEW")
    # :datom/e still equals run_id after rebind — un-touched.
    assert rebound[":datom/e"] == run_id
    # But :datom/tx (the audit signature slot) DID advance.
    assert rebound[":datom/tx"] != datom[":datom/tx"]


# ---------------------------------------------------------------------------
# (f) Recomputed signature matches _content_hash of new canonical content.
# ---------------------------------------------------------------------------
def test_rebind_signature_matches_content_hash_of_new_content() -> None:
    """The recomputed signature must equal the explicit ``_content_hash``
    of the canonicalised content with the new prev_hash — pins the
    Merkle re-derivation contract used by :func:`verify_chain`."""
    entry = _build_entry(prev_hash="sha256:OLD")
    datom = audit_entry_to_datom(entry)
    rebound = rebind_audit_datom_prev_hash(datom, "sha256:NEW")

    # Re-decode the rebound datom to get the recomputed AuditEntry.
    rebound_entry = datom_to_audit_entry(rebound)
    # Pin the canonical-hash recomputation — same shape verify_chain uses.
    d = rebound_entry.to_dict()
    d.pop("id")
    expected_id = _content_hash(d)
    assert rebound_entry.id == expected_id
    assert rebound[":datom/provenance"][":signature"] == expected_id


# ---------------------------------------------------------------------------
# (g) verify_chain passes when fed [entry, rebound_entry] correctly chained.
# ---------------------------------------------------------------------------
def test_verify_chain_passes_when_rebind_links_to_prior() -> None:
    """End-to-end: an entry chained off a prior entry's id verifies."""
    # Entry 1: genesis.
    entry1 = _build_entry(prev_hash=None, args_hash="h1")
    # Entry 2: built with stale prev_hash (the bug PG-W1 fixes — the
    # in-memory pointer was already stale by the time we rebind).
    entry2_stale = _build_entry(prev_hash="sha256:STALE", args_hash="h2")
    datom2 = audit_entry_to_datom(entry2_stale)

    # Rebind entry2 to point at entry1 — the actual locked head.
    rebound2 = rebind_audit_datom_prev_hash(datom2, entry1.id)
    rebound2_entry = datom_to_audit_entry(rebound2)

    # Chain verifies: entry1 → rebound2_entry.
    assert verify_chain([entry1, rebound2_entry]) is True


# ---------------------------------------------------------------------------
# (h) verify_chain fails when prev_hash doesn't form a chain.
# ---------------------------------------------------------------------------
def test_verify_chain_fails_when_chain_is_broken() -> None:
    """Sanity: verify_chain detects a deliberately broken chain — pins the
    falsifiability of the chain check the rebind helper supports."""
    entry1 = _build_entry(prev_hash=None, args_hash="h1")
    # Entry 2 with a prev_hash that does NOT match entry1.id.
    entry2 = _build_entry(prev_hash="sha256:WRONG", args_hash="h2")
    # entry2.id is a valid content hash but the chain is broken at
    # the prev_hash reference.
    assert verify_chain([entry1, entry2]) is False


# ---------------------------------------------------------------------------
# (i) Rebind preserves all non-prev_hash content (op, args_hash, etc.).
# ---------------------------------------------------------------------------
def test_rebind_preserves_non_chain_fields() -> None:
    """Only chain-related fields change; op / args_hash / verdict /
    latency_ms / recorded_at / handler_chain / principal / run_id are
    preserved verbatim."""
    entry = _build_entry(
        prev_hash="sha256:OLD",
        op=":llm/call",
        args_hash="sha256:abcdef",
        run_id="11111111-2222-3333-4444-555555555555",
    )
    datom = audit_entry_to_datom(entry)
    rebound = rebind_audit_datom_prev_hash(datom, "sha256:NEW")
    rebound_entry = datom_to_audit_entry(rebound)

    assert rebound_entry.op == entry.op
    assert rebound_entry.args_hash == entry.args_hash
    assert rebound_entry.verdict == entry.verdict
    assert rebound_entry.latency_ms == entry.latency_ms
    assert rebound_entry.recorded_at == entry.recorded_at
    assert rebound_entry.run_id == entry.run_id
    assert rebound_entry.handler_chain == entry.handler_chain
    assert rebound_entry.principal == entry.principal


# ---------------------------------------------------------------------------
# (j) Input dict is NOT mutated.
# ---------------------------------------------------------------------------
def test_rebind_does_not_mutate_input_dict() -> None:
    """Defensive: the caller's datom dict survives the call unchanged."""
    entry = _build_entry(prev_hash="sha256:OLD")
    datom = audit_entry_to_datom(entry)
    original_prev = datom[":datom/provenance"][":prev-hash"]
    original_sig = datom[":datom/provenance"][":signature"]
    original_tx = datom[":datom/tx"]
    original_e = datom[":datom/e"]

    _ = rebind_audit_datom_prev_hash(datom, "sha256:NEW")

    # Input dict completely untouched.
    assert datom[":datom/provenance"][":prev-hash"] == original_prev
    assert datom[":datom/provenance"][":signature"] == original_sig
    assert datom[":datom/tx"] == original_tx
    assert datom[":datom/e"] == original_e


# ---------------------------------------------------------------------------
# (k) Repeated rebind with same target is idempotent (after one apply).
# ---------------------------------------------------------------------------
def test_rebind_is_idempotent_on_already_rebound_datom() -> None:
    """After one rebind, a second rebind to the same target is a no-op
    (the fast-path triggers because the prev_hash now matches)."""
    entry = _build_entry(prev_hash="sha256:OLD")
    datom = audit_entry_to_datom(entry)
    rebound1 = rebind_audit_datom_prev_hash(datom, "sha256:NEW")
    rebound2 = rebind_audit_datom_prev_hash(rebound1, "sha256:NEW")
    # Second rebind hits the no-op fast-path.
    assert rebound2 is rebound1


# ---------------------------------------------------------------------------
# (l) Within-batch chain: rebind N entries linking each to prior.
# ---------------------------------------------------------------------------
def test_rebind_chain_three_entries_serially() -> None:
    """Simulate the within-batch rebind PostgresStore performs: each
    audit datom in a batch rebinds to the prior rebound datom's
    :signature."""
    e1 = _build_entry(prev_hash="sha256:STALE_A", args_hash="h1")
    e2 = _build_entry(prev_hash="sha256:STALE_B", args_hash="h2")
    e3 = _build_entry(prev_hash="sha256:STALE_C", args_hash="h3")

    d1 = audit_entry_to_datom(e1)
    d2 = audit_entry_to_datom(e2)
    d3 = audit_entry_to_datom(e3)

    # Cross-process locked head is some pre-existing tip.
    locked_head = "sha256:LOCKED_HEAD"

    # Rebind chain serially — each datom binds to prior rebound's sig.
    r1 = rebind_audit_datom_prev_hash(d1, locked_head)
    r1_sig = r1[":datom/provenance"][":signature"]
    r2 = rebind_audit_datom_prev_hash(d2, r1_sig)
    r2_sig = r2[":datom/provenance"][":signature"]
    r3 = rebind_audit_datom_prev_hash(d3, r2_sig)

    # Walk the rebound chain and verify continuity.
    re1 = datom_to_audit_entry(r1)
    re2 = datom_to_audit_entry(r2)
    re3 = datom_to_audit_entry(r3)

    assert re1.prev_hash == locked_head
    assert re2.prev_hash == re1.id
    assert re3.prev_hash == re2.id


# ---------------------------------------------------------------------------
# (m) Rebind output is wire-conformant.
# ---------------------------------------------------------------------------
def test_rebind_output_passes_self_conform() -> None:
    """``audit_entry_to_datom`` self-conforms at output; therefore the
    rebound datom is wire-conformant by construction. Round-tripping
    through ``datom_to_audit_entry`` should not raise."""
    entry = _build_entry(prev_hash="sha256:OLD")
    datom = audit_entry_to_datom(entry)
    rebound = rebind_audit_datom_prev_hash(datom, "sha256:NEW")

    # Round-trip and assert no exception — self-conform passed inside
    # audit_entry_to_datom.
    decoded = datom_to_audit_entry(rebound)
    assert decoded.prev_hash == "sha256:NEW"
