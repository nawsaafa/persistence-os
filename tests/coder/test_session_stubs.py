"""Phase 2.1a — direct Coder construction tests against in-memory substrate.

Per design § 5.2. Every behavioral method must raise
CoderStubNotImplemented (a NotImplementedError subclass) tagged with
the downstream sub-phase that fills it.
"""

import pytest

from persistence.coder import Coder, CoderStubNotImplemented
from persistence.effect.handlers import make_callable_llm_handler
from persistence.sdk import Substrate


@pytest.fixture
def substrate():
    with Substrate.open("memory") as s:
        yield s


@pytest.fixture
def substrate_with_echo():
    """Substrate with echo LLM handler so _decide() doesn't raise Unhandled."""
    from persistence.effect.handlers.raw import make_echo_llm_handler
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_echo_llm_handler(), position="bottom")
        yield s


def test_coder_constructs_with_defaults(substrate):
    coder = Coder(task="hi", substrate=substrate)
    assert coder.task == "hi"
    assert coder.substrate is substrate
    assert coder.confidence_threshold == 0.65
    assert coder.missing_confidence_default == 0.5


def test_coder_accepts_custom_confidence_threshold(substrate):
    coder = Coder(task="hi", substrate=substrate, confidence_threshold=0.8)
    assert coder.confidence_threshold == 0.8


def test_stub_subtype_inherits_from_not_implemented_error():
    # R1 fix-1 invariant: stub sentinel must remain a NotImplementedError
    # subclass so generic `pytest.raises(NotImplementedError)` matches,
    # but the CLI catches the narrow subtype only.
    assert issubclass(CoderStubNotImplemented, NotImplementedError)


def test_run_raises_on_first_stub(substrate_with_echo):
    # Phase 2.2a T6: _should_escalate_* are now filled. With echo handler,
    # echo returns kind="act" confidence=0.5 (missing_confidence_default)
    # which is below threshold 0.65 — _should_escalate_branch returns True
    # → _escalate_branch raises the stub.
    coder = Coder(task="hi", substrate=substrate_with_echo)
    with pytest.raises(CoderStubNotImplemented) as exc:
        coder.run()
    assert str(exc.value) == "Phase 2.3b — s.plan.mcts_search + s.txn.fork + s.plan.judge"


# F3 from impl ARIS R1: exact-equality pins each stub's downstream-phase
# tag AND its semantic hint. Substring matching let a typo or accidental
# message edit slip through as long as the phase prefix survived.
@pytest.mark.parametrize(
    "method_name, expected_message",
    [
        # _observe removed — filled in Phase 2.2a T4, no longer a stub.
        # _decide removed — filled in Phase 2.1b (Task 9), no longer a stub.
        # _act removed — filled in Phase 2.2a T5, no longer a stub.
        # _should_escalate_plan removed — filled in Phase 2.2a T6, no longer a stub.
        # _should_escalate_branch removed — filled in Phase 2.2a T6, no longer a stub.
        # _escalate_plan removed — filled in Phase 2.3a T7, no longer a stub.
        ("_escalate_branch",        "Phase 2.3b — s.plan.mcts_search + s.txn.fork + s.plan.judge"),
        ("_check_pause",            "Phase 2.3d — :repl/request datom check + pause/resume"),
    ],
)
def test_each_stub_raises_with_exact_message(
    substrate, method_name, expected_message
):
    coder = Coder(task="hi", substrate=substrate)
    method = getattr(coder, method_name)
    with pytest.raises(CoderStubNotImplemented) as exc:
        if method_name in ("_check_pause",):
            method()
        else:
            method(None)
    assert str(exc.value) == expected_message


# Phase 2.2a T6 — _should_escalate_* gate tests (one-liners filled)
def test_should_escalate_branch_returns_true_for_kind_branch():
    s = Substrate.open("memory")
    coder = Coder(task="t", substrate=s)
    from persistence.coder._types import LLMDecision
    assert coder._should_escalate_branch(LLMDecision(kind="branch", confidence=0.9, payload={})) is True
    s.close()


def test_should_escalate_branch_returns_true_below_threshold():
    s = Substrate.open("memory")
    coder = Coder(task="t", substrate=s)
    from persistence.coder._types import LLMDecision
    assert coder._should_escalate_branch(LLMDecision(kind="act", confidence=0.4, payload={})) is True
    s.close()


def test_should_escalate_branch_returns_false_above_threshold_act():
    s = Substrate.open("memory")
    coder = Coder(task="t", substrate=s)
    from persistence.coder._types import LLMDecision
    assert coder._should_escalate_branch(LLMDecision(kind="act", confidence=0.9, payload={})) is False
    s.close()


def test_should_escalate_plan_returns_true_for_kind_plan():
    s = Substrate.open("memory")
    coder = Coder(task="t", substrate=s)
    from persistence.coder._types import LLMDecision
    assert coder._should_escalate_plan(LLMDecision(kind="plan", confidence=0.9, payload={})) is True
    s.close()


def test_should_escalate_plan_returns_false_for_kind_act():
    s = Substrate.open("memory")
    coder = Coder(task="t", substrate=s)
    from persistence.coder._types import LLMDecision
    assert coder._should_escalate_plan(LLMDecision(kind="act", confidence=0.9, payload={})) is False
    s.close()


# test_escalate_plan_still_raises_stub removed — _escalate_plan filled in Phase 2.3a T7.
