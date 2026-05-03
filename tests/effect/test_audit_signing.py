"""Tests for Ed25519 signing on the audit chain (Mimir Phase B).

Covers:
- Bare signing primitives (generate / sign / verify / fingerprint)
- AuditEntry signature/signer_id fields + content-hash independence
- make_audit_handler signer integration
- verify_chain signature verification
- Tamper detection
"""
from __future__ import annotations

import base64

import pytest

from persistence.effect._signing import (
    fingerprint,
    generate_keypair,
    sign,
    verify,
)
from persistence.effect.handlers.audit import (
    AuditEntry,
    _content_hash,
    make_audit_handler,
    verify_chain,
)
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.handlers.raw import make_echo_llm_handler
from persistence.effect.runtime import Runtime, perform, with_runtime


# ---------------------------------------------------------------------------
# Bare signing primitives
# ---------------------------------------------------------------------------


class TestSigningPrimitives:
    def test_generate_keypair_returns_32_byte_keys(self) -> None:
        priv, pub = generate_keypair()
        assert isinstance(priv, bytes)
        assert isinstance(pub, bytes)
        assert len(priv) == 32
        assert len(pub) == 32

    def test_generate_keypair_is_random(self) -> None:
        # Two consecutive generations should produce different keypairs.
        priv1, pub1 = generate_keypair()
        priv2, pub2 = generate_keypair()
        assert priv1 != priv2
        assert pub1 != pub2

    def test_sign_returns_ed25519_prefixed_signature(self) -> None:
        priv, _ = generate_keypair()
        sig = sign("sha256:abc123", priv)
        assert sig.startswith("ed25519:")
        # Base64 body should round-trip through decode without error.
        body = sig[len("ed25519:") :]
        decoded = base64.urlsafe_b64decode(body)
        # Ed25519 signatures are exactly 64 bytes.
        assert len(decoded) == 64

    def test_verify_accepts_valid_signature(self) -> None:
        priv, pub = generate_keypair()
        content = "sha256:" + "a" * 64
        sig = sign(content, priv)
        assert verify(content, sig, pub) is True

    def test_verify_rejects_tampered_content(self) -> None:
        priv, pub = generate_keypair()
        content = "sha256:" + "a" * 64
        sig = sign(content, priv)
        # Flip one character.
        tampered = "sha256:" + "b" + "a" * 63
        assert verify(tampered, sig, pub) is False

    def test_verify_rejects_wrong_public_key(self) -> None:
        priv_a, _ = generate_keypair()
        _, pub_b = generate_keypair()
        content = "sha256:abc"
        sig = sign(content, priv_a)
        assert verify(content, sig, pub_b) is False

    def test_verify_rejects_malformed_signature_prefix(self) -> None:
        _, pub = generate_keypair()
        assert verify("sha256:abc", "rsa:fakesig", pub) is False
        assert verify("sha256:abc", "abc", pub) is False
        assert verify("sha256:abc", "", pub) is False

    def test_verify_rejects_malformed_signature_body(self) -> None:
        _, pub = generate_keypair()
        # Valid prefix, malformed base64 body.
        assert verify("sha256:abc", "ed25519:!!!not-base64!!!", pub) is False

    def test_verify_rejects_malformed_public_key(self) -> None:
        priv, _ = generate_keypair()
        sig = sign("sha256:abc", priv)
        # Public keys must be exactly 32 bytes; this is wrong length.
        assert verify("sha256:abc", sig, b"too-short") is False

    def test_verify_never_raises_on_invalid_signature(self) -> None:
        priv_a, _ = generate_keypair()
        _, pub_b = generate_keypair()
        sig = sign("sha256:abc", priv_a)
        # Should return False, not raise InvalidSignature.
        result = verify("sha256:different", sig, pub_b)
        assert result is False

    def test_fingerprint_is_deterministic(self) -> None:
        _, pub = generate_keypair()
        assert fingerprint(pub) == fingerprint(pub)

    def test_fingerprint_differs_for_different_keys(self) -> None:
        _, pub_a = generate_keypair()
        _, pub_b = generate_keypair()
        assert fingerprint(pub_a) != fingerprint(pub_b)

    def test_fingerprint_has_ed25519_pub_prefix(self) -> None:
        _, pub = generate_keypair()
        fp = fingerprint(pub)
        assert fp.startswith("ed25519-pub:")


# ---------------------------------------------------------------------------
# AuditEntry signature/signer_id fields
# ---------------------------------------------------------------------------


def _make_entry(
    *,
    op: str = ":llm/call",
    args_hash: str = "sha256:" + "0" * 64,
    signature: str | None = None,
    signer_id: str | None = None,
) -> AuditEntry:
    """Helper to construct an AuditEntry with deterministic content."""
    content = {
        "prev_hash": None,
        "op": op,
        "args_hash": args_hash,
        "verdict": "ok",
        "latency_ms": 10,
        "recorded_at": 1714737600.0,
        "result_hash": None,
        "error": None,
        "policy_id": None,
        "handler_chain": (),
        "principal": {},
        "run_id": None,
        "parent": None,
    }
    entry_id = _content_hash(content)
    return AuditEntry(
        id=entry_id,
        signature=signature,
        signer_id=signer_id,
        **content,
    )


class TestAuditEntrySignatureFields:
    def test_unsigned_entry_has_signature_none(self) -> None:
        entry = _make_entry()
        assert entry.signature is None
        assert entry.signer_id is None

    def test_signed_entry_carries_signature_and_signer_id(self) -> None:
        priv, pub = generate_keypair()
        signer_id = fingerprint(pub)

        # Build the unsigned entry first to capture its content hash.
        unsigned = _make_entry()
        sig = sign(unsigned.id, priv)
        signed = _make_entry(signature=sig, signer_id=signer_id)

        assert signed.signature == sig
        assert signed.signer_id == signer_id

    def test_signature_is_excluded_from_content_hash(self) -> None:
        """An entry's signature must NOT influence its content hash, or
        signing would be circular (signature signs the hash that signs the
        signature). Unsigned and signed-with-anything entries that share
        the same content fields must produce the same id."""
        unsigned = _make_entry()
        priv, pub = generate_keypair()
        sig = sign(unsigned.id, priv)
        signed = _make_entry(signature=sig, signer_id=fingerprint(pub))

        assert unsigned.id == signed.id

    def test_to_dict_omits_signature_and_signer_id(self) -> None:
        priv, pub = generate_keypair()
        unsigned = _make_entry()
        sig = sign(unsigned.id, priv)
        signed = _make_entry(signature=sig, signer_id=fingerprint(pub))

        d = signed.to_dict()
        assert "signature" not in d
        assert "signer_id" not in d


# ---------------------------------------------------------------------------
# Wire-form roundtrip
# ---------------------------------------------------------------------------


class TestSignatureWireRoundtrip:
    def test_to_edn_emits_signature_and_signer_id_when_set(self) -> None:
        priv, pub = generate_keypair()
        unsigned = _make_entry()
        sig = sign(unsigned.id, priv)
        signed = _make_entry(signature=sig, signer_id=fingerprint(pub))

        edn = signed.to_edn()
        assert edn[":audit/signature"] == sig
        assert edn[":audit/signer-id"] == fingerprint(pub)

    def test_to_edn_omits_signature_keys_when_unset(self) -> None:
        unsigned = _make_entry()
        edn = unsigned.to_edn()
        assert ":audit/signature" not in edn
        assert ":audit/signer-id" not in edn

    def test_from_edn_restores_signature_and_signer_id(self) -> None:
        priv, pub = generate_keypair()
        unsigned = _make_entry()
        sig = sign(unsigned.id, priv)
        signed = _make_entry(signature=sig, signer_id=fingerprint(pub))

        edn = signed.to_edn()
        restored = AuditEntry.from_edn(edn)
        assert restored.signature == sig
        assert restored.signer_id == fingerprint(pub)
        assert restored.id == signed.id


# ---------------------------------------------------------------------------
# make_audit_handler integration
# ---------------------------------------------------------------------------


class TestSignedAuditHandler:
    def _runtime_with(self, audit_handler) -> Runtime:
        clock = make_fixed_clock_handler(ts=1_712_000_000)
        raw = make_echo_llm_handler()
        return Runtime([raw, clock, audit_handler])

    def test_unsigned_handler_produces_unsigned_entries(self) -> None:
        entries: list[AuditEntry] = []
        h = make_audit_handler(entries, wraps=(":llm/call",))
        rt = self._runtime_with(h)

        with with_runtime(rt):
            perform(":llm/call", model="m", messages=[{"role": "user", "content": "hi"}])
        assert len(entries) == 1
        assert entries[0].signature is None
        assert entries[0].signer_id is None

    def test_signed_handler_produces_signed_entries(self) -> None:
        priv, pub = generate_keypair()
        signer_id = fingerprint(pub)
        entries: list[AuditEntry] = []
        h = make_audit_handler(
            entries,
            wraps=(":llm/call",),
            signer=(signer_id, priv),
        )
        rt = self._runtime_with(h)

        with with_runtime(rt):
            perform(":llm/call", model="m", messages=[{"role": "user", "content": "hi"}])
        assert len(entries) == 1
        entry = entries[0]
        assert entry.signature is not None
        assert entry.signature.startswith("ed25519:")
        assert entry.signer_id == signer_id

        # Signature must verify against the entry's content hash.
        assert verify(entry.id, entry.signature, pub) is True

    def test_signed_handler_signs_every_entry_in_a_chain(self) -> None:
        priv, pub = generate_keypair()
        signer_id = fingerprint(pub)
        entries: list[AuditEntry] = []
        h = make_audit_handler(
            entries,
            wraps=(":llm/call",),
            signer=(signer_id, priv),
        )
        rt = self._runtime_with(h)

        with with_runtime(rt):
            for i in range(3):
                perform(
                    ":llm/call",
                    model="m",
                    messages=[{"role": "user", "content": f"call-{i}"}],
                )

        assert len(entries) == 3
        for entry in entries:
            assert entry.signature is not None
            assert entry.signer_id == signer_id
            assert verify(entry.id, entry.signature, pub) is True


# ---------------------------------------------------------------------------
# verify_chain with signatures
# ---------------------------------------------------------------------------


class TestVerifyChainWithSignatures:
    def _signed_chain(self, n: int = 3):
        priv, pub = generate_keypair()
        signer_id = fingerprint(pub)
        entries: list[AuditEntry] = []
        h = make_audit_handler(
            entries,
            wraps=(":llm/call",),
            signer=(signer_id, priv),
        )
        clock = make_fixed_clock_handler(ts=1_712_000_000)
        raw = make_echo_llm_handler()
        rt = Runtime([raw, clock, h])
        with with_runtime(rt):
            for i in range(n):
                perform(
                    ":llm/call",
                    model="m",
                    messages=[{"role": "user", "content": f"call-{i}"}],
                )
        return entries, signer_id, pub

    def test_verify_chain_passes_with_correct_public_keys(self) -> None:
        entries, signer_id, pub = self._signed_chain(n=3)
        assert verify_chain(entries, public_keys={signer_id: pub}) is True

    def test_verify_chain_passes_with_no_public_keys_for_signed_entries(
        self,
    ) -> None:
        """Backward compat: callers who don't pass public_keys still get the
        existing hash-chain check; signatures are simply not verified."""
        entries, _, _ = self._signed_chain(n=3)
        assert verify_chain(entries) is True

    def test_verify_chain_fails_when_signature_tampered(self) -> None:
        entries, signer_id, pub = self._signed_chain(n=3)
        # Flip one character of the middle entry's signature.
        tampered = entries[1].signature
        assert tampered is not None
        flipped = tampered[: len("ed25519:") + 5] + (
            "B" if tampered[len("ed25519:") + 5] != "B" else "C"
        ) + tampered[len("ed25519:") + 6 :]
        from dataclasses import replace
        entries[1] = replace(entries[1], signature=flipped)
        assert verify_chain(entries, public_keys={signer_id: pub}) is False

    def test_verify_chain_fails_when_signer_id_unknown(self) -> None:
        entries, _, pub = self._signed_chain(n=2)
        # public_keys map doesn't include this signer.
        assert (
            verify_chain(entries, public_keys={"unknown-signer": pub}) is False
        )

    def test_verify_chain_fails_when_signed_entry_missing_signer_id(self) -> None:
        entries, signer_id, pub = self._signed_chain(n=1)
        from dataclasses import replace
        # Strip signer_id but keep signature.
        entries[0] = replace(entries[0], signer_id=None)
        assert verify_chain(entries, public_keys={signer_id: pub}) is False

    def test_verify_chain_with_unsigned_entries_ignores_public_keys(self) -> None:
        """Mixed chains (unsigned entries) should pass the hash check
        without trying to verify signatures on entries that have none."""
        clock = make_fixed_clock_handler(ts=1_712_000_000)
        raw = make_echo_llm_handler()
        entries: list[AuditEntry] = []
        h = make_audit_handler(entries, wraps=(":llm/call",))
        rt = Runtime([raw, clock, h])
        with with_runtime(rt):
            perform(":llm/call", model="m", messages=[{"role": "user", "content": "hi"}])
            perform(":llm/call", model="m", messages=[{"role": "user", "content": "hi2"}])
        # public_keys provided but no entry is signed → still passes.
        _, pub = generate_keypair()
        assert (
            verify_chain(entries, public_keys={"someone": pub}) is True
        )
