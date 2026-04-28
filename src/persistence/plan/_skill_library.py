"""Content-addressed skill registry backed by the fact store (v0.6.0a1, A5).

A skill is a tagged Plan AST that has cleared promotion. Skills are
identified by ``"skill/" + plan.id[:16]`` — a 16-hex-char namespace
prefix on top of the 32-hex Plan AST content hash. Re-registering the
same Plan AST is a no-op — the content hash IS the identity, and
:meth:`SkillLibrary.register` checks both the in-memory cache (fast
path) AND the fact store (cross-instance / post-restart slow path)
before emitting datoms.

Each ``register()`` call writes exactly three datoms to the fact store
(plain-string ``a`` keys, no leading colon — ``datom.py:114``
convention):

    Datom(e=skill_id, a="skill/plan",             v=plan.id)
    Datom(e=skill_id, a="skill/promotion-record", v=promotion_id)
    Datom(e=skill_id, a="skill/registered-at",    v=registered_at_ms)

All three share one transaction (atomic registration). The fact store
is the source of truth for the registration *graph* — what's registered,
when, against which promotion record. To round-trip the Plan AST and
the promotion record themselves (i.e. recover the actual ``Node``
object from a stored ``plan.id``), this module keeps two in-memory
caches populated by ``register()``:

    * ``_plans:    dict[str, Node]``         — plan.id → Node
    * ``_records:  dict[str, _PromotionRecordLike]``
                                              — promotion_id → record-like

Persistent cross-process reconstruction (i.e. de-content-hashing a
plan.id back to a Node after a process restart) requires a
deserializer — out of scope for v0.6.0a1, scheduled for Phase 3
NeSy 2027 promotion. ``list_skills()`` *without* a tag filter
enumerates from the fact store alone (no cache needed); *with* a tag
filter, only skills whose plan is currently in the cache contribute —
unsupported entries are silently skipped (matches the design §6
contract that the fact store records the registration graph but
deserialization across restarts is out of scope).

Decoupling from A7. A7 will ship the concrete ``PromotionRecord``
dataclass. A5 ships first; we type promotion-record arguments against
:class:`_PromotionRecordLike`, a structural ``Protocol`` whose only
requirement is a ``promotion_id: str`` field. A7's real
``PromotionRecord`` (and any other future variant) satisfies the
protocol by structural duck typing — no import-order coupling.

References:
    docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md §6
    docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md §2.A5
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Protocol

from persistence.fact.db import DB
from persistence.plan._ast import Node

__all__ = ["SkillLibrary"]


#: ``a`` (attribute) key for the ``plan_id`` triple in a registration.
#: Plain string per ``datom.py:114`` — no leading colon.
_A_PLAN: str = "skill/plan"

#: ``a`` key for the promotion-record reverse-lookup triple.
_A_PROMOTION_RECORD: str = "skill/promotion-record"

#: ``a`` key for the caller-supplied registration timestamp (ms epoch).
_A_REGISTERED_AT: str = "skill/registered-at"

#: Namespace prefix on the 16-hex-char Plan id slice.
_SKILL_ID_PREFIX: str = "skill/"

#: Width of the Plan id slice that becomes the ``skill_id`` body. 16 hex
#: chars = 64 bits → birthday-collision at ~2^32 skills (the roadmap
#: caps in low thousands; the headroom is ample).
_SKILL_ID_HEX_WIDTH: int = 16


class _PromotionRecordLike(Protocol):
    """Structural type for what SkillLibrary consumes from a promotion record.

    A7 ships the concrete ``PromotionRecord`` dataclass; A5 codes against
    this protocol so the modules stay decoupled. Only ``promotion_id``
    is read by SkillLibrary code; A7's full record (G1/G2/G3/G4 fields,
    timestamps, etc.) is preserved in the in-memory cache for round-trip
    via :meth:`SkillLibrary.lookup` but never inspected.

    Declared via ``@property`` (read-only access) rather than a class
    attribute so frozen-dataclass implementations — including A7's
    ``PromotionRecord(frozen=True, slots=True)`` and the test stubs —
    satisfy the protocol structurally. A bare class-attr declaration
    would imply mutability and pyright would reject frozen dataclasses
    as incompatible.
    """

    @property
    def promotion_id(self) -> str: ...


def _derive_skill_id(plan: Node) -> str:
    """Return ``"skill/" + plan.id[:16]`` — the canonical namespaced id.

    Made a helper rather than inlined so the derivation rule has one
    site to change if a future canonical-form bump narrows or widens
    the prefix slice. (Width changes would invalidate every persisted
    skill_id — same kind of canonical-form bump as
    ``PLAN_CANONICAL_VERSION``.)
    """
    return _SKILL_ID_PREFIX + plan.id[:_SKILL_ID_HEX_WIDTH]


def _epoch_datetime() -> datetime:
    """Reusable Unix-epoch sentinel used as ``valid_from`` for skill datoms.

    The fact store requires ``valid_from`` on every fact; skill
    registrations have no real-world valid-time semantics (a skill is
    "registered" at a wall-clock instant, not "valid from / to" a
    domain interval). Pinning ``valid_from`` to the Unix epoch keeps the
    bitemporal interface honest without inventing semantics. The
    transaction-time (``tx_time``) — set by ``DB.transact`` via the
    injectable clock — is the meaningful timestamp for queries like
    ``db.as_of(t)``.
    """
    return datetime.fromtimestamp(0, tz=timezone.utc)


class SkillLibrary:
    """Content-addressed skill registry over a :class:`DB`.

    The fact store is the source of truth for the registration *graph*.
    In-memory caches map Plan id → Node and promotion_id → record-like
    so :meth:`lookup` can return the original objects (byte-identity),
    not deserialized copies. The caches are populated only by
    :meth:`register`, so a freshly-constructed SkillLibrary over a DB
    that already contains skill datoms (e.g. a snapshot DB) can still
    enumerate skill ids via :meth:`list_skills` (no tag filter), but
    cannot resolve them to Node / record objects without re-registration.
    Persistent reconstruction is Phase 3 NeSy 2027 scope.
    """

    def __init__(self, db: DB) -> None:
        """Bind to ``db``. Caches start empty.

        Args:
            db: The fact store. Both ``register`` and ``list_skills``
                read it; ``register`` also writes to it. Each
                ``register`` call returns a new DB (functional
                ``DB.transact`` semantics) which the library swaps
                in place — the public surface still holds a single DB
                reference.
        """
        self._db: DB = db
        self._plans: dict[str, Node] = {}
        self._records: dict[str, _PromotionRecordLike] = {}

    def register(
        self,
        plan: Node,
        promotion_record: _PromotionRecordLike,
        *,
        registered_at_ms: int,
    ) -> str:
        """Register ``plan`` as a skill. Returns the ``skill_id``.

        Re-registration of an existing skill (same plan content) is a
        no-op: the same ``skill_id`` is returned and ZERO additional
        datoms are written. Idempotency is checked against the
        in-memory ``_plans`` cache — same plan content → same skill_id
        → already in cache. This is the de-content-hashing cache
        (skill_id is content-addressed, so ``skill_id in self._plans``
        is equivalent to "this exact Plan AST has been registered
        before").

        ``registered_at_ms`` is caller-supplied: production source TBD
        by Module 7 REPL or a ``:clock/now`` effect handler. No
        ``time.time()`` / ``time.monotonic()`` fallback — repo
        discipline (Phase B precedent).

        Args:
            plan: Plan AST root to register.
            promotion_record: Anything satisfying
                :class:`_PromotionRecordLike` (i.e. has a
                ``promotion_id: str`` field). A7's
                ``PromotionRecord`` will satisfy this; tests fabricate
                a minimal stub.
            registered_at_ms: Caller-supplied registration timestamp
                in milliseconds since the Unix epoch. Stored verbatim
                as the ``v`` of the ``skill/registered-at`` datom.

        Returns:
            The ``skill_id`` (``"skill/" + plan.id[:16]``).
        """
        skill_id = _derive_skill_id(plan)

        # Idempotency check, fast path: in-memory cache. Skill_id is
        # content-addressed (deterministic from plan.id), so cache
        # presence is equivalent to "this exact Plan AST has been
        # registered before by THIS SkillLibrary instance".
        if skill_id in self._plans:
            return skill_id

        # Idempotency check, slow path: another SkillLibrary instance
        # (or this same instance, post-restart with seeded store) may
        # already have written a ``skill/plan`` datom for this skill_id
        # to the fact store. The fact store is the source of truth for
        # the registration *graph*; cache absence is not authoritative.
        # One log scan detects the cross-instance case; we then return
        # without re-emitting datoms (matches docstring claim:
        # re-registration is a no-op, full stop).
        #
        # We populate ``_plans`` with the freshly-passed plan because
        # skill_id is sha256-derived from canonical Plan AST bytes — a
        # skill_id match is a content-equality proof (modulo birthday
        # collisions at 2^32 scale, which we accept). This lets THIS
        # instance's :meth:`lookup` return the plan back. We do NOT
        # populate ``_records``: the freshly-passed promotion_record
        # may not match the one originally registered against this
        # skill_id; the fact store's ``skill/promotion-record`` datom
        # is canonical for that mapping.
        for d in self._db.log():
            if d.e == skill_id and d.a == _A_PLAN and d.op == "assert":
                self._plans[skill_id] = plan
                return skill_id

        # Three datoms in a single transact() — atomic registration.
        # Plain-string ``a`` keys (no leading colon) per
        # ``datom.py:114``. ``valid_from`` is pinned to the Unix epoch
        # because skill registration has no real-world valid-time
        # semantics; the meaningful timestamp is ``tx_time``, which
        # ``DB.transact`` sets from the injected clock.
        valid_from = _epoch_datetime()
        promotion_id = promotion_record.promotion_id
        facts = [
            {
                "e": skill_id,
                "a": _A_PLAN,
                "v": plan.id,
                "valid_from": valid_from,
            },
            {
                "e": skill_id,
                "a": _A_PROMOTION_RECORD,
                "v": promotion_id,
                "valid_from": valid_from,
            },
            {
                "e": skill_id,
                "a": _A_REGISTERED_AT,
                "v": registered_at_ms,
                "valid_from": valid_from,
            },
        ]
        # ``DB.transact`` returns a NEW DB bound to the same store;
        # swap our reference so subsequent reads see the appended log.
        self._db = self._db.transact(facts, provenance={"source": "skill-library"})

        # Populate the in-memory caches *after* the transact so a
        # transact failure (which would leave the store unchanged)
        # cannot leave the caches in a half-populated state.
        self._plans[skill_id] = plan
        self._records[promotion_id] = promotion_record

        return skill_id

    def lookup(
        self, skill_id: str
    ) -> Optional[tuple[Node, _PromotionRecordLike]]:
        """Return ``(plan_node, promotion_record_like)`` or ``None``.

        Reads exclusively from the in-memory caches: ``skill_id`` →
        ``plan`` via ``_plans``, then ``plan_id`` carries through to
        the ``_records`` cache via the ``skill/promotion-record``
        datom's ``v``. Both caches are populated only by
        :meth:`register`, so cross-process reconstruction (after a
        process restart with no re-registration) returns ``None`` for
        skill ids whose Plan AST was not registered in this process.

        Args:
            skill_id: The namespaced id returned by :meth:`register`.

        Returns:
            ``(plan, record)`` if both are cached; ``None`` otherwise.
        """
        plan = self._plans.get(skill_id)
        if plan is None:
            return None
        # Resolve promotion_id from the fact store (the source of
        # truth for the registration graph). Walking the log is O(N)
        # over total datom count; the roadmap caps the workload such
        # that this is fine for v0.6.0a1. A future indexed lookup
        # ($e, $a) → datom would lift the constant factor.
        promotion_id: Optional[str] = None
        for d in self._db.log():
            if d.e == skill_id and d.a == _A_PROMOTION_RECORD and d.op == "assert":
                promotion_id = d.v
                break
        if promotion_id is None:
            # Cache hit on plan but no datom for the promotion record —
            # would mean the caches drifted from the fact store, which
            # ``register`` can't produce. Treat as "not registered" so
            # the caller surfaces a deterministic ``None``.
            return None
        record = self._records.get(promotion_id)
        if record is None:
            return None
        return (plan, record)

    def list_skills(self, *, tag_filter: Optional[str] = None) -> list[str]:
        """Enumerate registered skill ids.

        Without ``tag_filter``: returns every distinct skill id whose
        ``skill/plan`` datom is asserted in the fact store. Works for a
        SkillLibrary backed by a snapshot DB (e.g. one seeded from
        ``db.as_of(t).datoms``) — the fact-store enumeration is
        complete on its own.

        With ``tag_filter``: filters to skills whose Plan AST root tag
        equals ``tag_filter``. Requires the Plan AST to be in the
        in-memory ``_plans`` cache — skill ids that exist in the fact
        store but not in the cache (e.g. a snapshot DB whose original
        registrations happened in another process) are silently
        excluded.

        Args:
            tag_filter: Plan AST root tag to filter by (e.g. ``":seq"``)
                or ``None`` for no filter.

        Returns:
            List of skill ids. Order matches the underlying log's
            insertion order; callers requiring stability should sort.
        """
        skill_ids: list[str] = []
        seen: set[str] = set()
        for d in self._db.log():
            if d.a != _A_PLAN or d.op != "assert":
                continue
            sid = d.e
            if sid in seen:
                continue
            seen.add(sid)

            if tag_filter is not None:
                plan = self._plans.get(sid)
                if plan is None or plan.tag != tag_filter:
                    continue

            skill_ids.append(sid)
        return skill_ids
