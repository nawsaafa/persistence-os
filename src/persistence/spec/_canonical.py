"""Canonical specs for the Persistence OS boundary.

Registered automatically at import time of :mod:`persistence.spec`.
These specs are the authoritative schema for cross-module data — the Fact,
Effect, Plan, Replay modules build to these contracts without depending on
each other's implementations.

Schema sources (see ``docs/``):
- ``:persistence.fact/datom`` — agent1-fact-spec §1
- ``:persistence.effect/op`` + ``audit-entry`` — agent3-effect-spec §§1, 6
- ``:persistence.plan/node`` + ``skill`` — paper §4.3
- ``:persistence.replay/trajectory`` / ``fact`` / ``intervention`` — agent4-replay-spec §1
- ``:persistence.domain/decision`` / ``wacc-assumption`` — task brief
"""
from __future__ import annotations

import random as _random

from ._combinators import enum, keys, map_of, maybe, or_, ref, seq_of
from ._primitives import float_, inst, int_, str_, uuid_
from ._registry import register
from ._types import ConformError, Conformed, Spec


# Module-local rng for ``_generate`` hooks. ARIS R2 F5 bans bare
# ``random.random()`` / ``random.randint()`` / ``random.choice()`` in
# production source so all non-determinism stays inside a seedable
# boundary. Tests that need reproducible samples may call
# :func:`set_generator_seed` before generating. The production contract
# for a deterministic generator is :func:`persistence.effect.perform`
# on ``:sys/random`` (W-integration is aligning ops to leading-colon);
# this module-local rng is the Phase-1 fallback until that wiring lands.
_rng = _random.Random()


def set_generator_seed(seed: int | None) -> None:
    """Re-seed the canonical-spec generator rng.

    Intended for regression tests that need reproducible samples from
    ``Spec.generate()`` / quickcheck. Passing ``None`` reinitialises
    from OS entropy.
    """
    _rng.seed(seed)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: namespaced-keyword pattern: ":foo" or ":success?" or ":foo/bar" or ":foo.baz/qux-quux?"
#: matches EDN/Clojure kw convention incl. trailing predicate '?'
_KEYWORD_RE = r":[a-zA-Z][a-zA-Z0-9._?-]*(/[a-zA-Z][a-zA-Z0-9._?-]*)?"


class _KeywordSpec(Spec):
    """EDN-style namespaced keyword: ``:foo`` or ``:foo.bar/qux-quux``.

    Accepts strings that match :data:`_KEYWORD_RE`. The generator produces
    valid keywords (the generic regex sampler is too crude to use here).
    """

    spec_name: str = ":persistence.spec/keyword"

    def _conform(self, value):
        import re as _re
        if not isinstance(value, str):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected str, got {type(value).__name__}",
                hint="provide a namespaced keyword like ':foo/bar'",
            )
        if not _re.fullmatch(_KEYWORD_RE, value):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason="not a valid namespaced keyword",
                hint="start with ':', use letters/digits/._-, optional '/' for namespace",
            )
        return Conformed(value=value, spec_key=self.spec_name)

    def _generate(self):
        import string
        def seg():
            n = _rng.randint(2, 6)
            return _rng.choice(string.ascii_lowercase) + "".join(
                _rng.choices(string.ascii_lowercase + "-", k=n - 1))
        if _rng.random() < 0.5:
            return ":" + seg()
        return ":" + seg() + "/" + seg()


_keyword_spec = _KeywordSpec()

#: non-empty string helper
class _NonEmptyStr(Spec):
    spec_name: str = ":persistence.spec/non-empty-str"

    def _conform(self, value):
        if not isinstance(value, str):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected str, got {type(value).__name__}",
                hint="provide a non-empty string",
            )
        if not value:
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason="string is empty",
                hint="provide a non-empty string explaining the intent",
            )
        return Conformed(value=value, spec_key=self.spec_name)

    def _generate(self):
        import string
        n = _rng.randint(3, 20)
        return "".join(_rng.choices(string.ascii_letters + " ", k=n)).strip() or "x"


class _Sha256Spec(Spec):
    """Content-address string: ``sha256:<hex>``."""

    spec_name: str = ":persistence.spec/sha256"

    def _conform(self, value):
        import re as _re
        if not isinstance(value, str):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected str, got {type(value).__name__}",
                hint="provide a sha256:HEX content-address",
            )
        if not _re.fullmatch(r"sha256:[0-9a-fA-F]+", value):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason="not a sha256 address",
                hint="format as 'sha256:<hex>' (e.g. 'sha256:abc123')",
            )
        return Conformed(value=value, spec_key=self.spec_name)

    def _generate(self):
        import string
        n = _rng.choice([8, 16, 64])
        return "sha256:" + "".join(_rng.choices(string.hexdigits.lower(), k=n))


_sha256_spec = _Sha256Spec()


#: float in [0, 1] — probabilities, confidences, percents
class _UnitFloat(Spec):
    spec_name: str = ":persistence.spec/unit-float"

    def _conform(self, value):
        if isinstance(value, bool) or isinstance(value, int):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected float, got {type(value).__name__}",
                hint="provide a float in [0.0, 1.0]",
            )
        if not isinstance(value, float):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected float, got {type(value).__name__}",
                hint="provide a float in [0.0, 1.0]",
            )
        if not (0.0 <= value <= 1.0):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"out of range [0,1]: {value}",
                hint="provide a float between 0.0 and 1.0 inclusive",
            )
        return Conformed(value=value, spec_key=self.spec_name)

    def _generate(self):
        return _rng.random()


# ---------------------------------------------------------------------------
# 1. :persistence.fact/datom — the 8-tuple from agent1-fact-spec §1
# ---------------------------------------------------------------------------

_datom_op = enum(":assert", ":retract")

_provenance = keys(
    required={},
    optional={
        ":episode": uuid_(),
        ":source": _keyword_spec,
        ":model": str_(),
        ":prompt-hash": str_(),
        ":confidence": _UnitFloat(),
        ":signature": str_(),
    },
)

class _AnyValueSpec(Spec):
    """Spec that accepts any EDN-compatible value — the ``:datom/v`` slot.

    EDN values in this codebase include: str, int, float, bool, None, list,
    tuple, dict, uuid.UUID, datetime. We do NOT accept arbitrary objects —
    that would silently serialize something the downstream transactor cannot
    handle.
    """

    spec_name: str = ":persistence.spec/edn-value"

    _ALLOWED = (str, int, float, bool, type(None), list, tuple, dict)

    def _conform(self, value):
        import datetime as _dt
        import uuid as _uuid
        if isinstance(value, (str, int, float, bool, type(None), _uuid.UUID, _dt.datetime)):
            return Conformed(value=value, spec_key=self.spec_name)
        if isinstance(value, (list, tuple)):
            # recurse; ensure all elements are EDN values
            for i, v in enumerate(value):
                sub = self._conform(v)
                if not sub.is_ok:
                    return sub.with_path(i)
            return Conformed(value=value, spec_key=self.spec_name)
        if isinstance(value, dict):
            for k, v in value.items():
                sub = self._conform(v)
                if not sub.is_ok:
                    return sub.with_path(k)
            return Conformed(value=value, spec_key=self.spec_name)
        return ConformError(
            spec_key=self.spec_name,
            value=value,
            reason=f"not an EDN value: {type(value).__name__}",
            hint="use str/int/float/bool/None/list/tuple/dict/UUID/datetime",
        )

    def _generate(self):
        return _rng.choice([42, 3.14, "hello", True, None])


_any_value = _AnyValueSpec()


# ARIS R1 F2 / R3 F1: content addressing (sha256:<hex>) is load-bearing
# for the effect→fact audit boundary (paper §4.1). :datom/e accepts a
# canonical UUID OR a content-hash string; :datom/tx accepts an int OR a
# content-hash string. Relaxing the spec rather than coercing the effect
# module preserves content addressing end-to-end.
_datom_e = or_(uuid_(), _sha256_spec)
_datom_tx = or_(int_(), _sha256_spec)

_datom = keys(
    required={
        ":datom/e": _datom_e,
        ":datom/a": _keyword_spec,
        ":datom/v": _any_value,
        ":datom/tx": _datom_tx,
        ":datom/tx-time": inst(),
        ":datom/valid-from": inst(),
        ":datom/valid-to": maybe(inst()),
        ":datom/op": _datom_op,
        ":datom/provenance": _provenance,
    },
    optional={
        ":datom/invalidated-by": maybe(or_(int_(), _sha256_spec)),
    },
)

register(":persistence.fact/datom", _datom)


# ---------------------------------------------------------------------------
# 2. :persistence.effect/op — one of the 15 catalog keywords
# ---------------------------------------------------------------------------

EFFECT_OP_CATALOG = (
    ":llm/call",
    ":tool/call",
    ":mem/read",
    ":mem/write",
    ":decide",
    ":ask-user",
    ":emit-artifact",
    ":sleep",
    ":random",
    ":env/read",
    ":net/fetch",
    ":secret/use",
    ":cost/charge",
    ":clock/now",
    ":audit/emit",
)

_effect_op = enum(*EFFECT_OP_CATALOG)
register(":persistence.effect/op", _effect_op)


# ---------------------------------------------------------------------------
# 3. :persistence.effect/audit-entry — agent3-effect-spec §6
# ---------------------------------------------------------------------------

_verdict = enum(":allow", ":deny", ":deny-silently", ":require-approval", ":ok", ":error")

_cost = keys(
    required={":units": float_(), ":currency": _keyword_spec},
    optional={":category": _keyword_spec},
)

# ARIS R1 F6 — ``make_audit_handler(policy_id=None)`` is the default;
# requiring a keyword here means every in-module test fails conform on
# its own entries. Move :audit/policy-id to optional. When present, it
# must still be a namespaced keyword.
_audit_entry = keys(
    required={
        ":audit/id": uuid_(),
        ":audit/run-id": uuid_(),
        ":audit/parent": maybe(uuid_()),
        ":audit/op": ref(":persistence.effect/op"),
        ":audit/args": map_of(str_(), _any_value),
        ":audit/args-hash": str_(),
        ":audit/verdict": _verdict,
        ":audit/result": _any_value,
        ":audit/latency-ms": int_(),
        ":audit/cost": _cost,
        ":audit/valid-from": inst(),
        ":audit/recorded-at": inst(),
        ":audit/handler-chain": seq_of(_keyword_spec),
        ":audit/principal": map_of(_keyword_spec, _any_value),
        ":audit/prev-hash": maybe(str_()),
    },
    optional={
        ":audit/policy-id": _keyword_spec,
    },
)
register(":persistence.effect/audit-entry", _audit_entry)


# ---------------------------------------------------------------------------
# 4. :persistence.plan/node — a plan AST node (paper §4.3)
# ---------------------------------------------------------------------------

PLAN_NODE_KINDS = (
    # control operators
    ":seq", ":par", ":choice", ":loop", ":race", ":let", ":branch",
    # choice arms (per agent2 §1 ``[:case pred branch]``)
    ":case",
    # leaves
    ":tool-call", ":llm-call", ":code", ":checkpoint",
    # cognitive operators
    ":reflect", ":verify", ":call-skill",
    # binding / dataflow
    ":ref",
)

_plan_kind = enum(*PLAN_NODE_KINDS)

class _VersionSpec(Spec):
    """Version tag like ``v1``, ``v27``."""

    spec_name: str = ":persistence.spec/version"

    def _conform(self, value):
        import re as _re
        if not isinstance(value, str):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected str, got {type(value).__name__}",
                hint="provide a version tag like 'v1'",
            )
        if not _re.fullmatch(r"v\d+", value):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason="not a version tag",
                hint="format as 'v<integer>' (e.g. 'v3')",
            )
        return Conformed(value=value, spec_key=self.spec_name)

    def _generate(self):
        return f"v{_rng.randint(1, 99)}"


_version_spec = _VersionSpec()


class _PlanNodeVector(Spec):
    """Vector-shaped plan AST node per docs/agent2-plan-spec.md §1 + §8.

    Shape: ``[:node-type {attrs} & children]`` — an EDN vector whose
    first element is a keyword tag in :data:`PLAN_NODE_KINDS`, whose
    second element is a keyword-keyed attribute map (required key:
    ``:id`` — a content-addressed sha256 string), and whose remaining
    elements are recursive child nodes (each itself conforming to
    ``:persistence.plan/node``).

    Paper §4.7 elevates spec-first plan-node as a methodology contribution
    (parse-don't-validate at the AST level), so the shape matches the
    human-readable AST example in §8 rather than the map-form it used
    to be registered as. See ARIS Round 3 P-plan-node.
    """

    spec_name: str = ":persistence.plan/node"

    def _conform(self, value):
        # Must be a vector (list/tuple). Strings and dicts get clear hints.
        if isinstance(value, (str, bytes, bytearray)):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected list/tuple, got {type(value).__name__}",
                hint="plan nodes are vectors: [:tag {attrs} & children]",
            )
        if isinstance(value, dict):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason="expected list/tuple (vector form), got dict (map form)",
                hint=(
                    "ARIS Round 3: plan nodes are now vectors "
                    "[:tag {attrs} & children], not {:node/id ... :node/kind ...}"
                ),
            )
        if not isinstance(value, (list, tuple)):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"expected list/tuple, got {type(value).__name__}",
                hint="plan nodes are vectors: [:tag {attrs} & children]",
            )
        if len(value) < 2:
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"vector length {len(value)} < 2",
                hint=(
                    "plan nodes need at least a tag and an attrs map: "
                    "[:tag {:id ...} & children]"
                ),
            )

        tag, attrs, *children = value

        # Index 0: a keyword tag from PLAN_NODE_KINDS (or :case inside :choice)
        tag_result = _plan_kind.conform(tag)
        if not tag_result.is_ok:
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"tag {tag!r} is not a recognised plan node kind",
                path=(0,),
                sub_errors=(tag_result,),  # type: ignore[arg-type]
                hint=(
                    f"use one of: {list(PLAN_NODE_KINDS)}"
                ),
            )

        # Index 1: attrs map with :id required
        if not isinstance(attrs, dict):
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"attrs (index 1) must be a dict, got {type(attrs).__name__}",
                path=(1,),
                hint="second element is an attribute map like {:id 'sha256:...'}",
            )
        if ":id" not in attrs:
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason="attrs missing required ':id' key",
                path=(1,),
                hint="every plan node must carry a content-addressed :id",
            )
        id_result = _sha256_spec.conform(attrs[":id"])
        if not id_result.is_ok:
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=":id must be a sha256 string",
                path=(1, ":id"),
                sub_errors=(id_result,),  # type: ignore[arg-type]
                hint="generate via (sha256 (pr-str node-without-meta))",
            )
        # All attr keys must be keywords.
        for k in attrs.keys():
            kr = _keyword_spec.conform(k)
            if not kr.is_ok:
                return ConformError(
                    spec_key=self.spec_name,
                    value=value,
                    reason=f"attrs key {k!r} is not a keyword",
                    path=(1, k),
                    sub_errors=(kr,),  # type: ignore[arg-type]
                    hint="attr keys must be EDN keywords like ':tool', ':join'",
                )

        # Children: each must itself conform recursively. Deferred through
        # the registry ref so circular resolution works during module load.
        errors: list[ConformError] = []
        for i, child in enumerate(children):
            # A plain :ref indirection (per doc §1 "binding / dataflow")
            # takes the form [:ref :symbol] — a 2-vector whose second
            # element is a bare keyword, not a nested node. We accept it
            # as a valid child by detecting the head tag.
            if (
                isinstance(child, (list, tuple))
                and len(child) == 2
                and child[0] == ":ref"
                and isinstance(child[1], str)
                and _keyword_spec.conform(child[1]).is_ok
            ):
                continue
            sub = self.conform(child)
            if not sub.is_ok:
                errors.append(sub.with_path(2 + i))  # type: ignore[union-attr]

        if errors:
            return ConformError(
                spec_key=self.spec_name,
                value=value,
                reason=f"{len(errors)} child/children failed",
                sub_errors=tuple(errors),
            )

        # Normalise to a tuple so the conformed value is hashable-ish and
        # deterministic; children left as-is (they may be tuples already).
        return Conformed(value=tuple(value), spec_key=self.spec_name)

    def _generate(self):
        """Minimal leaf generator — produces ``[:seq {:id sha256:xx}]``."""
        import secrets
        node_id = "sha256:" + secrets.token_hex(4)
        return [":seq", {":id": node_id}]


_plan_node = _PlanNodeVector()
register(":persistence.plan/node", _plan_node)


# ---------------------------------------------------------------------------
# 5. :persistence.plan/skill — skill record
# ---------------------------------------------------------------------------

_skill_stats = keys(
    required={
        ":uses": int_(),
        ":success": _UnitFloat(),
        ":cost": float_(),
    },
)

_plan_skill = keys(
    required={
        ":skill/name": _keyword_spec,
        ":skill/version": _version_spec,
        ":skill/ast": ref(":persistence.plan/node"),
        ":skill/stats": _skill_stats,
    },
    optional={
        ":skill/parent": _version_spec,
        ":skill/embedding": seq_of(float_()),
        ":skill/docstring": str_(),
    },
)
register(":persistence.plan/skill", _plan_skill)


# ---------------------------------------------------------------------------
# 6/7/8. :persistence.replay/* — trajectory, fact, intervention
# ---------------------------------------------------------------------------

_seeds = keys(
    required={
        ":llm": int_(),
        ":tool": int_(),
        ":env": int_(),
    },
)

_trajectory_status = enum(":running", ":completed", ":failed", ":counterfactual")
_wall_clock_basis = enum(":recorded", ":now")

# ARIS R1 F5 — the Python agent reference implementation stores state/obs/
# action with bare string keys (spec §7 prototype uses ``"balance"``, not
# ``":balance"``). Keywords are the wire format; Python uses strings. Allow
# either so the replay module's own fixtures conform out of the box.
_trajectory_fact = keys(
    required={
        ":step": int_(),
        ":t": inst(),
        ":state": map_of(str_(), _any_value),
        ":obs": map_of(str_(), _any_value),
        ":action": map_of(str_(), _any_value),
    },
    optional={
        ":llm-in": map_of(str_(), _any_value),
        ":llm-out": map_of(str_(), _any_value),
        ":tool-calls": seq_of(_any_value),
        ":random-draws": map_of(str_(), _any_value),
    },
)
register(":persistence.replay/fact", _trajectory_fact)

# ARIS R1 F5 — same rationale as :persistence.replay/fact: Python
# reference impl uses str-keyed goal/outcome dicts; EDN keywords are the
# wire convention. Relax to accept either.
_trajectory = keys(
    required={
        ":trajectory/id": uuid_(),
        ":trajectory/parent-id": maybe(uuid_()),
        ":trajectory/branch-point": int_(),
        ":trajectory/agent": str_(),
        ":trajectory/goal": map_of(str_(), _any_value),
        ":trajectory/seeds": _seeds,
        ":trajectory/started-at": inst(),
        ":trajectory/wall-clock-basis": _wall_clock_basis,
        ":trajectory/status": _trajectory_status,
        ":trajectory/outcome": map_of(str_(), _any_value),
        ":trajectory/facts": seq_of(ref(":persistence.replay/fact")),
        ":trajectory/hash": str_(),
    },
    optional={
        ":trajectory/intervention": ref(":persistence.replay/intervention"),
        ":trajectory/tags": seq_of(_keyword_spec),
    },
)
register(":persistence.replay/trajectory", _trajectory)

_intervention = keys(
    required={
        ":step": int_(),
        ":field": _keyword_spec,
        ":new-value": _any_value,
    },
)
register(":persistence.replay/intervention", _intervention)


# ---------------------------------------------------------------------------
# 9. :persistence.domain/decision — decision shape
# ---------------------------------------------------------------------------

_decision = keys(
    required={
        ":question": _NonEmptyStr(),
        ":options": seq_of(_any_value, min=1),
        ":rationale": _NonEmptyStr(),
        ":choice": _any_value,
        ":confidence": _UnitFloat(),
    },
)
register(":persistence.domain/decision", _decision)


# ---------------------------------------------------------------------------
# 10. :persistence.domain/wacc-assumption — a sample domain spec
# ---------------------------------------------------------------------------

_wacc_assumption = keys(
    required={
        ":project-id": _NonEmptyStr(),
        ":percent": _UnitFloat(),
        ":source": _keyword_spec,
        ":confidence": _UnitFloat(),
    },
    optional={
        ":notes": str_(),
    },
)
register(":persistence.domain/wacc-assumption", _wacc_assumption)


# ---------------------------------------------------------------------------
# Keep references for tests/inspection
# ---------------------------------------------------------------------------

CANONICAL_SPECS = (
    ":persistence.fact/datom",
    ":persistence.effect/op",
    ":persistence.effect/audit-entry",
    ":persistence.plan/node",
    ":persistence.plan/skill",
    ":persistence.replay/trajectory",
    ":persistence.replay/fact",
    ":persistence.replay/intervention",
    ":persistence.domain/decision",
    ":persistence.domain/wacc-assumption",
)
