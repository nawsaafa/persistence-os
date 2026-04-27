"""Plan AST ↔ DSPy adapter (v0.6.0a1, Stream A — A3).

Forward direction of the optimizer bridge: walks a Plan AST and returns
a callable ``dspy.Module`` whose ``forward(**inputs)`` runs the same
plan via the dispatcher and returns an ``ExecutionResult`` envelope.
Per ``:llm-call`` node, instantiates ``dspy.Predict`` (default) or
``dspy.ChainOfThought`` when ``attrs.get("reasoning") == "cot"``,
using ``attrs["signature"]`` as the DSPy signature string.

Design contract (per
``docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md`` §5
"MIPROv2 integration boundary" + "What MIPROv2 sees", and
``docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md`` §2.A3):

* DSPy is **lazy-imported at function-call time**. Module-level import
  of this file MUST succeed without ``dspy-ai`` installed — verified
  by ``tests/plan/test_plan_to_dspy_adapter.py::
  test_optimize_module_imports_without_dspy``. Any ``import dspy``
  outside a function body breaks A1's no-DSPy guarantee for
  ``persistence.plan._execute`` (the execution core must be DSPy-free).
* The adapter does NOT touch ``_execute.py``'s import path — the
  execution core stays clean. We import ``execute`` from ``_execute``
  at module level (it is itself DSPy-free) and only pull DSPy when the
  caller actually invokes ``_plan_to_dspy_module``.
* The inverse adapter (``DSPy module → Plan AST``), ``optimize()``
  end-to-end, provenance pinning, and the ``OptimizedPlan`` dataclass
  land in **A4**. A3 ships only the forward adapter — internal API
  (no leading-underscore module level entry in ``__all__``).

References:
    docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md §5
    docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md §2.A3
"""
from __future__ import annotations

from typing import Any

from persistence.plan._ast import Node
from persistence.plan._dispatch import Dispatcher
from persistence.plan._errors import OptimizerNotAvailable
from persistence.plan._execute import execute
from persistence.plan._walk import walk

#: Internal — A3 does NOT add anything to ``persistence.plan.__all__``.
#: A4 will export ``optimize`` + ``OptimizedPlan`` once the e2e flow
#: is in place. Keeping ``_plan_to_dspy_module`` private until then.
__all__: list[str] = []


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
