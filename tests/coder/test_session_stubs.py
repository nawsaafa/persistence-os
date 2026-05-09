"""Phase 2.1a — direct Coder construction tests against in-memory substrate.

Per design § 5.2. Every behavioral method must raise
CoderStubNotImplemented (a NotImplementedError subclass) tagged with
the downstream sub-phase that fills it.
"""

import pytest

from persistence.coder import Coder, CoderStubNotImplemented
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


# Phase 2.3b T8 (LD1): the legacy `test_run_raises_on_first_stub` premise
# (echo returns kind="act" confidence=0.5 → confidence-below-threshold
# routes into the `_escalate_branch` stub) NO LONGER HOLDS. The
# confidence-based half of `_should_escalate_branch` was removed per
# LD1 R0 codex finding (type/shape mismatch — kind="act" payloads
# would reach a branch escalator that expects branch-specific payload).
#
# `_escalate_branch` is now filled. The only remaining stub on the
# critical path is `_check_pause` (Phase 2.3d) — and that's only reached
# by the loop after a successful `_act` / `_should_*` cycle, so its
# coverage is already exercised by the per-stub parametrize below.


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
        # _escalate_branch removed — filled in Phase 2.3b T8, no longer a stub.
        # _check_pause removed — filled in Phase 2.3d T1, no longer a stub.
        # All stubs filled — parametrize list intentionally empty.
        # When a new phase adds a stub, add it here with its phase tag.
        pytest.param(
            "_PLACEHOLDER_NO_STUBS_REMAINING",
            "",
            marks=pytest.mark.skip(reason="no remaining stubs — list kept for extensibility"),
        ),
    ],
)
def test_each_stub_raises_with_exact_message(
    substrate, method_name, expected_message
):
    coder = Coder(task="hi", substrate=substrate)
    method = getattr(coder, method_name)
    with pytest.raises(CoderStubNotImplemented) as exc:
        method()
    assert str(exc.value) == expected_message


# Phase 2.2a T6 — _should_escalate_* gate tests (one-liners filled)
def test_should_escalate_branch_returns_true_for_kind_branch():
    s = Substrate.open("memory")
    coder = Coder(task="t", substrate=s)
    from persistence.coder._types import LLMDecision
    assert coder._should_escalate_branch(LLMDecision(kind="branch", confidence=0.9, payload={})) is True
    s.close()


def test_should_escalate_branch_ignores_low_confidence_act():
    """Phase 2.3b T8 (LD1): `_should_escalate_branch` no longer triggers
    on confidence-below-threshold for kind="act" decisions. Per LD1 R0
    codex finding, the confidence-based half was removed because it
    routed kind="act" payloads (carrying `{op, args}`) into the branch
    escalator, which expects branch-specific payload contract.

    This test pins the LD1 invariant: kind="act" never reaches the
    branch path regardless of confidence."""
    s = Substrate.open("memory")
    coder = Coder(task="t", substrate=s)
    from persistence.coder._types import LLMDecision
    assert coder._should_escalate_branch(LLMDecision(kind="act", confidence=0.4, payload={})) is False
    assert coder._should_escalate_branch(LLMDecision(kind="act", confidence=0.0, payload={})) is False
    assert coder._should_escalate_branch(LLMDecision(kind="plan", confidence=0.1, payload={})) is False
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
