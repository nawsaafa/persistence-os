"""Phase 2.3b — branch-escalation error types.

Two exception classes funneled through `_searcher.py`:

- `BranchPayloadValidation(field, reason)` — raised at the ingestion
  layer (Stage 1 byte budget, Stage 2 EDN parse, Stage 3 semantic
  validate, Stage 4 mcts_config bounds-check). Mirrors 2.3a's
  `PlanPayloadValidation` shape exactly.

- `BranchSearchFailed(error_repr, search_id, iter_count, terminated_by)`
  — raised when MCTS search fails to produce a viable winner. LD4
  funnels two distinct paths into this single class:

    Path-1 (bubble-out): `mcts_search` raises `ExpanderContractError`
    or any expander provider exception. The bridge wraps with
    try/except, emits one `:act/result(op=":mcts/search", error=...)`
    datom, raises `BranchSearchFailed` with `search_id=None`,
    `iter_count=None`, `terminated_by=None`.

    Path-2 (post-search detection): `mcts_search` returns successfully
    but `MCTSResult.terminated_by == "all_evaluations_failed"`. The
    bridge inspects post-search and emits the same `:act/result` shape,
    raises `BranchSearchFailed` with all four fields populated from the
    returned `MCTSResult`.

Both paths funnel here so `Coder.run()` exits uniformly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class BranchPayloadValidation(Exception):
    """Ingestion-stage rejection (byte budget / EDN / semantic / bounds).

    Mirrors `persistence.coder._planner_errors.PlanPayloadValidation`
    field-by-field. The `field` is dotted-path notation
    (`"seed_plan_edn"`, `"mcts_config.max_iter"`) so the LLM (or
    diagnostic tooling) can locate the offending input precisely.
    """

    __slots__ = ("field", "reason")

    def __init__(self, *, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


# Mirrors `persistence.plan._mcts.TerminatedBy` — kept as Literal here
# rather than importing to avoid a runtime cycle (`_searcher_errors` is
# imported by `_searcher` which imports `_mcts`).
TerminatedBy = Literal[
    "max_iter",
    "max_unique_plans",
    "simple_regret",
    "wall_clock_exhausted",
    "all_evaluations_failed",
]


@dataclass(frozen=True, slots=True)
class BranchSearchFailed(Exception):
    """Search-layer failure (LD4 two-path funnel).

    `error_repr` is always populated. The other three fields are
    populated for Path-2 (post-search detection) and None for Path-1
    (bubble-out — search never completed enough to have a search_id).
    """

    error_repr: str
    search_id: str | None
    iter_count: int | None
    terminated_by: TerminatedBy | None

    def __str__(self) -> str:
        return self.error_repr
