"""Ed25519 signing primitives for the audit chain.

Mimir Phase B (14-week ship plan) — closes the "do you cryptographically prove
what your AI did?" gap. The audit chain is already Merkle-style content-hashed
(see ``persistence.effect.handlers.audit.verify_chain``); this module adds
detached Ed25519 signatures over each entry's content hash, enabling
third-party signature verification without trusting the runtime that produced
the chain.

Design:
- Signatures are detached and optional. Unsigned entries continue to verify
  via the existing hash chain; signed entries also verify Ed25519 signatures
  when a public key map is supplied to ``verify_chain``.
- The signature target is ``entry.id`` (the canonical content hash). Because
  the content hash already chains via ``prev_hash``, signing the hash also
  transitively attests to the entry's chain position.
- Wire format is ``"ed25519:<urlsafe_b64>"`` matching the existing
  ``"sha256:<hex>"`` prefix convention used elsewhere in the substrate.
- Raw 32-byte Ed25519 keys (no PEM/DER ceremony). Callers handle key storage
  and rotation; the substrate doesn't dictate either.
"""
from __future__ import annotations

import base64
import binascii
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


_SIG_PREFIX = "ed25519:"
_FINGERPRINT_PREFIX = "ed25519-pub:"


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate a fresh Ed25519 keypair as raw 32-byte bytes.

    Returns:
        ``(private_key_bytes, public_key_bytes)`` — both 32 bytes, no
        PEM/DER wrapping. Caller is responsible for storage.
    """
    priv = Ed25519PrivateKey.generate()
    priv_bytes = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv_bytes, pub_bytes


def sign(content_hash: str, private_key: bytes) -> str:
    """Sign ``content_hash`` with the raw Ed25519 ``private_key``.

    Args:
        content_hash: The string to sign — typically ``AuditEntry.id``,
            which is already a canonical content hash in the form
            ``"sha256:<hex>"``. Encoded as UTF-8 before signing.
        private_key: Raw 32-byte Ed25519 private key bytes.

    Returns:
        ``"ed25519:<urlsafe_b64>"`` — the detached signature.
    """
    priv = Ed25519PrivateKey.from_private_bytes(private_key)
    sig_bytes = priv.sign(content_hash.encode("utf-8"))
    return _SIG_PREFIX + base64.urlsafe_b64encode(sig_bytes).decode("ascii")


def verify(content_hash: str, signature: str, public_key: bytes) -> bool:
    """Verify ``signature`` against ``content_hash`` and ``public_key``.

    Returns ``True`` iff the signature is well-formed and valid for the
    given content hash and public key. Returns ``False`` for any failure
    mode (malformed signature, wrong key, tampered content, malformed
    public key) — never raises.

    Args:
        content_hash: The string that was signed.
        signature: ``"ed25519:<urlsafe_b64>"``.
        public_key: Raw 32-byte Ed25519 public key bytes.
    """
    if not signature.startswith(_SIG_PREFIX):
        return False
    try:
        sig_bytes = base64.urlsafe_b64decode(signature[len(_SIG_PREFIX) :])
    except (ValueError, binascii.Error):
        return False
    try:
        pub = Ed25519PublicKey.from_public_bytes(public_key)
    except (ValueError, TypeError):
        return False
    try:
        pub.verify(sig_bytes, content_hash.encode("utf-8"))
    except InvalidSignature:
        return False
    return True


def fingerprint(public_key: bytes) -> str:
    """Return a short opaque identifier for a public key.

    Useful as the ``signer_id`` field on ``AuditEntry`` when callers
    don't have a separate naming scheme. Truncates SHA-256 to 16 hex
    chars (64 bits) — collision risk is negligible for the keypairs a
    single Mimir deployment will operate.
    """
    digest = hashlib.sha256(public_key).hexdigest()
    return _FINGERPRINT_PREFIX + digest[:16]


__all__ = [
    "fingerprint",
    "generate_keypair",
    "sign",
    "verify",
]
