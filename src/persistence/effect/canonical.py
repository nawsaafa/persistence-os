"""Canonical JSON — deterministic, sorted-key, stable hash keys.

Used by audit (Merkle chain), cache (args-hash), and any other place that
needs a content-addressable representation of a value. Rejects non-JSON
types (sets, bytes, dataclasses) so the hash is never silently lossy.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_dumps(value: Any) -> str:
    """Serialize ``value`` to canonical JSON.

    - Keys are sorted at every nesting level.
    - No whitespace (compact separators).
    - NaN/Infinity rejected (allow_nan=False).
    - Non-JSON types raise TypeError (we refuse to hash what we cannot
      losslessly round-trip).
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    """Return ``"sha256:<hex>"`` of the canonical UTF-8 encoding of ``value``."""
    payload = canonical_dumps(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()
