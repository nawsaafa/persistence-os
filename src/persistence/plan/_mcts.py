"""MCTS over the content-addressed Plan AST (v0.6.5; design §5, §6, §11).

B1-B5: Action ADT + applicator, MCTSConfig, MCTSNode/Edge dataclasses,
Expander + Evaluator Protocols + static stubs + LLM wirings.
B6-B7: ``mcts_search`` core loop (SELECT/EXPAND/EVALUATE/BACKUP per
§16) + 6-reason termination + ``MCTSResult``.
B8: ``:mcts/iteration`` provenance + ``mcts/prev-hash`` Merkle chain +
one ``db.transact()`` per iteration; schema/builders in ``_mcts_datoms.py``.
"""
from __future__ import annotations

import hashlib
import json
import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Literal, Protocol, Union, runtime_checkable

from persistence.plan._ast import Node
from persistence.plan._errors import ExpanderContractError, PlanDepthExceeded
from persistence.plan import _mcts_datoms as _datoms

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


class _PlanCycleDetected(ValueError):
    """``ComposeWithSkillAction`` would graft the candidate inside a
    skill_plan that already contains it (design §14). Subclass of
    ``ValueError`` so the loop's existing ``except (ValueError,
    IndexError, PlanDepthExceeded)`` catches it;
    :func:`_classify_apply_failure` maps it to ``"compose_creates_cycle"``."""


class _SkillNotRegistered(ValueError):
    """``ComposeWithSkillAction`` referenced a ``skill_id`` not present in
    the SkillLibrary, or no library was passed. Subclass of ``ValueError``
    so the loop's existing catch handles it;
    :func:`_classify_apply_failure` maps it to ``"skill_not_registered"``
    by isinstance — no fragile message-substring match."""


def _collect_node_ids(node: Node, acc: set[str]) -> None:
    """In-place DFS collect of ``Node.id`` over ``node`` and descendants."""
    acc.add(node.id)
    for child in node.children:
        _collect_node_ids(child, acc)


def _apply_compose_with_skill(
    plan: Node,
    action: ComposeWithSkillAction,
    skill_library: "SkillLibrary | None",
) -> Node:
    """Wrap subtree at ``target_path`` inside the looked-up skill plan (design §6)."""
    if skill_library is None:
        raise _SkillNotRegistered(
            "ComposeWithSkillAction requires skill_library"
        )
    looked_up = skill_library.lookup(action.skill_id)
    if looked_up is None:
        raise _SkillNotRegistered(
            f"ComposeWithSkillAction: skill_id {action.skill_id!r} not registered"
        )
    skill_plan, _record = looked_up
    skill_depth = _plan_depth(skill_plan)
    if skill_depth > MAX_PLAN_DEPTH // 2:
        raise PlanDepthExceeded(
            f"ComposeWithSkillAction: skill {action.skill_id!r} plan "
            f"depth {skill_depth} > MAX_PLAN_DEPTH//2={MAX_PLAN_DEPTH // 2}"
        )
    # Cycle guard (design §14): skill_plan must NOT already contain the
    # candidate plan's content-hash as a subtree. Cheap subtree-hash scan.
    skill_ids: set[str] = set()
    _collect_node_ids(skill_plan, skill_ids)
    if plan.id in skill_ids:
        raise _PlanCycleDetected(
            f"ComposeWithSkillAction: skill {action.skill_id!r} plan "
            f"already contains candidate plan id {plan.id!r} as a subtree"
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


def judge(plan: Node, *, evaluator: Evaluator) -> float:
    """Score ``plan`` using ``evaluator`` and return the resulting float.

    Phase 2.0f curated invocation surface for the :class:`Evaluator`
    Protocol. Pure thin wrapper — delegates to
    ``evaluator.evaluate(plan)`` with no transformation. Caller embeds
    any rubric / criteria inside the evaluator's own state (typically
    ``LLMJudgeEvaluator(provider=lambda p: my_llm.score(p, criteria=...))``).

    Args:
        plan: the Plan AST to score.
        evaluator: any object satisfying the :class:`Evaluator` Protocol —
            ``LLMJudgeEvaluator``, ``_StaticEvaluator``, or a custom
            implementation. Keyword-only to prevent accidental
            positional misuse at the call site.

    Returns:
        The float returned by ``evaluator.evaluate(plan)``.

    Raises:
        Whatever the evaluator raises. The MCTS loop (B9) wraps
        non-finite returns and exceptions into
        :class:`EvaluatorContractError`; this top-level surface does
        NOT — callers who want that envelope go through
        :func:`mcts_search`.

    Phase 2.0f / Bhatt principle 5 (multi-agent collaboration). The
    curated SDK method ``s.plan.judge`` is a thin pass-through to this
    function.
    """
    return evaluator.evaluate(plan)


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
# B8 LIFT COMPLETE: provenance machinery (`:mcts/iteration` schema,
# `:mcts/search` summary, `mcts/prev-hash` Merkle chain, output payload
# records, reject-record builder, synthetic-time helper) lives in
# `_mcts_datoms.py`. The loop here threads `prev_hash` across iterations
# and accumulates per-iteration `pending_facts` lists for one
# `db.transact()` call per iteration (design §13 transaction granularity).


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
    pending_facts: list[dict[str, Any]],
    *,
    search_id: str,
    iter_index: int,
    prev_hash: str,
    started_at_ms: int,
) -> tuple[int, str]:
    """Apply each proposed action; build ``MCTSEdge`` for successes; record rejects.

    Returns ``(successes, new_prev_hash)``. Each rejected proposal
    appends a ``phase="reject"`` iteration row's facts (7 facts; design
    §13) to ``pending_facts``, advancing ``prev_hash`` along the
    Merkle chain in proposal-iteration order (design §13 within-
    transaction ordering — W2 MINOR-7 pin).

    Within-parent deterministic dedup: a repeated ``action_hash`` under
    the same parent is silently skipped (first proposal wins, proposal-
    iteration order is the tie-break)."""
    plan = plan_by_id[node.plan_id]
    successes = 0
    for action, _prior in proposals:
        try:
            new_plan = apply_action(plan, action, skill_library=skill_library)
        except (ValueError, IndexError, PlanDepthExceeded) as exc:
            reason = _classify_apply_failure(exc)
            # Phase 2.3c.2 LD3 — forward skill_library so reject records
            # of ComposeWithSkillAction carry composed_skill_content_hash.
            output_value = _datoms._reject_record(
                action, node.plan_id, reason, error=exc,
                skill_library=skill_library,
            )
            inputs_hash = _datoms._hash_payload({
                "plan_id": node.plan_id,
                "action_hash": _action_hash(action),
            })
            group = _datoms._make_iter_facts(
                search_id=search_id,
                iter_index=iter_index,
                phase="reject",
                plan_id=node.plan_id,
                inputs_hash=inputs_hash,
                output_value=output_value,
                prev_hash=prev_hash,
                started_at_ms=started_at_ms,
                action_hash=_action_hash(action),
                error=exc,
            )
            pending_facts.extend(group.facts)
            prev_hash = group.content_hash
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
            prior=_prior,
        )
        node.children[action_hash] = edge
        successes += 1
    return successes, prev_hash


def _classify_apply_failure(exc: BaseException) -> str:
    """Map an ``apply_action`` raise to a design §13 reject ``reason`` tag."""
    if isinstance(exc, PlanDepthExceeded):
        return "plan_too_deep"
    if isinstance(exc, _PlanCycleDetected):
        return "compose_creates_cycle"
    if isinstance(exc, _SkillNotRegistered):
        return "skill_not_registered"
    return "plan_construction_raised"


def _emit_eval_reject(
    *,
    db: "DB | None",
    search_id: str,
    iter_index: int,
    leaf_plan_id: str,
    inputs_hash: str,
    reason: str,
    started_at_ms: int,
    pending_facts: list[dict[str, Any]],
    prev_hash: str,
    error: BaseException | None = None,
    raw_score: object = None,
) -> str:
    """Emit one ``phase="reject"`` evaluator-side group + flush iter txn.

    Lifted from the three near-identical eval-reject branches (raised /
    None / non-finite). Returns the new ``prev_hash``; caller does
    ``iter_index += 1`` + ``continue``."""
    rec = _datoms._reject_record(
        None, leaf_plan_id, reason, error=error, raw_score=raw_score,
    )
    group = _datoms._make_iter_facts(
        search_id=search_id, iter_index=iter_index, phase="reject",
        plan_id=leaf_plan_id, inputs_hash=inputs_hash,
        output_value=rec, prev_hash=prev_hash,
        started_at_ms=started_at_ms, action_hash=None, error=error,
    )
    pending_facts.extend(group.facts)
    if db is not None and pending_facts:
        db.transact(
            pending_facts,
            provenance=_datoms._iter_provenance(search_id, iter_index),
        )
    return group.content_hash


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

    Runs SELECT → EXPAND → EVALUATE → BACKUP. Returns winner + tree
    summary. Does NOT call ``optimize()`` / ``promote()`` (caller's
    closed loop, design §12 ADR-9). Evaluator runs on the JUST-EXPANDED
    PARENT (design §16 inv 5; rollout depth = 1). Determinism: byte-
    identical under fixed inputs (design §10).

    B8 provenance (design §13). When ``db is not None``: one
    ``db.transact`` per iteration with ``phase="expand"`` /
    ``phase="evaluate"`` / ``phase="reject"`` rows; ``mcts/prev-hash``
    chains every group; iter 0 prepends the ``mcts/search`` summary;
    search end writes winner / iter-count / terminated-by /
    finished-at. Cache-hit-only iterations emit zero datoms.
    ``db is None`` short-circuits the transacts (chain still advances
    for symmetry; unobservable)."""
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
    config_hash = _hash_config(config)
    search_id = _derive_search_id(initial_plan.id, config, started_at_ms)

    # Initial Merkle anchor (design §13): iter 0's first prev-hash
    # links to the up-front search-summary content hash. Only the
    # known-at-start slots participate (winner/iter-count/terminated-by/
    # finished-at are written at search end).
    prev_hash: str = _datoms._hash_search_summary(
        initial_plan.id, config_hash, started_at_ms
    )

    iter_index = 0
    eval_attempts = 0
    eval_failures = 0
    terminated_by: TerminatedBy = "max_iter"
    search_summary_emitted = False  # iter 0's transact prepends summary

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
        pending_facts: list[dict[str, Any]] = []

        # iter 0: prepend the ``mcts/search`` summary group.
        if not search_summary_emitted:
            pending_facts.extend(_datoms._build_search_summary_start_facts(
                search_id=search_id, initial_plan_id=initial_plan.id,
                config_hash=config_hash, started_at_ms=started_at_ms,
            ))
            search_summary_emitted = True

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
                # phase="expand": full-payload proposal records (W2 M4
                # canonical Node bytes for synthesized Nodes). Phase
                # 2.3c.2 LD3 — forward skill_library so
                # ComposeWithSkillAction proposals carry the looked-up
                # plan's content hash in provenance for replay-explainability.
                expand_output = [
                    _datoms._expand_proposal_record(a, p, skill_library=skill_library)
                    for a, p in proposals
                ]
                expand_group = _datoms._make_iter_facts(
                    search_id=search_id, iter_index=iter_index,
                    phase="expand", plan_id=node.plan_id,
                    inputs_hash=_datoms._expand_inputs_hash(
                        node.plan_id, config.expander_k
                    ),
                    output_value=expand_output, prev_hash=prev_hash,
                    started_at_ms=started_at_ms, action_hash=None,
                )
                pending_facts.extend(expand_group.facts)
                prev_hash = expand_group.content_hash
            else:
                proposals = cached_proposals
            if not proposals:
                node.is_terminal = True
            else:
                # ``_populate_children`` chains prev_hash for rejected
                # proposals AFTER the expand row (design §13 W2 MINOR-7).
                _, prev_hash = _populate_children(
                    node, proposals, plan_by_id, transposition,
                    skill_library, pending_facts,
                    search_id=search_id, iter_index=iter_index,
                    prev_hash=prev_hash, started_at_ms=started_at_ms,
                )
                if not node.children:
                    node.is_terminal = True

        # --- "exhausted" termination — root has no children at iter 0 ---
        if (
            iter_index == 0
            and node is root
            and root.is_terminal
            and not root.children
        ):
            terminated_by = "exhausted"
            if db is not None and pending_facts:
                db.transact(
                    pending_facts,
                    provenance=_datoms._iter_provenance(search_id, iter_index),
                )
            iter_index += 1
            break

        # --- 3. EVALUATE on the JUST-EXPANDED PARENT (design §16 inv 5) ---
        leaf_plan_id = node.plan_id
        cached_score = evaluator_cache.get(leaf_plan_id)
        if cached_score is not None:
            score: float = cached_score
        else:
            eval_attempts += 1
            inputs_hash = _datoms._evaluate_inputs_hash(leaf_plan_id)
            try:
                raw_score = evaluator.evaluate(plan_by_id[leaf_plan_id])
            except Exception as exc:  # noqa: BLE001 — design §14 reject capture
                eval_failures += 1
                prev_hash = _emit_eval_reject(
                    db=db, search_id=search_id, iter_index=iter_index,
                    leaf_plan_id=leaf_plan_id, inputs_hash=inputs_hash,
                    reason="evaluator_raised", started_at_ms=started_at_ms,
                    pending_facts=pending_facts, prev_hash=prev_hash,
                    error=exc,
                )
                iter_index += 1
                continue
            if raw_score is None:
                eval_failures += 1
                prev_hash = _emit_eval_reject(
                    db=db, search_id=search_id, iter_index=iter_index,
                    leaf_plan_id=leaf_plan_id, inputs_hash=inputs_hash,
                    reason="evaluator_returned_none",
                    started_at_ms=started_at_ms,
                    pending_facts=pending_facts, prev_hash=prev_hash,
                )
                iter_index += 1
                continue
            if not _is_finite_score(raw_score):
                eval_failures += 1
                prev_hash = _emit_eval_reject(
                    db=db, search_id=search_id, iter_index=iter_index,
                    leaf_plan_id=leaf_plan_id, inputs_hash=inputs_hash,
                    reason="evaluator_returned_non_finite",
                    started_at_ms=started_at_ms,
                    pending_facts=pending_facts, prev_hash=prev_hash,
                    raw_score=raw_score,
                )
                iter_index += 1
                continue
            evaluator_cache[leaf_plan_id] = raw_score
            score = raw_score
            evaluate_group = _datoms._make_iter_facts(
                search_id=search_id, iter_index=iter_index,
                phase="evaluate", plan_id=leaf_plan_id,
                inputs_hash=inputs_hash, output_value=raw_score,
                prev_hash=prev_hash, started_at_ms=started_at_ms,
                action_hash=None,
            )
            pending_facts.extend(evaluate_group.facts)
            prev_hash = evaluate_group.content_hash

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

        # One ``db.transact`` per MCTS iteration (design §13);
        # cache-hit-only iters skip (design §10 cache-miss-only).
        if db is not None and pending_facts:
            db.transact(
                pending_facts,
                provenance=_datoms._iter_provenance(search_id, iter_index),
            )

        iter_index += 1

    # Winner selection. ``all_evaluations_failed`` falls back to
    # ``initial_plan`` (root edges have visits=0; q meaningless).
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

    # Search-end summary (design §13): winner / iter-count /
    # terminated-by / finished-at — only known here. The
    # ``not search_summary_emitted`` guard is symmetric with the start
    # path; degenerate (zero-iter) runs prepend the start group.
    if db is not None:
        finish_facts: list[dict[str, Any]] = []
        if not search_summary_emitted:
            finish_facts.extend(_datoms._build_search_summary_start_facts(
                search_id=search_id, initial_plan_id=initial_plan.id,
                config_hash=config_hash, started_at_ms=started_at_ms,
            ))
        finish_facts.extend(_datoms._build_search_summary_finish_facts(
            search_id=search_id, started_at_ms=started_at_ms,
            iter_count=iter_index, winner_plan_id=winner_plan_id,
            terminated_by=terminated_by,
        ))
        db.transact(finish_facts, provenance={
            "source": _datoms._SOURCE_TAG,
            _datoms._ATTR_SEARCH_ID: search_id,
            "phase": "search-finish",
        })

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


# --- B9: skill-library 4-gate promotion hook lives in `_mcts_promote.py` -- #
# ``mcts_promote`` + ``MCTSPromotionResult`` are re-exported through the
# package ``__init__.py`` per design §12 ADR-9.