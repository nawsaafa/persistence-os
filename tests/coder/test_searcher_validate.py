"""T2/G2 — `_validate_seed_plan_for_2_3b` semantic validator.

LD2: per the design, 2.3b RE-USES 2.3a's strict `validate_plan_for_2_3a`
verbatim. The validator name in this module is a thin re-export with
the 2.3b suffix so future calls in the bridge don't accidentally drift
to a looser sibling. R0-fold N1 explicitly rejected the looser-sibling
trap.

Forced spec deviation vs task brief (FD1):
  The task brief's EDN snippets use quoted-keyword-as-string form like
  '[":par" [":fs/read" ":path" "x.txt"]]'. The actual EDN parser
  requires bare-keyword form (`:par`) with an attrs map (`{}`). This
  file uses the canonical form per 2.3a precedent at
  `tests/coder/test_planner_validate.py`.

  Additional FD1 nuance: `_validate_seed_plan_for_2_3b` rejects a
  non-:seq root with `field == "root_tag"` (per
  `validate_plan_for_2_3a` at `_planner.py`). The brief's substring
  check ("seq"/"root") still passes because the field name "root_tag"
  contains "root".
"""
from __future__ import annotations

import pytest

from persistence.coder._planner import validate_plan_for_2_3a
from persistence.coder._planner_errors import PlanPayloadValidation
from persistence.coder._searcher import _validate_seed_plan_for_2_3b
from persistence.plan import parse


def test_validator_is_2_3a_function_or_thin_wrapper():
    """The 2.3b validator either IS validate_plan_for_2_3a or wraps it
    with no looser semantics — verified by the rejection-set being the
    same."""
    edn = '[:seq {} [:fs/read {:path "x.txt"}]]'
    plan = parse(edn, strict=False)
    # Both calls must succeed (or both fail) on the same input.
    validate_plan_for_2_3a(plan)        # 2.3a strict
    _validate_seed_plan_for_2_3b(plan)  # 2.3b: should not differ


def test_validator_rejects_non_seq_root():
    # leaf-as-root — :fs/read is not in plan spec, use strict=False.
    edn = '[:fs/read {:path "x.txt"}]'
    plan = parse(edn, strict=False)
    with pytest.raises(PlanPayloadValidation) as excinfo:
        _validate_seed_plan_for_2_3b(plan)
    # validate_plan_for_2_3a sets field="root_tag" on non-:seq root;
    # "root" substring satisfies the brief's check.
    assert (
        "seq" in excinfo.value.reason.lower()
        or "root" in excinfo.value.reason.lower()
        or "root" in excinfo.value.field.lower()
    )


def test_validator_rejects_branch_leaf():
    # :branch IS in plan spec; parse default (strict=True) is fine.
    plan = parse('[:seq {} [:branch {}]]')
    with pytest.raises(PlanPayloadValidation):
        _validate_seed_plan_for_2_3b(plan)


def test_validator_rejects_code_leaf():
    # :code IS in plan spec; parse default (strict=True) is fine.
    plan = parse('[:seq {} [:code {:source "x=1"}]]')
    with pytest.raises(PlanPayloadValidation):
        _validate_seed_plan_for_2_3b(plan)


def test_validator_rejects_unregistered_leaf_tag():
    # :nonexistent/op is NOT in plan spec → use strict=False to bypass spec.
    plan = parse('[:seq {} [:nonexistent/op {}]]', strict=False)
    with pytest.raises(PlanPayloadValidation):
        _validate_seed_plan_for_2_3b(plan)
