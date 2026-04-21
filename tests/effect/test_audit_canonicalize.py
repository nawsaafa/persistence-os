"""ARIS Round 5 W5-audit-canonicalize — ``AuditEntry`` canonicalises
sibling keyword-keyed fields at construction time (closes R1 N7, R3
R4-N1, R3 R4-N2).

Round 4 added ``Datom.__post_init__`` canonicalisation for ``a`` and
``provenance["source"]`` (W-wire), but three sibling fields in
``AuditEntry`` share the same shape defect:

- ``policy_id`` — ``policy_eval.py`` emits bare-string policy IDs
  (``"unknown"``, ``"bankability-v3"``). ``make_audit_handler`` passes
  these verbatim. The ``:audit/policy-id`` spec slot requires a keyword
  form, so any entry with a bare-string policy_id raised at
  ``to_edn`` self-conform time (R1 N7).
- ``handler_chain`` — ``from_edn`` strips keywords back to bare
  strings, but the ``verify_chain`` Merkle check rehashes the content
  (including ``handler_chain``). Pre-keyworded chains break the
  round-trip because the hashed form differed from the reconstructed
  form (R3 R4-N1).
- ``principal`` keys — same class of issue reachable through
  ``from_edn ∘ to_edn`` on pre-keyworded principals (R3 R4-N2).

Mirror the ``Datom.__post_init__`` pattern from W-wire: normalise all
three siblings to a canonical internal form so callers cannot construct
two ``AuditEntry`` values that differ only by a leading colon. With
normalisation in place, ``to_edn`` serialisation becomes a straight
``":" + x`` without branching on "already colon".
"""
from __future__ import annotations

import uuid

import pytest

from persistence.effect.handlers.audit import AuditEntry, verify_chain


def _base_kwargs(**overrides) -> dict:
    base = dict(
        id="sha256:" + "a" * 64,
        prev_hash=None,
        op=":llm/call",
        args_hash="sha256:" + "b" * 64,
        verdict="ok",
        latency_ms=42,
        recorded_at=1_700_000_000.0,
        result_hash="sha256:" + "c" * 64,
        error=None,
        policy_id=None,
        handler_chain=(),
        principal={},
        run_id=str(uuid.uuid4()),
        parent=None,
    )
    base.update(overrides)
    return base


class TestPolicyIdCanonicalizationAtInit:
    """policy_id must be stored in keyword form (leading ``":"``)
    regardless of how the caller supplied it. Closes R1 N7.
    """

    def test_bare_string_policy_id_gets_leading_colon(self):
        """A bare-string ``policy_id="bankability-v3"`` (the production
        shape emitted by ``policy_eval.py``) must be canonicalised to
        ``":bankability-v3"`` at construction.
        """
        entry = AuditEntry(**_base_kwargs(policy_id="bankability-v3"))
        assert entry.policy_id == ":bankability-v3"

    def test_pre_keyworded_policy_id_unchanged(self):
        """Idempotent on already-keyworded input."""
        entry = AuditEntry(**_base_kwargs(policy_id=":bankability-v3"))
        assert entry.policy_id == ":bankability-v3"

    def test_none_policy_id_unchanged(self):
        """The default ``policy_id=None`` passes through untouched."""
        entry = AuditEntry(**_base_kwargs(policy_id=None))
        assert entry.policy_id is None

    def test_policy_id_unknown_canonicalised(self):
        """policy_eval.py emits the literal string ``"unknown"`` when
        the policy map has no ``policy/id`` — this is the more common
        production shape. Must canonicalise to ``":unknown"``."""
        entry = AuditEntry(**_base_kwargs(policy_id="unknown"))
        assert entry.policy_id == ":unknown"

    def test_bare_policy_id_conforms_to_audit_entry_spec(self):
        """The explicit reproducer for R1 N7: a bare-string policy_id
        must not raise at to_edn self-conform time anymore.
        """
        entry = AuditEntry(**_base_kwargs(policy_id="bankability-v3"))
        # Before W5-audit-canonicalize this raised ValueError because
        # the :audit/policy-id slot required a keyword and the bare
        # string slipped through.
        edn = entry.to_edn()
        assert edn[":audit/policy-id"] == ":bankability-v3"


class TestHandlerChainCanonicalizationAtInit:
    """handler_chain entries must be stored in a canonical bare-string
    form (no leading colons) regardless of how the caller supplied
    them. Closes R3 R4-N1.
    """

    def test_bare_chain_unchanged(self):
        entry = AuditEntry(**_base_kwargs(handler_chain=("audit", "llm")))
        assert entry.handler_chain == ("audit", "llm")

    def test_pre_keyworded_chain_stripped(self):
        """A pre-keyworded chain (e.g. reconstructed via ``from_edn``)
        must be canonicalised to bare form at construction."""
        entry = AuditEntry(**_base_kwargs(handler_chain=(":audit", ":llm")))
        assert entry.handler_chain == ("audit", "llm")

    def test_mixed_chain_uniformly_stripped(self):
        """Mixed-input chains yield uniformly-bare internal state."""
        entry = AuditEntry(**_base_kwargs(handler_chain=(":audit", "llm", ":tool")))
        assert entry.handler_chain == ("audit", "llm", "tool")

    def test_double_colon_idempotent(self):
        """``lstrip(":")`` — ``"::audit"`` canonicalises to
        ``"audit"``, not ``":audit"``. Idempotent under repeat
        construction (R3 R4-N3 sibling issue)."""
        entry = AuditEntry(**_base_kwargs(handler_chain=("::audit",)))
        assert entry.handler_chain == ("audit",)


class TestPrincipalKeysCanonicalizationAtInit:
    """Principal dict keys must be stored in a canonical bare-string
    form regardless of how the caller supplied them. Closes R3 R4-N2.
    """

    def test_bare_principal_keys_unchanged(self):
        entry = AuditEntry(**_base_kwargs(principal={"agent": "dfi"}))
        assert entry.principal == {"agent": "dfi"}

    def test_pre_keyworded_principal_keys_stripped(self):
        entry = AuditEntry(**_base_kwargs(principal={":agent": "dfi"}))
        assert entry.principal == {"agent": "dfi"}

    def test_mixed_principal_keys_uniformly_stripped(self):
        entry = AuditEntry(
            **_base_kwargs(principal={":agent": "dfi", "team": "bankability"})
        )
        assert entry.principal == {"agent": "dfi", "team": "bankability"}


class TestFromEdnRoundTripPreservesVerifyChain:
    """Closes R3 R4-N1 regression: before W5-audit-canonicalize, a
    pre-keyworded handler_chain broke ``verify_chain`` after a
    ``from_edn(to_edn(...))`` round-trip because the content hash was
    computed over keyworded strings but ``from_edn`` stripped them
    back to bare strings, so recomputing the hash mismatched.
    """

    def test_verify_chain_survives_to_edn_from_edn(self):
        """Construct an entry with a pre-keyworded handler_chain; its
        computed id must remain verifiable after the round-trip.
        """
        from persistence.effect.handlers.audit import _content_hash

        # Build the content in the exact shape make_audit_handler uses.
        content: dict = dict(
            prev_hash=None,
            op=":llm/call",
            args_hash="sha256:" + "b" * 64,
            verdict="ok",
            latency_ms=42,
            recorded_at=1_700_000_000.0,
            result_hash="sha256:" + "c" * 64,
            error=None,
            # Caller provides pre-keyworded chain; __post_init__ must
            # canonicalise so the content hash is deterministic.
            policy_id=None,
            handler_chain=(":audit", ":llm"),
            principal={":agent": "dfi"},
            run_id=str(uuid.uuid4()),
            parent=None,
        )
        # Canonicalise the content to match __post_init__ behaviour,
        # then compute the id.
        canonical_content = dict(content)
        canonical_content["handler_chain"] = tuple(
            h.lstrip(":") for h in canonical_content["handler_chain"]
        )
        canonical_content["principal"] = {
            (k[1:] if isinstance(k, str) and k.startswith(":") else k): v
            for k, v in canonical_content["principal"].items()
        }
        entry_id = _content_hash(canonical_content)
        original = AuditEntry(id=entry_id, **content)

        # Round-trip through the wire form.
        wire = original.to_edn()
        restored = AuditEntry.from_edn(wire)

        # Both should be equivalent and both should verify.
        assert verify_chain([original]) is True
        assert verify_chain([restored]) is True
        # The restored entry's id matches original.
        assert restored.id == original.id


class TestFromEdnToEdnEqualityHolds:
    """ARIS R2 R4-G1 bonus — with canonicalisation at __post_init__,
    ``from_edn(to_edn(e)) == e`` holds by dataclass equality.
    """

    def test_round_trip_equality_on_bare_input(self):
        # Construct an entry with production-shape bare-string fields.
        # With R1 N7 fixed, policy_id must also canonicalise so that
        # equality of restored == original does not fail on the
        # policy_id slot.
        # Build content first, then compute the proper content hash.
        from persistence.effect.handlers.audit import _content_hash

        content: dict = dict(
            prev_hash=None,
            op=":llm/call",
            args_hash="sha256:" + "b" * 64,
            verdict="ok",
            latency_ms=42,
            recorded_at=1_700_000_000.0,
            result_hash="sha256:" + "c" * 64,
            error=None,
            policy_id="bankability-v3",  # bare, canonicalised to :bankability-v3
            handler_chain=("audit", "llm"),
            principal={"agent": "dfi"},
            run_id=str(uuid.uuid4()),
            parent=None,
        )
        # Construct a dummy AuditEntry to get the canonicalised content
        # shape, then compute its id, then rebuild with correct id.
        tmp = AuditEntry(id="sha256:" + "0" * 64, **content)
        d = tmp.to_dict()
        d.pop("id")
        entry_id = _content_hash(d)
        original = AuditEntry(id=entry_id, **content)

        restored = AuditEntry.from_edn(original.to_edn())
        assert restored == original
