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


@dataclass(frozen=True, slots=True)
class Node:
    """Immutable plan AST node.

    Fields:
        tag:      keyword-form string like ":seq", ":llm-call" (leading colon required)
        attrs:    attributes map (keyword-keyed strings → arbitrary values)
        children: ordered tuple of child Nodes (possibly empty)

    The :id property is a 16-hex-char sha256 prefix of the canonical form
    (see _canonical_dict + _id_hex). Two Nodes with identical content hash-collide.
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
        # All children must be Node instances
        for i, child in enumerate(self.children):
            if not isinstance(child, Node):
                raise ValueError(
                    f"Node.children[{i}] must be Node, got {type(child).__name__}"
                )

    @property
    def id(self) -> str:
        """16-hex-char sha256 prefix of canonical form.

        Matches persistence.replay._canonical pattern: json.dumps with
        sort_keys=True, separators=(',', ':'). Two Nodes with identical
        content hash-collide — that IS the content-addressing contract.

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
        return digest[:16]


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
