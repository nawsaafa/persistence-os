"""Trajectory + Fact dataclasses — schema per agent4-replay-spec §1.

All non-determinism captured in effect handler cache + per-step random_draws.
Serialization is JSON-compatible (sets become sorted lists on the wire).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Optional


def _canonical(obj: Any) -> str:
    """Canonical JSON — sorted keys, no whitespace. Safe for content hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, set):
        return sorted(obj)
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


@dataclass
class Fact:
    """One step of a trajectory — all effect-captured state at step `step`."""

    step: int
    t: Any  # wall-clock timestamp or logical tick; treated opaquely.
    state: dict
    obs: dict
    llm_in: dict
    llm_out: dict
    action: dict
    tool_calls: list = field(default_factory=list)
    random_draws: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Fact":
        return cls(**d)


@dataclass
class Trajectory:
    """Full record of an agent run — facts + lineage + outcome.

    Schema from agent4-replay-spec §1. Parent-id + branch-point + intervention
    forms a lineage DAG; counterfactuals are first-class trajectories.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_id: Optional[str] = None
    branch_point: Optional[int] = None
    intervention: Optional[dict] = None
    agent: str = ""
    goal: dict = field(default_factory=dict)
    seeds: dict = field(default_factory=lambda: {"llm": 0, "tool": 0, "env": 0})
    started_at: Optional[str] = None
    wall_clock_basis: str = "recorded"  # :recorded | :now
    status: str = "completed"  # :running | :completed | :failed | :counterfactual
    outcome: dict = field(default_factory=dict)
    facts: list = field(default_factory=list)
    hash: Optional[str] = None
    tags: set = field(default_factory=set)
    # Module-internal (not part of the published schema): the EffectHandler
    # cache used during record, so replay can reuse it.
    cache: dict = field(default_factory=dict)
    # Structured call log — used by the replay handler to detect prompt-hash
    # drift (matching other-args but different prompt_hash ⇒ loud error).
    call_log: list = field(default_factory=list)
    # Set by replay() when the counterfactual extended past the factual
    # observation window — a honest marker that the suffix is :extrapolated.
    extrapolated: bool = False

    # ------------------------------------------------------------------ JSON

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "branch_point": self.branch_point,
            "intervention": self.intervention,
            "agent": self.agent,
            "goal": self.goal,
            "seeds": self.seeds,
            "started_at": self.started_at,
            "wall_clock_basis": self.wall_clock_basis,
            "status": self.status,
            "outcome": self.outcome,
            "facts": [f.to_dict() for f in self.facts],
            "hash": self.hash,
            "tags": sorted(self.tags),
            "cache": self.cache,
            "call_log": self.call_log,
            "extrapolated": self.extrapolated,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=_json_default)

    @classmethod
    def from_dict(cls, d: dict) -> "Trajectory":
        tags = set(d.get("tags", []) or [])
        facts = [Fact.from_dict(f) for f in d.get("facts", [])]
        return cls(
            id=d.get("id") or str(uuid.uuid4()),
            parent_id=d.get("parent_id"),
            branch_point=d.get("branch_point"),
            intervention=d.get("intervention"),
            agent=d.get("agent", ""),
            goal=d.get("goal", {}) or {},
            seeds=d.get("seeds", {"llm": 0, "tool": 0, "env": 0}) or {},
            started_at=d.get("started_at"),
            wall_clock_basis=d.get("wall_clock_basis", "recorded"),
            status=d.get("status", "completed"),
            outcome=d.get("outcome", {}) or {},
            facts=facts,
            hash=d.get("hash"),
            tags=tags,
            cache=d.get("cache", {}) or {},
            call_log=list(d.get("call_log", []) or []),
            extrapolated=bool(d.get("extrapolated", False)),
        )

    @classmethod
    def from_json(cls, s: str) -> "Trajectory":
        return cls.from_dict(json.loads(s))


# ----------------------------------------------------------------------- hash


_HASH_IGNORE_FIELDS = frozenset(
    {
        "id",
        "hash",
        "started_at",
        "cache",
        "call_log",
        # Lineage metadata — a counterfactual whose facts + outcome match the
        # factual byte-for-byte (NO-OP intervention case) must share the same
        # content hash even though its `parent_id` / `branch_point` /
        # `intervention` / `status` fields describe the lineage.
        "parent_id",
        "branch_point",
        "intervention",
        "status",
    }
)


def trajectory_hash(t: Trajectory) -> str:
    """Content-addressed SHA-256 of a trajectory.

    Ignores `id`, `started_at`, `hash` itself, and the in-memory `cache`
    (which is a function of the facts we already hash).
    """
    payload = {
        k: v
        for k, v in t.to_dict().items()
        if k not in _HASH_IGNORE_FIELDS
    }
    return "sha256:" + hashlib.sha256(_canonical(payload).encode()).hexdigest()
