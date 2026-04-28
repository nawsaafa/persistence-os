"""B9 — skill-library 4-gate promotion hook (design §12 ADR-9).

Composes ``mcts_search`` (B6/B7/B8) with Stream A's ``promote()`` 4-gate
+ ``SkillLibrary.register`` into the canonical closed-loop recipe.

Lives in its own module so ``_mcts.py`` stays under the design-§18 LOC
ceiling. All public types are re-exported from
``persistence.plan.__init__`` (B9 surface delta:
``mcts_promote`` + ``MCTSPromotionResult``).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from persistence.plan._ast import Node
from persistence.plan._mcts import (
    Evaluator,
    Expander,
    MCTSConfig,
    MCTSResult,
    mcts_search,
)

if TYPE_CHECKING:
    from persistence.fact.db import DB
    from persistence.plan._promotion import PromotionRecord, ReplayEngine
    from persistence.plan._skill_library import SkillLibrary
    from persistence.replay.trajectory import Trajectory


__all__ = ["MCTSPromotionResult", "mcts_promote"]


@dataclass(frozen=True, slots=True)
class MCTSPromotionResult:
    """End-to-end result of one ``mcts_promote`` call.

    Bundles ``search`` (:class:`MCTSResult`), ``promotion``
    (:class:`PromotionRecord`), ``skill_id`` (``"skill/<16-hex>"`` from
    :meth:`SkillLibrary.register`), and ``winner_plan`` (alias of
    ``search.winner`` for the most common call site).
    """

    search: MCTSResult
    promotion: "PromotionRecord"
    skill_id: str
    winner_plan: Node


def mcts_promote(
    initial_plan: Node,
    *,
    expander: Expander,
    evaluator: Evaluator,
    started_at_ms: int,
    db: "DB",
    skill_library: "SkillLibrary",
    held_out_trajectories: "Sequence[Trajectory]",
    replay_engine: "ReplayEngine",
    audit_window: tuple[int, int],
    optimized_score: float,
    baseline_score: float,
    promoted_at_ms: int,
    registered_at_ms: int,
    config: MCTSConfig | None = None,
    epsilon: float | None = None,
    min_g1_count: int | None = None,
    g4_fn: Callable[[], dict] | None = None,
) -> MCTSPromotionResult:
    """Run MCTS search, then 4-gate-promote the winner, then register.

    Composition (design §12 ADR-9): ``mcts_search → promote → register``.
    ``optimize()`` is NOT chained — the search itself plays the
    optimization role, keeping the closed-loop test path free of DSPy.
    The full v0.6.5 closed loop (search + prompt-tuning + promote) is
    the caller's recipe (design §12 line 661-666); this function ships
    the simpler search-as-optimizer composition.

    Purely additive over Stream A. The ``promote`` / gate-fn surfaces
    are unchanged.

    The ``epsilon`` / ``min_g1_count`` / ``g4_fn`` kwargs default to
    ``None`` and forward only when set; ``promote()``'s own defaults
    stay authoritative otherwise. ``held_out_trajectories`` is
    materialized via ``list(...)`` so a generator caller-arg is
    consumed exactly once. ``audit_window`` is the inclusive
    ``(start_ms, end_ms)`` G2 window (caller's choice — wider windows
    mean stricter G2 verification).

    Returns :class:`MCTSPromotionResult` on all-four-gates-pass. Raises
    :class:`GateFailure` (from :func:`promote`) on any gate failure;
    MCTS provenance is already in ``db``, ``skill_library`` is
    unchanged. ``mcts_search``'s own raises
    (``ExpanderContractError``, ``MetricNotRegistered``) propagate
    without reaching ``promote``.
    """
    # Lazy import — local circular-import guard (promote() is in this
    # package).
    from persistence.plan._promotion import promote

    # 1. Pure search.
    result = mcts_search(
        initial_plan,
        expander=expander,
        evaluator=evaluator,
        started_at_ms=started_at_ms,
        config=config,
        skill_library=skill_library,
        db=db,
    )

    # 2. Forward only set overrides → :func:`promote`'s defaults
    # (`_DEFAULT_G3_EPSILON`, `_DEFAULT_MIN_COUNT`, `gate_g4_stub`)
    # stay authoritative when the caller leaves a kwarg ``None``.
    promote_kwargs: dict[str, Any] = {
        "db": db,
        "optimized_score": optimized_score,
        "baseline_score": baseline_score,
        "held_out_trajectories": list(held_out_trajectories),
        "replay_engine": replay_engine,
        "audit_window": audit_window,
        "promoted_at_ms": promoted_at_ms,
    }
    if epsilon is not None:
        promote_kwargs["epsilon"] = epsilon
    if min_g1_count is not None:
        promote_kwargs["min_g1_count"] = min_g1_count
    if g4_fn is not None:
        promote_kwargs["g4_fn"] = g4_fn
    record = promote(result.winner, **promote_kwargs)

    # 3. Register the promoted winner.
    skill_id = skill_library.register(
        result.winner, record, registered_at_ms=registered_at_ms
    )

    return MCTSPromotionResult(
        search=result,
        promotion=record,
        skill_id=skill_id,
        winner_plan=result.winner,
    )
