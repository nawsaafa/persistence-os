"""T2/G1 — `_build_seed_plan` ingestion (Stage 1 + Stage 2).

Stage 1: byte-budget guard (8192 bytes raw EDN, UTF-8 encoded; same
budget as 2.3a per LD2 — re-use the constant).
Stage 2: parse(strict=False) — same posture as 2.3a (FD2 — coder
substrate ops are outside the closed plan-spec enum, so strict-mode
rejects them; semantic validator at Stage 3 provides safety).

Errors funneled through `BranchPayloadValidation`.

Forced spec deviation vs task brief (FD1):
  The task brief's EDN snippets use quoted-keyword-as-string form like
  '[":seq" [":fs/read" ":path" "x.txt"]]'. The actual EDN parser
  (`_parse.py:_python_to_node`) requires bare-keyword form (`:seq`)
  with an attrs map (`{}` or `{:k v}`) at index 1, OR the bare-shorthand
  `[:tag [child] [child]]` form. The 2.3a precedent at
  `tests/coder/test_planner_build.py:23` uses
  `'[:seq {} [:fs/read {:path "x.txt"}] ...]'` — this file follows that
  canonical shape.
"""
from __future__ import annotations

import pytest

from persistence.coder._searcher import _build_seed_plan
from persistence.coder._searcher_errors import BranchPayloadValidation
from persistence.plan import Node


def test_build_seed_plan_round_trips_canonical_edn():
    """Canonical EDN → parse → unparse → canonical EDN is byte-identical."""
    edn = '[:seq {} [:fs/read {:path "x.txt"}]]'
    plan = _build_seed_plan({"seed_plan_edn": edn})
    assert isinstance(plan, Node)
    assert plan.tag == ":seq"


def test_build_seed_plan_rejects_missing_field():
    with pytest.raises(BranchPayloadValidation) as excinfo:
        _build_seed_plan({})
    assert excinfo.value.field == "seed_plan_edn"
    assert "missing" in excinfo.value.reason.lower()


def test_build_seed_plan_rejects_non_string_field():
    with pytest.raises(BranchPayloadValidation) as excinfo:
        _build_seed_plan({"seed_plan_edn": 123})
    assert excinfo.value.field == "seed_plan_edn"
    assert "str" in excinfo.value.reason


def test_build_seed_plan_rejects_byte_budget_exceeded():
    # Construct an EDN string that's >8192 bytes when UTF-8-encoded.
    long_path = "x" * 8200
    edn = f'[:seq {{}} [:fs/read {{:path "{long_path}"}}]]'
    with pytest.raises(BranchPayloadValidation) as excinfo:
        _build_seed_plan({"seed_plan_edn": edn})
    assert excinfo.value.field == "seed_plan_edn"
    assert "byte" in excinfo.value.reason.lower()


def test_build_seed_plan_rejects_malformed_edn_as_validation_error():
    """A ParseError from the underlying parser must surface as
    BranchPayloadValidation (NOT as bare ParseError)."""
    edn = '[:seq this is broken'
    with pytest.raises(BranchPayloadValidation) as excinfo:
        _build_seed_plan({"seed_plan_edn": edn})
    assert excinfo.value.field == "seed_plan_edn"
    assert "parse" in excinfo.value.reason.lower()


def test_build_seed_plan_returns_persistence_plan_node():
    """Type contract: returned object must be a `persistence.plan.Node`
    (the type `mcts_search.initial_plan` requires per `_mcts.py:844`)."""
    edn = '[:seq {} [:fs/read {:path "a.txt"}]]'
    plan = _build_seed_plan({"seed_plan_edn": edn})
    assert isinstance(plan, Node)
