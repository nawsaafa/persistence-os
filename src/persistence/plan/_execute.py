"""Plan AST execution layer (v0.6.0a1).

Wraps `Dispatcher.dispatch` in a typed result envelope so callers can
distinguish ok / failed runs, recover the failed node and exception
context, and consume per-leaf results without losing the
content-addressed `node_id` ↔ result mapping.

Design contract (per `docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md`
§4 + impl plan §2.A1):

* The execution core MUST remain importable without the optional `dspy-ai`
  dependency installed. Nothing in this module — module-level OR
  function-level — may `import dspy`. Optimizer wiring lives in
  `_optimize.py`, which is intentionally NOT imported here.
* Walk-order failure semantics: if a registered handler raises, dispatch
  halts at that node, partial `leaf_results` (everything dispatched
  before the failing node) are preserved, and `status="failed"` with
  `FailureInfo` populated. Successor nodes are NOT executed.
* `UnimplementedNodeKindError` raised by the walker (`:code` / `:branch`
  leaves) is RE-RAISED unchanged — the caller's contract is broken; we
  do not paper over it as a `failed` status.
* No wall-clock APIs (`time.time()`, `time.monotonic()`, etc.) — when
  latency measurement lands, it routes through a `:clock/now` effect
  injected at handler boundary. See repo discipline + `tests/test_wall_clock_ban.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from persistence.plan._ast import Node
from persistence.plan._dispatch import Dispatcher
from persistence.plan._walk import walk

__all__ = [
    "ExecutionResult",
    "FailureInfo",
    "LeafResult",
    "execute",
]


#: Sentinel handler-id when a node's `attrs` dict has no `"handler-id"`
#: key. Plain string, no leading colon (Plan-AST attr-key rule —
#: `_ast.py:99` rejects colon-prefixed keys at construction time).
_DEFAULT_HANDLER_ID: str = "<default>"


@dataclass(frozen=True, slots=True)
class LeafResult:
    """Per-node execution record for one dispatched node.

    Emitted in walk order (parent-before-children DFS) for each node
    whose `Node.tag` had a registered handler in the dispatcher.

    Attributes:
        node_id: 32-hex-char content-addressed id of the dispatched node.
        tag: Node tag (e.g. ``":llm-call"``, ``":tool-call"``).
        handler_id: Identifier of the handler bound to this node. Read
            from ``node.attrs.get("handler-id", "<default>")`` —
            defensive on missing key, since not every plan threads a
            `handler-id` attr (e.g. tests, structural tags like ``:seq``).
        result: The handler's return value (may be ``None``).
    """

    node_id: str
    tag: str
    handler_id: str
    result: Any


@dataclass(frozen=True, slots=True)
class FailureInfo:
    """Failure diagnostic for a halted execution.

    Populated only when ``ExecutionResult.status == "failed"``.

    Attributes:
        failed_node_id: Content-addressed id of the node whose handler raised.
        failed_tag: Tag of the failing node.
        error_class: ``type(exc).__name__`` — class name only, no module.
        error_repr: ``repr(exc)`` — NOT ``str(exc)``. Repr encodes the
            exception class + args in a form intended to be unambiguous;
            ``str(exc)`` typically drops the class. Reproduction-ready.
    """

    failed_node_id: str
    failed_tag: str
    error_class: str
    error_repr: str


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Result envelope for one ``execute()`` call.

    Attributes:
        plan_id: Content-addressed id of the root Node (i.e. ``plan.id``).
        status: ``"ok"`` if every dispatched handler returned without
            raising; ``"failed"`` if any handler raised. Walker-level
            errors (``UnimplementedNodeKindError``) RE-RAISE rather
            than producing a ``"failed"`` result.
        leaf_results: Tuple (frozen) of per-leaf results in walk order.
            Length equals the number of nodes whose tag had a registered
            handler AND were reached before any halt. On failure, this
            captures the partial trace up to (but not including) the
            failing node.
        failure: ``FailureInfo`` if and only if ``status == "failed"``.
    """

    plan_id: str
    status: Literal["ok", "failed"]
    leaf_results: tuple[LeafResult, ...]
    failure: FailureInfo | None


def execute(
    plan: Node,
    dispatcher: Dispatcher,
    *,
    env: dict | None = None,
) -> ExecutionResult:
    """Execute ``plan`` through ``dispatcher``, returning a typed envelope.

    Walks ``plan`` in DFS parent-before-children order via
    :func:`persistence.plan._walk.walk`. For each visited node whose tag
    has a registered handler, calls the handler with ``(node, env)`` and
    records a :class:`LeafResult`. Nodes with no registered handler
    are skipped silently — same contract as ``Dispatcher.dispatch``.

    Args:
        plan: Root Node of the plan AST to execute.
        dispatcher: Handler registry. Re-uses the v0.4 dispatcher seam.
        env: Shared environment passed to every handler. Defaults to a
            fresh empty dict per call (no shared mutable default — every
            ``env=None`` call gets its own dict).

    Returns:
        :class:`ExecutionResult` with ``status`` and ``leaf_results``
        populated. ``failure`` is non-None iff ``status == "failed"``.

    Raises:
        UnimplementedNodeKindError: The walker hit ``:code`` or
            ``:branch`` in leaf position (not yet supported in v0.6).
            This is the caller's contract being broken — we re-raise
            unchanged rather than swallowing into ``failed``.
    """
    if env is None:
        env = {}

    leaf_results: list[LeafResult] = []
    failure: FailureInfo | None = None

    def _visitor(node: Node, _path: tuple[str, ...]) -> None:
        # Capture-then-record: `failure` is set on first handler raise
        # and read on every subsequent visit to short-circuit. We can't
        # `return` from the visitor to abort the walk (walk has no abort
        # hook), so we stash the failure and let subsequent calls bail.
        nonlocal failure
        if failure is not None:
            return
        # We invoke the handler ourselves rather than going through
        # `dispatch()`: per-node failure capture (which node failed +
        # partial leaf_results) requires wrapping the call in a
        # `try/except`, which `dispatch()` does not do.
        handler = dispatcher.get_handler(node.tag)
        if handler is None:
            return
        try:
            result = handler(node, env)
        except Exception as exc:  # noqa: BLE001 — capture-then-record contract
            # Narrow to `Exception` (NOT `BaseException`): KeyboardInterrupt,
            # SystemExit, GeneratorExit are control-flow signals, not
            # handler failures. They propagate out of execute() unchanged.
            failure = FailureInfo(
                failed_node_id=node.id,
                failed_tag=node.tag,
                error_class=type(exc).__name__,
                error_repr=repr(exc),
            )
            return
        handler_id = node.attrs.get("handler-id", _DEFAULT_HANDLER_ID)
        leaf_results.append(
            LeafResult(
                node_id=node.id,
                tag=node.tag,
                handler_id=handler_id,
                result=result,
            )
        )

    # walk() may raise UnimplementedNodeKindError — re-raise unchanged
    # per the design contract.
    walk(plan, visitor=_visitor)

    status: Literal["ok", "failed"] = "failed" if failure is not None else "ok"
    return ExecutionResult(
        plan_id=plan.id,
        status=status,
        leaf_results=tuple(leaf_results),
        failure=failure,
    )
