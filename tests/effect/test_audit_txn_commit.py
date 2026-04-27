"""v0.5.1 N2 — AuditEntry.txn_commit field.

Closes a latent ``args_hash`` corruption found in v0.5.0a1: every audit
entry written for a txn-replayed intent had ``_txn_commit=<uuid>`` baked
into its ``args_hash``, so the same intent across two different commits
produced different hashes — breaking any audit-log analysis that keyed
off args.

The fix promotes ``txn_commit`` to a first-class ``AuditEntry`` field
fed by a typed ``Runtime.perform`` kwarg. The audit handler explicitly
pops ``"_txn_commit"`` from args before hashing, so ``args_hash``
becomes a pure function of the call's arguments. Test 5 below
(``test_args_hash_excludes_txn_commit``) is the corruption-fix pin.

Tests are organised in spec order (impl plan § N2):

1. ``test_audit_entry_accepts_txn_commit_field`` — dataclass shape.
2. ``test_audit_entry_to_datom_emits_effect_txn_commit_when_set`` —
   wire emission, symmetric with ``:episode``.
3. ``test_datom_to_audit_entry_reads_effect_txn_commit`` — wire round trip.
4. ``test_runtime_perform_accepts_txn_commit_kwarg`` — typed seam.
5. ``test_args_hash_excludes_txn_commit`` — the corruption-fix pin.
6. ``test_legacy_args_underscore_txn_commit_still_lifted_to_field`` —
   direct callers passing ``args["_txn_commit"]`` still work.
"""
from __future__ import annotations

import uuid

from persistence.effect.handlers.audit import (
    AuditEntry,
    audit_entry_to_datom,
    datom_to_audit_entry,
    make_audit_handler,
)
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.handlers.raw import make_echo_llm_handler
from persistence.effect.runtime import Runtime, perform, with_runtime


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _stack_with_audit(entries, clock_ts: int = 1_712_000_000) -> Runtime:
    audit = make_audit_handler(entries, wraps={":llm/call"})
    raw = make_echo_llm_handler()
    clock = make_fixed_clock_handler(ts=clock_ts)
    return Runtime([raw, clock, audit])


# ---------------------------------------------------------------------------
# 1. Dataclass shape
# ---------------------------------------------------------------------------


def test_audit_entry_accepts_txn_commit_field():
    """``AuditEntry`` carries a typed ``txn_commit: str | None`` field
    that defaults to ``None``. Direct construction with the field set
    round-trips through ``to_dict``.
    """
    commit_id = str(uuid.uuid4())
    entry = AuditEntry(
        id="sha256:" + "0" * 64,
        prev_hash=None,
        op=":llm/call",
        args_hash="sha256:" + "1" * 64,
        verdict="ok",
        latency_ms=10,
        recorded_at=1_700_000_000.0,
        txn_commit=commit_id,
    )
    assert entry.txn_commit == commit_id
    # Defaults to None when omitted.
    entry_no_txn = AuditEntry(
        id="sha256:" + "0" * 64,
        prev_hash=None,
        op=":llm/call",
        args_hash="sha256:" + "1" * 64,
        verdict="ok",
        latency_ms=10,
        recorded_at=1_700_000_000.0,
    )
    assert entry_no_txn.txn_commit is None


# ---------------------------------------------------------------------------
# 2/3. Wire round-trip
# ---------------------------------------------------------------------------


def test_audit_entry_to_datom_emits_effect_txn_commit_when_set():
    """When ``entry.txn_commit`` is set, the datom's
    ``:datom/provenance`` carries ``:effect/txn-commit``. Symmetric with
    ``:episode`` — the key is omitted entirely when the field is None.
    """
    commit_id = str(uuid.uuid4())
    entry = AuditEntry(
        id="sha256:" + "a" * 64,
        prev_hash=None,
        op=":llm/call",
        args_hash="sha256:" + "b" * 64,
        verdict="ok",
        latency_ms=10,
        recorded_at=1_700_000_000.0,
        txn_commit=commit_id,
    )
    datom = audit_entry_to_datom(entry)
    assert datom[":datom/provenance"][":effect/txn-commit"] == commit_id

    entry_none = AuditEntry(
        id="sha256:" + "a" * 64,
        prev_hash=None,
        op=":llm/call",
        args_hash="sha256:" + "b" * 64,
        verdict="ok",
        latency_ms=10,
        recorded_at=1_700_000_000.0,
        txn_commit=None,
    )
    datom_none = audit_entry_to_datom(entry_none)
    assert ":effect/txn-commit" not in datom_none[":datom/provenance"]


def test_datom_to_audit_entry_reads_effect_txn_commit():
    """Wire round trip: ``audit_entry_to_datom`` then
    ``datom_to_audit_entry`` preserves ``txn_commit`` byte-identically.
    """
    commit_id = str(uuid.uuid4())
    entry = AuditEntry(
        id="sha256:" + "a" * 64,
        prev_hash=None,
        op=":llm/call",
        args_hash="sha256:" + "b" * 64,
        verdict="ok",
        latency_ms=10,
        recorded_at=1_700_000_000.0,
        txn_commit=commit_id,
    )
    round_tripped = datom_to_audit_entry(audit_entry_to_datom(entry))
    assert round_tripped.txn_commit == commit_id

    # And the None case round-trips to None, not to a missing key.
    entry_none = AuditEntry(
        id="sha256:" + "a" * 64,
        prev_hash=None,
        op=":llm/call",
        args_hash="sha256:" + "b" * 64,
        verdict="ok",
        latency_ms=10,
        recorded_at=1_700_000_000.0,
        txn_commit=None,
    )
    rt_none = datom_to_audit_entry(audit_entry_to_datom(entry_none))
    assert rt_none.txn_commit is None


# ---------------------------------------------------------------------------
# 4. Typed seam
# ---------------------------------------------------------------------------


def test_runtime_perform_accepts_txn_commit_kwarg():
    """``Runtime.perform(op, args, txn_commit="abc")`` lifts the
    commit_id onto the AuditEntry's ``txn_commit`` field.
    """
    commit_id = str(uuid.uuid4())
    entries: list[AuditEntry] = []
    rt = _stack_with_audit(entries)
    with with_runtime(rt):
        # Direct rt.perform — bypasses the module-level ``perform`` shim
        # so we can pass the typed kwarg without going through dosync.
        rt.perform(
            ":llm/call",
            {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            txn_commit=commit_id,
        )
    assert len(entries) == 1
    assert entries[0].txn_commit == commit_id


# ---------------------------------------------------------------------------
# 5. THE CORRUPTION-FIX PIN
# ---------------------------------------------------------------------------


def test_args_hash_excludes_txn_commit():
    """Pin the v0.5.1 N2 fix: the same args dispatched with two
    different ``txn_commit`` values produces *identical* ``args_hash``
    on both AuditEntries.

    In v0.5.0a1, ``_txn_commit`` was stuffed into the args dict and
    reached ``canonical_hash(args)``, so this test would have failed —
    different commit_ids would have produced different args_hashes. The
    fix pops the sentinel before hashing.
    """
    entries: list[AuditEntry] = []
    rt = _stack_with_audit(entries)
    args = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    with with_runtime(rt):
        rt.perform(":llm/call", dict(args), txn_commit="commit-A")
        rt.perform(":llm/call", dict(args), txn_commit="commit-B")
    assert len(entries) == 2
    # txn_commit differs across the two entries.
    assert entries[0].txn_commit == "commit-A"
    assert entries[1].txn_commit == "commit-B"
    # But args_hash is identical: it's a pure function of the args.
    assert entries[0].args_hash == entries[1].args_hash, (
        "args_hash must be invariant under txn_commit — same args, "
        "different commits, must produce the same hash. v0.5.0a1 leaked "
        f"commit_id into the hash; got {entries[0].args_hash} vs "
        f"{entries[1].args_hash}."
    )


# ---------------------------------------------------------------------------
# 6. Legacy direct-args path
# ---------------------------------------------------------------------------


def test_legacy_args_underscore_txn_commit_still_lifted_to_field():
    """Direct callers that pass ``args={"_txn_commit": "x", ...}``
    (the legacy v0.5.0a1 wire) still get ``"x"`` lifted to the
    AuditEntry's ``txn_commit`` field. The audit handler's ``args.pop``
    runs uniformly regardless of which path stuffed the key in.

    Also pins that ``args_hash`` is invariant under the legacy path —
    the leftover sentinel is stripped before hashing whether it arrived
    via the typed kwarg or via direct args injection.
    """
    entries: list[AuditEntry] = []
    rt = _stack_with_audit(entries)
    args_clean = {"model": "m", "messages": []}
    args_with_legacy_sentinel = {**args_clean, "_txn_commit": "legacy-commit"}
    with with_runtime(rt):
        # Reference run: typed kwarg path.
        rt.perform(":llm/call", dict(args_clean), txn_commit="typed-commit")
        # Legacy run: caller stuffs ``_txn_commit`` into args themselves.
        rt.perform(":llm/call", dict(args_with_legacy_sentinel))
    assert len(entries) == 2
    typed_entry, legacy_entry = entries
    # Typed and legacy paths both populate the field.
    assert typed_entry.txn_commit == "typed-commit"
    assert legacy_entry.txn_commit == "legacy-commit"
    # And ``args_hash`` is identical across both paths because the
    # sentinel is popped before hashing in either case.
    assert typed_entry.args_hash == legacy_entry.args_hash


# ---------------------------------------------------------------------------
# 7. v0.5.1 W1 fix-pass — MAJOR-1: audit-chain hash continuity for non-txn
# ---------------------------------------------------------------------------


def test_audit_chain_hash_continuity_for_non_txn_calls():
    """v0.5.1 W1 fix-pass — MAJOR-1 (R1): a non-txn audit entry (no
    ``txn_commit`` at all) must produce a content hash AND a wire datom
    that are byte-identical to a v0.5.0a1-shape entry of the same op.

    Before the fix, ``make_audit_handler`` unconditionally inserted
    ``"txn_commit": None`` into the hashed content dict, breaking
    v0.5.0a1→v0.5.1 chain continuity for every non-txn entry — the same
    args produced a different ``entry.id`` between releases purely
    because of an extra ``None`` slot in the canonicalised content.

    Pin: an AuditEntry with ``txn_commit=None`` and another with no
    ``txn_commit`` set explicitly produce
    - the same ``entry.id`` (content-hash continuity), and
    - the same ``audit_entry_to_datom(...)`` provenance dict (no
      ``:effect/txn-commit`` key on either side).
    """
    base_kwargs = dict(
        id="sha256:" + "a" * 64,  # valid sha256 wire shape for audit_entry_to_datom
        prev_hash=None,
        op=":llm/call",
        args_hash="sha256:" + "b" * 64,
        verdict="ok",
        latency_ms=10,
        recorded_at=1_700_000_000.0,
    )
    # Two ways to express "no txn": explicit None vs default-omitted.
    e_explicit_none = AuditEntry(**{**base_kwargs, "txn_commit": None})
    e_default_omitted = AuditEntry(**base_kwargs)
    # Default field value is None for both shapes — first invariant.
    assert e_explicit_none.txn_commit is None
    assert e_default_omitted.txn_commit is None

    # Compute the canonical content hash that ``make_audit_handler``
    # would assign as ``entry.id``. Both ``to_dict()`` calls must skip
    # the key (the fix in ``AuditEntry.to_dict``), so the resulting
    # hashes are equal.
    from persistence.effect.handlers.audit import _content_hash
    d_explicit = e_explicit_none.to_dict()
    d_default = e_default_omitted.to_dict()
    d_explicit.pop("id")
    d_default.pop("id")
    # MAJOR-1 invariant 1: both paths omit the key in to_dict.
    assert "txn_commit" not in d_explicit
    assert "txn_commit" not in d_default
    # MAJOR-1 invariant 2: same content shape → same content hash.
    assert _content_hash(d_explicit) == _content_hash(d_default)

    # MAJOR-1 invariant 3: the wire datom omits the provenance key on
    # both sides (audit_entry_to_datom already conditional on this).
    datom_explicit = audit_entry_to_datom(e_explicit_none)
    datom_default = audit_entry_to_datom(e_default_omitted)
    assert ":effect/txn-commit" not in datom_explicit[":datom/provenance"]
    assert ":effect/txn-commit" not in datom_default[":datom/provenance"]
    # And the full provenance dicts are equal — no key drift sneaks in
    # via a None-placeholder elsewhere.
    assert datom_explicit[":datom/provenance"] == datom_default[":datom/provenance"]
