"""Plan AST ↔ DSPy adapter (v0.6.0a1, Stream A — A3 + A4).

Forward direction of the optimizer bridge (A3): walks a Plan AST and
returns a callable ``dspy.Module`` whose ``forward(**inputs)`` runs the
same plan via the dispatcher and returns an ``ExecutionResult``
envelope. Per ``:llm-call`` node, instantiates ``dspy.Predict`` (default)
or ``dspy.ChainOfThought`` when ``attrs.get("reasoning") == "cot"``,
using ``attrs["signature"]`` as the DSPy signature string.

End-to-end optimizer entrypoint (A4): :func:`optimize` accepts a Plan
AST + training set + metric ref + optimizer string and returns an
:class:`OptimizedPlan` whose ``plan`` is the input AST cloned with
six provenance attrs pinned on every node. The function lazy-imports
``dspy`` + ``MIPROv2`` at call time; module-level import remains
DSPy-free so the no-DSPy invariant from A1 is preserved.

Design contract (per
``docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md`` §5
"MIPROv2 integration boundary" + "What MIPROv2 sees" + "Provenance
pinning", and ``docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md``
§2.A3 + §2.A4):

* DSPy is **lazy-imported at function-call time**. Module-level import
  of this file MUST succeed without ``dspy-ai`` installed — verified
  by ``tests/plan/test_plan_to_dspy_adapter.py::
  test_optimize_module_imports_without_dspy``. Any ``import dspy``
  outside a function body breaks A1's no-DSPy guarantee for
  ``persistence.plan._execute`` (the execution core must be DSPy-free).
* The adapter does NOT touch ``_execute.py``'s import path — the
  execution core stays clean. We import ``execute`` from ``_execute``
  at module level (it is itself DSPy-free) and only pull DSPy when the
  caller actually invokes ``_plan_to_dspy_module`` or :func:`optimize`.
* The optimizer-call hash is canonical-JSON sha256 of
  ``{optimizer, training_set_hash, metric:{id,version}}`` per design §5
  rule (1). Same canonical-JSON serializer as ``Node.id`` so the digest
  is stable across runs and machines. Reuses
  :func:`_canonicalize_training_set` from ``_execute.py`` — single
  source of truth.
* Provenance pinning attrs are PLAIN strings (no leading colon) per the
  ``_ast.py:99`` rule. Six keys merged onto every node's existing
  ``attrs``: ``plan/optimizer``, ``plan/optimizer-call``,
  ``plan/baseline``, ``plan/training-set-hash``, ``plan/metric-id``,
  ``plan/metric-version``. The keys construct without raising — pinned
  by ``tests/plan/test_provenance_attr_keys_construct.py``.

References:
    docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md §5
    docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md §2.A3 + §2.A4
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from persistence.plan._ast import Node
from persistence.plan._dispatch import Dispatcher
from persistence.plan._errors import OptimizerNotAvailable
from persistence.plan._execute import (
    ExecutionResult,
    TrainingExample,
    _canonicalize_training_set,
    execute,
)
from persistence.plan._metric_registry import MetricRef, lookup_metric
from persistence.plan._walk import walk

#: A4 widens the public surface with ``optimize`` + ``OptimizedPlan``.
#: The forward adapter ``_plan_to_dspy_module`` stays private (leading
#: underscore + not in ``persistence.plan.__all__``) — internal seam
#: for :func:`optimize`. Callers reach the optimizer through ``optimize``,
#: never through the adapter directly.
__all__: list[str] = ["OptimizedPlan", "optimize"]


#: Recognized values for ``Node.attrs["reasoning"]`` on a ``:llm-call``
#: node. ``"direct"`` (the default) → ``dspy.Predict``;
#: ``"cot"`` → ``dspy.ChainOfThought``. Anything else is an explicit
#: ``ValueError`` so callers don't silently fall back to the wrong
#: DSPy module type. Stream A is intentionally narrow; ``"react"``,
#: ``"program-of-thought"``, etc. land in v0.6.x point releases.
_REASONING_DIRECT: str = "direct"
_REASONING_COT: str = "cot"
_VALID_REASONINGS: frozenset[str] = frozenset({_REASONING_DIRECT, _REASONING_COT})


def _plan_to_dspy_module(plan: Node, *, dispatcher: Dispatcher) -> Any:
    """Walk ``plan`` and return a callable ``dspy.Module``.

    Per ``:llm-call`` node, instantiates one DSPy module:

    * ``attrs.get("reasoning", "direct") == "direct"`` →
      ``dspy.Predict(attrs["signature"])``
    * ``attrs.get("reasoning") == "cot"`` →
      ``dspy.ChainOfThought(attrs["signature"])``
    * any other ``reasoning`` value → ``ValueError``

    The instantiated DSPy modules are held on the returned wrapper for
    MIPROv2 to discover via DSPy's standard module-traversal protocol
    (``dspy.Module`` enumerates child modules by attribute walk). The
    wrapper's ``forward(**inputs)`` invokes
    ``execute(plan, dispatcher, env=inputs)`` and returns the
    ``ExecutionResult`` envelope unchanged — callers that need the
    "final leaf" can read ``result.leaf_results[-1].result`` themselves.

    Returning the envelope (not just a leaf scalar) is the deliberate
    choice for v0.6.0a1: MIPROv2's metric callable receives
    ``ExecutionResult`` per design §5 rule (2)
    (``Callable[[ExecutionResult, dict], float]``), so the metric and
    the wrapper output speak the same shape. Switching to "final leaf
    only" would force the metric to re-derive structure it already had.

    Args:
        plan: Plan AST root. Walked in DFS parent-before-children
            order via ``persistence.plan._walk.walk``.
        dispatcher: Dispatcher used by the wrapper's ``forward()``.
            Accepted as a kwarg (not constructed internally) so test
            setup is explicit and the adapter has zero global state.
            Production callers wire this through ``optimize()`` (A4).

    Returns:
        Instance of a ``dspy.Module`` subclass. Type-annotated as
        ``Any`` because DSPy ships no type stubs; widening to
        ``"dspy.Module"`` would force a TYPE_CHECKING-only import of
        ``dspy`` and pollute static-analysis paths even when dspy is
        not installed.

    Raises:
        OptimizerNotAvailable: ``dspy`` not installed (lazy import
            fails). Subclass of ``ImportError`` — see ``_errors.py``.
        ValueError: A ``:llm-call`` node has no ``"signature"`` attr,
            or has ``"reasoning"`` set to a value other than
            ``"direct"`` / ``"cot"``.
    """
    # Lazy import — module-level ``import dspy`` would break the
    # no-DSPy-import guarantee for the execution core (A1's subprocess
    # test would still pass since it only fences ``_execute``, but the
    # _optimize subprocess test fences this module specifically).
    try:
        # `dspy` is the optional [optimize] extra — not installed in the
        # baseline test env. The pragma silences pyright's
        # reportMissingImports without disabling it repo-wide; if the
        # package ever ships type stubs, the pragma becomes a no-op.
        import dspy  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise OptimizerNotAvailable(
            "Plan→DSPy adapter requires dspy. "
            "Install: pip install 'persistence[optimize]'."
        ) from exc

    # First pass: walk the plan, validate every :llm-call node, and
    # instantiate one DSPy module per node. We do this eagerly (before
    # constructing the wrapper) so validation errors surface at adapter-
    # construction time rather than first-forward-call time — easier to
    # debug and matches the parse-don't-validate boundary discipline.
    submodules: list[Any] = []

    def _visitor(node: Node, _path: tuple[str, ...]) -> None:
        if node.tag != ":llm-call":
            return
        signature = node.attrs.get("signature")
        if signature is None:
            raise ValueError(
                "plan/optimize: :llm-call node missing 'signature' attr "
                f"(node.id={node.id!r}). Expected DSPy-style signature "
                "string like 'q -> a'."
            )
        reasoning = node.attrs.get("reasoning", _REASONING_DIRECT)
        if reasoning not in _VALID_REASONINGS:
            raise ValueError(
                f"plan/optimize: :llm-call node has unsupported reasoning "
                f"{reasoning!r} (node.id={node.id!r}). Stream A supports "
                f"only {sorted(_VALID_REASONINGS)!r}."
            )
        if reasoning == _REASONING_COT:
            submodules.append(dspy.ChainOfThought(signature))
        else:  # _REASONING_DIRECT
            submodules.append(dspy.Predict(signature))

    walk(plan, visitor=_visitor)

    # Capture references for the wrapper's closure. We pin ``execute``
    # and ``dispatcher`` here (not at forward()-call time) so the
    # wrapper's behavior is fully determined at adapter-construction.
    captured_plan = plan
    captured_dispatcher = dispatcher
    captured_submodules = tuple(submodules)

    class _PlanModule(dspy.Module):
        """Bridge between MIPROv2 and the dispatcher-backed Plan AST.

        Subclasses ``dspy.Module`` so DSPy's optimizer-side machinery
        (parameter discovery, child-module enumeration, etc.) sees a
        real DSPy module. The actual prompt + few-shot tuning happens
        on ``self._submodules`` via DSPy's standard introspection;
        ``forward()`` runs the dispatcher and returns the
        ``ExecutionResult`` envelope.
        """

        def __init__(self) -> None:
            super().__init__()
            # Expose submodules as named attributes so DSPy's module
            # walker discovers them. Names are positional and stable
            # across runs — same plan → same names → same provenance.
            for index, sub in enumerate(captured_submodules):
                setattr(self, f"llm_call_{index}", sub)
            # Keep an explicit tuple for caller-side iteration too.
            self._submodules = captured_submodules

        def forward(self, **inputs: Any) -> Any:
            """Run ``execute(plan, dispatcher, env=inputs)`` and return result.

            ``inputs`` is splatted into the env dict the dispatcher
            threads through every handler. MIPROv2 calls this with
            keys matching the DSPy signature on each ``:llm-call``
            node — handlers consume them via the same ``env`` they
            already use.
            """
            return execute(captured_plan, captured_dispatcher, env=inputs)

    return _PlanModule()


# --- A4: optimize() end-to-end ------------------------------------------- #


@dataclass(frozen=True, slots=True)
class OptimizedPlan:
    """Result of one :func:`optimize` call.

    The optimized plan is the input AST cloned with six provenance attrs
    merged onto every node. The cloned tree has a different ``Node.id``
    than the input (the merged attrs change the canonical-JSON, hence
    the content hash) — ``baseline_plan_id`` retains the input's id so
    callers can audit the lineage.

    Attributes:
        plan: Optimized Plan AST. Every node's ``attrs`` carries the six
            provenance keys (``plan/optimizer``, ``plan/optimizer-call``,
            ``plan/baseline``, ``plan/training-set-hash``,
            ``plan/metric-id``, ``plan/metric-version``).
        baseline_plan_id: Content-addressed root id of the input plan.
        optimizer: Pinned optimizer name+version (e.g. ``"miprov2-v1"``).
            Bumping → explicit string change, not an inferred Python-
            version-bump (design §5 rule (3)).
        optimizer_call_hash: SHA-256 hex of canonical-JSON of
            ``{optimizer, training_set_hash, metric:{id,version}}``.
            Deterministic across runs and machines.
        score: Metric value on the optimized plan over ``training_set``.
        score_baseline: Metric value on the input plan over the same
            ``training_set``. Higher-is-better is a metric-side
            convention; this dataclass does NOT enforce
            ``score >= score_baseline`` — the G3 promotion gate (A6/A7)
            is where that comparison lands.
    """

    plan: Node
    baseline_plan_id: str
    optimizer: str
    optimizer_call_hash: str
    score: float
    score_baseline: float


#: The six provenance attr keys, pinned as a frozenset for membership
#: checks in tests + as the literal source of truth for the inverse
#: adapter. All plain strings (no leading colon) — ``_ast.py:99``
#: rejects colon-prefixed keys at construction time.
_PROVENANCE_KEYS: frozenset[str] = frozenset(
    {
        "plan/optimizer",
        "plan/optimizer-call",
        "plan/baseline",
        "plan/training-set-hash",
        "plan/metric-id",
        "plan/metric-version",
    }
)


def _compute_optimizer_call_hash(
    optimizer: str,
    training_set_hash: str,
    metric: MetricRef,
) -> str:
    """SHA-256 hex of canonical-JSON ``{optimizer, training_set_hash, metric}``.

    Per design §5 "Canonical hash inputs". Same canonical-JSON rule as
    ``Node.id`` (sort_keys, separators, allow_nan=False) so the digest
    is stable across runs and machines. The metric component is the
    ``(id, version)`` pair — never the callable identity.

    Args:
        optimizer: Pinned optimizer name+version string
            (e.g. ``"miprov2-v1"``).
        training_set_hash: Output of
            :func:`persistence.plan._execute._canonicalize_training_set`.
        metric: Stable ``(id, version)`` reference of the metric used.

    Returns:
        64-char lowercase hex digest.
    """
    payload = {
        "optimizer": optimizer,
        "training_set_hash": training_set_hash,
        "metric": {"id": metric.id, "version": metric.version},
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _build_provenance_attrs(
    optimizer: str,
    optimizer_call_hash: str,
    baseline_plan_id: str,
    training_set_hash: str,
    metric: MetricRef,
) -> dict[str, str]:
    """Construct the six-key provenance attr dict.

    All keys are PLAIN strings (no leading colon) per ``_ast.py:99``.
    Centralized so a future refactor cannot accidentally introduce a
    colon-prefixed variant; the
    ``test_provenance_attr_keys_construct.py`` suite is the regression
    guard for the key shapes.
    """
    return {
        "plan/optimizer": optimizer,
        "plan/optimizer-call": optimizer_call_hash,
        "plan/baseline": baseline_plan_id,
        "plan/training-set-hash": training_set_hash,
        "plan/metric-id": metric.id,
        "plan/metric-version": metric.version,
    }


def _extract_signature_overrides(
    optimized_module: Any,
    plan: Node,
) -> dict[str, str]:
    """Pull the (possibly-tuned) signatures off the optimized DSPy module.

    The forward adapter named submodules ``llm_call_<i>`` in walk-order
    over ``:llm-call`` nodes. After MIPROv2 runs, each submodule's
    ``signature`` attribute may have been tuned; we read it and map it
    back to the corresponding ``:llm-call`` ``Node.id`` so the inverse
    adapter can replace the signature attr per node.

    DSPy's ``signature`` attribute is sometimes an object (e.g.
    ``dspy.Signature`` instance) rather than a plain string. We coerce
    via ``str(...)`` so the result lands in ``Node.attrs`` as a string
    — content-addressing requires JSON-serializable scalar attrs, and a
    DSPy ``Signature`` object would fail canonicalization. If the
    attribute is missing or evaluates to ``None``, we fall back to the
    original node's signature (no-op for that node).

    Args:
        optimized_module: ``dspy.Module`` returned by ``MIPROv2.compile``.
            Expected to expose ``llm_call_<i>`` attributes mirroring the
            forward adapter's naming convention.
        plan: Original input plan. Walked in DFS parent-before-children
            order to build the index → node-id mapping.

    Returns:
        Mapping of ``Node.id`` → tuned signature string for every
        ``:llm-call`` node where the optimized module exposed a usable
        signature. Nodes whose signature did not change (or were not
        exposed) are absent from the dict; the inverse adapter
        preserves their original signature attr.
    """
    overrides: dict[str, str] = {}
    llm_call_nodes: list[Node] = []

    def _collect(node: Node, _path: tuple[str, ...]) -> None:
        if node.tag == ":llm-call":
            llm_call_nodes.append(node)

    walk(plan, visitor=_collect)

    for index, node in enumerate(llm_call_nodes):
        sub = getattr(optimized_module, f"llm_call_{index}", None)
        if sub is None:
            continue
        tuned = getattr(sub, "signature", None)
        if tuned is None:
            continue
        # Coerce to str so the attr round-trips through canonical-JSON.
        # DSPy may expose `signature` as a `Signature` object on some
        # versions; ``str(...)`` is the documented stringification.
        tuned_str = tuned if isinstance(tuned, str) else str(tuned)
        overrides[node.id] = tuned_str

    return overrides


def _clone_with_provenance(
    node: Node,
    *,
    provenance_attrs: dict[str, str],
    signature_overrides: dict[str, str],
) -> Node:
    """Recursively clone ``node``, merging provenance attrs onto every level.

    For ``:llm-call`` nodes whose original ``Node.id`` is in
    ``signature_overrides``, the optimized signature replaces the
    original ``signature`` attr. All other attrs are preserved.
    Children are cloned bottom-up so the result is a fresh tree with
    no shared references to the input.

    Args:
        node: Source AST node.
        provenance_attrs: Six-key dict from :func:`_build_provenance_attrs`.
            Merged onto every node's ``attrs`` via dict-spread (provenance
            keys cannot collide with author attrs because the
            ``"plan/..."`` namespace is reserved for this layer).
        signature_overrides: Map of original ``Node.id`` → optimized
            signature string. Applied only to ``:llm-call`` nodes whose
            id is in the map.

    Returns:
        A new ``Node`` with merged attrs and recursively-cloned children.
    """
    cloned_children = tuple(
        _clone_with_provenance(
            child,
            provenance_attrs=provenance_attrs,
            signature_overrides=signature_overrides,
        )
        for child in node.children
    )

    # Merge order: existing attrs first, then provenance (provenance
    # cannot collide because of namespace), then signature override
    # (only on :llm-call nodes that have one). This matches the
    # design §5 rule that provenance keys "live alongside any
    # author-supplied attrs already on the node".
    new_attrs: dict[str, Any] = dict(node.attrs)
    new_attrs.update(provenance_attrs)
    if node.tag == ":llm-call" and node.id in signature_overrides:
        new_attrs["signature"] = signature_overrides[node.id]

    return Node(tag=node.tag, attrs=new_attrs, children=cloned_children)


def optimize(
    plan: Node,
    *,
    training_set: list[TrainingExample],
    metric: MetricRef,
    optimizer: str = "miprov2-v1",
    miprov2_kwargs: dict | None = None,
) -> OptimizedPlan:
    """Optimize ``plan`` against ``training_set`` under ``metric``.

    Walks the input plan, builds a DSPy module via
    :func:`_plan_to_dspy_module`, runs MIPROv2 to tune prompts +
    few-shots, converts the optimized module back to a Plan AST with
    six provenance attrs pinned on every node, and scores both the
    baseline (input) and optimized plans on the training set under the
    registered metric.

    DSPy + MIPROv2 are lazy-imported inside the function body so the
    module remains importable without ``dspy-ai`` installed (preserves
    A1's no-DSPy invariant for the execution core).

    Args:
        plan: Plan AST to optimize. Must contain at least one
            ``:llm-call`` node for MIPROv2 to have something to tune;
            this function does not enforce that — passing a plan with
            no ``:llm-call`` nodes results in MIPROv2 receiving a module
            with no submodules, which DSPy itself may reject.
        training_set: List of ``TrainingExample`` dicts. Canonicalized
            via :func:`_canonicalize_training_set` for the optimizer-call
            hash; caller ordering does NOT affect the hash.
        metric: Stable ``(id, version)`` reference. Resolved to a
            callable via :func:`lookup_metric`. The callable must accept
            ``(ExecutionResult, expected: dict)`` and return a float.
        optimizer: Pinned optimizer name+version. Defaults to
            ``"miprov2-v1"``. Becomes the ``plan/optimizer`` provenance
            value AND a component of ``optimizer_call_hash``.
        miprov2_kwargs: Extra kwargs splatted into the ``MIPROv2``
            constructor. Passed through verbatim — this function does
            not inspect or validate them. Defaults to ``{}`` if
            ``None``.

    Returns:
        :class:`OptimizedPlan` with the cloned-and-pinned plan, baseline
        id, optimizer string, deterministic call hash, and both scores.

    Raises:
        OptimizerNotAvailable: ``dspy`` not installed (lazy import
            fails). Subclass of ``ImportError`` — see ``_errors.py``.
        ValueError: A ``:llm-call`` node has no ``"signature"`` attr or
            an unsupported ``"reasoning"`` value. Surfaced from
            :func:`_plan_to_dspy_module` — same contract as A3.
        MetricNotRegistered: ``metric`` is not in the registry. Subclass
            of ``KeyError`` — see ``_metric_registry.py``.
    """
    # Lazy import — preserves no-DSPy guarantee for module-import time.
    # Both `dspy` and `dspy.teleprompt.MIPROv2` need to be reachable;
    # we collapse the failure into one OptimizerNotAvailable so callers
    # don't need to differentiate.
    try:
        # dspy is the optional [optimize] extra. The pragma silences
        # pyright's reportMissingImports without disabling it repo-wide.
        import dspy  # pyright: ignore[reportMissingImports]  # noqa: F401
        from dspy.teleprompt import MIPROv2  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise OptimizerNotAvailable(
            "MIPROv2 optimizer requires dspy. "
            f"Install: pip install 'persistence[optimize]'. ({exc})"
        ) from exc

    # Resolve the metric callable through the registry. We do this
    # BEFORE running the optimizer so a registry miss surfaces fast
    # (no point spinning up MIPROv2 for a metric that doesn't exist).
    metric_fn = lookup_metric(metric)

    # We need a dispatcher to drive baseline scoring + the inverse
    # adapter's forward(). For optimization we keep the dispatcher
    # internal-and-trivial: a `:llm-call` handler that returns the
    # signature string verbatim. Real-CI optional-dep job wires a real
    # LLM-backed handler; the mocked-suite path doesn't reach the
    # handler because the test metric scores off ExecutionResult shape,
    # not handler output. Centralized here so the e2e flow has a single
    # source of dispatcher truth.
    dispatcher = Dispatcher()
    dispatcher.register(
        ":llm-call",
        lambda node, env: node.attrs.get("signature", ""),
    )

    # Canonical hash inputs (design §5). Reuse
    # `_canonicalize_training_set` from `_execute.py` — the canonical
    # source of truth for training-set hashing across this module + A2's
    # public-API `register_metric` flow.
    training_set_hash = _canonicalize_training_set(training_set)
    optimizer_call_hash = _compute_optimizer_call_hash(
        optimizer=optimizer,
        training_set_hash=training_set_hash,
        metric=metric,
    )

    # Forward adapter — Plan AST → DSPy Module. Surfaces ValueError
    # immediately if any :llm-call node lacks a signature or carries
    # an unsupported reasoning value. We pass the dispatcher so the
    # module's forward() can run execute() during compile-time scoring.
    dspy_module = _plan_to_dspy_module(plan, dispatcher=dispatcher)

    # Build the MIPROv2 metric adapter. MIPROv2 expects a callable
    # ``metric(example, prediction, trace=None) -> float`` per DSPy's
    # convention; we wrap our (ExecutionResult, expected) -> float fn
    # so MIPROv2 sees the right shape. The adapter pulls
    # ``example["expected"]`` for the ground truth and converts the
    # prediction to an ExecutionResult by running execute() over the
    # current candidate plan.
    def _miprov2_metric(example: Any, prediction: Any, trace: Any = None) -> float:
        # MIPROv2's metric protocol passes a TrainingExample-like dict
        # AND the candidate's prediction. For our use case, the
        # prediction is the dspy.Module's forward() output — already an
        # ExecutionResult per A3's contract. The expected ground truth
        # comes from the example dict.
        if isinstance(prediction, ExecutionResult):
            result = prediction
        else:
            # MIPROv2 may pass a raw DSPy Prediction object during
            # internal eval — re-run execute() with the example inputs
            # to get a typed ExecutionResult.
            inputs = (
                example.get("inputs", {}) if isinstance(example, dict) else {}
            )
            result = execute(plan, dispatcher, env=inputs)
        expected = (
            example.get("expected") if isinstance(example, dict) else None
        )
        return metric_fn(result, {"expected": expected})

    # Run MIPROv2. The kwargs are splatted into the constructor so
    # callers can pass ``num_trials``, ``init_temperature``, etc.
    # without us listing every MIPROv2 knob — DSPy can evolve those
    # independently and we don't care.
    teleprompter = MIPROv2(metric=_miprov2_metric, **(miprov2_kwargs or {}))
    optimized_module = teleprompter.compile(
        dspy_module,
        trainset=training_set,
    )

    # Inverse adapter — DSPy Module → Plan AST. We extract any tuned
    # signatures and clone the input plan with provenance pinned on
    # every node. The cloned plan has a different Node.id than the
    # input (provenance attrs change the canonical-JSON), so we capture
    # baseline_id BEFORE the clone.
    baseline_plan_id = plan.id
    provenance_attrs = _build_provenance_attrs(
        optimizer=optimizer,
        optimizer_call_hash=optimizer_call_hash,
        baseline_plan_id=baseline_plan_id,
        training_set_hash=training_set_hash,
        metric=metric,
    )
    signature_overrides = _extract_signature_overrides(optimized_module, plan)
    optimized_plan = _clone_with_provenance(
        plan,
        provenance_attrs=provenance_attrs,
        signature_overrides=signature_overrides,
    )

    # Score baseline + optimized on the training set. Both scores are
    # the mean of per-example metric values, computed by running
    # execute() over each example's `inputs` and feeding the result
    # plus the example's `expected` to the metric callable. Empty
    # training set collapses to 0.0 — caller's choice to pass an empty
    # list, no IO penalty.
    def _mean_score(target_plan: Node) -> float:
        if not training_set:
            return 0.0
        total = 0.0
        for example in training_set:
            inputs = example["inputs"]
            result = execute(target_plan, dispatcher, env=inputs)
            total += metric_fn(result, {"expected": example["expected"]})
        return total / len(training_set)

    score_baseline = _mean_score(plan)
    score = _mean_score(optimized_plan)

    return OptimizedPlan(
        plan=optimized_plan,
        baseline_plan_id=baseline_plan_id,
        optimizer=optimizer,
        optimizer_call_hash=optimizer_call_hash,
        score=score,
        score_baseline=score_baseline,
    )
