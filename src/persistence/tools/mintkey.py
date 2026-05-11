"""Ed25519 keypair minting CLI for persistence-os.

Phase 7 T1 (FD-MINTKEY-CLI resolution): thin wrapper over
`persistence.effect._signing.generate_keypair()`. Writes the private
key in PEM form to `--out` so it can be referenced as
`PERSISTENCE_AUDIT_KEY=file:///<abs-out-path>` per LD-4 prereqs.

Usage:
    python -m persistence.tools.mintkey --out ~/.persistence/keys/agent.pem
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)


def mint_keypair_to_pem(out_path: Path) -> None:
    """Generate an Ed25519 keypair and write the private key as PEM."""
    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(pem)
    # Restrict permissions (private key)
    out_path.chmod(0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m persistence.tools.mintkey",
        description="Mint an Ed25519 keypair for PERSISTENCE_AUDIT_KEY.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output path for the PEM-encoded private key.",
    )
    args = parser.parse_args(argv)

    out_path = args.out.expanduser().resolve()
    if out_path.exists():
        print(f"refusing to overwrite existing key at {out_path}", file=sys.stderr)
        return 1

    mint_keypair_to_pem(out_path)
    print(f"wrote {out_path}")
    print(f"export PERSISTENCE_AUDIT_KEY=file://{out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
