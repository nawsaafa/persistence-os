# src/persistence/http/blob_store.py
"""Filesystem content-addressed blob store (Phase 2.1c, Design §6).

Atomic writes via os.link from a temp file (POSIX). First-writer-wins on
the actual byte write; duplicates are detected via os.path.exists pre-write.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class BlobStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _hex_to_path(self, hex_digest: str) -> Path:
        return self.root / hex_digest[:2] / hex_digest[2:]

    def put(self, content: bytes) -> tuple[str, int, bool]:
        """Returns (hash, size_bytes, duplicate)."""
        hex_digest = hashlib.sha256(content).hexdigest()
        target = self._hex_to_path(hex_digest)
        if target.exists():
            return f"sha256:{hex_digest}", len(content), True
        target.parent.mkdir(parents=True, exist_ok=True)
        # write to temp file in same dir, then os.link for atomicity
        with tempfile.NamedTemporaryFile(
            dir=target.parent, delete=False, prefix=".tmp-",
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            os.link(tmp_path, target)
            return f"sha256:{hex_digest}", len(content), False
        except FileExistsError:
            # concurrent write won the race
            return f"sha256:{hex_digest}", len(content), True
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass

    def get(self, hash_str: str) -> Optional[bytes]:
        if not _HASH_PATTERN.match(hash_str):
            raise ValueError(f"malformed hash: {hash_str!r}")
        hex_digest = hash_str[len("sha256:"):]
        path = self._hex_to_path(hex_digest)
        if not path.exists():
            return None
        return path.read_bytes()
