"""Node AST + canonical form + content-addressed :id."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


def _freeze_attrs(attrs: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Return a read-only view of attrs. Does not deep-freeze values."""
    if attrs is None:
        return MappingProxyType({})
    # Shallow-freeze by wrapping in a new dict then MappingProxyType.
    return MappingProxyType(dict(attrs))


#: sha256 truncation width (hex chars). 32 = 128 bits. Exposed as a module
#: constant so callers can assert against it rather than hard-coding.
ID_HEX_WIDTH: int = 32


@dataclass(frozen=True, slots=True)
class Node:
    """Immutable plan AST node.

    Fields:
        tag:      keyword-form string like ":seq", ":llm-call" (leading colon required)
        attrs:    attributes map (keyword-keyed strings → arbitrary values)
        children: ordered tuple of child Nodes (possibly empty)

    The :id property is a 32-hex-char (128-bit) sha256 prefix of the
    canonical form (see _canonical_dict). Content-addressing contract:

    - :id = sha256(canonical-json(tag, attrs, children-ids-recursive))[:32]
    - Two Nodes with identical content hash-identically; different content
      hashes to a different :id with probability ~(1 - 2^-128) per pair.
    - Birthday-collision probability 1% reached at ~2×10^18 plans; the
      widened width is what lets the paper back the Merkle-DAG claim
      against adversarial inputs without requiring rehash checks.
    - Non-finite floats (NaN, Inf) in attrs are rejected at :id time —
      NaN != NaN would let two content-equal Nodes hash-collide yet compare
      non-equal.
    """

    tag: str
    attrs: Mapping[str, Any] = field(default_factory=dict)
    children: tuple["Node", ...] = ()

    def __post_init__(self) -> None:
        # dataclass(frozen=True) rejects direct assignment; use object.__setattr__
        object.__setattr__(self, "attrs", _freeze_attrs(self.attrs))
        if not isinstance(self.children, tuple):
            object.__setattr__(self, "children", tuple(self.children))
        # Validate tag shape — must be keyword-form string
        if not isinstance(self.tag, str) or not self.tag.startswith(":"):
            raise ValueError(
                f"Node.tag must be keyword-form string like ':seq', got {self.tag!r}"
            )
        # Validate attr keys — plain strings, no leading colon, non-empty,
        # not the reserved content-addressed 'id' key.
        # Content-addressing forbids ambiguity: {":prompt": v} vs {"prompt": v}
        # would hash differently despite meaning the same thing. Non-string keys
        # (int, None, bytes) would canonical-serialize through str() and could
        # likewise collide with their str-equivalents. And 'id' (or its colon-
        # prefixed form ':id') is reserved — Node.id is the computed content
        # address, not an author-supplied attr. The parser strips both forms
        # at parse time (_parse.py::_python_to_node); construction rejects
        # them symmetrically so internal callers cannot bypass the strip.
        for k in self.attrs.keys():
            if not isinstance(k, str):
                raise ValueError(
                    f"Node.attrs keys must be plain strings without leading colon; "
                    f"got {k!r} ({type(k).__name__})"
                )
            if not k:
                raise ValueError(
                    f"Node.attrs keys must be plain strings without leading colon; "
                    f"got empty string"
                )
            if k == "id" or k == ":id":
                raise ValueError(
                    "Node.attrs key 'id' is reserved — Node.id is "
                    "content-addressed and computed, not author-supplied. "
                    "Parser strips both `id` and `:id` at parse time; "
                    "construction rejects them symmetrically."
                )
            if k.startswith(":"):
                raise ValueError(
                    f"Node.attrs keys must be plain strings without leading colon "
                    f"(internal convention); got {k!r}. Strip the ':' at parse time."
                )
        # All children must be Node instances
        for i, child in enumerate(self.children):
            if not isinstance(child, Node):
                raise ValueError(
                    f"Node.children[{i}] must be Node, got {type(child).__name__}"
                )

    @property
    def id(self) -> str:
        """32-hex-char (128-bit) sha256 prefix of canonical form.

        Matches persistence.replay._canonical pattern: json.dumps with
        sort_keys=True, separators=(',', ':'). Two Nodes with identical
        content hash-collide — that IS the content-addressing contract.

        Width: 128 bits (32 hex chars). See ID_HEX_WIDTH. The birthday-
        collision argument: for P(collision) ≤ 1%, N ≤ sqrt(2 * 2^128 * 0.01)
        ≈ 2.6×10^18 plans. Widened from 64-bit (16 hex) in R2 because the
        narrower form only covered ~6×10^8 plans before adversarial
        collision risk — insufficient to back the paper's Merkle-DAG claim.

        Non-finite floats (NaN, Inf, -Inf) in attrs are rejected: NaN
        violates reflexive equality (NaN != NaN), which would make two
        content-equal Nodes hash-collide but compare non-equal,
        invalidating the Merkle DAG claim. Inf/-Inf cannot round-trip
        through strict JSON. We pass ``allow_nan=False`` to ``json.dumps``
        and surface a domain-specific error when it trips.
        """
        try:
            canonical = json.dumps(
                _canonical_dict(self),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except ValueError as exc:
            # json.dumps raises ValueError("Out of range float values ...")
            # on NaN/Inf when allow_nan=False. Re-raise with plan-specific
            # message so callers can match on it.
            raise ValueError(
                "Node.id: non-finite float (NaN/Inf) in attrs is not allowed; "
                "content-addressing requires reflexive equality"
            ) from exc
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return digest[:ID_HEX_WIDTH]


def _canonical_dict(node: Node) -> dict[str, Any]:
    """Convert Node to a dict for canonical hashing.

    Attrs are kept as-is (sorting happens at json.dumps with sort_keys=True).
    Nested Node values in attrs would be a misuse — attrs hold EDN scalars
    and containers only. If a child-shaped value appears in attrs, we leave
    it; canonical serialization will still be deterministic via sort_keys.
    """
    return {
        "tag": node.tag,
        "attrs": dict(node.attrs),
        "children": [_canonical_dict(c) for c in node.children],
    }
