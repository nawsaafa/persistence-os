"""MCTS provenance datom builders + ``mcts/prev-hash`` Merkle chain (B8).

Lifted out of ``_mcts.py`` per the B6 escape-hatch. All names are
private; the schema tests import the constants directly.

Design references: §10 (cache-miss-only ADR-7), §13 (datom shape +
``mcts/prev-hash`` recipe + transaction granularity), §16 invariant 6
(exactly one ``phase="evaluate"`` per BACKUP iteration).

Audit-chain context (closes R2.M2): the MCTS Merkle chain is SEPARATE
from G2's effect-handler audit chain (``audit/`` prefix). The ``mcts/``
prefix opts OUT of G2's window scan.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, TYPE_CHECKING

from persistence.plan._ast import Node, _canonical_dict

if TYPE_CHECKING:
    from persistence.plan._mcts import Action
    from persistence.plan._skill_library import SkillLibrary


# --- Schema constants (design §13) -------------------------------------- #

#: Namespace prefix; intentionally distinct from G2's ``audit/`` so MCTS
#: datoms opt OUT of ``gate_g2_audit_chain``'s window scan.
_MCTS_PREFIX: str = "mcts/"
#: Documentation/test anchor: tests assert no MCTS datom collides here.
_AUDIT_PREFIX_BLACKLIST: str = "audit/"
#: Schema version, bumped on canonical-form-altering changes; recorded
#: on the ``mcts/search`` summary so older replays detect mismatch.
_SCHEMA_VERSION: str = "v0.6.5"

# --- Attribute keys ---- per-iteration row (design §13 line 736-745) --- #

_ATTR_SEARCH_ID: str = "mcts/search-id"
_ATTR_ITER_INDEX: str = "mcts/iter-index"
_ATTR_PHASE: str = "mcts/phase"
_ATTR_PLAN_ID: str = "mcts/plan-id"
_ATTR_INPUTS_HASH: str = "mcts/inputs-hash"
_ATTR_OUTPUT: str = "mcts/output"
_ATTR_PREV_HASH: str = "mcts/prev-hash"
_ATTR_ERROR_CLASS: str = "mcts/error-class"
_ATTR_ERROR_REPR: str = "mcts/error-repr"

# --- Attribute keys ---- ``mcts/search`` summary (design §13 line 706-718) ---

_ATTR_INITIAL_PLAN_ID: str = "mcts/initial-plan-id"
_ATTR_WINNER_PLAN_ID: str = "mcts/winner-plan-id"
_ATTR_CONFIG_HASH: str = "mcts/config-hash"
_ATTR_ITER_COUNT: str = "mcts/iter-count"
_ATTR_TERMINATED_BY: str = "mcts/terminated-by"
_ATTR_STARTED_AT: str = "mcts/started-at"
_ATTR_FINISHED_AT: str = "mcts/finished-at"
_ATTR_SCHEMA_VERSION: str = "mcts/schema-version"

#: Per-iter row required keys (design §13); raise-paths additionally
#: emit error-class/error-repr (``_OPTIONAL_ITER_ATTRS``).
_REQUIRED_ITER_ATTRS: frozenset[str] = frozenset({
    _ATTR_SEARCH_ID, _ATTR_ITER_INDEX, _ATTR_PHASE, _ATTR_PLAN_ID,
    _ATTR_INPUTS_HASH, _ATTR_OUTPUT, _ATTR_PREV_HASH,
})
_OPTIONAL_ITER_ATTRS: frozenset[str] = frozenset({
    _ATTR_ERROR_CLASS, _ATTR_ERROR_REPR,
})

#: ``mcts/search`` summary required-keys at search start (winner /
#: iter-count / terminated-by / finished-at are written at search end).
_REQUIRED_SEARCH_START_ATTRS: frozenset[str] = frozenset({
    _ATTR_INITIAL_PLAN_ID, _ATTR_CONFIG_HASH, _ATTR_STARTED_AT,
    _ATTR_SCHEMA_VERSION,
})
_REQUIRED_SEARCH_FINISH_ATTRS: frozenset[str] = frozenset({
    _ATTR_WINNER_PLAN_ID, _ATTR_ITER_COUNT, _ATTR_TERMINATED_BY,
    _ATTR_FINISHED_AT,
})


#: Closed ``reason`` enum for ``phase="reject"`` records (design §13/§14).
_REJECT_REASONS: frozenset[str] = frozenset({
    "plan_construction_raised", "plan_too_deep", "compose_creates_cycle",
    "skill_not_registered", "evaluator_raised", "evaluator_returned_none",
    "evaluator_returned_non_finite",
})

_SOURCE_TAG: str = "mcts-search"


# --- Canonical-JSON helper (mirror ``Node.id`` contract) ---------------- #


def _canonical_json(payload: object) -> str:
    """Canonical-JSON encoding (sorted keys, no NaN); mirrors ``Node.id``."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _hash_payload(payload: object) -> str:
    return _sha256_hex(_canonical_json(payload))


# --- Node ↔ canonical-dict round-trip (design §13 W2 M4 closure) ------- #


def _node_canonical(node: Node) -> dict[str, Any]:
    """Canonical-JSON-ready dict for ``node`` (W2 M4 fix).

    Mirrors ``_ast._canonical_dict`` so ``new_leaf_canonical`` /
    ``new_child_canonical`` records survive in the audit log alongside
    the hash. Round-trip: ``_node_from_canonical(_node_canonical(n)).id
    == n.id`` (pinned in test_mcts_expand_output_payload_schema)."""
    return _canonical_dict(node)


def _node_from_canonical(canonical: Mapping[str, Any]) -> Node:
    """Inverse of ``_node_canonical``; replay re-materializes Nodes (design §13)."""
    children_raw = canonical.get("children", [])
    if not isinstance(children_raw, (list, tuple)):
        raise ValueError(
            f"_node_from_canonical: children must be list/tuple, "
            f"got {type(children_raw).__name__}"
        )
    attrs_raw = canonical.get("attrs", {})
    if not isinstance(attrs_raw, dict):
        raise ValueError(
            f"_node_from_canonical: attrs must be dict, "
            f"got {type(attrs_raw).__name__}"
        )
    tag_raw = canonical.get("tag")
    if not isinstance(tag_raw, str):
        raise ValueError(
            f"_node_from_canonical: tag must be str, "
            f"got {type(tag_raw).__name__}"
        )
    children = tuple(
        _node_from_canonical(c) for c in children_raw  # type: ignore[arg-type]
    )
    return Node(tag=tag_raw, attrs=dict(attrs_raw), children=children)


# --- Iter-row entity-id recipe (design §13 line 747-758) --------------- #

#: ``iter_id`` content-address slice width (16 hex / 64 bits — the
#: ``(search_id, iter_index, phase, plan_id, action_hash)`` tuple is
#: unique by construction within a search).
_ITER_ID_HEX_WIDTH: int = 16
_ITER_ID_PREFIX: str = "mcts-iter/"


def _iter_id(
    search_id: str,
    iter_index: int,
    phase: str,
    plan_id: str,
    action_hash: str | None,
) -> str:
    """Content-addressed entity id for one iteration row (design §13)."""
    digest = _hash_payload({
        "search_id": search_id, "iter_index": iter_index, "phase": phase,
        "plan_id": plan_id, "action_hash": action_hash,
    })
    return _ITER_ID_PREFIX + digest[:_ITER_ID_HEX_WIDTH]


def _expand_proposal_record(
    action: "Action",
    prior: float,
    *,
    skill_library: "SkillLibrary | None" = None,
) -> dict[str, Any]:
    """Per-proposal record inside a ``phase="expand"`` output.

    Stores BOTH ``action_hash`` AND canonical Node bytes
    (``new_leaf_canonical`` / ``new_child_canonical``) for synthesized
    Nodes (W2 M4 closure — the hash alone is one-way).

    Phase 2.3c.2 LD3: when ``action`` is a ``ComposeWithSkillAction``
    AND ``skill_library`` is provided AND the lookup succeeds, the
    record additionally carries ``composed_skill_content_hash`` —
    the looked-up skill plan's content-addressed ``plan.id`` (32 hex
    chars). This is provenance for replay-explainability (audit trail
    of which skill_id resolved to what content_hash); replay byte-
    identity itself is provided by the winner Plan AST being content-
    addressed (R0-fold B4 narrowed claim).

    When ``skill_library`` is None or the skill_id is unregistered, the
    ``composed_skill_content_hash`` field is omitted (not emitted as
    None) — matches the ``txn_commit`` emit-only-when-set pattern.
    """
    # Local imports break the ``_mcts`` ↔ ``_mcts_datoms`` cycle.
    from persistence.plan._mcts import (
        AddStepAction,
        ComposeWithSkillAction,
        SubstituteLeafAction,
        _action_hash,
    )

    base: dict[str, Any] = {
        "action_hash": _action_hash(action),
        "action_kind": type(action).__name__,
        "prior": float(prior),
    }
    payload: dict[str, Any] = {
        "target_path": list(action.target_path),
    }
    if isinstance(action, SubstituteLeafAction):
        payload["new_leaf_id"] = action.new_leaf.id
        payload["new_leaf_canonical"] = _node_canonical(action.new_leaf)
    elif isinstance(action, AddStepAction):
        payload["at"] = action.at
        payload["new_child_id"] = action.new_child.id
        payload["new_child_canonical"] = _node_canonical(action.new_child)
    elif isinstance(action, ComposeWithSkillAction):
        # Skill plan is recoverable via SkillLibrary.lookup on replay;
        # skill_id is itself content-addressed. No canonical Node needed.
        payload["skill_id"] = action.skill_id
        # Phase 2.3c.2 LD3 — pin the looked-up skill plan's content
        # hash for replay-explainability. Omit when lookup is not
        # possible (no library / unregistered skill_id) so the field
        # is emit-only-when-set, mirroring txn_commit + parent_audit_entry_id.
        if skill_library is not None:
            looked_up = skill_library.lookup(action.skill_id)
            if looked_up is not None:
                looked_up_plan, _record = looked_up
                payload["composed_skill_content_hash"] = looked_up_plan.id
    else:  # pyright: ignore[reportUnreachable]
        raise ValueError(  # pyright: ignore[reportUnreachable]
            f"unknown action kind: {type(action).__name__}"
        )
    base["action_payload"] = payload
    return base


def _reject_record(
    action: "Action | None",
    plan_id: str,
    reason: str,
    *,
    error: BaseException | None = None,
    raw_score: object = None,
    skill_library: "SkillLibrary | None" = None,
) -> dict[str, Any]:
    """``phase="reject"`` output record. Action-side carries full payload
    (Prop 6); evaluator-side passes ``action=None``.

    Phase 2.3c.2 LD3: ``skill_library`` is forwarded to
    :func:`_expand_proposal_record` so ``ComposeWithSkillAction`` reject
    records carry ``composed_skill_content_hash`` symmetrically with
    expand records.
    """
    if reason not in _REJECT_REASONS:
        raise ValueError(
            f"_reject_record: reason {reason!r} not in {_REJECT_REASONS!r}"
        )
    record: dict[str, Any] = {"plan_id": plan_id, "reason": reason}
    if action is not None:
        full = _expand_proposal_record(action, 0.0, skill_library=skill_library)
        record["action_hash"] = full["action_hash"]
        record["action_kind"] = full["action_kind"]
        record["action_payload"] = full["action_payload"]
        record["target_path"] = full["action_payload"]["target_path"]
    else:
        record["action_hash"] = None
        record["action_kind"] = None
        record["action_payload"] = None
        record["target_path"] = None
    if error is not None:
        record["error_class"] = type(error).__name__
        record["error_repr"] = repr(error)
    if raw_score is not None:
        record["raw_score_repr"] = repr(raw_score)
    return record


def _expand_inputs_hash(plan_id: str, expander_k: int) -> str:
    """Hash of the inputs to the expander on this iteration."""
    return _hash_payload({"plan_id": plan_id, "expander_k": expander_k})


def _evaluate_inputs_hash(plan_id: str) -> str:
    """Hash of the inputs to the evaluator on this iteration."""
    return _hash_payload({"plan_id": plan_id})


def _hash_search_summary(
    initial_plan_id: str, config_hash: str, started_at_ms: int
) -> str:
    """Content hash of the ``mcts/search`` start summary; iter 0's anchor."""
    return _hash_payload({
        "initial_plan_id": initial_plan_id,
        "config_hash": config_hash,
        "started_at": started_at_ms,
        "schema_version": _SCHEMA_VERSION,
    })


def _hash_iter_content(
    *,
    search_id: str,
    iter_index: int,
    phase: str,
    plan_id: str,
    inputs_hash: str,
    output_value: object,
    error_class: str | None,
    error_repr: str | None,
) -> str:
    """Content hash of one iteration row (excludes ``prev-hash`` slot)."""
    return _hash_payload({
        _ATTR_SEARCH_ID: search_id,
        _ATTR_ITER_INDEX: iter_index,
        _ATTR_PHASE: phase,
        _ATTR_PLAN_ID: plan_id,
        _ATTR_INPUTS_HASH: inputs_hash,
        _ATTR_OUTPUT: output_value,
        _ATTR_ERROR_CLASS: error_class,
        _ATTR_ERROR_REPR: error_repr,
    })


def _synthetic_valid_from(started_at_ms: int, iter_index: int) -> datetime:
    """Synthetic ``valid_from`` (design §13 ``t = started_at + iter_index``)."""
    epoch_seconds = (started_at_ms + iter_index) / 1000.0
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)



def _build_search_summary_start_facts(
    *,
    search_id: str,
    initial_plan_id: str,
    config_hash: str,
    started_at_ms: int,
) -> list[dict[str, Any]]:
    """Up-front-known ``mcts/search`` summary fact group (iter 0 prepend)."""
    vf = _synthetic_valid_from(started_at_ms, 0)
    pairs = (
        (_ATTR_INITIAL_PLAN_ID, initial_plan_id),
        (_ATTR_CONFIG_HASH, config_hash),
        (_ATTR_STARTED_AT, started_at_ms),
        (_ATTR_SCHEMA_VERSION, _SCHEMA_VERSION),
    )
    return [{"e": search_id, "a": a, "v": v, "valid_from": vf} for a, v in pairs]


def _build_search_summary_finish_facts(
    *,
    search_id: str,
    started_at_ms: int,
    iter_count: int,
    winner_plan_id: str,
    terminated_by: str,
) -> list[dict[str, Any]]:
    """Search-end ``mcts/search`` summary fact group; ``finished-at`` synthetic."""
    finished_at = started_at_ms + iter_count
    vf = _synthetic_valid_from(started_at_ms, iter_count)
    pairs = (
        (_ATTR_WINNER_PLAN_ID, winner_plan_id),
        (_ATTR_ITER_COUNT, iter_count),
        (_ATTR_TERMINATED_BY, terminated_by),
        (_ATTR_FINISHED_AT, finished_at),
    )
    return [{"e": search_id, "a": a, "v": v, "valid_from": vf} for a, v in pairs]


@dataclass(frozen=True, slots=True)
class _IterDatomGroup:
    """One iteration row: 7 (or 9 with error) facts at the same ``iter_id``."""

    iter_id: str
    facts: list[dict[str, Any]]
    content_hash: str  # the next datom's prev-hash


def _make_iter_facts(
    *,
    search_id: str,
    iter_index: int,
    phase: str,
    plan_id: str,
    inputs_hash: str,
    output_value: Any,
    prev_hash: str,
    started_at_ms: int,
    action_hash: str | None,
    error: BaseException | None = None,
) -> _IterDatomGroup:
    """Assemble one iteration row + advance the Merkle chain.

    ``output_value`` shape per phase: expand → list[proposal-record];
    evaluate → float; reject → reject-record dict. ``error`` is non-None
    only on ``phase="reject"`` with ``reason="evaluator_raised"``."""
    if phase not in ("expand", "evaluate", "reject"):
        raise ValueError(
            f"_make_iter_facts: phase {phase!r} not in "
            f"('expand', 'evaluate', 'reject')"
        )
    iter_entity = _iter_id(search_id, iter_index, phase, plan_id, action_hash)
    error_class = type(error).__name__ if error is not None else None
    error_repr = repr(error) if error is not None else None
    vf = _synthetic_valid_from(started_at_ms, iter_index)
    content_hash = _hash_iter_content(
        search_id=search_id, iter_index=iter_index, phase=phase,
        plan_id=plan_id, inputs_hash=inputs_hash, output_value=output_value,
        error_class=error_class, error_repr=error_repr,
    )
    pairs: list[tuple[str, Any]] = [
        (_ATTR_SEARCH_ID, search_id),
        (_ATTR_ITER_INDEX, iter_index),
        (_ATTR_PHASE, phase),
        (_ATTR_PLAN_ID, plan_id),
        (_ATTR_INPUTS_HASH, inputs_hash),
        (_ATTR_OUTPUT, output_value),
        (_ATTR_PREV_HASH, prev_hash),
    ]
    if error is not None:
        pairs.append((_ATTR_ERROR_CLASS, error_class))
        pairs.append((_ATTR_ERROR_REPR, error_repr))
    facts = [
        {"e": iter_entity, "a": a, "v": v, "valid_from": vf}
        for a, v in pairs
    ]
    return _IterDatomGroup(
        iter_id=iter_entity, facts=facts, content_hash=content_hash
    )


def _iter_provenance(search_id: str, iter_index: int) -> dict[str, Any]:
    """Provenance dict attached to each per-iteration ``db.transact``."""
    return {
        "source": _SOURCE_TAG,
        _ATTR_SEARCH_ID: search_id,
        _ATTR_ITER_INDEX: iter_index,
    }


# All names are private (underscore-prefix). No public ``__all__`` —
# everything imported by ``_mcts.py`` and the schema tests is by name
# directly.
