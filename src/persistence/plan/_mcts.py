"""MCTS over the content-addressed Plan AST (v0.6.5; design §5, §6, §11, ADR-11).

B1: Action ADT + canonical hash + pure ``apply_action`` + depth guard.
B2: ``MCTSConfig`` frozen dataclass + ``__post_init__`` validation.
B3: ``MCTSNode`` + ``MCTSEdge`` non-frozen dataclasses (slots=True).
B4: ``Expander`` Protocol + ``_StaticExpander`` + ``LLMExpander`` (design §8).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Literal, Protocol, Union, runtime_checkable

from persistence.plan._ast import Node
from persistence.plan._errors import PlanDepthExceeded

if TYPE_CHECKING:
    from persistence.plan._skill_library import SkillLibrary

__all__ = [
    "Action",
    "AddStepAction",
    "ComposeWithSkillAction",
    "Expander",
    "LLMExpander",
    "MAX_PLAN_DEPTH",
    "MCTSConfig",
    "MCTSEdge",
    "MCTSNode",
    "SubstituteLeafAction",
    "apply_action",
]

#: Plan-depth ceiling enforced post-construction by ``apply_action``;
#: ``ComposeWithSkillAction`` additionally pre-checks against
#: ``MAX_PLAN_DEPTH // 2`` (design §6 two-layer guard).
MAX_PLAN_DEPTH: int = 32

#: Tolerance for the ``Expander`` prior-sum-to-1.0 contract (design §8).
#: The MCTS loop (B6) asserts ``abs(sum(priors) - 1.0) < _PRIOR_TOL`` on
#: every cache miss; failure raises ``ExpanderContractError``. Empty
#: proposal lists are exempt (terminal-node signal; design §10).
_PRIOR_TOL: float = 1e-6


@dataclass(frozen=True, slots=True)
class SubstituteLeafAction:
    """Replace the Node at ``target_path`` with ``new_leaf``."""
    target_path: tuple[int, ...]
    new_leaf: Node


@dataclass(frozen=True, slots=True)
class AddStepAction:
    """Insert ``new_child`` at index ``at`` under the Node at ``target_path``."""
    target_path: tuple[int, ...]
    at: int
    new_child: Node


@dataclass(frozen=True, slots=True)
class ComposeWithSkillAction:
    """Wrap the subtree at ``target_path`` inside the registered skill ``skill_id``."""
    target_path: tuple[int, ...]
    skill_id: str


#: Closed Action union; dispatch is ``isinstance``-strict (design §5).
Action = Union[SubstituteLeafAction, AddStepAction, ComposeWithSkillAction]


def _action_hash(action: Action) -> str:
    """Return sha256 hex of ``action``'s structural payload (design §5).

    Nested Nodes contribute via ``Node.id`` (the ``_ast.py`` canonical-
    form helper), NOT via ``dataclasses.asdict`` recursion."""
    path_list = list(action.target_path)
    if isinstance(action, SubstituteLeafAction):
        payload: dict[str, object] = {
            "kind": "SubstituteLeafAction",
            "target_path": path_list,
            "new_leaf_id": action.new_leaf.id,
        }
    elif isinstance(action, AddStepAction):
        payload = {
            "kind": "AddStepAction",
            "target_path": path_list,
            "at": action.at,
            "new_child_id": action.new_child.id,
        }
    elif isinstance(action, ComposeWithSkillAction):
        payload = {
            "kind": "ComposeWithSkillAction",
            "target_path": path_list,
            "skill_id": action.skill_id,
        }
    else:
        raise ValueError(f"unknown action kind: {type(action).__name__}")  # pyright: ignore[reportUnreachable]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _plan_depth(plan: Node) -> int:
    """Return depth of ``plan`` (leaf = 1; recursive max+1)."""
    if not plan.children:
        return 1
    return 1 + max(_plan_depth(c) for c in plan.children)


def _resolve_path(plan: Node, path: tuple[int, ...]) -> Node:
    """Walk ``plan`` along child indices in ``path``; raise ``IndexError`` on miss."""
    node = plan
    for i, idx in enumerate(path):
        if idx < 0 or idx >= len(node.children):
            raise IndexError(
                f"path {path!r} step {i} index {idx} out of range "
                f"(parent has {len(node.children)} children)"
            )
        node = node.children[idx]
    return node


def _replace_at_path(plan: Node, path: tuple[int, ...], replacement: Node) -> Node:
    """Return a new tree with ``path`` swapped for ``replacement`` (pure recursion)."""
    if not path:
        return replacement
    head, *rest = path
    if head < 0 or head >= len(plan.children):
        raise IndexError(
            f"path step index {head} out of range "
            f"(parent has {len(plan.children)} children)"
        )
    new_child = _replace_at_path(plan.children[head], tuple(rest), replacement)
    new_children = (*plan.children[:head], new_child, *plan.children[head + 1 :])
    return Node(tag=plan.tag, attrs=plan.attrs, children=new_children)


def _apply_add_step(plan: Node, action: AddStepAction) -> Node:
    """Insert ``action.new_child`` at ``action.at`` under ``action.target_path``."""
    parent = _resolve_path(plan, action.target_path)
    at = action.at
    if at < 0 or at > len(parent.children):
        raise IndexError(
            f"AddStepAction.at {at} out of range "
            f"(parent has {len(parent.children)} children)"
        )
    new_children = (*parent.children[:at], action.new_child, *parent.children[at:])
    new_parent = Node(tag=parent.tag, attrs=parent.attrs, children=new_children)
    return _replace_at_path(plan, action.target_path, new_parent)


def _apply_compose_with_skill(
    plan: Node,
    action: ComposeWithSkillAction,
    skill_library: "SkillLibrary | None",
) -> Node:
    """Wrap subtree at ``target_path`` inside the looked-up skill plan (design §6)."""
    if skill_library is None:
        raise ValueError("ComposeWithSkillAction requires skill_library")
    looked_up = skill_library.lookup(action.skill_id)
    if looked_up is None:
        raise ValueError(
            f"ComposeWithSkillAction: skill_id {action.skill_id!r} not registered"
        )
    skill_plan, _record = looked_up
    skill_depth = _plan_depth(skill_plan)
    if skill_depth > MAX_PLAN_DEPTH // 2:
        raise PlanDepthExceeded(
            f"ComposeWithSkillAction: skill {action.skill_id!r} plan "
            f"depth {skill_depth} > MAX_PLAN_DEPTH//2={MAX_PLAN_DEPTH // 2}"
        )
    subtree = _resolve_path(plan, action.target_path)
    new_skill_root = Node(
        tag=skill_plan.tag,
        attrs=skill_plan.attrs,
        children=(subtree, *skill_plan.children),
    )
    return _replace_at_path(plan, action.target_path, new_skill_root)


def apply_action(
    plan: Node,
    action: Action,
    *,
    skill_library: "SkillLibrary | None" = None,
) -> Node:
    """Return ``plan`` with ``action`` applied (pure; design §6).

    Dispatch is ``isinstance``-strict (third-party look-alikes raise
    ``ValueError``). Result depth is post-checked against
    ``MAX_PLAN_DEPTH``; overflow raises ``PlanDepthExceeded``."""
    if isinstance(action, SubstituteLeafAction):
        # _replace_at_path raises IndexError on invalid target_path.
        new_plan = _replace_at_path(plan, action.target_path, action.new_leaf)
    elif isinstance(action, AddStepAction):
        new_plan = _apply_add_step(plan, action)
    elif isinstance(action, ComposeWithSkillAction):
        new_plan = _apply_compose_with_skill(plan, action, skill_library)
    else:
        raise ValueError(f"unknown action kind: {type(action).__name__}")  # pyright: ignore[reportUnreachable]
    new_depth = _plan_depth(new_plan)
    if new_depth > MAX_PLAN_DEPTH:
        raise PlanDepthExceeded(
            f"apply_action produced a plan of depth {new_depth} "
            f"> MAX_PLAN_DEPTH={MAX_PLAN_DEPTH}"
        )
    return new_plan


@dataclass(frozen=True, slots=True)
class MCTSConfig:
    """PUCT search hyperparameters + termination knobs (design §11, ADR-8).

    All numeric fields are guarded in ``__post_init__`` by a
    bool-isinstance check FIRST (``isinstance(True, int) is True`` —
    Stream A's W1.B / G4 anti-pattern), then a numeric type check, then
    a positivity check.

    ``wall_clock_budget_ms`` is in the schema for forward-compat but
    the v0.6.5 search loop ignores it (no ``:clock/now`` thread; design
    §11 / §15). ``seed`` has no consumer in v0.6.5 — reserved for v0.7+
    Dirichlet noise / shuffle tie-break (design §10)."""

    max_iter: int = 200
    max_unique_plans: int = 64
    simple_regret_threshold: float | None = None
    simple_regret_window: int = 5
    wall_clock_budget_ms: int | None = None
    c_puct: float = 1.4
    expander_k: int = 4
    seed: int = 0

    def __post_init__(self) -> None:
        # Bool-isinstance check FIRST on every numeric field. ``bool`` is a
        # subclass of ``int`` in Python — ``isinstance(True, int) is True``;
        # ``True > 0`` is ``True`` accidentally and ``False > 0`` is
        # ``False`` (vacuous threshold-never-fires). Stream A W1.B / G4.
        for fld in (
            "c_puct",
            "max_iter",
            "max_unique_plans",
            "expander_k",
            "simple_regret_window",
        ):
            v = getattr(self, fld)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                raise ValueError(
                    f"MCTSConfig.{fld} must be a positive number, got {v!r}"
                )

        srt = self.simple_regret_threshold
        if srt is not None:
            if isinstance(srt, bool) or not isinstance(srt, (int, float)) or srt < 0:
                raise ValueError(
                    f"MCTSConfig.simple_regret_threshold must be None or a "
                    f"non-negative number, got {srt!r}"
                )

        wcb = self.wall_clock_budget_ms
        if wcb is not None:
            # Must be ``int`` (NOT ``float``, NOT ``bool``) and positive.
            if isinstance(wcb, bool) or not isinstance(wcb, int) or wcb <= 0:
                raise ValueError(
                    f"MCTSConfig.wall_clock_budget_ms must be None or a "
                    f"positive int, got {wcb!r}"
                )

        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError(f"MCTSConfig.seed must be an int, got {self.seed!r}")


@dataclass(slots=True)
class MCTSEdge:
    """One outgoing edge from an ``MCTSNode`` (design §4, §16, ADR-1).

    NOT ``frozen=True`` — ``visits_through_edge`` and
    ``total_value_through_edge`` are mutated in BACKUP (design §16,
    step 4). The other fields are de-facto immutable post-construction;
    the search loop only writes to the two counters."""

    action_hash: str
    action: Action
    child_plan_id: str
    prior: float
    visits_through_edge: int = 0
    total_value_through_edge: float = 0.0


@dataclass(slots=True)
class MCTSNode:
    """A search-tree node keyed by Plan AST ``Node.id`` (design §4, ADR-1).

    NOT ``frozen=True`` — ``visits``, ``total_value``, ``is_terminal``,
    and the dict-membership of ``children`` mutate during the search
    loop. ``slots=True`` keeps the per-node footprint small under
    transposition tables of thousands of plans.

    Note: there is no ``prior`` on the node (priors are softmax-
    normalized per-parent and live on ``MCTSEdge.prior``) and no parent
    pointer (the search graph is a DAG over plan ids; BACKUP walks the
    path traversed in the current iteration — design §4, §16)."""

    plan_id: str
    visits: int = 0
    total_value: float = 0.0
    children: dict[str, MCTSEdge] = field(default_factory=dict)
    is_terminal: bool = False

    @property
    def q_value(self) -> float:
        """Mean evaluator score backed up through this node (design §4).

        Returns ``0.0`` when ``visits == 0`` (cold-start guard; not NaN,
        not raise)."""
        return 0.0 if self.visits == 0 else self.total_value / self.visits


# --- B4: Expander Protocol + implementations (design §8, ADR-5) --------- #


@runtime_checkable
class Expander(Protocol):
    """Proposes ``(action, prior)`` pairs at expansion time (design §8).

    Implementations are typically LLM-backed but the Protocol shape is
    open: deterministic stubs satisfy it for tests, symbolic expanders
    satisfy it for v0.7+, and "always propose skill X" fixtures satisfy
    it for closed-loop integration tests.

    The expander MUST return softmax-normalized priors (sum to 1.0
    within ``_PRIOR_TOL``). MCTS does NOT re-normalize — the expander
    owns the policy distribution; MCTS owns the tree. Empty sequence =
    terminal-node signal (no prior-sum check); the MCTS loop marks the
    ``MCTSNode`` as ``is_terminal`` and skips further selection.
    """

    def propose(self, plan: Node, *, k: int) -> Sequence[tuple[Action, float]]:
        """Return at most ``k`` ``(action, prior)`` pairs for ``plan``."""
        ...


class LLMExpander:
    """Production wiring; the LLM provider closure is the caller's concern.

    The constructor takes a ``provider: Callable[[Node, int], Sequence[
    tuple[Action, float]]]`` that the caller wires to whichever LLM
    dispatch path is appropriate (Anthropic / Bedrock / etc.). MCTS does
    NOT own the registry seam (design §17 ADR-5: registry indirection
    rejected in W1). The provider is responsible for prompt composition,
    JSON-mode response parsing, and softmax-normalizing priors. The MCTS
    loop (B6) validates ``sum(priors) ≈ 1.0`` on cache miss; this class
    is pure delegation.
    """

    __slots__ = ("_provider",)

    def __init__(
        self,
        provider: Callable[[Node, int], Sequence[tuple[Action, float]]],
    ) -> None:
        self._provider = provider

    def propose(self, plan: Node, *, k: int) -> Sequence[tuple[Action, float]]:
        """Delegate to the wired provider closure (design §8)."""
        return self._provider(plan, k)


class _StaticExpander:
    """Test-only stub. Pinned signature mirrors ``LLMExpander`` for B-series fixtures.

    ``proposals`` is keyed by ``plan.id`` (the content-addressed Plan AST
    hash). On unknown ``plan.id``, ``on_unknown="empty"`` returns ``()``
    (terminal-node signal); ``on_unknown="raise"`` raises ``KeyError``.
    Returned sequence is truncated to at most ``k`` entries — the
    caller's beam-width hint is honoured deterministically by slicing
    the head of the pinned proposal list.
    """

    __slots__ = ("_proposals", "_on_unknown")

    def __init__(
        self,
        proposals: dict[str, Sequence[tuple[Action, float]]],
        *,
        on_unknown: Literal["empty", "raise"] = "empty",
    ) -> None:
        self._proposals = proposals
        self._on_unknown = on_unknown

    def propose(self, plan: Node, *, k: int) -> Sequence[tuple[Action, float]]:
        """Look up by ``plan.id``; truncate to ``k``; honour ``on_unknown``."""
        pinned = self._proposals.get(plan.id)
        if pinned is None:
            if self._on_unknown == "raise":
                raise KeyError(plan.id)
            return ()
        # Tuple-construction materialises the Sequence so the caller
        # cannot accidentally consume a single-pass iterator (design §8
        # forbids generator returns; the Sequence contract is the
        # minimal correct one).
        return tuple(pinned[:k])


# B5-B9 to follow
