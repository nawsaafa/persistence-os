"""A4 — Provenance pin attr keys MUST construct on ``Node(...)``.

Pins the design contract per
``docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md`` §5
"Provenance pinning" + ``docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md``
§2.A4.

The optimized AST emitted by ``optimize()`` carries six provenance keys
on every node's ``Node.attrs``. Those keys are PLAIN STRINGS — no leading
colon — because ``_ast.py:99`` raises ``ValueError`` on any colon-prefixed
attr key. This test suite is the regression guard for that contract: if
a future refactor accidentally adds ``:`` to a provenance key (e.g.
``":plan/optimizer"``), every ``Node`` in every optimized plan would
fail to construct, tanking the suite. The tests here pin each key
individually + all six together, so the failure surface is small,
named, and grep-able.
"""
from __future__ import annotations

import pytest

from persistence.plan._ast import Node


# --- Per-key construction tests ------------------------------------------- #


def test_plan_optimizer_attr_constructs() -> None:
    """``plan/optimizer`` (plain string, no leading colon) constructs cleanly."""
    n = Node(tag=":llm-call", attrs={"plan/optimizer": "miprov2-v1"})
    assert n.attrs["plan/optimizer"] == "miprov2-v1"


def test_plan_optimizer_call_attr_constructs() -> None:
    """``plan/optimizer-call`` accepts a sha256 hex digest value."""
    digest = "a" * 64  # 64-hex sha256 placeholder
    n = Node(tag=":llm-call", attrs={"plan/optimizer-call": digest})
    assert n.attrs["plan/optimizer-call"] == digest


def test_plan_baseline_attr_constructs() -> None:
    """``plan/baseline`` accepts a 32-hex Node.id-shaped value."""
    plan_id = "0123456789abcdef0123456789abcdef"
    n = Node(tag=":llm-call", attrs={"plan/baseline": plan_id})
    assert n.attrs["plan/baseline"] == plan_id


def test_plan_training_set_hash_attr_constructs() -> None:
    """``plan/training-set-hash`` accepts a sha256-hex digest value."""
    digest = "b" * 64
    n = Node(tag=":llm-call", attrs={"plan/training-set-hash": digest})
    assert n.attrs["plan/training-set-hash"] == digest


def test_plan_metric_id_attr_constructs() -> None:
    """``plan/metric-id`` accepts a stable metric identifier."""
    n = Node(tag=":llm-call", attrs={"plan/metric-id": "exact-match"})
    assert n.attrs["plan/metric-id"] == "exact-match"


def test_plan_metric_version_attr_constructs() -> None:
    """``plan/metric-version`` accepts a metric version string."""
    n = Node(tag=":llm-call", attrs={"plan/metric-version": "v1"})
    assert n.attrs["plan/metric-version"] == "v1"


# --- All-six-together test ------------------------------------------------ #


def test_all_six_provenance_keys_construct_together() -> None:
    """All six provenance keys on one node together — the realistic shape."""
    attrs = {
        "plan/optimizer": "miprov2-v1",
        "plan/optimizer-call": "a" * 64,
        "plan/baseline": "0" * 32,
        "plan/training-set-hash": "b" * 64,
        "plan/metric-id": "exact-match",
        "plan/metric-version": "v1",
    }
    n = Node(tag=":llm-call", attrs=attrs)
    # Every key round-trips byte-identical.
    for k, v in attrs.items():
        assert n.attrs[k] == v


def test_provenance_keys_coexist_with_author_attrs() -> None:
    """Provenance keys merge cleanly alongside author-supplied attrs.

    The inverse adapter merges six provenance keys into existing attrs
    — confirms the merge surface doesn't collide with the canonical
    `signature` attr on a `:llm-call` node.
    """
    attrs = {
        "signature": "q -> a",
        "reasoning": "direct",
        "plan/optimizer": "miprov2-v1",
        "plan/optimizer-call": "a" * 64,
        "plan/baseline": "0" * 32,
        "plan/training-set-hash": "b" * 64,
        "plan/metric-id": "exact-match",
        "plan/metric-version": "v1",
    }
    n = Node(tag=":llm-call", attrs=attrs)
    assert n.attrs["signature"] == "q -> a"
    assert n.attrs["plan/optimizer"] == "miprov2-v1"


# --- Regression guards: leading colon MUST raise -------------------------- #


@pytest.mark.parametrize(
    "bad_key",
    [
        ":plan/optimizer",
        ":plan/optimizer-call",
        ":plan/baseline",
        ":plan/training-set-hash",
        ":plan/metric-id",
        ":plan/metric-version",
    ],
)
def test_leading_colon_provenance_key_raises_value_error(bad_key: str) -> None:
    """Any of the six keys with a leading colon → ``ValueError`` at construction.

    This is the core regression guard for the entire provenance pinning
    layer: if a future refactor accidentally types ``":plan/optimizer"``
    instead of ``"plan/optimizer"`` somewhere in ``_optimize.py``, the
    very next ``Node(...)`` would raise ``ValueError`` per
    ``_ast.py:99``. The test pins that the validation is still active.
    """
    with pytest.raises(ValueError, match="leading colon"):
        Node(tag=":llm-call", attrs={bad_key: "value"})
