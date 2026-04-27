"""A3 — `_plan_to_dspy_module` forward adapter (Plan AST → DSPy Module).

Pins the contract per
docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md §5 (MIPROv2
integration boundary, "What MIPROv2 sees") and
docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md §2.A3.

The adapter walks a Plan AST and instantiates one DSPy module per
`:llm-call` node:

* ``attrs.get("reasoning", "direct") == "direct"`` → ``dspy.Predict``
* ``attrs.get("reasoning") == "cot"``           → ``dspy.ChainOfThought``
* anything else                                  → ``ValueError``

DSPy is mocked in this suite via ``sys.modules["dspy"] = MagicMock()``
so the test process does NOT need ``dspy-ai`` installed. A separate
subprocess test asserts the module-level ``import persistence.plan._optimize``
succeeds with ``sys.modules['dspy'] = None`` — i.e. dspy is lazy-imported
at function-call time, never at module-import time.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Iterator
from unittest.mock import MagicMock

import pytest

from persistence.plan._ast import Node
from persistence.plan._dispatch import Dispatcher
from persistence.plan._errors import OptimizerNotAvailable


# --- DSPy mocking fixture -------------------------------------------------- #


@pytest.fixture
def mock_dspy(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """Install a MagicMock at ``sys.modules['dspy']`` for the test duration.

    The adapter does ``import dspy`` inside its function body, so the
    mock must be installed BEFORE the adapter is called. We keep it
    scoped to the test to avoid leakage into other tests in the same
    pytest run.

    Yields:
        The MagicMock instance — tests assert against
        ``mock_dspy.Predict.assert_called_once_with(...)`` etc.
    """
    mock = MagicMock(name="dspy")
    # Make Module a class-like that can be subclassed. MagicMock by default
    # is not a valid base class, so we hand-roll a stub that the adapter
    # can subclass.

    class _StubModule:
        # Mirrors the dspy.Module surface the adapter needs: an __init__
        # the subclass can call via super(), and nothing else.
        def __init__(self) -> None:
            return None

    mock.Module = _StubModule
    monkeypatch.setitem(sys.modules, "dspy", mock)
    yield mock


# --- Helpers --------------------------------------------------------------- #


def _llm_call(signature: str | None = None, reasoning: str | None = None) -> Node:
    """Construct a ``:llm-call`` leaf with optional signature + reasoning."""
    attrs: dict[str, str] = {}
    if signature is not None:
        attrs["signature"] = signature
    if reasoning is not None:
        attrs["reasoning"] = reasoning
    return Node(tag=":llm-call", attrs=attrs)


def _seq(*children: Node) -> Node:
    """Construct a ``:seq`` parent with the given children."""
    return Node(tag=":seq", children=children)


def _dispatcher_with(handler) -> Dispatcher:
    """Dispatcher with one ``:llm-call`` handler registered."""
    d = Dispatcher()
    d.register(":llm-call", handler)
    return d


# --- Public API surface ---------------------------------------------------- #


def test_plan_to_dspy_module_is_internal_not_in_public_all() -> None:
    """A3 ships the adapter as internal — A4 will add the public ``optimize``.

    Verifies the adapter is NOT in ``persistence.plan.__all__`` so we
    don't accidentally widen the public surface ahead of A4.
    """
    import persistence.plan as plan

    assert "_plan_to_dspy_module" not in plan.__all__
    assert "optimize" not in plan.__all__  # A4 lands this, not A3


def test_optimize_module_imports(mock_dspy: MagicMock) -> None:
    """``persistence.plan._optimize`` imports cleanly with mock dspy installed.

    Sanity check that the module-level imports of ``_optimize`` don't
    blow up. The substantive no-dspy guarantee is in the subprocess
    test below.
    """
    import persistence.plan._optimize as opt

    assert hasattr(opt, "_plan_to_dspy_module")


# --- One :llm-call: Predict (default) -------------------------------------- #


def test_one_llm_call_default_reasoning_uses_predict(mock_dspy: MagicMock) -> None:
    """Single ``:llm-call`` with only ``signature`` → ``dspy.Predict("q -> a")``."""
    from persistence.plan._optimize import _plan_to_dspy_module

    plan = _llm_call(signature="q -> a")
    d = _dispatcher_with(lambda n, env: "answer")

    module = _plan_to_dspy_module(plan, dispatcher=d)

    mock_dspy.Predict.assert_called_once_with("q -> a")
    mock_dspy.ChainOfThought.assert_not_called()
    # Returned module must be callable via .forward(**inputs) — type is
    # `dspy.Module` subclass; the test asserts the affordance, not the
    # concrete type (DSPy doesn't ship stubs and the mock is loose).
    assert callable(getattr(module, "forward", None))


def test_one_llm_call_explicit_direct_reasoning_uses_predict(
    mock_dspy: MagicMock,
) -> None:
    """``reasoning="direct"`` is the explicit form of the default → Predict."""
    from persistence.plan._optimize import _plan_to_dspy_module

    plan = _llm_call(signature="q -> a", reasoning="direct")
    d = _dispatcher_with(lambda n, env: "answer")

    _plan_to_dspy_module(plan, dispatcher=d)

    mock_dspy.Predict.assert_called_once_with("q -> a")
    mock_dspy.ChainOfThought.assert_not_called()


# --- One :llm-call: ChainOfThought ---------------------------------------- #


def test_one_llm_call_cot_reasoning_uses_chain_of_thought(
    mock_dspy: MagicMock,
) -> None:
    """``reasoning="cot"`` → ``dspy.ChainOfThought("q -> a")``."""
    from persistence.plan._optimize import _plan_to_dspy_module

    plan = _llm_call(signature="q -> a", reasoning="cot")
    d = _dispatcher_with(lambda n, env: "answer")

    _plan_to_dspy_module(plan, dispatcher=d)

    mock_dspy.ChainOfThought.assert_called_once_with("q -> a")
    mock_dspy.Predict.assert_not_called()


# --- Multiple :llm-call in :seq ------------------------------------------- #


def test_multiple_llm_calls_in_seq_each_gets_predict(mock_dspy: MagicMock) -> None:
    """Two ``:llm-call``s in ``:seq`` → ``Predict`` called twice with each sig."""
    from persistence.plan._optimize import _plan_to_dspy_module

    plan = _seq(
        _llm_call(signature="q -> a"),
        _llm_call(signature="a -> summary"),
    )
    d = _dispatcher_with(lambda n, env: "ok")

    _plan_to_dspy_module(plan, dispatcher=d)

    assert mock_dspy.Predict.call_count == 2
    sigs = {call.args[0] for call in mock_dspy.Predict.call_args_list}
    assert sigs == {"q -> a", "a -> summary"}


def test_seq_with_mixed_reasoning_routes_each_independently(
    mock_dspy: MagicMock,
) -> None:
    """One ``cot`` + one default in ``:seq`` → 1×ChainOfThought + 1×Predict."""
    from persistence.plan._optimize import _plan_to_dspy_module

    plan = _seq(
        _llm_call(signature="q -> a", reasoning="cot"),
        _llm_call(signature="a -> summary"),
    )
    d = _dispatcher_with(lambda n, env: "ok")

    _plan_to_dspy_module(plan, dispatcher=d)

    mock_dspy.ChainOfThought.assert_called_once_with("q -> a")
    mock_dspy.Predict.assert_called_once_with("a -> summary")


# --- No :llm-call --------------------------------------------------------- #


def test_no_llm_call_returns_module_that_walks_dispatcher(
    mock_dspy: MagicMock,
) -> None:
    """Plan with no ``:llm-call`` → a Module whose ``forward()`` runs ``execute()``.

    Pinned semantics: the adapter still returns a callable Module even
    when no ``dspy.Predict``/``dspy.ChainOfThought`` instance was created.
    ``forward(**inputs)`` invokes ``execute(plan, dispatcher, env=inputs)``
    and returns the resulting ``ExecutionResult``.
    """
    from persistence.plan._optimize import _plan_to_dspy_module

    plan = Node(tag=":seq")  # no children, no :llm-call
    d = Dispatcher()  # no handlers registered

    module = _plan_to_dspy_module(plan, dispatcher=d)

    # No DSPy module instance should have been created — there were no
    # :llm-call nodes to route.
    mock_dspy.Predict.assert_not_called()
    mock_dspy.ChainOfThought.assert_not_called()

    # forward() returns the ExecutionResult — adapter doesn't pretend
    # there's a "final leaf" when there isn't one.
    out = module.forward()
    assert out.status == "ok"
    assert out.leaf_results == ()
    assert out.plan_id == plan.id


def test_forward_with_one_llm_call_runs_dispatcher(mock_dspy: MagicMock) -> None:
    """``forward(**inputs)`` runs ``execute()`` and returns the result envelope.

    Confirms the design §5 contract: "the returned dspy.Module's
    forward(**inputs) runs the dispatcher and returns the final leaf
    result" — concretely we return the ``ExecutionResult`` envelope so
    callers can inspect leaf_results / status / failure.
    """
    from persistence.plan._optimize import _plan_to_dspy_module

    plan = _llm_call(signature="q -> a")
    seen_envs: list[dict] = []

    def handler(node: Node, env: dict) -> str:
        seen_envs.append(dict(env))  # snapshot for later assertion
        return f"answered({env.get('q')})"

    d = _dispatcher_with(handler)
    module = _plan_to_dspy_module(plan, dispatcher=d)

    result = module.forward(q="hello")

    # Handler saw the kwargs as env.
    assert seen_envs == [{"q": "hello"}]
    # ExecutionResult round-tripped.
    assert result.status == "ok"
    assert len(result.leaf_results) == 1
    assert result.leaf_results[0].result == "answered(hello)"


# --- Errors --------------------------------------------------------------- #


def test_missing_signature_attr_raises_value_error(mock_dspy: MagicMock) -> None:
    """``:llm-call`` without ``signature`` attr → clear ``ValueError``."""
    from persistence.plan._optimize import _plan_to_dspy_module

    plan = _llm_call()  # no signature attr
    d = _dispatcher_with(lambda n, env: "ok")

    with pytest.raises(ValueError, match="signature"):
        _plan_to_dspy_module(plan, dispatcher=d)


def test_unknown_reasoning_value_raises_value_error(mock_dspy: MagicMock) -> None:
    """``reasoning="react"`` (unsupported) → ``ValueError`` with the value cited.

    Stream A only ships ``direct`` + ``cot``. ``react`` (or any other
    string) is rejected at adapter-construction time so callers don't
    silently get the wrong DSPy module.
    """
    from persistence.plan._optimize import _plan_to_dspy_module

    plan = _llm_call(signature="q -> a", reasoning="react")
    d = _dispatcher_with(lambda n, env: "ok")

    with pytest.raises(ValueError, match="react"):
        _plan_to_dspy_module(plan, dispatcher=d)


def test_dspy_not_installed_raises_optimizer_not_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``sys.modules['dspy'] = None`` → ``OptimizerNotAvailable`` at call time.

    Setting a module to ``None`` in ``sys.modules`` makes subsequent
    ``import`` raise ``ImportError`` — the standard idiom for fencing
    optional-dep paths in tests. We invoke the adapter and assert the
    domain-typed error fires (not a raw ``ImportError``).
    """
    monkeypatch.setitem(sys.modules, "dspy", None)
    # Drop any cached _optimize so the lazy import inside the function
    # body runs against the poisoned sys.modules state. The module
    # itself imports cleanly without dspy (asserted by subprocess test
    # below); only the function call should trip.
    monkeypatch.delitem(sys.modules, "persistence.plan._optimize", raising=False)

    from persistence.plan._optimize import _plan_to_dspy_module

    plan = _llm_call(signature="q -> a")
    d = _dispatcher_with(lambda n, env: "ok")

    with pytest.raises(OptimizerNotAvailable, match="dspy"):
        _plan_to_dspy_module(plan, dispatcher=d)


# --- No-DSPy module-import guarantee (subprocess fence) ------------------- #


def test_optimize_module_imports_without_dspy() -> None:
    """``import persistence.plan._optimize`` succeeds with ``sys.modules['dspy']=None``.

    Mirrors ``tests/plan/test_execute_no_dspy_dep.py``. Runs in a fresh
    subprocess so the dspy-poisoning doesn't leak into other tests; if
    ``_optimize`` had a module-level ``import dspy`` (or any module-
    level path that pulls dspy), this would fail with ImportError.
    """
    code = (
        "import sys\n"
        "sys.modules['dspy'] = None\n"
        "import persistence.plan._optimize\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "ok" in result.stdout
