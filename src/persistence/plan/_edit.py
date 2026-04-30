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
    "insert_step_after",
    "insert_step_before",
    "delete_step",
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


def _splice_first(
    node: Node,
    step_id: str,
    splicer: Callable[[tuple[Node, ...], int], tuple[Node, ...]],
) -> tuple[Node, Node | None]:
    """Walk pre-order DFS; on first match of a CHILD whose id == ``step_id``,
    call ``splicer(siblings_tuple, idx)`` to produce the new sibling tuple.

    Used by insert_step_after / insert_step_before / delete_step where
    the operation rewrites the parent's children list rather than the
    matched node itself.

    Returns ``(new_tree, matched_old_node)`` or ``(node, None)`` if no
    match. Note: a root-level match (``node.id == step_id``) is NOT
    found by this walker — root has no parent to splice into. Callers
    surface that as ``StepIdNotFound`` with a "root cannot be a sibling-
    relative target" message.
    """
    # Search children directly first (so the parent gets to splice).
    for idx, child in enumerate(node.children):
        if child.id == step_id:
            new_children = splicer(node.children, idx)
            return _dc_replace(node, children=tuple(new_children)), child

    # No direct child match — recurse.
    new_children_list: list[Node] = []
    matched: Node | None = None
    for child in node.children:
        if matched is None:
            new_child, child_matched = _splice_first(child, step_id, splicer)
            if child_matched is not None:
                matched = child_matched
                new_children_list.append(new_child)
            else:
                new_children_list.append(child)
        else:
            new_children_list.append(child)

    if matched is None:
        return node, None
    return _dc_replace(node, children=tuple(new_children_list)), matched


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


def insert_step_after(
    plan: Node,
    step_id: str,
    new_step: Node,
    *,
    tx: "Transaction",
) -> Node:
    """Insert ``new_step`` immediately after the child step matching ``step_id``.

    The matched step's parent's ``children`` tuple is rebuilt as
    ``(..., matched, new_step, ...)``. ``step_id`` MUST be a non-root
    node — root-level inserts have no defined parent.

    Args:
        plan: the Plan AST root.
        step_id: the 32-hex ``Node.id`` of the step that ``new_step``
            should appear AFTER.
        new_step: the node to insert.
        tx: the active ``Transaction``.

    Returns:
        A new ``Node`` (root) with ``new_step`` spliced in.

    Raises:
        PlanEditOutsideDosync: if called outside a ``dosync`` body.
        StepIdNotFound: if ``step_id`` is missing OR matches the root
            (root has no parent slot for sibling-relative inserts;
            wrap the plan in a ``:seq`` for top-level prepend / append).
    """
    _require_dosync()
    plan_id_before = plan.id

    def _splicer(siblings: tuple[Node, ...], idx: int) -> tuple[Node, ...]:
        return siblings[: idx + 1] + (new_step,) + siblings[idx + 1 :]

    new_tree, matched = _splice_first(plan, step_id, _splicer)
    if matched is None:
        raise StepIdNotFound(
            f"insert_step_after: step_id {step_id!r} not found as a "
            f"non-root node in plan (root={plan_id_before!r}). Root-level "
            f"sibling inserts are undefined (root has no parent); wrap "
            f"the plan in a :seq if you need to prepend / append at the "
            f"top level."
        )
    _ = tx  # unused-arg gate for typecheckers; wired up in C4
    return new_tree


def insert_step_before(
    plan: Node,
    step_id: str,
    new_step: Node,
    *,
    tx: "Transaction",
) -> Node:
    """Insert ``new_step`` immediately before the child step matching ``step_id``.

    Symmetric to :func:`insert_step_after`. Same root-level constraint.
    """
    _require_dosync()
    plan_id_before = plan.id

    def _splicer(siblings: tuple[Node, ...], idx: int) -> tuple[Node, ...]:
        return siblings[:idx] + (new_step,) + siblings[idx:]

    new_tree, matched = _splice_first(plan, step_id, _splicer)
    if matched is None:
        raise StepIdNotFound(
            f"insert_step_before: step_id {step_id!r} not found as a "
            f"non-root node in plan (root={plan_id_before!r}). Root-level "
            f"sibling inserts are undefined (root has no parent); wrap "
            f"the plan in a :seq if you need to prepend / append at the "
            f"top level."
        )
    _ = tx  # unused-arg gate for typecheckers; wired up in C4
    return new_tree


def delete_step(
    plan: Node,
    step_id: str,
    *,
    tx: "Transaction",
) -> Node:
    """Remove the child step matching ``step_id`` from its parent's children.

    Args:
        plan: the Plan AST root.
        step_id: the 32-hex ``Node.id`` of the step to remove.
        tx: the active ``Transaction``.

    Returns:
        A new ``Node`` (root) with the matched step removed.

    Raises:
        PlanEditOutsideDosync: if called outside a ``dosync`` body.
        StepIdNotFound: if ``step_id`` is missing OR matches the root
            (deleting the root has no defined return plan; restructure
            to delete a child of a wrapping ``:seq``).

    Caveat (Phase 2.0a):
        The design § 4.1 line 285 specifies "only allowed if no
        downstream step has executed". That falsifiable check requires
        threading a ``completed_step_ids`` set through the Transaction
        object — a substrate change deferred to a follow-up
        (substrate-backlog #200; see scratch impl plan decision 2).
        Until then, ``delete_step`` is permissive: any step inside a
        dosync may be deleted regardless of whether downstream steps
        have already run. Callers that care MUST validate at the call
        site.
    """
    _require_dosync()
    plan_id_before = plan.id

    # TODO #140 follow-up: downstream-execution check (substrate-backlog #200)
    # Once Transaction tracks completed_step_ids, raise
    # PlanEditDownstreamExecuted here when any downstream step's id is
    # in the completed set. Until then, deletion is unconditional.

    def _splicer(siblings: tuple[Node, ...], idx: int) -> tuple[Node, ...]:
        return siblings[:idx] + siblings[idx + 1 :]

    new_tree, matched = _splice_first(plan, step_id, _splicer)
    if matched is None:
        raise StepIdNotFound(
            f"delete_step: step_id {step_id!r} not found as a non-root "
            f"node in plan (root={plan_id_before!r}). Root-level deletes "
            f"are undefined (no parent to splice from, no defined return "
            f"plan); restructure to delete a child of a wrapping :seq."
        )
    _ = tx  # unused-arg gate for typecheckers; wired up in C4
    return new_tree
