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

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from persistence.plan._ast import Node
from persistence.plan._dispatch import Dispatcher
from persistence.plan._walk import walk

__all__ = [
    "ExecutionResult",
    "FailureInfo",
    "LeafResult",
    "TrainingExample",
    "execute",
]


class TrainingExample(TypedDict):
    """Closed-shape training example for `optimize()` / canonicalization.

    Two keys exactly: ``inputs`` (model-facing kwargs dict) and
    ``expected`` (free-form ground truth). Extra keys are rejected at
    canonicalization time so the hash schema cannot drift silently.

    Attributes:
        inputs: Model-facing kwargs as a dict. Must be a dict (not a
            list/string/scalar) because downstream adapters splat it as
            ``**kwargs`` into a DSPy module's ``forward()``.
        expected: Ground-truth value the metric compares against. Free-form
            JSON-serializable (the canonicalizer uses ``json.dumps`` with
            ``allow_nan=False`` so NaN/Inf inside ``expected`` is also
            rejected — same rule as ``inputs``).
    """

    inputs: dict
    expected: Any


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


#: Closed schema for the validator. Both keys are required; nothing else
#: is allowed. Membership comparison is via frozenset for O(1) checks.
_TRAINING_EXAMPLE_KEYS: frozenset[str] = frozenset({"inputs", "expected"})


def _canonicalize_training_set(training_set: list[TrainingExample]) -> str:
    """Deterministic SHA-256 hex of a training set.

    Per design §5 rule (1), the digest is the reproducibility hook for
    paper §6 numeric tables: same examples → same hash → same provenance,
    regardless of run, machine, or caller-supplied ordering. Only
    canonical-JSON bytes feed sha256 — no Python-callable hashing, no
    float drift.

    Steps:

    1. Validate the schema for each example: keys must be exactly
       ``{"inputs", "expected"}``; ``inputs`` must be a ``dict``.
       Violations raise ``ValueError`` with the offending example
       index in the message.
    2. Encode each validated example via the same canonical-JSON
       serializer as ``Node.id`` (``sort_keys=True``,
       ``separators=(',',':')``, ``allow_nan=False``). Sorted keys
       absorb dict-key ordering noise inside ``inputs`` and
       ``expected``; ``allow_nan=False`` rejects NaN/Inf so the
       digest never depends on platform-specific float representations.
    3. Sort the canonicalized example strings lexicographically. Caller
       ordering of the list MUST NOT affect the hash.
    4. Join sorted examples with ``\\n``, utf-8 encode, sha256, return
       hexdigest.

    Args:
        training_set: List of `TrainingExample` dicts. Empty list is
            valid (returns the pinned empty-bytes sha256).

    Returns:
        64-char lowercase hex digest. Empty list returns
        ``"e3b0c44...b7852b855"`` (sha256 of ``b""``).

    Raises:
        ValueError: An example violates the schema (extra/missing key,
            ``inputs`` not a dict, or NaN/Inf in any nested float).
            The message includes the offending example index.
    """
    canonical_examples: list[str] = []

    for index, example in enumerate(training_set):
        # Step 1a: example must be a dict (TypedDict is a dict at runtime,
        # but the hint is structural — defend against accidental list
        # passing through TypedDict's loose type-check.)
        if not isinstance(example, dict):
            raise ValueError(
                f"_canonicalize_training_set: example at index {index} must "
                f"be a dict, got {type(example).__name__}."
            )

        # Step 1b: closed schema. Reject extras AND missing.
        keys = frozenset(example.keys())
        if keys != _TRAINING_EXAMPLE_KEYS:
            extras = keys - _TRAINING_EXAMPLE_KEYS
            missing = _TRAINING_EXAMPLE_KEYS - keys
            problems: list[str] = []
            if missing:
                # Sort for deterministic message — set iteration order
                # leaks into pytest match patterns otherwise.
                problems.append(f"missing keys {sorted(missing)!r}")
            if extras:
                problems.append(f"extra keys {sorted(extras)!r}")
            raise ValueError(
                f"_canonicalize_training_set: example at index {index} has "
                f"{'; '.join(problems)}. Allowed keys: ['expected', 'inputs']."
            )

        # Step 1c: `inputs` must be a dict so adapters can splat it as
        # **kwargs into the DSPy module's forward().
        inputs = example["inputs"]
        if not isinstance(inputs, dict):
            raise ValueError(
                f"_canonicalize_training_set: example at index {index} has "
                f"'inputs' of type {type(inputs).__name__}; required: dict."
            )

        # Step 2: canonical-JSON encode. Same rules as Node.id.
        # allow_nan=False rejects NaN/Inf inside `inputs` OR `expected`
        # (json.dumps walks recursively). The ValueError from json.dumps
        # is re-raised with the example index so callers can locate the
        # offender quickly.
        try:
            encoded = json.dumps(
                {"inputs": inputs, "expected": example["expected"]},
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (ValueError, TypeError) as exc:
            # ValueError from json.dumps with allow_nan=False fires on
            # NaN/Inf. TypeError fires on non-JSON-serializable values
            # (e.g. set, custom class without __json__). We raise
            # ValueError uniformly so callers have one exception class
            # to match.
            raise ValueError(
                f"_canonicalize_training_set: example at index {index} could "
                f"not be canonicalized ({exc}). NaN/Inf and non-JSON types "
                f"are rejected — content-addressing requires reflexive equality."
            ) from exc

        canonical_examples.append(encoded)

    # Step 3: sort canonicalized strings. Caller ordering must not affect
    # the digest. We sort the post-encode strings (not the source dicts)
    # so the comparison key is byte-level deterministic.
    canonical_examples.sort()

    # Step 4: join + sha256. Empty list collapses to b"" — empty join,
    # empty encode, sha256("") = e3b0c44...
    payload = "\n".join(canonical_examples).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
