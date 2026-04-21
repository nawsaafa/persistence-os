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

from ._combinators import enum, keys, map_of, maybe, or_, ref, seq_of
from ._primitives import float_, inst, int_, str_, uuid_
from ._registry import register
from ._types import ConformError, Conformed, Spec

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
        import random
        import string
        def seg():
            n = random.randint(2, 6)
            return random.choice(string.ascii_lowercase) + "".join(
                random.choices(string.ascii_lowercase + "-", k=n - 1))
        if random.random() < 0.5:
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
        import random
        import string
        n = random.randint(3, 20)
        return "".join(random.choices(string.ascii_letters + " ", k=n)).strip() or "x"


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
        import random
        import string
        n = random.choice([8, 16, 64])
        return "sha256:" + "".join(random.choices(string.hexdigits.lower(), k=n))


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
        import random
        return random.random()


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
        import random
        return random.choice([42, 3.14, "hello", True, None])


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
    # leaves
    ":tool-call", ":llm-call", ":code", ":checkpoint",
    # cognitive operators
    ":reflect", ":verify", ":call-skill",
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
        import random
        return f"v{random.randint(1, 99)}"


_version_spec = _VersionSpec()


_plan_node = keys(
    required={
        ":node/id": _sha256_spec,
        ":node/kind": _plan_kind,
    },
    optional={
        ":node/children": seq_of(_any_value),  # recursive; we don't enforce shape here
        ":node/args": map_of(_keyword_spec, _any_value),
        ":node/docstring": str_(),
        ":node/meta": map_of(_keyword_spec, _any_value),
    },
)
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

_trajectory = keys(
    required={
        ":trajectory/id": uuid_(),
        ":trajectory/parent-id": maybe(uuid_()),
        ":trajectory/branch-point": int_(),
        ":trajectory/agent": str_(),
        ":trajectory/goal": map_of(_keyword_spec, _any_value),
        ":trajectory/seeds": _seeds,
        ":trajectory/started-at": inst(),
        ":trajectory/wall-clock-basis": _wall_clock_basis,
        ":trajectory/status": _trajectory_status,
        ":trajectory/outcome": map_of(_keyword_spec, _any_value),
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
