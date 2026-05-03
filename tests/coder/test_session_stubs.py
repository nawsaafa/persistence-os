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


def test_run_raises_on_first_stub(substrate):
    coder = Coder(task="hi", substrate=substrate)
    with pytest.raises(CoderStubNotImplemented) as exc:
        coder.run()
    assert "Phase 2.2a" in str(exc.value)
    assert "substrate read via s.fact.q" in str(exc.value)


@pytest.mark.parametrize(
    "method_name, expected_phase_tag",
    [
        ("_observe",                 "Phase 2.2a"),
        ("_decide",                  "Phase 2.1b"),
        ("_act",                     "Phase 2.2a"),
        ("_should_escalate_plan",    "Phase 2.3a"),
        ("_escalate_plan",           "Phase 2.3a"),
        ("_should_escalate_branch",  "Phase 2.3b"),
        ("_escalate_branch",         "Phase 2.3b"),
        ("_check_pause",             "Phase 2.3d"),
    ],
)
def test_each_stub_raises_with_phase_tag(
    substrate, method_name, expected_phase_tag
):
    coder = Coder(task="hi", substrate=substrate)
    method = getattr(coder, method_name)
    fake_arg = None
    with pytest.raises(CoderStubNotImplemented) as exc:
        if method_name in ("_observe", "_check_pause"):
            method()
        else:
            method(fake_arg)
    assert expected_phase_tag in str(exc.value)
