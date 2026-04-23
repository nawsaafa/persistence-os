"""persistence.plan — homoiconic plan AST module (v0.1).

Commits to three claims (see docs/plans/2026-04-23-persistence-plan-v0.1-design.md):
1. Plans are content-addressed Merkle DAGs
2. Parse round-trips byte-identical
3. Spec validation catches malformed plans
"""
from __future__ import annotations

from persistence.plan._ast import Node
from persistence.plan._errors import ParseError, UnimplementedNodeKindError
from persistence.plan._interpret import walk
from persistence.plan._parse import parse, unparse

__all__ = [
    "Node",
    "ParseError",
    "UnimplementedNodeKindError",
    "parse",
    "unparse",
    "walk",
]
