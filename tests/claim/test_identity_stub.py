"""Phase 2.1c — CallerIdentity stub forward-compat tests (Design §10.1).

The CallerIdentity stub in 2.1c is a placeholder that always returns None.
The real Ed25519 verify pipeline lands in 2.1c.5 — see
`docs/plans/2026-05-04-phase-2.1c.5-caller-identity-design.md`.
"""
from persistence.claim._identity import CallerIdentity


def test_caller_identity_attest_returns_none_in_2_1c():
    # signature is None or absent — stub does no verification
    assert CallerIdentity.attest(caller_id=None, payload=b"", signature=None) is None


def test_caller_identity_attest_returns_none_even_with_valid_looking_inputs():
    # 2.1c stub does NOT verify; always returns None until 2.1c.5 wires real Ed25519
    assert CallerIdentity.attest(
        caller_id="context-mimir",
        payload=b'{"some": "json"}',
        signature=b"any-bytes-here",
    ) is None
