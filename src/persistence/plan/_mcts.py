"""MCTS over the content-addressed Plan AST (v0.6.5; design §5, §6, §11, ADR-11).

B1: Action ADT + canonical hash + pure ``apply_action`` + depth guard.
B2: ``MCTSConfig`` frozen dataclass + ``__post_init__`` validation.
B3: ``MCTSNode`` + ``MCTSEdge`` non-frozen dataclasses (slots=True).
B4: ``Expander`` Protocol + ``_StaticExpander`` + ``LLMExpander`` (design §8).
B5: ``Evaluator`` Protocol + ``_StaticEvaluator`` + ``LLMJudgeEvaluator`` (design §9).
B6: ``mcts_search`` core loop — SELECT/EXPAND/EVALUATE/BACKUP per §16
    pseudocode + ``MCTSResult`` (no provenance datom side-effects yet;
    B8 wires the schema validation + ``:mcts/search`` summary write).
"""
from __future__ import annotations

import hashlib
import json
import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Literal, Protocol, Union, runtime_checkable

from persistence.plan._ast import Node
from persistence.plan._errors import ExpanderContractError, PlanDepthExceeded

if TYPE_CHECKING:
    from persistence.fact.db import DB
    from persistence.plan._skill_library import SkillLibrary

__all__ = [
    "Action",
    "AddStepAction",
    "ComposeWithSkillAction",
    "Evaluator",
    "Expander",
    "LLMExpander",
    "LLMJudgeEvaluator",
    "MAX_PLAN_DEPTH",
    "MCTSConfig",
    "MCTSEdge",
    "MCTSNode",
    "MCTSResult",
    "SubstituteLeafAction",
    "apply_action",
    "mcts_search",
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


# --- B5: Evaluator Protocol + implementations (design §9, ADR-6) -------- #


@runtime_checkable
class Evaluator(Protocol):
    """Returns a scalar reward for a leaf plan (design §9, ADR-6).

    Return type is exactly ``float`` — never ``None``. Failure is
    signalled by raising; ``error_class != null`` in the provenance
    datom is the unambiguous "raised vs returned" signal (design §13).
    The MCTS loop (B9) caches by ``plan.id`` and rejects non-finite
    scores at the boundary via ``EvaluatorContractError``.
    """

    def evaluate(self, plan: Node) -> float:
        """Return a finite scalar reward for ``plan``."""
        ...


class LLMJudgeEvaluator:
    """Production wiring; LLM provider closure is the caller's concern.

    Pure delegation to a ``provider: Callable[[Node], float]``. MCTS
    does NOT own the registry seam (design §17 ADR-6 mirrors ADR-5).
    The MCTS loop (B9) validates finite-score and caches by ``plan.id``.
    """

    __slots__ = ("_provider",)

    def __init__(self, provider: Callable[[Node], float]) -> None:
        self._provider = provider

    def evaluate(self, plan: Node) -> float:
        """Delegate to the wired provider closure (design §9)."""
        return self._provider(plan)


class _StaticEvaluator:
    """Test-only stub. Pinned signature mirrors ``LLMJudgeEvaluator``.

    ``scores`` keyed by ``plan.id``. On unknown ``plan.id``,
    ``on_unknown="zero"`` returns ``0.0`` (mirrors ``MCTSNode.q_value``'s
    zero-not-NaN posture); ``on_unknown="raise"`` raises ``KeyError``.
    """

    __slots__ = ("_scores", "_on_unknown")

    def __init__(
        self,
        scores: dict[str, float],
        *,
        on_unknown: Literal["zero", "raise"] = "zero",
    ) -> None:
        self._scores = scores
        self._on_unknown = on_unknown

    def evaluate(self, plan: Node) -> float:
        """Look up ``plan.id`` in pinned scores; honour ``on_unknown`` on miss."""
        pinned = self._scores.get(plan.id)
        if pinned is None:
            if self._on_unknown == "raise":
                raise KeyError(plan.id)
            return 0.0
        return pinned


def _is_finite_score(value: float) -> bool:
    """True iff ``value`` is finite numeric (design §9 NaN/Inf defense).

    Rejects NaN, +Inf, -Inf, AND ``bool`` (Stream A W1.B / G4 anti-
    pattern). Raise-site is B9's reject path."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


# --- B6: mcts_search core loop (design §15, §16, ADR-7) ---------------- #
#
# B8 LIFTING ESCAPE HATCH: when the file crosses the design §18 LOC
# budget (≤800 watermark), B8 should lift `_build_iteration_datoms` and
# the evaluator-side reject placeholders into a separate `_mcts_datoms.py`
# helper file. B6 ships placeholder dicts via `_eval_reject_record` and
# inline `pending_datoms.append({...})` calls; B8's schema-validation
# refactor is the natural lift point. The B6 surface itself stays in
# `_mcts.py` — only the datom-building helpers move.


#: Search-id hex width (design §15 module-globals enumeration). Mirrors
#: ``_skill_library._SKILL_ID_HEX_WIDTH = 16`` for consistency across
#: content-addressed identifier namespaces.
_SEARCH_ID_HEX_WIDTH: int = 16

#: Termination-reason union (design §11, ADR-8). 6 reasons OR-combined;
#: ``wall_clock`` is unreachable in v0.6.5 (forward-compat slot).
TerminatedBy = Literal[
    "max_iter",
    "max_unique_plans",
    "simple_regret",
    "wall_clock",
    "exhausted",
    "all_evaluations_failed",
]


def _hash_config(config: MCTSConfig) -> str:
    """Return a stable canonical-JSON sha256 hex digest of ``config`` (design §13).

    All fields are JSON-native; ``allow_nan=False`` mirrors ``Node.id``."""
    payload: dict[str, object] = {
        "max_iter": config.max_iter,
        "max_unique_plans": config.max_unique_plans,
        "simple_regret_threshold": config.simple_regret_threshold,
        "simple_regret_window": config.simple_regret_window,
        "wall_clock_budget_ms": config.wall_clock_budget_ms,
        "c_puct": config.c_puct,
        "expander_k": config.expander_k,
        "seed": config.seed,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _derive_search_id(
    initial_plan_id: str, config: MCTSConfig, started_at_ms: int
) -> str:
    """Return ``"mcts/<16-hex>"`` content-addressed search id (design §13).

    Deterministic function of ``(initial_plan_id, config_hash,
    started_at_ms)``: identical triples produce byte-identical ids."""
    payload: dict[str, object] = {
        "initial_plan_id": initial_plan_id,
        "config_hash": _hash_config(config),
        "started_at": started_at_ms,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return "mcts/" + digest[:_SEARCH_ID_HEX_WIDTH]


def _select_child(node: MCTSNode, c_puct: float) -> MCTSEdge | None:
    """Return the PUCT-best edge from ``node``; ``None`` if no children (design §7).

    Formula: ``Q(s,a) + c_puct * P(s,a) * sqrt(parent.visits) / (1 + N(s,a))``
    where ``Q = total_value_through_edge / max(1, visits_through_edge)``
    (cold-start ``0.0``). Tie-break: first edge in dict-insertion
    order (Python 3.7+ preserves insertion order; determinism-safe)."""
    if not node.children:
        return None
    parent_visits = node.visits
    sqrt_parent = math.sqrt(parent_visits) if parent_visits > 0 else 1.0
    best_edge: MCTSEdge | None = None
    best_score = -math.inf
    for edge in node.children.values():
        n_sa = edge.visits_through_edge
        q = (edge.total_value_through_edge / n_sa) if n_sa > 0 else 0.0
        u = c_puct * edge.prior * sqrt_parent / (1 + n_sa)
        score = q + u
        if score > best_score:
            best_score = score
            best_edge = edge
    return best_edge


def _populate_children(
    node: MCTSNode,
    proposals: Sequence[tuple[Action, float]],
    plan_by_id: dict[str, Node],
    transposition: dict[str, MCTSNode],
    skill_library: "SkillLibrary | None",
    pending_datoms: list[object],
) -> int:
    """Apply each proposed action; build ``MCTSEdge`` for successes; record rejects.

    Returns the number of successful expansions. Failures append a
    placeholder reject record to ``pending_datoms`` (B8 closes the
    schema validation). Within-parent deterministic dedup: a repeated
    ``action_hash`` under the same parent is silently skipped (first
    proposal wins, proposal-iteration order is the tie-break)."""
    plan = plan_by_id[node.plan_id]
    successes = 0
    for action, prior in proposals:
        try:
            new_plan = apply_action(plan, action, skill_library=skill_library)
        except (ValueError, IndexError, PlanDepthExceeded) as exc:
            # B8: schema validation + serialization wires here. For B6,
            # record a placeholder dict so reject-counting tests have a
            # non-empty pending_datoms accumulator to scan.
            pending_datoms.append(
                {
                    "phase": "reject",
                    "plan_id": node.plan_id,
                    "action_hash": _action_hash(action),
                    "reason": _classify_apply_failure(action, exc),
                    "error_class": type(exc).__name__,
                    "error_repr": repr(exc),
                }
            )
            continue
        action_hash = _action_hash(action)
        if action_hash in node.children:
            # Same parent + same action_hash → same child. Silently
            # dedup; the existing edge stays.
            continue
        child_plan_id = new_plan.id
        plan_by_id.setdefault(child_plan_id, new_plan)
        transposition.setdefault(child_plan_id, MCTSNode(plan_id=child_plan_id))
        edge = MCTSEdge(
            action_hash=action_hash,
            action=action,
            child_plan_id=child_plan_id,
            prior=prior,
        )
        node.children[action_hash] = edge
        successes += 1
    return successes


def _classify_apply_failure(action: Action, exc: BaseException) -> str:
    """Map an ``apply_action`` raise to a design §13 reject ``reason`` tag."""
    if isinstance(exc, PlanDepthExceeded):
        return "plan_too_deep"
    if isinstance(action, ComposeWithSkillAction):
        msg = str(exc)
        if "skill_library" in msg or "not registered" in msg:
            return "skill_not_registered"
    return "plan_construction_raised"


@dataclass(frozen=True, slots=True)
class MCTSResult:
    """Frozen result of one ``mcts_search`` invocation (design §15).

    All fields are deterministic functions of the inputs; two
    identical-input invocations produce byte-identical ``MCTSResult``
    instances incl. ``search_id`` (content-addressed per design §13 /
    ADR-7, NOT a UUID).

    ``tree_dump`` is the load-bearing determinism pin: a frozen tuple
    of ``(parent_plan_id, child_plan_id, action_hash, visits, q)`` per
    edge, lex-sorted by ``(parent_plan_id, child_plan_id,
    action_hash)`` under string-lex (UTF-8 byte ordering)."""

    winner: Node
    winner_plan_id: str
    initial_plan_id: str
    search_id: str
    iter_count: int
    unique_plans_visited: int
    terminated_by: TerminatedBy
    root_q: float
    tree_dump: tuple[tuple[str, str, str, int, float], ...]


def _build_tree_dump(
    transposition: dict[str, MCTSNode],
) -> tuple[tuple[str, str, str, int, float], ...]:
    """Return the canonical ``tree_dump`` (design §15).

    Every edge becomes a 5-tuple ``(parent_plan_id, child_plan_id,
    action_hash, visits, q_value)``; the dump is lex-sorted by the
    first three elements (every element is a hex ``str``, so Python's
    default ``str.__lt__`` produces the byte-ordered comparison).
    ``q`` is the EDGE q-value (cold-start unvisited edges = ``0.0``)."""
    rows: list[tuple[str, str, str, int, float]] = []
    for parent_plan_id, parent in transposition.items():
        for action_hash, edge in parent.children.items():
            n_sa = edge.visits_through_edge
            q = (edge.total_value_through_edge / n_sa) if n_sa > 0 else 0.0
            rows.append(
                (parent_plan_id, edge.child_plan_id, action_hash, n_sa, q)
            )
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    return tuple(rows)


def _pick_winner_edge(root: MCTSNode) -> MCTSEdge | None:
    """Return the most-visited root edge (design §16 / OQ-2 AlphaZero).

    Tie-break: highest q_value, then ``action_hash`` lex-lowest.
    Returns ``None`` iff the root has no children."""
    if not root.children:
        return None
    candidates = list(root.children.values())

    def _key(e: MCTSEdge) -> tuple[int, float, str]:
        n_sa = e.visits_through_edge
        q = (e.total_value_through_edge / n_sa) if n_sa > 0 else 0.0
        return (n_sa, q, e.action_hash)

    # Higher visits / q win; lex-LOWEST action_hash breaks final tie.
    best = candidates[0]
    best_n, best_q, best_hash = _key(best)
    for e in candidates[1:]:
        n, q, h = _key(e)
        if (n, q) > (best_n, best_q) or (
            (n, q) == (best_n, best_q) and h < best_hash
        ):
            best, best_n, best_q, best_hash = e, n, q, h
    return best


def _eval_reject_record(
    plan_id: str,
    reason: str,
    *,
    error: BaseException | None = None,
    raw_score: object = None,
) -> dict[str, object]:
    """Build a placeholder evaluator-side reject record for ``pending_datoms``.

    B8 closes the schema validation; today's records are dicts so B6
    tests can scan for ``phase="reject"`` + ``reason`` without
    depending on the datom schema."""
    record: dict[str, object] = {
        "phase": "reject",
        "plan_id": plan_id,
        "reason": reason,
    }
    if error is not None:
        record["error_class"] = type(error).__name__
        record["error_repr"] = repr(error)
    if raw_score is not None:
        record["raw_score_repr"] = repr(raw_score)
    return record


def mcts_search(
    initial_plan: Node,
    *,
    expander: Expander,
    evaluator: Evaluator,
    started_at_ms: int,
    config: MCTSConfig | None = None,
    skill_library: "SkillLibrary | None" = None,
    db: "DB | None" = None,
) -> MCTSResult:
    """PUCT tree search over the content-addressed Plan AST (design §15, §16).

    Runs SELECT → EXPAND → EVALUATE → BACKUP per design §16 pseudocode.
    Returns the winner Plan + a tree summary. Does NOT call
    ``optimize()`` or ``promote()`` — the closed loop is the caller's
    recipe (design §12 ADR-9):

    .. code-block:: python

        result = mcts_search(initial_plan, expander=..., evaluator=..., started_at_ms=t)
        optimized = optimize(result.winner, training_set=..., ...)
        record = promote(optimized.plan, db=db, ...)
        skill_id = SkillLibrary(db).register(optimized.plan, record, ...)

    Determinism contract (design §10): under fixed
    ``(initial_plan, config, started_at_ms, expander_responses,
    evaluator_responses)`` the search trajectory is byte-identical.

    Evaluator runs on the JUST-EXPANDED PARENT (design §16 invariant
    5; rollout depth = 1).

    B6 ships zero datom side-effects: ``pending_datoms`` is built
    locally during the iteration but never transacted; B8 closes the
    schema validation + ``:mcts/search`` summary write."""
    if not isinstance(started_at_ms, int) or isinstance(started_at_ms, bool):
        raise ValueError(
            f"mcts_search.started_at_ms must be a positive int, got {started_at_ms!r}"
        )
    if started_at_ms <= 0:
        raise ValueError(
            f"mcts_search.started_at_ms must be a positive int, got {started_at_ms!r}"
        )
    if config is None:
        config = MCTSConfig()

    transposition: dict[str, MCTSNode] = {}
    expander_cache: dict[str, tuple[tuple[Action, float], ...]] = {}
    evaluator_cache: dict[str, float] = {}
    plan_by_id: dict[str, Node] = {initial_plan.id: initial_plan}

    root = MCTSNode(plan_id=initial_plan.id)
    transposition[initial_plan.id] = root
    search_id = _derive_search_id(initial_plan.id, config, started_at_ms)

    iter_index = 0
    eval_attempts = 0
    eval_failures = 0
    terminated_by: TerminatedBy = "max_iter"

    # Simple-regret consecutive-window tracker (design §11).
    simple_regret_streak = 0

    while True:
        # --- Termination checks BEFORE running iteration ---
        if iter_index >= config.max_iter:
            terminated_by = "max_iter"
            break
        if len(transposition) >= config.max_unique_plans:
            terminated_by = "max_unique_plans"
            break
        if (
            config.simple_regret_threshold is not None
            and iter_index >= config.simple_regret_window
            and len(root.children) >= 2
        ):
            sorted_edges = sorted(
                root.children.values(),
                key=lambda e: e.visits_through_edge,
                reverse=True,
            )
            top1, top2 = sorted_edges[0], sorted_edges[1]
            n1 = top1.visits_through_edge
            n2 = top2.visits_through_edge
            q1 = (top1.total_value_through_edge / n1) if n1 > 0 else 0.0
            q2 = (top2.total_value_through_edge / n2) if n2 > 0 else 0.0
            gap = q1 - q2
            if gap >= config.simple_regret_threshold:
                simple_regret_streak += 1
                if simple_regret_streak >= config.simple_regret_window:
                    terminated_by = "simple_regret"
                    break
            else:
                simple_regret_streak = 0
        # all_evaluations_failed: every attempted evaluator call raised.
        # Surface as termination only after at least one attempt. Design
        # §18 + impl plan §B7: emit ``UserWarning`` so callers don't
        # silently consume a winner==initial_plan result.
        if eval_attempts > 0 and eval_attempts == eval_failures:
            terminated_by = "all_evaluations_failed"
            warnings.warn(
                f"mcts_search terminated by all_evaluations_failed: "
                f"every evaluator call raised "
                f"({eval_failures} failures over {eval_attempts} attempts); "
                f"winner = initial_plan",
                UserWarning,
                stacklevel=2,
            )
            break

        path: list[tuple[MCTSNode, MCTSEdge]] = []
        pending_datoms: list[object] = []

        # --- 1. SELECT: walk root → leaf via PUCT ---
        node = root
        while node.children and not node.is_terminal:
            edge = _select_child(node, config.c_puct)
            if edge is None:
                break
            path.append((node, edge))
            node = transposition[edge.child_plan_id]

        # --- 2. EXPAND: if not terminal AND no children yet ---
        if not node.is_terminal and not node.children:
            cached_proposals = expander_cache.get(node.plan_id)
            if cached_proposals is None:
                proposals = tuple(
                    expander.propose(plan_by_id[node.plan_id], k=config.expander_k)
                )
                if proposals and abs(sum(p for _, p in proposals) - 1.0) >= _PRIOR_TOL:
                    raise ExpanderContractError(
                        f"Expander.propose returned priors summing to "
                        f"{sum(p for _, p in proposals)!r}, not 1.0 ± _PRIOR_TOL "
                        f"(plan_id={node.plan_id!r})"
                    )
                expander_cache[node.plan_id] = proposals
                # B8: schema validation + serialization wires here.
                pending_datoms.append(
                    {
                        "phase": "expand",
                        "plan_id": node.plan_id,
                        "proposals": proposals,
                    }
                )
            else:
                proposals = cached_proposals
            if not proposals:
                node.is_terminal = True
            else:
                # Iter 0 root-empty short-circuit handled at iter end via
                # "exhausted" check (root.is_terminal AND no children).
                _populate_children(
                    node,
                    proposals,
                    plan_by_id,
                    transposition,
                    skill_library,
                    pending_datoms,
                )
                if not node.children:
                    # Every proposal rejected → no descent possible.
                    node.is_terminal = True

        # --- "exhausted" termination — root has no children at iter 0 ---
        if (
            iter_index == 0
            and node is root
            and root.is_terminal
            and not root.children
        ):
            terminated_by = "exhausted"
            iter_index += 1
            # B8: schema validation + db.transact(pending_datoms, t=...) wires here.
            break

        # --- 3. EVALUATE on the JUST-EXPANDED PARENT (design §16 inv 5) ---
        leaf_plan_id = node.plan_id
        cached_score = evaluator_cache.get(leaf_plan_id)
        if cached_score is not None:
            score: float = cached_score
        else:
            eval_attempts += 1
            try:
                raw_score = evaluator.evaluate(plan_by_id[leaf_plan_id])
            except Exception as exc:  # noqa: BLE001 — design §14 reject capture
                eval_failures += 1
                pending_datoms.append(_eval_reject_record(
                    leaf_plan_id, "evaluator_raised", error=exc
                ))
                iter_index += 1
                continue
            if raw_score is None:
                eval_failures += 1
                pending_datoms.append(_eval_reject_record(
                    leaf_plan_id, "evaluator_returned_none"
                ))
                iter_index += 1
                continue
            if not _is_finite_score(raw_score):
                eval_failures += 1
                pending_datoms.append(_eval_reject_record(
                    leaf_plan_id, "evaluator_returned_non_finite",
                    raw_score=raw_score,
                ))
                iter_index += 1
                continue
            evaluator_cache[leaf_plan_id] = raw_score
            score = raw_score
            pending_datoms.append({
                "phase": "evaluate",
                "plan_id": leaf_plan_id,
                "score": raw_score,
            })

        # --- 4. BACKUP along the traversed path ---
        for parent_node, edge in path:
            parent_node.visits += 1
            edge.visits_through_edge += 1
            edge.total_value_through_edge += score
            parent_node.total_value += score
        # The leaf itself receives a +1 visit (design §16 inv 2 leaf-of-path
        # case + the §16 pseudocode closing `node.visits += 1`).
        node.visits += 1
        node.total_value += score

        # B8: schema validation + db.transact(pending_datoms, t=...) wires here.

        iter_index += 1

    # --- Winner selection ---
    # Design §11 / impl plan §B7: when termination is
    # ``all_evaluations_failed`` the winner falls back to
    # ``initial_plan`` regardless of whether root accumulated children
    # (root edges all have visits=0; their q is meaningless).
    if terminated_by == "all_evaluations_failed":
        winner_plan = initial_plan
        winner_plan_id = initial_plan.id
        root_q = 0.0
    else:
        winner_edge = _pick_winner_edge(root)
        if winner_edge is None:
            winner_plan = initial_plan
            winner_plan_id = initial_plan.id
            root_q = 0.0
        else:
            winner_plan = plan_by_id[winner_edge.child_plan_id]
            winner_plan_id = winner_edge.child_plan_id
            n_sa = winner_edge.visits_through_edge
            root_q = (
                (winner_edge.total_value_through_edge / n_sa) if n_sa > 0 else 0.0
            )

    return MCTSResult(
        winner=winner_plan,
        winner_plan_id=winner_plan_id,
        initial_plan_id=initial_plan.id,
        search_id=search_id,
        iter_count=iter_index,
        unique_plans_visited=len(transposition),
        terminated_by=terminated_by,
        root_q=root_q,
        tree_dump=_build_tree_dump(transposition),
    )


# B7-B9 to follow
