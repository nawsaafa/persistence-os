"""T3/G7 — `_resolve_mcts_config` payload override path.

Stage 4: payload["mcts_config"] dict-merge over branch-bridge default.
Bool-numeric rejection (FD2: MCTSConfig.__post_init__ at _mcts.py:279
rejects bool numerics; bridge must coerce BEFORE construction).
Bounds enforcement (LD6: max_iter <= MAX_BRANCH_MAX_ITER=50;
expander_k <= MAX_BRANCH_EXPANDER_K=4).
"""
from __future__ import annotations

import pytest

from persistence.coder._searcher import (
    _BRANCH_BRIDGE_DEFAULT_CONFIG,
    _resolve_mcts_config,
)
from persistence.coder._searcher_errors import BranchPayloadValidation


def test_resolve_returns_default_when_payload_omits_mcts_config():
    """Empty/missing payload -> branch-bridge default."""
    cfg = _resolve_mcts_config({"seed_plan_edn": "..."})
    assert cfg == _BRANCH_BRIDGE_DEFAULT_CONFIG
    assert cfg.max_iter == 50
    assert cfg.expander_k == 4


def test_resolve_overrides_with_valid_smaller_max_iter():
    """Override with smaller max_iter (within bounds)."""
    cfg = _resolve_mcts_config({"mcts_config": {"max_iter": 25}})
    assert cfg.max_iter == 25
    assert cfg.expander_k == 4   # default preserved


def test_resolve_rejects_bool_numeric_max_iter():
    """JSON true/false MUST be rejected BEFORE MCTSConfig construction
    (FD2: MCTSConfig.__post_init__ rejects bool but raises ValueError
    not BranchPayloadValidation; we want BranchPayloadValidation)."""
    with pytest.raises(BranchPayloadValidation) as excinfo:
        _resolve_mcts_config({"mcts_config": {"max_iter": True}})
    assert "mcts_config.max_iter" in excinfo.value.field
    assert "bool" in excinfo.value.reason.lower() or "true" in excinfo.value.reason.lower()


def test_resolve_rejects_max_iter_over_cap():
    """LD6: max_iter > MAX_BRANCH_MAX_ITER (50) MUST be rejected."""
    with pytest.raises(BranchPayloadValidation) as excinfo:
        _resolve_mcts_config({"mcts_config": {"max_iter": 51}})
    assert "max_iter" in excinfo.value.field
    assert "cap" in excinfo.value.reason.lower() or "50" in excinfo.value.reason


def test_resolve_rejects_expander_k_over_cap():
    """LD6: expander_k > MAX_BRANCH_EXPANDER_K (4) MUST be rejected."""
    with pytest.raises(BranchPayloadValidation) as excinfo:
        _resolve_mcts_config({"mcts_config": {"expander_k": 5}})
    assert "expander_k" in excinfo.value.field
    assert "cap" in excinfo.value.reason.lower() or "4" in excinfo.value.reason
