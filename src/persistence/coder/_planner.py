"""Phase 2.3a — plan-escalation bridge.

Wires the LLM-driven decision loop's `kind="plan"` decisions into the
`persistence.plan` substrate via a three-stage ingestion pipeline:

  Stage 1 (byte budget)  : payload["plan_edn"] ≤ MAX_PLAN_EDN_BYTES
  Stage 2 (parse)        : parse(plan_edn, strict=False)
  Stage 3 (semantic)     : root=:seq, depth≤4, nodes≤64, banned-leaves,
                            leaf-tags-must-be-registered

After validation, `_escalate_plan_body` registers 10 substrate ops as
plan-leaf handlers on a fresh `Dispatcher` (per LD5), runs
`s.plan.execute(plan, dispatcher)`, and:
- on status=ok: emits per-leaf `:act/result` datoms in walk order, plus
  one `:plan/done` provenance datom; returns.
- on status=failed: emits partial-trace `:act/result`s + a failure-shaped
  `:act/result` for the failing leaf (recovered via pre-walked id→Node
  map) + a final `:plan/execute` summary; raises `PlanExecutionFailed`.

Plan execution is a TERMINAL mode-switch — `Coder.run()` returns
immediately after `_escalate_plan_body` per the early-return contract
at `_session.py:74-82`.

Forced spec deviations vs impl plan:
  FD1: Node.tag is KEYWORD-FORM (":seq", ":fs/read") after EDN parse,
       NOT bare form as the impl plan claimed. `_ast.py:67-103`
       __post_init__ enforces `tag.startswith(":")` at construction.
       All tag comparisons and REGISTERED_LEAF_TAGS use keyword-form.
  FD2: parse() must use strict=False for 2.3a coder-specific tags
       (:fs/read, :code/run, :git/diff etc.) because the plan spec
       enum (checked in strict=True mode) only knows the closed set
       [:seq, :par, :choice, :loop, :race, :let, :branch, :case,
        :tool-call, :llm-call, :code, :checkpoint, :reflect, :verify,
        :call-skill, :ref]. Coder substrate op tags are NOT in that
       enum. Stage 3 (validate_plan_for_2_3a) provides the safety
       that strict mode would otherwise add.
  FD3: walk() returns list[str] (list of node `:id` strings), NOT
       list[Node] or Iterator[Node]. Use visitor callback to collect
       nodes; use _depth() recursive helper for depth check.
  FD4: ParseError is at persistence.plan._errors (also re-exported
       from persistence.plan directly) — import from ._errors directly
       for clarity.
"""
from __future__ import annotations

from typing import Any, Mapping

from persistence.coder._planner_errors import PlanPayloadValidation
from persistence.plan import Dispatcher, Node, parse, walk
from persistence.plan._errors import ParseError
from persistence.sdk import Substrate

__all__ = [
    "MAX_PLAN_DEPTH",
    "MAX_PLAN_EDN_BYTES",
    "MAX_PLAN_NODES",
    "REGISTERED_LEAF_TAGS",
    "_build_plan_from_payload",
    "_register_substrate_handlers",
    "validate_plan_for_2_3a",
]

#: Stage 1 byte budget (raw EDN bytes, UTF-8 encoded) — design § LD6.
MAX_PLAN_EDN_BYTES: int = 8192

#: Stage 3 plan-node-count budget — design § LD6.
MAX_PLAN_NODES: int = 64

#: Stage 3 plan-depth budget — design § LD6.
MAX_PLAN_DEPTH: int = 4

#: 10 substrate ops registered as plan-leaf handlers per LD5.
#: Stored as KEYWORD-FORM (with leading colon) to match Node.tag after
#: EDN parse — `_ast.py:67-103` __post_init__ enforces `tag.startswith(":")`.
#: FD1: impl plan claimed BARE form; corrected to keyword-form per calibration.
REGISTERED_LEAF_TAGS: frozenset[str] = frozenset({
    ":fs/read", ":fs/write", ":fs/glob", ":fs/grep",
    ":shell/exec", ":code/run",
    ":git/diff", ":git/status", ":git/log", ":git/commit",
})

#: Banned leaf tags — keyword-form; would crash walk() per _execute.py:166-170.
#: FD1: bare form "branch"/"code" corrected to ":branch"/":code".
_BANNED_LEAF_TAGS: frozenset[str] = frozenset({":branch", ":code"})


def _build_plan_from_payload(payload: Mapping[str, Any]) -> Node:
    """Stage 1 + Stage 2 of plan ingestion: byte-budget + parse.

    Uses parse(strict=False) — FD2: 2.3a coder-specific tags (:fs/read,
    :code/run, :git/diff etc.) are outside the closed plan spec enum that
    strict=True enforces. Stage 3 (validate_plan_for_2_3a) provides the
    semantic safety layer instead.

    Raises:
        PlanPayloadValidation: missing field, non-string field, byte
            budget exceeded, or parse raised ParseError.
    """
    plan_edn = payload.get("plan_edn")
    if plan_edn is None:
        raise PlanPayloadValidation(
            field="plan_edn",
            reason="missing required field 'plan_edn' in decision payload",
        )
    if not isinstance(plan_edn, str):
        raise PlanPayloadValidation(
            field="plan_edn",
            reason=f"expected str, got {type(plan_edn).__name__}",
        )
    # Stage 1: byte budget BEFORE invoking the parser.
    encoded_len = len(plan_edn.encode("utf-8"))
    if encoded_len > MAX_PLAN_EDN_BYTES:
        raise PlanPayloadValidation(
            field="plan_edn",
            reason=(
                f"exceeds {MAX_PLAN_EDN_BYTES}-byte budget "
                f"({encoded_len} bytes)"
            ),
        )
    # Stage 2: parse — strict=False per FD2.
    try:
        return parse(plan_edn, strict=False)
    except ParseError as exc:
        raise PlanPayloadValidation(
            field="plan_edn",
            reason=f"parse error: {exc}",
        ) from exc


def _depth(node: Node) -> int:
    """Max depth from `node` to deepest leaf (root has depth 1)."""
    if not node.children:
        return 1
    return 1 + max(_depth(c) for c in node.children)


def _collect_all_nodes(plan: Node) -> list[Node]:
    """Collect all nodes in depth-first order via walk visitor.

    FD3: walk() returns list[str] (node IDs), not list[Node].
    Use the optional visitor callback to accumulate Node references.
    walk() raises UnimplementedNodeKindError for :branch/:code leaves,
    so banned-tag checks MUST precede this call (done in validate_plan_for_2_3a).
    """
    nodes: list[Node] = []
    walk(plan, visitor=lambda node, _path: nodes.append(node))
    return nodes


def validate_plan_for_2_3a(plan: Node) -> None:
    """Stage 3 semantic validator. Raises PlanPayloadValidation on any
    violation; returns None on success.

    Constraints:
        1. Root tag is `:seq` (keyword-form; FD1 — impl plan used bare "seq").
        2. Total node count ≤ MAX_PLAN_NODES.
        3. Max depth ≤ MAX_PLAN_DEPTH.
        4. No `:branch` or `:code` leaves (would crash walk() per _walk.py:49).
        5. Every leaf tag is in REGISTERED_LEAF_TAGS (Dispatcher silently
           skips unregistered tags per _dispatch.py:73-80).
    """
    # Constraint 1: root tag check (keyword-form per FD1).
    if plan.tag != ":seq":
        raise PlanPayloadValidation(
            field="root_tag",
            reason=f"root must be ':seq', got '{plan.tag}'",
        )

    # Constraint 4 (banned leaves) + Constraint 5 (registered leaves):
    # Check BEFORE calling walk() — walk raises UnimplementedNodeKindError
    # for :branch/:code leaves, so we must catch them here first.
    # Tightened to all descendants (not just direct children) via recursion.
    _check_leaves_recursive(plan)

    # Constraint 2: node count — use visitor to collect all nodes post-ban-check.
    all_nodes = _collect_all_nodes(plan)
    node_count = len(all_nodes)
    if node_count > MAX_PLAN_NODES:
        raise PlanPayloadValidation(
            field="plan_nodes",
            reason=f"node count {node_count} exceeds budget {MAX_PLAN_NODES}",
        )

    # Constraint 3: depth.
    depth = _depth(plan)
    if depth > MAX_PLAN_DEPTH:
        raise PlanPayloadValidation(
            field="plan_depth",
            reason=f"depth {depth} exceeds budget {MAX_PLAN_DEPTH}",
        )


def _check_leaves_recursive(node: Node) -> None:
    """Recursively validate leaf nodes against banned + registered sets.

    A leaf is any node with no children. Raises PlanPayloadValidation for:
    - `:branch` or `:code` leaves (would crash walk per _execute.py:166-170)
    - Any leaf tag not in REGISTERED_LEAF_TAGS

    Note: the root `:seq` node itself is not a leaf (it has children),
    so it doesn't need to be in REGISTERED_LEAF_TAGS.
    """
    if not node.children:
        # This is a leaf node.
        if node.tag in _BANNED_LEAF_TAGS:
            raise PlanPayloadValidation(
                field="leaf_tag",
                reason=(
                    f"leaf '{node.tag}' is banned (would crash walk "
                    f"with UnimplementedNodeKindError per _walk.py:49)"
                ),
            )
        if node.tag not in REGISTERED_LEAF_TAGS:
            raise PlanPayloadValidation(
                field="leaf_tag",
                reason=(
                    f"leaf tag '{node.tag}' is unregistered; "
                    f"Dispatcher would silently skip it. Allowed: "
                    f"{sorted(REGISTERED_LEAF_TAGS)}"
                ),
            )
    else:
        # Non-leaf: recurse into children.
        for child in node.children:
            _check_leaves_recursive(child)


def _make_adapter(substrate: Substrate, tag: str):
    """Bind one substrate-op adapter for the given keyword-form tag.

    Adapter shape: (node, env) -> Any. Calls substrate.effect.perform
    with the same keyword-form tag and a dict-copy of node.attrs.
    """

    def adapter(node: Node, env: dict[str, Any]) -> Any:
        # FD3-T3: dict() coerces frozen Mapping -> plain dict for
        # substrate.effect.perform's args param.
        return substrate.effect.perform(tag, dict(node.attrs))

    adapter.__name__ = f"_plan_adapter_{tag.lstrip(':').replace('/', '_')}"
    return adapter


def _register_substrate_handlers(
    dispatcher: Dispatcher,
    substrate: Substrate,
) -> None:
    """Register the 10 LD5 substrate ops as plan-leaf handlers.

    Per design LD5, the dispatcher is fresh per `_escalate_plan_body`
    invocation, so double-registration cannot occur in normal flow.
    REGISTERED_LEAF_TAGS uses keyword-form tags (T2 FD1) matching
    Node.tag's required shape.
    """
    for tag in sorted(REGISTERED_LEAF_TAGS):
        dispatcher.register(tag, _make_adapter(substrate, tag))
