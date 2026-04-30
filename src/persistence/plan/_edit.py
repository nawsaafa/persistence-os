"""Plan Edit API — in-flight Plan mutation under transaction (#140).

See ``docs/plans/2026-04-30-phase-2-persistence-coder-design.md`` § 4.1
+ ADR-6 + § 3.7 replay-table row ``:plan/edit`` for the design ground
truth, and ``docs/plans/2026-04-30-phase-2.0a-plan-edit-impl.md`` for
the impl decisions.

## Public surface (incremental — see commit history)

C2 (this commit) lands :func:`edit_step` and the dosync-gate helper.
C3 adds insert/delete. C4 wires the ``:plan/edit`` audit datom into
each op via ``tx.effect()``.

## Identity contract — `step_id` is `Node.id`

Every public fn here takes a ``step_id: str`` parameter that is the
32-hex content-address (``Node.id``) of the target step. Identifying
steps by content-address aligns with ADR-6's
``(plan_id, step_id, before_op_hash, after_op_hash, txn_id)`` audit
datom shape — every key is a content-hash.

**Caveat:** when a Plan AST contains two content-identical subtrees,
both share a ``Node.id``. The edit ops then target the **first
occurrence in pre-order DFS walk order**.
"""
from __future__ import annotations

from dataclasses import replace as _dc_replace
from typing import TYPE_CHECKING, Callable

from persistence.plan._ast import Node
from persistence.plan._errors import (
    PlanEditOutsideDosync,
    StepIdNotFound,
)
from persistence.txn.intents import is_in_dosync

if TYPE_CHECKING:
    from persistence.txn.transaction import Transaction


__all__ = [
    "edit_step",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_dosync() -> None:
    """Raise PlanEditOutsideDosync if the current context is not in dosync."""
    if not is_in_dosync():
        raise PlanEditOutsideDosync(
            "Plan edit ops (edit_step / insert_step_after / "
            "insert_step_before / delete_step) must run inside a "
            "db.dosync(...) body. Plan edits without an enclosing txn "
            "would skip the :plan/edit audit datom and break "
            "byte-identity replay (ADR-6)."
        )


def _replace_first(
    node: Node,
    step_id: str,
    builder: Callable[[Node], Node],
) -> tuple[Node, Node | None]:
    """Walk pre-order DFS; on first match of ``step_id`` apply ``builder(matched)``
    and return ``(new_tree, matched_old_node)``. If no match found, returns
    ``(node, None)``.

    Pre-order = root checked first. This matches the natural reading
    order of a Plan tree.
    """
    # Root match check first (pre-order semantics).
    if node.id == step_id:
        return builder(node), node

    # Recurse over children, replacing the first child subtree that
    # contains a match. Frozen dataclass means we always rebuild via
    # dataclasses.replace.
    new_children: list[Node] = []
    matched: Node | None = None
    for child in node.children:
        if matched is None:
            new_child, child_matched = _replace_first(child, step_id, builder)
            if child_matched is not None:
                matched = child_matched
                new_children.append(new_child)
            else:
                new_children.append(child)
        else:
            new_children.append(child)

    if matched is None:
        return node, None
    return _dc_replace(node, children=tuple(new_children)), matched


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def edit_step(
    plan: Node,
    step_id: str,
    new_op: Node,
    *,
    tx: "Transaction",
) -> Node:
    """Replace the subtree rooted at ``step_id`` with ``new_op``.

    Args:
        plan: the Plan AST root (immutable; not modified).
        step_id: the 32-hex ``Node.id`` of the target step.
        new_op: the replacement subtree.
        tx: the active ``Transaction`` (from the enclosing ``dosync``
            body). Used in C4 to queue the ``:plan/edit`` audit datom.

    Returns:
        A new ``Node`` (root) with the matched step replaced. The
        original ``plan`` is unchanged (frozen dataclass invariant).

    Raises:
        PlanEditOutsideDosync: if called outside a ``dosync`` body.
        StepIdNotFound: if ``step_id`` does not match any node in the
            plan under pre-order DFS walk.
    """
    _require_dosync()
    plan_id_before = plan.id
    new_tree, matched = _replace_first(plan, step_id, lambda _old: new_op)
    if matched is None:
        raise StepIdNotFound(
            f"edit_step: step_id {step_id!r} not found in plan "
            f"(root={plan_id_before!r}). Identity is content-address "
            f"(Node.id, 32-hex). See _edit.py module docstring for the "
            f"duplicate-subtree caveat."
        )
    # Audit emission lands in C4 — this commit is functional shape only.
    _ = tx  # unused-arg gate for typecheckers; wired up in C4
    return new_tree
