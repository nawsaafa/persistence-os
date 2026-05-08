"""Tamper-evident audit chain — Mimir Phase B demo.

Builds a 3-call signed audit chain via the persistence-os effect runtime,
verifies it with the public key, flips one character of a signature, and
shows ``verify_chain`` returns ``False``.

The point: a third party who holds only the public key can verify what the
agent did, without trusting the runtime that produced the chain.

Run:
    python examples/demo_audit_tamper.py
"""

from __future__ import annotations

from dataclasses import replace

from persistence.effect._signing import fingerprint, generate_keypair
from persistence.effect.handlers.audit import (
    AuditEntry,
    make_audit_handler,
    verify_chain,
)
from persistence.effect.handlers.clock import make_fixed_clock_handler
from persistence.effect.handlers.raw import make_echo_llm_handler
from persistence.effect.runtime import Runtime, perform, with_runtime


def main() -> None:
    priv, pub = generate_keypair()
    signer_id = fingerprint(pub)
    entries: list[AuditEntry] = []

    runtime = Runtime(
        [
            make_echo_llm_handler(),
            make_fixed_clock_handler(ts=1_712_000_000),
            make_audit_handler(
                entries,
                wraps=(":llm/call",),
                signer=(signer_id, priv),
            ),
        ]
    )

    with with_runtime(runtime):
        for i in range(3):
            perform(
                ":llm/call",
                model="m",
                messages=[{"role": "user", "content": f"call-{i}"}],
            )

    print(f"Built signed audit chain of {len(entries)} entries.")
    print(f"Signer: {signer_id}\n")

    assert verify_chain(entries, public_keys={signer_id: pub}) is True
    print("verify_chain(clean)    -> True")

    sig = entries[1].signature
    assert sig is not None
    pos = len("ed25519:") + 5
    flipped = sig[:pos] + ("B" if sig[pos] != "B" else "C") + sig[pos + 1 :]
    entries[1] = replace(entries[1], signature=flipped)
    print(f"\nFlipped one character at position {pos} of entries[1].signature.")

    assert verify_chain(entries, public_keys={signer_id: pub}) is False
    print("verify_chain(tampered) -> False  <- tamper detected")


if __name__ == "__main__":
    main()
