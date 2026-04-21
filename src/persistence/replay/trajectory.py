"""Trajectory + Fact dataclasses — schema per agent4-replay-spec §1.

All non-determinism captured in effect handler cache + per-step random_draws.
Serialization is JSON-compatible (sets become sorted lists on the wire).

Two serialization surfaces:

- ``to_dict`` / ``to_json`` / ``from_dict`` / ``from_json`` — the
  Python-native JSON form with snake_case keys. Used for replay cache
  round-trips.
- ``to_edn`` / ``from_edn`` — the EDN wire form that conforms to
  ``:persistence.replay/trajectory`` in :mod:`persistence.spec`. Used at
  the cross-module boundary (ARIS R3 F3). The spec requires EDN keyword
  keys, tz-aware ``:trajectory/started-at``, keyword-prefixed seeds /
  wall-clock-basis / status, and a non-null ``:trajectory/hash``.
"""
from __future__ import annotations

import datetime as _dt
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

    # ---------------------------------------------------------------- EDN wire
    #
    # Converter to/from the :persistence.replay/fact spec shape. The spec
    # relaxation (ARIS R1 F5) accepts str keys inside :state/:obs/:action,
    # so the Python dicts pass through unchanged. :t is coerced to a
    # tz-aware datetime because the spec is strict there.

    def to_edn(self) -> dict:
        t_val = self.t
        if isinstance(t_val, (int, float)):
            # numeric logical tick → wall-clock fallback (epoch UTC of tick).
            t_val = _dt.datetime.fromtimestamp(float(t_val), tz=_dt.timezone.utc)
        elif isinstance(t_val, _dt.datetime) and t_val.tzinfo is None:
            t_val = t_val.replace(tzinfo=_dt.timezone.utc)
        edn: dict[str, Any] = {
            ":step": self.step,
            ":t": t_val,
            ":state": self.state,
            ":obs": self.obs,
            ":action": self.action,
        }
        if self.llm_in:
            edn[":llm-in"] = self.llm_in
        if self.llm_out:
            edn[":llm-out"] = self.llm_out
        if self.tool_calls:
            edn[":tool-calls"] = self.tool_calls
        if self.random_draws:
            edn[":random-draws"] = self.random_draws
        return edn

    @classmethod
    def from_edn(cls, d: dict) -> "Fact":
        return cls(
            step=d[":step"],
            t=d[":t"],
            state=d.get(":state", {}),
            obs=d.get(":obs", {}),
            action=d.get(":action", {}),
            llm_in=d.get(":llm-in", {}),
            llm_out=d.get(":llm-out", {}),
            tool_calls=list(d.get(":tool-calls", []) or []),
            random_draws=dict(d.get(":random-draws", {}) or {}),
        )


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

    # ---------------------------------------------------------------- EDN wire
    #
    # ARIS R3 F3 — the canonical :persistence.replay/trajectory spec is the
    # cross-module wire shape. to_edn / from_edn translate between the
    # Python-native form (snake_case keys, string seeds) and the EDN wire
    # form (:trajectory/* keys, keyword seeds/basis/status, tz-aware
    # :trajectory/started-at). Keep to_dict untouched — internal JSON
    # round-trip is a different use case.

    def to_edn(self) -> dict:
        """Return the EDN wire form that conforms to
        ``:persistence.replay/trajectory``.

        :trajectory/id is a UUID (generates one on the fly if self.id is
        not UUID-parseable — a Trajectory freshly constructed without an
        explicit id already has a UUID).
        """
        # Resolve :trajectory/id into a uuid.UUID (spec.conform accepts
        # strings too, but normalising here means downstream code can rely
        # on the type).
        try:
            id_val: Any = uuid.UUID(self.id) if isinstance(self.id, str) else self.id
        except (ValueError, AttributeError):
            id_val = self.id  # let the spec reject it if we must
        try:
            parent_val: Any = (
                uuid.UUID(self.parent_id) if isinstance(self.parent_id, str)
                else self.parent_id
            )
        except (ValueError, AttributeError):
            parent_val = self.parent_id
        # :trajectory/branch-point is int_ in the spec; default to 0 when
        # None so the Trajectory-before-replay case still conforms.
        branch_point = 0 if self.branch_point is None else self.branch_point
        # :trajectory/started-at must be tz-aware.
        started_at = _started_at_to_inst(self.started_at)
        # Seeds: string keys ("llm") -> keyworded (":llm").
        seeds = {(":" + k if isinstance(k, str) and not k.startswith(":") else k): v
                 for k, v in self.seeds.items()}
        # Hash: spec requires str_(); default to a sentinel sha256 if unset.
        hash_val = self.hash if self.hash is not None else "sha256:unset"
        # Tags: keywordise string tags (":demo", not "demo").
        tags = [":" + t if isinstance(t, str) and not t.startswith(":") else t
                for t in sorted(self.tags)]

        edn: dict[str, Any] = {
            ":trajectory/id": id_val,
            ":trajectory/parent-id": parent_val,
            ":trajectory/branch-point": branch_point,
            ":trajectory/agent": self.agent,
            ":trajectory/goal": self.goal,
            ":trajectory/seeds": seeds,
            ":trajectory/started-at": started_at,
            ":trajectory/wall-clock-basis": _kw(self.wall_clock_basis),
            ":trajectory/status": _kw(self.status),
            ":trajectory/outcome": self.outcome,
            ":trajectory/facts": [f.to_edn() for f in self.facts],
            ":trajectory/hash": hash_val,
        }
        if self.intervention is not None:
            edn[":trajectory/intervention"] = self.intervention
        if tags:
            edn[":trajectory/tags"] = tags
        return edn

    @classmethod
    def from_edn(cls, d: dict) -> "Trajectory":
        """Inverse of :func:`to_edn`. Accepts either the EDN wire form or a
        dict returned by :func:`to_edn` (they're the same shape).
        """
        id_raw = d.get(":trajectory/id")
        id_str = str(id_raw) if id_raw is not None else str(uuid.uuid4())
        parent_raw = d.get(":trajectory/parent-id")
        parent_str = str(parent_raw) if parent_raw is not None else None

        seeds_raw = d.get(":trajectory/seeds", {})
        seeds = {(k[1:] if isinstance(k, str) and k.startswith(":") else k): v
                 for k, v in seeds_raw.items()}

        started_at_raw = d.get(":trajectory/started-at")
        started_at = (
            started_at_raw.isoformat()
            if isinstance(started_at_raw, _dt.datetime) else started_at_raw
        )

        wall_clock_basis = _from_kw(d.get(":trajectory/wall-clock-basis", "recorded"))
        status = _from_kw(d.get(":trajectory/status", "completed"))

        tags_raw = d.get(":trajectory/tags", []) or []
        tags = set(
            (t[1:] if isinstance(t, str) and t.startswith(":") else t) for t in tags_raw
        )

        branch_point_raw = d.get(":trajectory/branch-point")

        facts_raw = d.get(":trajectory/facts", []) or []
        facts = [Fact.from_edn(f) if _looks_like_edn_fact(f) else Fact.from_dict(f)
                 for f in facts_raw]

        return cls(
            id=id_str,
            parent_id=parent_str,
            branch_point=branch_point_raw,
            intervention=d.get(":trajectory/intervention"),
            agent=d.get(":trajectory/agent", "") or "",
            goal=d.get(":trajectory/goal", {}) or {},
            seeds=seeds,
            started_at=started_at,
            wall_clock_basis=wall_clock_basis,
            status=status,
            outcome=d.get(":trajectory/outcome", {}) or {},
            facts=facts,
            hash=d.get(":trajectory/hash"),
            tags=tags,
        )


# ---------------------------------------------------------------- EDN helpers

def _kw(value: str) -> str:
    """Prepend a leading colon if missing. Idempotent."""
    return value if value.startswith(":") else ":" + value


def _from_kw(value: str) -> str:
    """Strip the leading colon if present. Idempotent on bare strings."""
    return value[1:] if isinstance(value, str) and value.startswith(":") else value


def _started_at_to_inst(raw: Any) -> _dt.datetime:
    """Coerce ``started_at`` (optional ISO string) to a tz-aware datetime.

    None → UNIX epoch UTC so a Trajectory that hasn't been recorded yet
    still conforms to ``:trajectory/started-at``.
    """
    if raw is None:
        return _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
    if isinstance(raw, _dt.datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=_dt.timezone.utc)
        return raw
    if isinstance(raw, str):
        # Accept trailing 'Z' as UTC.
        s = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
        parsed = _dt.datetime.fromisoformat(s)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=_dt.timezone.utc)
        return parsed
    raise TypeError(f"started_at type not supported: {type(raw).__name__}")


def _looks_like_edn_fact(f: Any) -> bool:
    return isinstance(f, dict) and any(k.startswith(":") for k in f.keys())


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
