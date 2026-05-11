"""Phase 2.4a G4 — env-keyed Ed25519 signer round-trip + falsifiability.

LD-4: ``PERSISTENCE_AUDIT_KEY=file:///abs/path/key.pem`` → CLI bootstrap
parses the URI, loads the PEM, derives ``signer_id`` = ``"ed25519:" +
sha256(pem_bytes)[:16]``, and threads ``(signer_id, raw_priv_bytes)``
through ``Substrate.open(audit_signer=...)`` →
``canonical_audit_stack(signer=...)`` → ``make_audit_handler(signer=...)``.

Mimir Phase B already landed the leaf-level signing primitive at
``handlers/audit.py:488-518`` + ``effect/_signing.py``. T4's job: the
two missing kwarg-passthrough layers
(``sdk/_facade.Substrate.open`` and
``effect/_audit_stack.canonical_audit_stack``) plus the env-parse +
SystemExit-on-unknown-scheme guard at ``coder/__main__.py``.

Tests (4):

  1. ``test_env_keyed_signer_signs_audit_entries`` — happy path:
     env-set + valid PEM → every emitted AuditEntry carries
     ``signature`` + ``signer_id`` matching the
     ``"ed25519:<sha256(pem)[:16]>"`` derivation; ``verify_chain`` with
     the matching public_keys map returns True.

  2. ``test_env_unset_produces_unsigned_entries`` — env-unset →
     entries have ``signature is None`` and ``signer_id is None``
     (pre-2.4a backward compat).

  3. ``test_tamper_breaks_signature_verification`` — Class-A
     falsifiability: rebuild an entry with a swapped ``args_hash`` that
     keeps the surface fields but contradicts the signed id →
     ``verify_chain`` returns False (content-hash mismatch detected by
     the chain verifier; the per-entry signature itself is left
     untouched on the rebuilt entry, so the failure is detected at
     content-binding rather than at raw signature verification).

  4. ``test_unknown_uri_scheme_systemexits`` — env set to
     ``pem:abc123`` → ``_load_audit_signer_from_env`` raises
     SystemExit with a message naming the unsupported scheme.

Forced spec deviations:
  FD-T4.1: spec mentions ``verify_audit_chain``; the actual helper at
    ``persistence.effect.handlers.audit:429`` is ``verify_chain``
    (re-exported as ``persistence.effect.verify_chain``). Accepts
    ``public_keys: dict[str, bytes]``. No new helper added.

  FD-T4.2: ``_signing.sign`` requires RAW 32-byte private key bytes
    (per ``effect/_signing.py:60-74``), not PEM. The CLI's
    ``_load_audit_signer_from_env`` extracts raw bytes via
    ``cryptography.hazmat.primitives.serialization.load_pem_private_key``
    + ``private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())``
    so the substrate-internal contract stays raw-only. The PEM is the
    on-disk operator surface; raw is the internal wire shape.

  FD-T4.3: tamper test (#3) cannot mutate an AuditEntry in place
    because it is ``@dataclass(frozen=True)``. We instead construct a
    NEW AuditEntry with a contradictory field (``args_hash`` swapped),
    keeping the original ``id`` + ``signature``. ``verify_chain``
    recomputes the content hash from the swapped fields, observes
    ``recomputed_id != entry.id``, and returns False at the
    pre-signature hash check. Direct signature verification with
    ``_signing.verify`` against a recomputed hash also fails.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from persistence.coder import _provider as _provider_mod
from persistence.coder import __main__ as coder_main
from persistence.coder.__main__ import (
    _AUDIT_KEY_ENV,
    _build_substrate_and_handlers,
    _load_audit_signer_from_env,
)
from persistence.effect import verify_chain
from persistence.effect.handlers.audit import AuditEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_pem(tmp_path: Path) -> tuple[Path, bytes, bytes]:
    """Generate an ephemeral Ed25519 keypair, write PEM to tmp_path.

    Returns ``(pem_path, pem_bytes, raw_pub_bytes)``. Caller takes the
    public-key bytes for verify_chain.
    """
    priv = Ed25519PrivateKey.generate()
    pem_bytes = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pem_path = tmp_path / "test.pem"
    pem_path.write_bytes(pem_bytes)
    raw_pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return pem_path, pem_bytes, raw_pub


def _make_args() -> argparse.Namespace:
    """Synthesize the argparse Namespace `_build_substrate_and_handlers`
    consumes (per FD-T1.1 — the CLI parser does not accept the
    test-only provider value)."""
    return argparse.Namespace(
        task="noop",
        db_path=None,
        provider="auto",
        model="claude-opus-4-7",
        max_iters=1,
    )


def _trigger_audit_entries(substrate, n: int = 3) -> None:
    """Emit ``n`` :llm/call audit entries against the bootstrap's echo
    handler so the test has a non-empty signed chain to verify."""
    for i in range(n):
        substrate.effect.perform(
            ":llm/call",
            {
                "model": "test-model",
                "messages": [{"role": "user", "content": f"ping-{i}"}],
            },
        )


@pytest.fixture
def _force_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``detect_or_explicit`` to the echo-floor return so the test
    outcome does not depend on the test machine's claude-agent-sdk /
    ANTHROPIC_API_KEY availability (per FD-T1.1)."""
    monkeypatch.setattr(
        coder_main,
        "detect_or_explicit",
        lambda _provider: (None, "echo"),
    )
    monkeypatch.setattr(
        _provider_mod,
        "detect_or_explicit",
        lambda _provider: (None, "echo"),
    )


# ---------------------------------------------------------------------------
# G4.1 — Happy path: env-set → all entries signed, verify_chain True
# ---------------------------------------------------------------------------


def test_env_keyed_signer_signs_audit_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _force_echo: None,
) -> None:
    """G4.1: PERSISTENCE_AUDIT_KEY → every AuditEntry signed.

    Falsifiability: if either kwarg-passthrough layer is missing
    (Substrate.open's audit_signer drop, or canonical_audit_stack's
    signer-forward to make_audit_handler), make_audit_handler receives
    signer=None → entries are unsigned → assert .signature is not None
    fails. This is the regression-pin for the LD-4 wiring chain.
    """
    pem_path, pem_bytes, raw_pub = _generate_pem(tmp_path)
    monkeypatch.setenv(_AUDIT_KEY_ENV, f"file://{pem_path}")
    expected_signer_id = (
        f"ed25519:{hashlib.sha256(pem_bytes).hexdigest()[:16]}"
    )

    args = _make_args()
    substrate, _ = _build_substrate_and_handlers(args)
    try:
        _trigger_audit_entries(substrate, n=3)

        entries = list(substrate._canonical_audit_entries)
        # The audit handler emits one AuditEntry per :llm/call (plus
        # potentially others depending on what the canonical stack
        # wraps). Filter for :llm/call to keep the assertion sharp.
        llm_entries = [e for e in entries if e.op == ":llm/call"]
        assert len(llm_entries) == 3, (
            f"expected 3 :llm/call entries; got {len(llm_entries)}"
        )

        for entry in llm_entries:
            assert entry.signature is not None, (
                "entry.signature is None — LD-4 kwarg-passthrough is "
                "broken (signer didn't reach make_audit_handler)"
            )
            assert entry.signer_id == expected_signer_id, (
                f"signer_id mismatch: expected {expected_signer_id!r}, "
                f"got {entry.signer_id!r}"
            )
            assert entry.signature.startswith("ed25519:"), (
                f"signature has wrong prefix: {entry.signature!r}"
            )

        # End-to-end chain verification with the matching public key.
        assert verify_chain(
            llm_entries,
            public_keys={expected_signer_id: raw_pub},
        ), "verify_chain rejected the signed chain — signature/key mismatch"
    finally:
        substrate.close()


# ---------------------------------------------------------------------------
# G4.2 — env-unset → unsigned entries (pre-2.4a backward compat)
# ---------------------------------------------------------------------------


def test_env_unset_produces_unsigned_entries(
    monkeypatch: pytest.MonkeyPatch,
    _force_echo: None,
) -> None:
    """G4.2: env-unset → entries have signature=None + signer_id=None.

    Backward-compat invariant: pre-2.4a callers that never set the env
    var must observe the same unsigned chain shape.
    """
    monkeypatch.delenv(_AUDIT_KEY_ENV, raising=False)
    assert _load_audit_signer_from_env() is None

    args = _make_args()
    substrate, _ = _build_substrate_and_handlers(args)
    try:
        _trigger_audit_entries(substrate, n=3)

        entries = list(substrate._canonical_audit_entries)
        llm_entries = [e for e in entries if e.op == ":llm/call"]
        assert len(llm_entries) == 3

        for entry in llm_entries:
            assert entry.signature is None, (
                f"unsigned-mode entry has signature {entry.signature!r}"
            )
            assert entry.signer_id is None, (
                f"unsigned-mode entry has signer_id {entry.signer_id!r}"
            )

        # verify_chain with NO public_keys also passes (hash-only check).
        assert verify_chain(llm_entries) is True
    finally:
        substrate.close()


# ---------------------------------------------------------------------------
# G4.3 — Tamper detection: swapped args_hash → verify_chain False
# ---------------------------------------------------------------------------


def test_tamper_breaks_signature_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _force_echo: None,
) -> None:
    """G4.3: tamper an entry's content → verify_chain returns False.

    AuditEntry is frozen, so the tamper synthesizes a NEW entry with
    the original ``id`` + ``signature`` but a contradictory
    ``args_hash``. ``verify_chain`` recomputes the content hash from
    the new fields, observes ``recomputed_id != entry.id``, and returns
    False at the hash check (before the signature check is even
    reached) per the audit.py:455-462 guard. This is the falsifiability
    proof that signed entries cannot be silently mutated post-signing.
    """
    pem_path, pem_bytes, raw_pub = _generate_pem(tmp_path)
    monkeypatch.setenv(_AUDIT_KEY_ENV, f"file://{pem_path}")
    expected_signer_id = (
        f"ed25519:{hashlib.sha256(pem_bytes).hexdigest()[:16]}"
    )

    args = _make_args()
    substrate, _ = _build_substrate_and_handlers(args)
    try:
        _trigger_audit_entries(substrate, n=2)

        entries = list(substrate._canonical_audit_entries)
        llm_entries = [e for e in entries if e.op == ":llm/call"]
        assert len(llm_entries) == 2

        # Pre-condition: the untampered chain verifies.
        assert verify_chain(
            llm_entries,
            public_keys={expected_signer_id: raw_pub},
        )

        # Tamper: swap args_hash on a clone of entry[0]. Frozen
        # dataclass → synthesize a new AuditEntry via .to_dict() round-
        # trip with one field flipped.
        original = llm_entries[0]
        d: dict[str, Any] = original.to_dict()
        # Flip a deterministic byte in args_hash so the recomputed
        # content hash diverges from the signed id.
        old_hash: str = d["args_hash"]
        d["args_hash"] = "sha256:" + ("0" * 64)
        # Reconstruct a frozen entry with the swapped field but the
        # ORIGINAL id + signature + signer_id.
        tampered = AuditEntry(
            id=original.id,
            signature=original.signature,
            signer_id=original.signer_id,
            **{k: v for k, v in d.items() if k not in {"id"}},
        )
        # Sanity: the tamper actually changed something.
        assert tampered.args_hash != old_hash

        tampered_chain = [tampered] + llm_entries[1:]
        # verify_chain returns False — the recomputed content hash for
        # the tampered entry no longer matches the signed id.
        assert verify_chain(
            tampered_chain,
            public_keys={expected_signer_id: raw_pub},
        ) is False, (
            "tampered chain unexpectedly verified — content-hash check "
            "is not catching the swapped args_hash"
        )
    finally:
        substrate.close()


# ---------------------------------------------------------------------------
# G4.4 — Unknown URI scheme → SystemExit
# ---------------------------------------------------------------------------


def test_unknown_uri_scheme_systemexits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G4.4: PERSISTENCE_AUDIT_KEY=pem:abc123 → SystemExit at bootstrap.

    Env-set-but-broken is a hard fail: the operator's intent was clearly
    "sign this run", and silently falling through to unsigned-mode would
    defeat the accountability contract. The scheme name is included in
    the error message so operators can fix the URI.
    """
    monkeypatch.setenv(_AUDIT_KEY_ENV, "pem:abc123")
    with pytest.raises(SystemExit) as exc_info:
        _load_audit_signer_from_env()
    msg = str(exc_info.value)
    assert "pem" in msg, f"error message missing scheme name: {msg!r}"
    assert "file:///" in msg or "file:" in msg, (
        f"error message should hint at the supported scheme: {msg!r}"
    )


def test_missing_pem_file_systemexits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """G4.4 (companion): file:/// pointing at a non-existent path also
    SystemExits — same hard-fail rationale."""
    missing = tmp_path / "does-not-exist.pem"
    monkeypatch.setenv(_AUDIT_KEY_ENV, f"file://{missing}")
    with pytest.raises(SystemExit) as exc_info:
        _load_audit_signer_from_env()
    msg = str(exc_info.value)
    assert "cannot read PEM file" in msg or "PEM" in msg
