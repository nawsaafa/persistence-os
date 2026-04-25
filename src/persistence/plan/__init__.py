"""persistence.plan — homoiconic plan AST module (v0.3.0a1).

Commits to three claims (see docs/plans/2026-04-23-persistence-plan-v0.1-design.md):
1. Plans are content-addressed Merkle DAGs
2. Parse round-trips byte-identical
3. Spec validation catches malformed plans

v0.3.0a1 closes R3-M4 (deferred from v0.2.0a1 ARIS gate): a coercion
registry that lets authors put ``datetime``, ``Decimal``, ``UUID``,
``bytes``, ``frozenset``, and EDN symbols in attrs without breaking
``Node.id``. See docs/plans/2026-04-24-r3-m4-coercion-registry-design.md.
"""
from __future__ import annotations

from persistence.plan._ast import (
    ID_HEX_WIDTH,
    PLAN_CANONICAL_VERSION,
    Node,
)
from persistence.plan._coerce import (
    Coercion,
    lookup_coercion,
    register_coercion,
    unregister_coercion,
)
from persistence.plan._errors import ParseError, UnimplementedNodeKindError
from persistence.plan._interpret import walk
from persistence.plan._parse import parse, unparse

__all__ = [
    "Coercion",
    "ID_HEX_WIDTH",
    "Node",
    "ParseError",
    "PLAN_CANONICAL_VERSION",
    "UnimplementedNodeKindError",
    "lookup_coercion",
    "parse",
    "register_coercion",
    "unparse",
    "unregister_coercion",
    "walk",
]
