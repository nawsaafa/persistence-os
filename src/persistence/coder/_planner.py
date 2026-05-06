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

import uuid
import datetime as dt
from typing import TYPE_CHECKING, Any, Mapping

from persistence.coder._planner_errors import PlanPayloadValidation
from persistence.effect.canonical import canonical_dumps, canonical_hash
from persistence.plan import Dispatcher, Node, parse, walk
from persistence.plan._errors import ParseError
from persistence.sdk import Substrate

if TYPE_CHECKING:
    from persistence.coder._types import LLMDecision

__all__ = [
    "MAX_PLAN_DEPTH",
    "MAX_PLAN_EDN_BYTES",
    "MAX_PLAN_NODES",
    "REGISTERED_LEAF_TAGS",
    "_build_plan_from_payload",
    "_escalate_plan_body",
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


# ---------------------------------------------------------------------------
# T4 helpers — :act/result emission + :plan/done provenance
# ---------------------------------------------------------------------------

def _summarize_leaf_result(
    handler_result: Any,
    *,
    plan_id: str,
    node_id: str,
    tag: str,
    handler_id: str,
) -> dict[str, Any]:
    """Build the LD3 result_summary dict for a plan leaf's :act/result.

    Extends the 2.2a {op,args_hash,result_summary,error,latency_ms}
    envelope by packing plan-context keys {plan_id,node_id,tag,handler_id}
    INSIDE the result_summary field alongside the handler's own result.
    Single :act/result shape preserved so 2.2b LD3 _render_latest_action
    works without branching.

    LD3 contract (from 2.2b): handler output passes through 2.2a's
    `_summarize_result` FIRST — the ONLY cap on per-field string-blob
    size in the result_summary contract (`_prompt.py:53,67` documents
    "already capped — render verbatim" downstream). Without this hop, a
    `:fs/read` returning a 60KB file would land verbatim in
    `:act/result.v` and render verbatim next iteration.

    Key precedence: plan-context keys {plan_id,node_id,tag,handler_id}
    take PRECEDENCE over any same-named keys returned by the handler.
    A handler returning {"tag": "v0.9.0a1"} (e.g. a future :git/log
    op) must NOT clobber the LD3 plan-context tag=":git/log".
    Implemented as `{**base, **plan_context}` — later keys win in
    dict-spread.

    Non-dict handler returns are wrapped under key "value" so plan-
    context keys can attach. None handler returns yield context keys only.
    """
    # Local import avoids a circular module dependency between
    # _planner.py and _session.py (both live in persistence.coder).
    from persistence.coder._session import _summarize_result

    plan_context: dict[str, Any] = {
        "plan_id": plan_id,
        "node_id": node_id,
        "tag": tag,
        "handler_id": handler_id,
    }
    if handler_result is None:
        return plan_context
    base = _summarize_result(handler_result)  # 2.2a 512-char cap
    if not isinstance(base, dict):
        # _summarize_result is identity on non-dict inputs — wrap so
        # plan-context keys can attach to a single dict envelope.
        base = {"value": base}
    # `{**base, **plan_context}` — plan-context keys WIN on collision.
    return {**base, **plan_context}


def _emit_act_result(
    *,
    substrate: Substrate,
    valid_from: dt.datetime,
    op: str,
    args_hash: str,
    result_summary: Any,
    error: str | None,
    latency_ms: int,
) -> None:
    """Emit one :act/result datom via s.fact.transact.

    Mirrors the shape emitted by Coder._act in _session.py — same
    {op, args_hash, result_summary, error, latency_ms} envelope —
    so 2.2b LD3 _render_latest_action works without branching.
    """
    substrate.fact.transact([{
        "e": uuid.uuid4().hex,   # noqa: wall-clock — entity-id (txn precedent)
        "a": ":act/result",
        "v": canonical_dumps({
            "op": op,
            "args_hash": args_hash,
            "result_summary": result_summary,
            "error": error,
            "latency_ms": latency_ms,
        }),
        "valid_from": valid_from,
    }])


def _emit_plan_done(
    *,
    substrate: Substrate,
    valid_from: dt.datetime,
    plan_id: str,
    leaf_count: int,
    walk_order_node_ids: list[str],
) -> None:
    """Emit the :plan/done provenance datom via s.fact.transact.

    LD2: :plan/done is a provenance datom, NOT an audit op — it is NOT
    in CANONICAL_AUDIT_WRAPPED_OPS. The drift-pin / G7 are unaffected.
    Shape per LD2: {plan_id, leaf_count, walk_order_node_ids}. Success
    is implicit by emission — failure-path raises PlanExecutionFailed
    BEFORE this datom is written, so a present :plan/done means status=ok.
    """
    substrate.fact.transact([{
        "e": uuid.uuid4().hex,   # noqa: wall-clock — entity-id (txn precedent)
        "a": ":plan/done",
        "v": canonical_dumps({
            "plan_id": plan_id,
            "leaf_count": leaf_count,
            "walk_order_node_ids": walk_order_node_ids,
        }),
        "valid_from": valid_from,
    }])


# ---------------------------------------------------------------------------
# T4 — _escalate_plan_body happy path (LD0+LD2+LD3+LD5)
# ---------------------------------------------------------------------------

def _escalate_plan_body(coder: Any, decision: "LLMDecision") -> None:
    """Happy-path plan escalation pipeline (T4 — LD0+LD2+LD3+LD5).

    This is the SOLE entry point that `Coder._escalate_plan` will wire
    to in T7 — a single thin call. The body does the WHOLE pipeline:

      1. Build:    plan = _build_plan_from_payload(decision.payload)
      2. Validate: validate_plan_for_2_3a(plan)
      3. Pre-walk: id_to_node map for leaf attribution
      4. Dispatcher: substrate.plan.new_dispatcher()
      5. Register: 10 substrate-op handlers (LD5)
      6. Execute:  substrate.plan.execute(plan, dispatcher)
      7. on status="failed": raise PlanExecutionFailed (T5 placeholder)
      8. on status="ok": emit per-leaf :act/result + :plan/done; return None

    Plan execution is a TERMINAL mode-switch (LD0). On success, returns
    None and Coder.run() exits naturally via the post-escalation early-
    return at _session.py:79. On failure, PlanExecutionFailed propagates
    out of run() — caller's contract.

    Args:
        coder: The Coder instance. Provides `coder.substrate` for all
            substrate calls. Typed `Any` to break a hypothetical import
            cycle with _session.py at type-checking time; runtime is
            duck-typed on `.substrate`.
        decision: The plan-kind LLMDecision from _decide. Its payload
            must carry `plan_edn` (str). Stage-1/Stage-2/Stage-3 errors
            surface as PlanPayloadValidation.

    Forced spec deviation — T2 FD1 cascade:
      leaf.tag is ALREADY keyword-form (":fs/read" etc.) after execution.
      MUST use leaf.tag directly — NOT f":{leaf.tag}" (would produce
      "::fs/read"). Same for result_summary.tag.

    Forced spec deviation — latency_ms:
      Plan leaves report latency_ms=0 at 2.3a. Per-leaf wall-clock
      tracking deferred to 2.4a (:sys/now / :clock/now route).
    """
    from persistence.coder._planner_errors import PlanExecutionFailed

    substrate = coder.substrate
    valid_from = dt.datetime.now(dt.timezone.utc)  # noqa: wall-clock

    # Step 1+2: build + validate. Stage-1/2/3 raise PlanPayloadValidation.
    plan = _build_plan_from_payload(decision.payload)
    validate_plan_for_2_3a(plan)

    # Step 3: pre-walk — build id→Node map for leaf attribution.
    # FD3: walk() returns list[str] (node IDs), not list[Node].
    # Use _collect_all_nodes (visitor-based) to get Node references.
    all_nodes = _collect_all_nodes(plan)
    id_to_node: dict[str, Node] = {n.id: n for n in all_nodes}

    # Step 4: fresh dispatcher per LD5.
    dispatcher = substrate.plan.new_dispatcher()

    # Step 5: register 10 substrate-op handlers.
    _register_substrate_handlers(dispatcher, substrate)

    # Step 6: execute.
    result = substrate.plan.execute(plan, dispatcher)

    # Step 7: failure path — LD4 (R0-fold B1) full pipeline.
    if result.status == "failed":
        # 1) Partial-trace: emit :act/result for each successful leaf.
        #    The failing leaf is NOT in result.leaf_results — execute()
        #    bails BEFORE appending it (confirmed at _execute.py:193-205).
        for leaf in result.leaf_results:
            node = id_to_node[leaf.node_id]
            _emit_act_result(
                substrate=substrate,
                valid_from=valid_from,
                op=leaf.tag,                        # FD1: keyword-form direct, NOT f":{leaf.tag}"
                args_hash=canonical_hash(dict(node.attrs)),
                result_summary=_summarize_leaf_result(
                    leaf.result,
                    plan_id=plan.id,
                    node_id=leaf.node_id,
                    tag=leaf.tag,                   # FD1: keyword-form direct
                    handler_id=leaf.handler_id,
                ),
                error=None,
                latency_ms=0,
            )
        # 2) Failure-shaped :act/result for the failing leaf.
        #    Attrs + handler-id recovered via the pre-walked id→Node map
        #    (R0-fold B1 resolution: the failing leaf is NOT in
        #    result.leaf_results so we cannot get its context from there).
        failed_node = id_to_node[result.failure.failed_node_id]
        failed_handler_id = failed_node.attrs.get("handler-id", "<default>")
        _emit_act_result(
            substrate=substrate,
            valid_from=valid_from,
            op=result.failure.failed_tag,           # FD1: keyword-form direct
            args_hash=canonical_hash(dict(failed_node.attrs)),
            result_summary=_summarize_leaf_result(
                None,                               # no handler result — execution raised
                plan_id=plan.id,
                node_id=result.failure.failed_node_id,
                tag=result.failure.failed_tag,      # FD1: keyword-form direct
                handler_id=failed_handler_id,
            ),
            error=result.failure.error_repr,        # R1-NICE: already class-prefixed repr
            latency_ms=0,
        )
        # 3) Final :plan/execute summary datom.
        _emit_act_result(
            substrate=substrate,
            valid_from=valid_from,
            op=":plan/execute",
            args_hash=canonical_hash({"plan_id": plan.id}),
            result_summary={"plan_id": plan.id},
            error=result.failure.error_repr,
            latency_ms=0,
        )
        # 4) Re-raise. No __cause__ chain — execute() already captured the
        #    original exception into FailureInfo (string-only) per
        #    _execute.py:96-114. Native traceback UNAVAILABLE in 2.3a
        #    (documented limitation per design § 7 + LD4; queued v0.9.x).
        raise PlanExecutionFailed(failure=result.failure)

    # Step 8a: emit per-leaf :act/result datoms in walk order.
    for leaf in result.leaf_results:
        node = id_to_node[leaf.node_id]
        _emit_act_result(
            substrate=substrate,
            valid_from=valid_from,
            op=leaf.tag,                                   # FD1: keyword-form direct, NOT f":{leaf.tag}"
            args_hash=canonical_hash(dict(node.attrs)),
            result_summary=_summarize_leaf_result(
                leaf.result,
                plan_id=plan.id,
                node_id=leaf.node_id,
                tag=leaf.tag,                              # FD1: keyword-form direct
                handler_id=leaf.handler_id,
            ),
            error=None,
            latency_ms=0,                                  # deferred to 2.4a
        )

    # Step 8b: emit :plan/done provenance datom (LD2).
    # walk_order_node_ids = result.leaf_results node_id sequence (walk
    # order per s.plan.execute capture-then-record contract).
    walk_order_node_ids = [lr.node_id for lr in result.leaf_results]
    _emit_plan_done(
        substrate=substrate,
        valid_from=valid_from,
        plan_id=plan.id,
        leaf_count=len(result.leaf_results),
        walk_order_node_ids=walk_order_node_ids,
    )

    # Step 8c: return None — terminal mode-switch (LD0).
    return None
