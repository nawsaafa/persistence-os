"""Phase 2.1b — prompt construction + tool schema + text fallback parser.

`EMIT_DECISION_TOOL_SCHEMA` is the ONLY tool exposed to the LLM in
2.1b per LD4 (decision/action split). Real effect tools (:fs/, :shell/,
:code/, :git/) land in 2.2a — they are NEVER reachable by the LLM
directly; the substrate routes intents instead.

The text-fenced fallback parser handles backends that do not natively
support tool-use (Mode 3 callable wired to a text-only LLM, or any
provider whose tool_calls field is empty).
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from ._types import Observation

_PLAN_EDN_GUIDANCE = """\
When emitting kind="plan", the payload["plan_edn"] field carries an \
EDN-formatted plan (s.plan.parse-compatible canonical EDN). Constraints:
- Root MUST be :seq. No other root kinds at this phase.
- Leaves MUST be one of: :fs/read, :fs/write, :fs/glob, :fs/grep, \
:shell/exec, :code/run, :git/diff, :git/status, :git/log, :git/commit, \
:skill/define, :skill/lookup.
- NO bare :branch or bare :code leaves — these are plan-spec primitives \
reserved for later phases (MCTS branch in 2.3b, sandboxed code execution \
in v0.2). The :code/run leaf in the registered list above is a different \
op (Phase 2.2b coder substrate handler) and IS allowed.
- Max 64 nodes, max depth 4, max 8192 bytes.

Examples:

(1) Read a file, run code, then diff:
[:seq {} [:fs/read {:path "src/foo.py"}] [:code/run {:source "print(42)"}] [:git/diff {}]]

(2) Grep then commit:
[:seq {} [:fs/grep {:pattern "TODO" :path "src/"}] [:git/commit {:message "wip"}]]"""


_SKILL_GUIDANCE = """\
Skills are reusable Plan AST fragments registered to a content-addressed \
library. Two ops let you work with skills directly inside plans:

- :skill/define {:plan-edn "<canonical-EDN>" :promotion-id "<opaque-id>" \
:registered-at-ms <int>} — registers the inner plan_edn as a skill. \
Returns {:skill-id "<skill/abc...>" :plan-id "<32-hex>"}. Re-defining the \
SAME plan content is idempotent (zero new fact-store datoms; same skill_id \
returned). The :skill-id is content-addressed: identical Plan ASTs always \
hash to the same skill_id.

- :skill/lookup {:skill-id "<skill/abc...>"} — retrieves a previously \
registered skill. Returns {:plan-edn "<canonical-EDN>" :promotion-id "<...>" \
:plan-id "<32-hex>"}. Raises if the skill_id is not registered.

When to use:
- :skill/define after completing a generalizable sub-task whose Plan AST \
you might want to reuse (e.g. a "lint + format + commit" composition).
- :skill/lookup when facing a similar sub-task; splice the returned \
:plan-edn VERBATIM into a subsequent plan to reuse the registered logic.

Procedural recall pattern (this is the only skill-use mode in 2.3c.1; \
runtime composition + LLM-call recursion ship in 2.3c.2):
  iter N    -> :skill/define registers a skill, returns :skill-id
  iter N+M  -> :skill/lookup retrieves the :plan-edn for that :skill-id
  iter N+M+1-> emit a new plan that splices the looked-up :plan-edn as a \
sub-tree (byte-identically — content-addressing depends on it)

Constraint: :promotion-id is OPAQUE provenance only. 2.3c.1 makes NO \
promotion-validity claims; A7 PromotionRecord (queued for v0.9.x) MAY \
later reject skills registered this way."""


_BRANCH_EDN_GUIDANCE = """\
When you emit kind="branch", provide a SEED plan that the substrate's \
MCTS engine will explore structural variations around. Use this when \
multiple leaf compositions might achieve the goal and you want the \
substrate to evaluate alternatives — NOT when you have a definite \
linear plan (use kind="plan" instead, which executes directly).

Payload shape:
  {"seed_plan_edn": "<canonical-EDN>",       // required
   "mcts_config": {"max_iter": 25,           // optional override
                   "expander_k": 3}}         // (max_iter <= 50,
                                             //  expander_k <= 4)

Example seed plan (3 leaves under :seq):
[:seq {} [:fs/read {:path "src/main.py"}] \
[:git/diff {:revisions ["HEAD"]}] \
[:fs/write {:path "/tmp/summary.txt" :bytes_or_text "..."}]]

The MCTS expander proposes Substitute / AddStep variations; the \
evaluator scores candidates; the highest-visited root edge is executed \
once. Losing branches NEVER run their leaves.\
"""


EMIT_DECISION_TOOL_SCHEMA: dict[str, Any] = {
    "name": "emit_decision",
    "description": (
        "Emit your structured next-step decision for the persistence-coder agent. "
        "kind='act' for a single tool invocation; kind='plan' for a multi-step "
        "composition; kind='branch' if you are uncertain and want the agent to "
        "fork-and-explore. confidence is in [0.0, 1.0]; values below 0.65 trigger "
        "branch escalation. " + _PLAN_EDN_GUIDANCE + "\n\n" + _SKILL_GUIDANCE
    ),
    "input_schema": {
        "type": "object",
        "required": ["kind", "confidence", "payload"],
        "additionalProperties": False,
        "properties": {
            "kind": {"type": "string", "enum": ["act", "plan", "branch"]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "payload": {"type": "object"},  # 2.1b: loose; tightens 2.2a+
        },
    },
}


def _render_latest_action(action: Mapping[str, Any]) -> list[str]:
    """LD3 — render the most-recent action with explicit field formatting.

    Bypasses build_messages' [:200] truncation for the LATEST action only;
    older history entries (in 'Recent loop history' block) keep the cap.

    Fields:
      op: dispatched op name
      error: error string (or 'none' when None)
      result_summary: dict — render each (key, value) explicitly:
        - str values: render with `|\\n    <value>` (block-string indented).
          The upstream _summarize_result already truncates strings >512 chars.
        - list values: collapse to `count=N, first_3=<repr of first 3>`.
        - other: repr() truncated at 200 chars.
      result_summary=None: render placeholder '(no result_summary — exception path)'.
    """
    parts = ["Latest action output:"]
    parts.append(f"  op: {action.get('op', '?')}")
    parts.append(f"  error: {action.get('error') or 'none'}")
    rs = action.get("result_summary")
    if rs is None:
        parts.append("  (no result_summary — exception path)")
    elif isinstance(rs, dict):
        for k, v in rs.items():
            if isinstance(v, str):
                # _summarize_result already capped at 512; render verbatim.
                parts.append(f"  {k}: |\n    {v}")
            elif isinstance(v, list):
                parts.append(f"  {k}: count={len(v)}, first_3={v[:3]!r}")
            else:
                parts.append(f"  {k}: {v!r}"[:202])
    else:
        parts.append(f"  result_summary: {rs!r}"[:202])
    return parts


def build_messages(task: str, obs: Observation) -> list[dict[str, Any]]:
    """Construct the LLM message list.

    Appends a 'Recent loop history' section when obs has non-empty
    decisions or actions; section is omitted entirely on the first iter
    (zero-prompt-overhead when both tuples are empty).
    """
    parts = [f"Task: {task}", "", "Use emit_decision to respond."]
    # Phase 2.2b LD3 — render latest action verbatim BEFORE the
    # truncated history block, so stdout/stderr/traceback content
    # reaches the LLM without the [:200] cap eating sentinels.
    if obs.recent_actions:
        parts.append("")
        parts.extend(_render_latest_action(obs.recent_actions[-1]))
    if obs.recent_decisions or obs.recent_actions:
        parts.extend(["", f"Recent loop history (iter {obs.iter_count}):"])
        if obs.recent_decisions:
            parts.append("Decisions:")
            for d in obs.recent_decisions[-3:]:  # truncate for prompt cost
                parts.append(f"  - {json.dumps(d, sort_keys=True)[:200]}")
        if obs.recent_actions:
            parts.append("Actions:")
            for a in obs.recent_actions[-3:]:
                parts.append(f"  - {json.dumps(a, sort_keys=True)[:200]}")
    return [{"role": "user", "content": "\n".join(parts)}]


_DECISION_ENVELOPE_RE = re.compile(r"<decision>\s*(\{.*?\})\s*</decision>", re.DOTALL)


def parse_text_decision(text: str) -> dict[str, Any] | None:
    """Tier-2 fallback parser for ``<decision>{json}</decision>`` envelope.

    Returns ``None`` if envelope absent OR JSON invalid OR shape doesn't
    match the required ``{kind, confidence, payload}`` fields. Caller
    falls to tier 3 (missing-confidence default).
    """
    if not (m := _DECISION_ENVELOPE_RE.search(text)):
        return None
    try:
        parsed = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("kind") not in {"act", "plan", "branch"}:
        return None
    try:
        confidence = float(parsed["confidence"])
    except (KeyError, ValueError, TypeError):
        return None
    if not (0.0 <= confidence <= 1.0):
        return None
    payload = parsed.get("payload", {})
    if not isinstance(payload, dict):
        return None
    return {
        "kind": parsed["kind"],
        "confidence": confidence,
        "payload": payload,
    }


#: Phase 2.3b — JSON-mode contract for `LLMExpander.provider` proposals.
#: One tool-use call per expansion; the response carries `proposals`,
#: a sequence of action descriptors with raw `logit` values that the
#: bridge softmax-normalizes into priors. ComposeWithSkillAction
#: proposals are accepted by the schema (LLM may emit them) but dropped
#: at the wrapper layer per LD3 — defer to 2.3c.
EMIT_BRANCH_PROPOSAL_TOOL_SCHEMA: dict = {
    "name": "emit_branch_proposals",
    "description": (
        "Propose up to k structural actions to apply to the seed plan. "
        "Each action is one of: SubstituteLeafAction (replace a leaf at "
        "target_path with a new EDN-encoded leaf), AddStepAction (insert "
        "a new child at index `at` under the node at target_path), or "
        "ComposeWithSkillAction (deferred — will be ignored). Provide a "
        "raw `logit` per action; the bridge softmax-normalizes them so "
        "MCTS receives a proper probability distribution."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": [
                                "SubstituteLeafAction",
                                "AddStepAction",
                                "ComposeWithSkillAction",
                            ],
                        },
                        "target_path": {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 0},
                        },
                        "new_leaf_edn": {"type": "string"},
                        "at": {"type": "integer", "minimum": 0},
                        "new_child_edn": {"type": "string"},
                        "skill_id": {"type": "string"},
                        "logit": {"type": "number"},
                    },
                    "required": ["kind", "target_path", "logit"],
                },
            },
        },
        "required": ["proposals"],
    },
}


_BRANCH_EXPANDER_SYSTEM_PROMPT: str = """\
You are the EXPANDER of a Monte-Carlo tree search over a Plan AST.

Given a seed plan, propose up to k structural variations as Actions.
Each action edits the plan structurally (substitute a leaf, add a
step), NOT by executing it. Provide a raw `logit` (any real number)
per action; the bridge softmax-normalizes them.

Constraints:
- Actions must be SubstituteLeafAction or AddStepAction.
  ComposeWithSkillAction is reserved for Phase 2.3c — emit only as
  last resort; the bridge will drop it.
- New leaves must be ONE of these tags: :fs/read, :fs/write, :fs/glob,
  :fs/grep, :shell/exec, :code/run, :git/diff, :git/status, :git/log,
  :git/commit. (Same set as Phase 2.3a leaf handlers.)
- Plan structure must remain :seq-rooted with depth <= 4 and
  node-count <= 64. The bridge dry-runs your action and rejects any
  proposal that would violate these.

Return proposals via the `emit_branch_proposals` tool.
"""


#: Phase 2.3b — JSON-mode contract for `LLMJudgeEvaluator.provider`.
#: One tool-use call per evaluation; the response carries a single
#: `score` field in [0.0, 1.0]. Out-of-range values are clamped at the
#: bridge layer rather than raised (avoid spurious EvaluatorContractError
#: for off-by-epsilon JSON parsing).
EMIT_BRANCH_SCORE_TOOL_SCHEMA: dict = {
    "name": "emit_branch_score",
    "description": (
        "Score the given plan in [0.0, 1.0]. Higher = more likely to "
        "achieve the goal. The bridge clamps out-of-range values."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {"type": "number"},
        },
        "required": ["score"],
    },
}


_BRANCH_EVALUATOR_SYSTEM_PROMPT: str = """\
You are the EVALUATOR of a Monte-Carlo tree search over a Plan AST.

Given a candidate plan, return a SINGLE score in [0.0, 1.0] estimating
how likely this plan, if executed, will achieve the user's goal.

Higher = more likely. Use the full range; don't cluster around 0.5.

Return the score via the `emit_branch_score` tool.
"""
