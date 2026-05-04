"""Caller identity attestation stub (Phase 2.1c, Design §16; full design in
2.1c.5 stub doc).

In 2.1c, `CallerIdentity.attest(...)` always returns None — the substrate
records `caller_identity: null` on every emitted datom. The real Ed25519
keypair-registry-backed verification pipeline is rescoped to 2.1c.5 with a
falsifiable acceptance signal (an xfail-strict test marker in
tests/http/test_auth.py) that flips to PASS when 2.1c.5 lands.

The class shape is intentionally pre-shaped so 2.1c.5 only has to fill in
the body — no public API change required at the seam.
"""
from __future__ import annotations

from typing import Optional


class CallerIdentity:
    """Caller-identity attestation primitive. Stub in 2.1c, real in 2.1c.5."""

    @staticmethod
    def attest(
        caller_id: Optional[str],
        payload: bytes,
        signature: Optional[bytes],
    ) -> Optional[str]:
        """Verify a caller-signed payload and return the verified caller_id.

        2.1c behavior: always returns None (stub).
        2.1c.5 behavior: returns caller_id if the Ed25519 signature verifies
        against the registered pubkey for caller_id; raises otherwise.

        Returns:
            None in 2.1c (no attestation).
            In 2.1c.5: caller_id (str) on successful verification.
        """
        return None
