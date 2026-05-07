"""T1 — `_searcher_errors.py` exception types.

Phase 2.3b errors module. Mirrors 2.3a's `_planner_errors.py` shape
exactly — `BranchPayloadValidation(field, reason)` for ingestion-stage
rejections; `BranchSearchFailed(error_repr, search_id, iter_count,
terminated_by)` for search-layer failures funneled per LD4.
"""
from __future__ import annotations

import pytest

from persistence.coder._searcher_errors import (
    BranchPayloadValidation,
    BranchSearchFailed,
)


def test_branch_payload_validation_carries_field_and_reason():
    err = BranchPayloadValidation(field="seed_plan_edn", reason="missing")
    assert err.field == "seed_plan_edn"
    assert err.reason == "missing"
    assert "seed_plan_edn" in str(err)
    assert "missing" in str(err)


def test_branch_payload_validation_is_exception():
    with pytest.raises(BranchPayloadValidation):
        raise BranchPayloadValidation(field="x", reason="y")


def test_branch_search_failed_carries_all_four_fields():
    err = BranchSearchFailed(
        error_repr="ExpanderContractError('prior sum 0.9 != 1.0')",
        search_id="abc123",
        iter_count=12,
        terminated_by="all_evaluations_failed",
    )
    assert err.error_repr == "ExpanderContractError('prior sum 0.9 != 1.0')"
    assert err.search_id == "abc123"
    assert err.iter_count == 12
    assert err.terminated_by == "all_evaluations_failed"


def test_branch_search_failed_path_one_optional_fields_none():
    """Path-1 (bubble-out): search never completed — search_id/iter_count/terminated_by are None."""
    err = BranchSearchFailed(
        error_repr="ValueError('expander provider raised')",
        search_id=None,
        iter_count=None,
        terminated_by=None,
    )
    assert err.error_repr.startswith("ValueError")
    assert err.search_id is None
    assert err.iter_count is None
    assert err.terminated_by is None


def test_branch_search_failed_str_contains_error_repr():
    err = BranchSearchFailed(
        error_repr="RuntimeError('boom')",
        search_id="s1",
        iter_count=3,
        terminated_by="max_iter",
    )
    assert "RuntimeError('boom')" in str(err)
