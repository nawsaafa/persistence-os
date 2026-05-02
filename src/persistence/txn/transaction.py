"""Transaction — the object passed into a dosync body.

Captures read_set, write_set, effect_intent_log, and metadata
(t_start snapshot, attempt count, commit_id allocated at success).

The retry loop and commit gate live alongside this class and are wired
in via ``Transaction._run`` (added in Task B7).
"""
from __future__ import annotations

import time
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from persistence.spec import conform as _spec_conform
from persistence.spec import registered_keys as _registered_keys
from persistence.txn.conflict import any_datoms_since
from persistence.txn.errors import (
    NestedDosyncNotSupported,
    RefValueNotImmutable,
    TxnDeadlineExceeded,
    TxnRetryExhausted,
)
from persistence.txn.intents import (
    EffectIntent,
    clear_dosync_guard,
    is_in_dosync,
    set_dosync_guard,
)
from persistence.txn.ref import Ref, is_immutable_value


@dataclass
class Transaction:
    """Mutable transaction state passed into a dosync body.

    Lives for one body invocation. On retry, a fresh Transaction is
    constructed; the previous one's read_set / write_set / intent_log
    are discarded.

    Fields are mutable on purpose — the body progressively builds the
    sets via ``deref``/``assoc``/``alter``/``effect``. The Transaction
    itself does not commit; ``_run`` (Task B7) drives commit and retry.
    """

    db: Any
    t_start: datetime
    attempt: int
    read_set: set[Ref] = field(default_factory=set)
    write_set: dict[Ref, Any] = field(default_factory=dict)
    ensure_set: set[Ref] = field(default_factory=set)
    effect_intent_log: list[EffectIntent] = field(default_factory=list)
    # v0.5.2 § F3 — commute_log entries are body-order tuples
    # ``(ref, fn_id, args)`` where ``args`` is a tuple of positional
    # args. Each entry is reapplied at commit-time inside the
    # writer-lock against the latest committed value. NOT added to
    # read_set — commute is conflict-free by design.
    commute_log: list[tuple[Ref, str, tuple]] = field(default_factory=list)
    # Eager body-time cache of commute results — keyed by ref. Populated
    # by ``tx.commute`` so a subsequent ``tx.deref`` on the same ref
    # returns the optimistic body-eager value (intra-txn case 4 of
    # § F3). Distinct from ``write_set`` because explicit writes win
    # over commute at commit-time (case 2); keeping them separate keeps
    # that invariant explicit at the data-structure level.
    _commute_eager: dict[Ref, Any] = field(default_factory=dict)
    # Phase 2.0d W1 (M3) — opaque fact dicts staged for commit-time
    # transact_batch. Populated by surfaces that need to atomically
    # commit "extra" facts outside the ref-write_set model (e.g.
    # ``s.txn.fold_into`` queues the chosen branch's facts here so
    # they ride the outer dosync commit and roll back if the outer
    # raises). Each entry has the same shape as
    # :meth:`persistence.fact.DB.transact_batch`'s ``facts`` arg.
    # The list is appended to in body-order; commit-time emission
    # preserves order. Pre-W1 callers that bypassed the txn's commit
    # path via direct ``db.transact_batch`` mid-dosync no longer
    # need to do so — and the bypass was unsafe under retry / outer
    # raise.
    staged_facts: list[dict] = field(default_factory=list)
    commit_id: str | None = None
    # Phase 2.0e (#175) — set of Node.id values for plan steps that have
    # completed execution within this transaction. Populated by the Plan
    # executor (persistence.plan._execute) at the leaf-completion boundary
    # (lands as a follow-up commit during Phase 2.3a Plan escalation gate
    # work; this field is the substrate-side commitment surface). Consulted
    # by persistence.plan._edit.delete_step to enforce the design § 4.1
    # "only allowed if no downstream step has executed" invariant — without
    # this field the check could only be call-site validation, which would
    # let a buggy agent silently delete a step whose effect already ran.
    # Mutable on purpose; appended to in body-order; reset on retry by
    # virtue of a fresh Transaction being constructed each attempt.
    completed_step_ids: set[str] = field(default_factory=set)

    def now(self) -> datetime:
        """Frozen t_start. Use this instead of datetime.now() inside
        a dosync body so the value is deterministic across retries.
        """
        return self.t_start

    def assoc(self, ref: Ref, value: Any) -> None:
        """Queue a write of ``value`` to ``ref`` at commit time.

        Raises ``RefBranchMismatch`` if ``ref`` was constructed against
        a different DB. Raises ``RefValueNotImmutable`` if ``value`` is
        mutable.
        """
        from persistence.txn.errors import RefBranchMismatch
        from persistence.txn._db_extension import _get_db_id

        if ref.db_id != _get_db_id(self.db):
            raise RefBranchMismatch(
                f"ref {ref!r} belongs to a different DB than this dosync"
            )
        if not is_immutable_value(value):
            raise RefValueNotImmutable(
                f"tx.assoc value must be immutable; got {type(value).__name__!r}. "
                f"Use persistence.txn.freeze(...) or wrap in pyrsistent."
            )
        self.write_set[ref] = value

    def add_facts(self, facts: list[dict]) -> None:
        """Phase 2.0d W1 (M3) — stage opaque fact dicts for commit-time
        atomic batch.

        ``facts`` has the same shape :meth:`persistence.fact.DB.transact_batch`
        accepts: a list of dicts with ``e`` / ``a`` / ``v`` keys (and
        optional ``valid_from`` / ``valid_to`` / ``op``). The dicts
        are stored on ``tx.staged_facts`` in append order; at commit
        time they ride the same atomic ``transact_batch`` call as the
        ref write_set + commute reapply + commit datom — so an
        outer-dosync raise rolls them back too.

        This is the supported path for surfaces that need to commit
        facts without going through ``tx.assoc`` / ``tx.alter`` (e.g.
        ``s.txn.fold_into`` queueing the chosen branch's facts after
        ``DB.fork`` has resolved). Pre-W1 callers used
        ``db.transact_batch`` mid-dosync, which committed
        immediately and broke atomicity under outer-raise / retry.

        Args:
            facts: list of fact dicts to stage. The list is shallow-
                copied here so subsequent caller mutation does not
                reach the txn's staged log.
        """
        if not isinstance(facts, list):
            raise TypeError(
                f"tx.add_facts: facts must be a list, got "
                f"{type(facts).__name__}"
            )
        # Shallow-copy each dict so caller cannot mutate post-stage.
        # The dict shape is opaque from this layer's perspective —
        # commit-time spec validation lands in transact_batch.
        self.staged_facts.extend(dict(f) for f in facts)

    def effect(self, op: str, **kwargs: Any) -> None:
        """Queue an effect intent to be replayed atomically at commit.

        Validation note: the kwargs are NOT validated at this call site.
        Validation against ``:persistence.txn/intent-log`` happens at commit
        time when the intent is serialised onto the commit datom's
        provenance. A ``tx.effect()`` call that succeeds may still cause
        the enclosing dosync to raise ``SpecError`` at commit if the kwargs
        aren't EDN-conformant (e.g., raw ``datetime`` objects, custom
        classes). Callers expecting eager validation must conform their
        kwargs themselves before calling ``tx.effect()``.

        See ``persistence.txn._specs._EdnValueSpec`` for the per-value rule
        (scalars + lists/tuples + str-keyed dicts; rejects datetime).
        """
        self.effect_intent_log.append(EffectIntent(op=op, kwargs=dict(kwargs)))

    def deref(self, ref: Ref) -> Any:
        """Snapshot read of ``ref`` at ``t_start``.

        Branch-checked: ``ref`` must belong to this transaction's DB.
        Adds ``ref`` to the read_set. If the body has already written
        to this ref via ``assoc`` or ``alter``, returns the pending
        write (read-your-own-writes); otherwise returns the value of
        the underlying entity at ``t_start``, or ``None`` if no
        ``:value`` attribute has been asserted yet.
        """
        from persistence.txn.errors import RefBranchMismatch
        from persistence.txn._db_extension import _get_db_id

        if ref.db_id != _get_db_id(self.db):
            raise RefBranchMismatch(
                f"ref {ref!r} belongs to a different DB than this dosync"
            )
        self.read_set.add(ref)
        if ref in self.write_set:
            return self.write_set[ref]
        # v0.5.2 § F3 — intra-txn case 4: a previous ``tx.commute`` on
        # this ref produced an optimistic body-eager value. Return it
        # so the body sees its own (read-your-own-commutes). The
        # ``read_set.add`` above is idempotent (it's a set), so calling
        # ``deref`` multiple times after ``commute`` does NOT inflate
        # the conflict-detection footprint. Note: ``commute`` itself
        # does NOT add to read_set (the entire point) — but if the
        # body subsequently ``deref``s the ref, that deref-call DOES
        # take the read-set hit, which is correct read-your-own-writes
        # semantics.
        if ref in self._commute_eager:
            return self._commute_eager[ref]
        # Snapshot read at t_start. The ref's value lives under the
        # ``ref.spec_attr`` attribute name (default ``"value"`` preserves
        # v0.5.0a1 behavior bit-for-bit). Two refs sharing an eid but
        # carrying different ``spec_attr`` resolve to different attribute
        # values on the same entity — this is the per-ref-attribute
        # contract introduced in v0.5.1 (rev O / N3).
        view = self.db.as_of(self.t_start)
        entity_attrs = view.entity(ref.eid)
        return entity_attrs.get(ref.spec_attr) if entity_attrs else None

    def alter(self, ref: Ref, fn: Callable, *args: Any) -> Any:
        """Read ``ref`` at snapshot, apply ``fn(snapshot_value, *args)``,
        queue the result as a write, return the new value.

        Adds ``ref`` to the read_set (so concurrent writes to ``ref``
        cause this transaction to retry). The new value must be
        immutable; ``RefValueNotImmutable`` is raised otherwise.
        """
        current = self.deref(ref)
        new_value = fn(current, *args)
        self.assoc(ref, new_value)
        return new_value

    def commute(self, ref: Ref, fn_id: str, *args: Any) -> Any:
        """Commutative write — eager-at-body + reapply-at-commit.

        Two-phase semantic (matches Clojure
        ``LockingTransaction.doCommute``):

        1. **At this call:** look up ``fn_id`` in the curated registry,
           apply against the optimistic committed-or-locally-written
           value, append ``(ref, fn_id, args)`` to ``commute_log``,
           cache the result so subsequent ``tx.deref(ref)`` returns
           it (read-your-own-commutes), and return the new value.
        2. **At commit (inside writer-lock):** re-read the ref's
           latest committed value, re-apply ``fn``, write the result.
           The body-eager value is discarded; only the commit-time
           value reaches the log.

        Soundness depends on ``fn`` being commutative: ``f(f(v, a),
        b) == f(f(v, b), a)``. The substrate does NOT verify this —
        the registry is curated to functions known to be commutative.

        ``ref`` is NOT added to ``read_set`` — that is the entire
        point of ``commute`` vs ``alter``: two parallel transactions
        calling ``tx.commute(counter, "inc-by", 1)`` both succeed with
        no retry and produce a final counter incremented by 2.

        Intra-txn semantics (4 cases — see § F3 line 307-312):

        - **Multiple commutes on same ref:** composed in body-order;
          eager value at call N is ``fn(eager_value_at_N-1, *args)``.
        - **Commute then assoc/alter:** explicit write WINS at commit;
          commute log entries on that ref are dropped.
        - **Assoc/alter then commute:** body-eager value seen by the
          commute is the just-set explicit value (so subsequent body
          reads see ``fn(explicit, *args)``); **at commit the explicit
          write WINS and the commute on that ref is DROPPED** —
          structurally identical to case 2 (write_set membership at
          commit-time drops commute regardless of body order). The
          design doc § F3 line 311 prose ("both apply; commute
          reapplies against explicit-write's value") is superseded by
          case 2's drop-on-write_set-membership invariant; v0.5.2 R2
          MAJOR-2 closure pinned the realised behavior in
          ``tests/persistence/txn/test_commute.py:328-367``.
        - **Deref after commute:** returns the optimistic body-eager
          value; idempotent on repeated derefs.

        Raises ``RefBranchMismatch`` if ``ref`` was constructed
        against a different DB. Raises ``ValueError`` if ``fn_id`` is
        not registered. Raises ``RefValueNotImmutable`` if the
        eager-applied result is mutable.

        See v0.5.2 design § F3 for the full semantics + acceptance
        gates.
        """
        from persistence.txn._commute import lookup_commute, _DEFAULT_COMMUTE_TABLE
        from persistence.txn.errors import RefBranchMismatch
        from persistence.txn._db_extension import _get_db_id

        if ref.db_id != _get_db_id(self.db):
            raise RefBranchMismatch(
                f"ref {ref!r} belongs to a different DB than this dosync"
            )
        fn = lookup_commute(fn_id)
        if fn is None:
            raise ValueError(
                f"tx.commute: unknown fn_id {fn_id!r}. Curated registry: "
                f"{sorted(_DEFAULT_COMMUTE_TABLE.keys())}. Use "
                f"register_commute (test-only, gated by "
                f"PERSISTENCE_TXN_ALLOW_RUNTIME_REGISTRATION) to add custom "
                f"fns."
            )
        # Eager base resolution — see intra-txn cases.
        if ref in self.write_set:
            # Case 3: explicit write happened earlier in body. Commute
            # eager-applies on top of the explicit value so subsequent
            # body reads see ``fn(explicit, *args)``. **At commit, the
            # explicit write wins and this commute entry is dropped**
            # (case 2's drop-on-write_set-membership rule covers cases
            # 2 and 3 uniformly — see ``_build_commute_facts`` and the
            # docstring above for the realised invariant). The
            # body-eager value here influences subsequent in-body reads
            # only, not the committed log.
            eager_base = self.write_set[ref]
        elif ref in self._commute_eager:
            # Case 1: a previous commute on the same ref produced an
            # optimistic body value. Compose in body-order.
            eager_base = self._commute_eager[ref]
        else:
            # Snapshot read at t_start (NOT via self.deref — we must
            # not add to read_set). Same path as deref's snapshot
            # branch.
            view = self.db.as_of(self.t_start)
            entity_attrs = view.entity(ref.eid)
            eager_base = (
                entity_attrs.get(ref.spec_attr) if entity_attrs else None
            )
        new_value = fn(eager_base, *args)
        if not is_immutable_value(new_value):
            raise RefValueNotImmutable(
                f"tx.commute eager-applied value must be immutable; "
                f"got {type(new_value).__name__!r} from fn_id {fn_id!r}. "
                f"The registered fn must return an immutable value (use "
                f"pyrsistent.PMap/PVector/PSet, frozenset, tuple, or a "
                f"frozen scalar)."
            )
        # ``args`` is a Python positional tuple — preserved as-is for
        # both the log entry and provenance emission. Tuple is the
        # canonical "frozen ordered sequence" shape.
        self.commute_log.append((ref, fn_id, args))
        self._commute_eager[ref] = new_value
        return new_value

    def ensure(self, ref: Ref) -> Any:
        """Add ``ref`` to ``ensure_set`` AND return its snapshot value.

        Mirrors Clojure ``LockingTransaction.ensure(ref)`` which returns
        the deref'd value so ``ensure`` is a strict superset of ``deref``
        for ergonomic chained reads. Internally calls ``self.deref(ref)``
        (which adds ``ref`` to ``read_set`` and returns the snapshot) then
        adds ``ref`` to ``ensure_set`` so any subsequent body code that
        ignores the return value still gets the conflict-padding behavior.

        Conflict detection at commit reads ``read_set | write_set |
        ensure_set`` against ``db.store.since(t_start)`` — if any datom
        on any of the three sets has ``tx_time > t_start``, the dosync
        retries.

        Note on the dual sets: because ``tx.ensure(ref)`` calls
        ``tx.deref(ref)`` which already adds to ``read_set``, the
        conflict-detection union is mathematically equivalent to just
        unioning ``ensure_set`` into ``read_set``. The semantic value
        of a separate ``ensure_set`` is **provenance distinguishability**
        — at commit time an external auditor reading the commit datom
        can tell which refs were "actually deref'd for value" (in
        ``:persistence.txn/read-set``) vs which were "padded for
        conflict-detection only" (in ``:persistence.txn/ensure-set``).

        See v0.5.2 design § F2.
        """
        from persistence.txn.errors import RefBranchMismatch
        from persistence.txn._db_extension import _get_db_id

        if ref.db_id != _get_db_id(self.db):
            raise RefBranchMismatch(
                f"ref {ref!r} belongs to a different DB than this dosync"
            )
        value = self.deref(ref)  # also adds to read_set + branch-checks
        self.ensure_set.add(ref)
        return value



DEFAULT_MAX_RETRIES = 256
WRITE_ATTR = "value"  # default ``Ref.spec_attr`` (Datom strips leading ":" from `a`)


def _raise_spec_error(result: Any) -> None:
    """v0.5.1 W1 fix-pass — R2 MINOR 3: centralised SpecError raise.

    ``SpecError`` lives in ``persistence.spec._registry`` and is not
    re-exported through ``persistence.spec.__all__`` (latent v0.5.0a1
    gap; tests that need it use the same submodule path, e.g.
    ``tests/plan/test_parse.py``). Two near-identical raise blocks
    inside this module — write-set spec validation and intent-log
    spec validation — collapse into this helper. The
    ``# type: ignore[arg-type]`` matches the canonical pattern at
    ``persistence/spec/_registry.py:71``.
    """
    from persistence.spec._registry import SpecError
    raise SpecError(result)  # type: ignore[arg-type]


def _spec_validate_writes(write_set: dict) -> None:
    """Run spec.conform over each write before tx-id allocation.

    v0.5.1 (rev O / N3): each ref carries its own ``spec_attr`` (default
    ``"value"``); we look up that key in the registry and conform the
    write against it. Refs with no spec registered for their attr pass
    through untouched. This generalises the v0.5.0a1 single-global-spec
    behavior — the default ``spec_attr="value"`` makes unannotated
    callers identical to before.

    Raises ``persistence.spec.SpecError`` on failure (caller propagates;
    no tx-id is burned).
    """
    keys = set(_registered_keys())
    for ref, value in write_set.items():
        spec_key = ref.spec_attr
        if spec_key not in keys:
            continue
        result = _spec_conform(spec_key, value)
        if not result.is_ok:
            _raise_spec_error(result)


def _build_write_facts(tx: "Transaction") -> list[dict]:
    """Build the list-of-fact-dicts payload for db.transact().

    v0.5.1 (rev O / N3): each fact's ``a`` is ``ref.spec_attr`` (default
    ``"value"``), so per-ref attribute names land on the Datom store
    directly. Datom strips a leading ``":"`` if present.
    """
    now = tx.db._clock()
    facts: list[dict] = []
    for ref, value in tx.write_set.items():
        facts.append({
            "e": ref.eid,
            "a": ref.spec_attr,
            "v": value,
            "valid_from": now,
        })
    return facts


def _build_commit_fact(tx: "Transaction", commit_id: str) -> dict:
    """Build the per-commit datom (one extra fact per dosync)."""
    now = tx.db._clock()
    return {
        "e": commit_id,
        "a": ":persistence.txn/commit-id",
        "v": commit_id,
        "valid_from": now,
    }


def _build_commute_facts(tx: "Transaction") -> tuple[list[dict], dict[Ref, Any]]:
    """Reapply each commute_log entry against latest committed value.

    Returns ``(facts_list, resolved)`` where ``resolved`` is a per-ref
    dict of the latest computed value — used both for the provenance
    emission cross-check and for intra-batch composition: when the
    same ref has multiple commute entries, the second entry's reapply
    sees the first entry's just-computed value (compose in body-order
    against the latest committed value as the seed).

    Refs that ALSO appear in ``tx.write_set`` are SKIPPED — the
    explicit write wins per § F3 intra-txn cases 2 AND 3 (commute-
    then-set OR set-then-commute both drop the commute fact at commit;
    the realised invariant is "any ref in write_set drops its commute
    log entries, regardless of body-order interleaving"). The
    explicit write fact is emitted separately by
    :func:`_build_write_facts`; this helper just refrains from
    emitting a competing commute fact for those refs. See
    ``Transaction.commute`` docstring + the case-3 pinning test in
    ``tests/persistence/txn/test_commute.py:328-367`` for the
    superseding-rationale on the design doc § F3 line 311 prose.

    Called from inside the writer-lock window in :func:`_commit_attempt`
    AFTER the conflict-detection check passes. Reads the latest
    committed value via ``tx.db.as_of(now)`` against the post-conflict-
    check store state.

    Raises ``RuntimeError`` if a ``fn_id`` recorded in the commute_log
    is no longer in the registry — that means the registry was mutated
    between body call and commit, which is forbidden under the
    static-registry contract.
    """
    from persistence.txn._commute import lookup_commute

    explicit_write_refs = set(tx.write_set.keys())
    facts: list[dict] = []
    resolved: dict[Ref, Any] = {}
    now = tx.db._clock()
    # ``view_at_now`` is the latest snapshot of the DB at this lock-held
    # moment. The conflict-check upstream proved that any datoms after
    # ``t_start`` on read_set/write_set/ensure_set refs are absent — but
    # commute refs were deliberately excluded from that union, so this
    # snapshot is the only place that sees concurrent commute updates.
    view_at_now = tx.db.as_of(now)
    for ref, fn_id, args in tx.commute_log:
        if ref in explicit_write_refs:
            # Case 2: explicit write wins; drop commute entries for
            # this ref. The explicit write fact is emitted by
            # ``_build_write_facts`` (already called by the caller).
            continue
        fn = lookup_commute(fn_id)
        if fn is None:
            raise RuntimeError(
                f"commute fn_id {fn_id!r} disappeared from registry "
                f"between body call and commit — registry mutation "
                f"during dosync is forbidden (the static-registry "
                f"contract guarantees cross-host determinism by "
                f"forbidding mid-flight changes)."
            )
        if ref in resolved:
            # Multiple commutes on same ref — compose in body-order
            # against the result of the previous commute's reapply.
            # (Case 1 of intra-txn semantics, applied at commit-time.)
            base = resolved[ref]
        else:
            entity_attrs = view_at_now.entity(ref.eid)
            base = entity_attrs.get(ref.spec_attr) if entity_attrs else None
        new_value = fn(base, *args)
        if not is_immutable_value(new_value):
            raise RefValueNotImmutable(
                f"commute reapply at commit produced a mutable value "
                f"({type(new_value).__name__!r}) for fn_id {fn_id!r}; "
                f"the registered fn must return an immutable value."
            )
        resolved[ref] = new_value
        facts.append({
            "e": ref.eid,
            "a": ref.spec_attr,
            "v": new_value,
            "valid_from": now,
        })
    return facts, resolved


def _build_commit_provenance(tx: "Transaction", commit_id: str) -> dict:
    """Build the dict passed as ``transact_batch(provenance=...)``.

    Reads the optional private ``tx._deadline_set`` flag set by ``_run``
    once before the retry loop (defaults to ``False`` — the CM path
    never sets it).

    v0.5.1 (rev O / N1) emits two additional keys:

    - ``:persistence.txn/read-set`` — sorted list of eids the body
      read via ``tx.deref`` (or implicitly via ``tx.alter``). Empty
      list when the body wrote without reading.
    - ``:persistence.txn/intent-log`` — list of
      ``{":op": str, ":kwargs": dict}`` items in queue order, one per
      ``tx.effect()`` call. Each item is conformed against the
      registered ``:persistence.txn/intent-log`` element spec; on
      failure ``SpecError`` is raised here, before the commit datom
      is written. This is option (3) from the design doc — strict
      validation at commit time, not at ``tx.effect()`` call site.
      v0.5.1 W1 fix-pass (R2 MINOR 1): this conformance call now runs
      OUTSIDE the ``store._lock`` window in ``_commit_attempt`` — it
      has no DB-state dependency, so on ``SpecError`` we never acquire
      the lock at all. The lock-held ``transact_batch`` call still
      never runs when this raises, so no datoms or commit_id are
      burned; the unwind is "no lock held".
    """
    intent_log_wire: list[dict] = [
        {":op": intent.op, ":kwargs": dict(intent.kwargs)}
        for intent in tx.effect_intent_log
    ]
    # Conform the whole list once: ``seq_of`` propagates per-index
    # paths in ``ConformError.sub_errors``, so a non-EDN kwarg at the
    # third intent reports as ``[2][":kwargs"][...]`` rather than
    # collapsing to index 0 (which would happen if we conformed each
    # item against the seq_of-wrapping registered key separately).
    result = _spec_conform(":persistence.txn/intent-log", intent_log_wire)
    if not result.is_ok:
        _raise_spec_error(result)

    # v0.5.2 § F3 — commute_log emitted in BODY-ORDER (not sorted).
    # Body-order is the natural deterministic order: a body that calls
    # ``tx.commute(r, "inc-by", 1)`` then ``tx.commute(r, "inc-by", 2)``
    # produces the same wire-form across any two replays of the same
    # body (under fixed clock + same registry). Sorting would lose the
    # "compose in body-order" invariant called out in
    # :func:`_build_commute_facts` — the second commute on a ref sees
    # the first's reapply result, so emitting them out-of-body-order
    # would falsely advertise an order they were not applied in.
    # ``args`` is converted tuple → list because EDN sequences are
    # serialized as lists; the wire form must round-trip cleanly under
    # the registered ``:persistence.txn/commute-log`` spec.
    commute_log_wire: list = [
        {":ref": ref.eid, ":fn-id": fn_id, ":args": list(args)}
        for ref, fn_id, args in tx.commute_log
    ]

    return {
        ":persistence.txn/commit-id": commit_id,
        ":persistence.txn/started-at": tx.t_start.isoformat(),
        ":persistence.txn/committed-at": tx.db._clock().isoformat(),
        ":persistence.txn/retry-count": tx.attempt,
        ":persistence.txn/non-deterministic-retry": getattr(
            tx, "_deadline_set", False
        ),
        ":persistence.txn/read-set": sorted(r.eid for r in tx.read_set),
        ":persistence.txn/ensure-set": sorted(r.eid for r in tx.ensure_set),
        ":persistence.txn/intent-log": intent_log_wire,
        ":persistence.txn/commute-log": commute_log_wire,
    }


def _runtime_active() -> bool:
    """Return ``True`` iff an effect runtime is currently installed
    (via :func:`persistence.effect.with_runtime` or equivalent).

    Phase 2.0d W2 (M5): exposed as a small helper so ``_commit_attempt``
    can pre-gate the ``AuditStackMissing`` check BEFORE the
    ``transact_batch`` call (true atomicity: no commit on missing
    runtime). Both this and the post-commit replay path consult the
    same ``persistence.effect.runtime._active`` ContextVar; the pair
    cannot disagree.
    """
    from persistence.effect.runtime import _active as _effect_active

    return _effect_active.get() is not None


def _replay_effect_intents(tx: "Transaction", commit_id: str) -> None:
    """Replay ``tx.effect_intent_log`` through the active effect runtime.

    Phase 2.0d W1 (R2 MAJOR M2 fix): when ``tx.effect_intent_log`` is
    non-empty AND no runtime is active, raise
    :class:`persistence.txn.AuditStackMissing` rather than silently
    dropping the intents. The pre-W1 behaviour ("return early if no
    runtime") was a footgun: code paths that queued ``:plan/edit`` /
    ``:fork/*`` / ``:code/exec`` / ``:fold/chosen`` intents on the
    transaction would commit cleanly and then silently lose the audit
    datoms whenever the substrate's default audit-stack install was
    subverted. The replay-byte-identity invariant from design § 3.7
    cannot hold under that regime.

    Phase 2.0d W2 (M5 fix): the W1 implementation raised AFTER the
    ``transact_batch`` call, which violated dosync atomicity (facts
    committed but audit datoms lost). The check is now duplicated as
    a PRE-commit gate inside :func:`_commit_attempt`, so under the
    normal commit path this function never sees the violating state.
    The post-commit raise below is kept as defense-in-depth: it
    surfaces the same error if a caller invokes
    ``_replay_effect_intents`` directly outside the commit_attempt
    flow (no realistic path under the public surface; preserved so
    direct-call tests / future refactors are guarded).

    The empty-intent-log + no-runtime case stays a no-op — that is the
    common "raw fact-only dosync" path (no effects queued, no runtime
    needed). The combination "empty intent log + active runtime" is
    also a no-op (the for-loop just does not iterate).

    v0.5.1 N2: passes ``commit_id`` via the typed ``txn_commit`` kwarg
    instead of stuffing ``"_txn_commit"`` into the intent's kwargs dict.
    The audit handler pops the sentinel before hashing args, so the
    same intent replayed across two different commits now produces the
    same ``args_hash`` (closes the v0.5.0a1 corruption where args_hash
    was polluted by commit_id). ``dict(intent.kwargs)`` defensively
    copies so the audit handler's in-place ``args.pop`` never reaches
    back to ``intent.kwargs``.
    """
    from persistence.effect.runtime import _active as _effect_active
    from persistence.txn.errors import AuditStackMissing

    rt = _effect_active.get()
    if rt is None:
        if tx.effect_intent_log:
            raise AuditStackMissing(
                f"dosync committed with {len(tx.effect_intent_log)} "
                f"queued effect intent(s) but no active effect runtime "
                f"to replay them through. The intents would have been "
                f"silently dropped under the pre-W1 'no-runtime → no-op' "
                f"rule, breaking the audit-replay invariant from design "
                f"§ 3.7 + ADR-6. Either install an audit handler stack "
                f"(persistence.effect.canonical_audit_stack(entries) is "
                f"the substrate-default factory; activate via "
                f"persistence.effect.with_runtime(rt)), or use "
                f"persistence.sdk.Substrate.open(uri) which installs "
                f"the canonical stack by default. Pass "
                f"Substrate.open(uri, audit=False) only for sandbox "
                f"tests where Merkle-chain enforcement is undesirable; "
                f"in that regime, do not queue audit-emitting intents."
            )
        return
    for intent in tx.effect_intent_log:
        rt.perform(intent.op, dict(intent.kwargs), txn_commit=commit_id)


def _commit_attempt(tx: "Transaction") -> bool:
    """Run the spec-validate / facts-build / lock+conflict-check / transact
    sequence atomically.

    Returns ``True`` if committed, ``False`` if the conflict check rejected
    it (caller decides retry vs raise). On success, sets ``tx.commit_id``
    and replays effect intents through the active runtime (if any).

    Both ``with db.dosync()`` and ``@db.dosync`` route their commit gate
    through this single helper.
    """
    # Commit gate 0 (Phase 2.0d W2 / M5 fix): ``AuditStackMissing``
    # PRE-commit gate. If the body queued effect intents but no effect
    # runtime is active, fail BEFORE ``transact_batch`` writes anything.
    # The W1 implementation raised AFTER ``transact_batch`` (inside
    # ``_replay_effect_intents``), which violated dosync atomicity:
    # facts committed and the audit datoms were silently lost. Moving
    # the check here gives true all-or-nothing semantics — on
    # ``AuditStackMissing`` no facts (refs / commute reapply / staged
    # fold_into facts / commit datom) are written.
    #
    # The post-commit raise inside ``_replay_effect_intents`` is kept
    # as defense-in-depth (it is unreachable under the normal flow now
    # that this gate covers the same condition; if a caller invokes
    # ``_replay_effect_intents`` directly, the post-commit raise still
    # surfaces the violation).
    if tx.effect_intent_log and not _runtime_active():
        from persistence.txn.errors import AuditStackMissing

        raise AuditStackMissing(
            f"dosync attempted to commit with {len(tx.effect_intent_log)} "
            f"queued effect intent(s) but no active effect runtime "
            f"to replay them through. The intents would have been "
            f"silently dropped under the pre-W1 'no-runtime → no-op' "
            f"rule, breaking the audit-replay invariant from design "
            f"§ 3.7 + ADR-6. The W2 fix raises this error BEFORE the "
            f"transact_batch call so no facts are committed (true "
            f"dosync atomicity). Either install an audit handler "
            f"stack (persistence.effect.canonical_audit_stack(entries) "
            f"is the substrate-default factory; activate via "
            f"persistence.effect.with_runtime(rt)), or use "
            f"persistence.sdk.Substrate.open(uri) which installs "
            f"the canonical stack by default. Pass "
            f"Substrate.open(uri, audit=False) only for sandbox "
            f"tests where Merkle-chain enforcement is undesirable; "
            f"in that regime, do not queue audit-emitting intents."
        )

    # Commit gate 1: spec validation BEFORE allocating tx-id.
    _spec_validate_writes(tx.write_set)

    # Commit gates 2+3: conflict check AND transact must be atomic.
    # Without the lock, two threads can both pass the MVCC check before
    # either writes, then both commit — losing increments (B7 concurrency
    # bug). Holding store._lock across check+write collapses the window
    # to zero: a thread that passes the check is guaranteed to commit
    # before any other thread's check runs.
    commit_id = str(_uuid.uuid4())  # noqa: wall-clock
    write_facts = _build_write_facts(tx)
    commit_fact = _build_commit_fact(tx, commit_id)
    # v0.5.1 W1 fix-pass — R2 MINOR 1: build the provenance dict OUTSIDE
    # the store lock. ``_build_commit_provenance`` runs the
    # ``:persistence.txn/intent-log`` spec conformance, which has zero
    # DB-state dependency — it only walks the intent_log and conforms it
    # against a pre-registered spec. On ``SpecError`` we want to fail
    # without holding ``store._lock`` under contention; on success we
    # narrow the lock window to the conflict-check + transact-batch core.
    provenance = _build_commit_provenance(tx, commit_id)
    with tx.db.store._lock:
        # v0.5.2 § F3: conflict-detection union UNCHANGED — commute
        # refs are DELIBERATELY excluded. That is the entire point of
        # ``commute`` vs ``alter``: two parallel transactions calling
        # the same commute on the same ref both succeed without retry.
        touched = (
            {r.eid for r in tx.read_set}
            | {r.eid for r in tx.write_set}
            | {r.eid for r in tx.ensure_set}
        )
        if any_datoms_since(tx.db, tx.t_start, touched):
            return False
        # v0.5.2 § F3: commute reapply happens INSIDE the lock,
        # AFTER the conflict check passes. The reapply reads the
        # latest committed value (NOT the t_start snapshot) — by
        # holding ``store._lock``, we serialise the read+write so
        # two parallel commutes can't both miss each other's writes.
        # Refs in both write_set and commute_log are skipped here
        # (case 2: explicit write wins).
        commute_facts, _resolved = _build_commute_facts(tx)
        # Apply atomically as one db.transact() — write_set + commute
        # reapply + staged_facts (Phase 2.0d W1 M3) + commit datom.
        # ``facts`` always contains at least the commit datom, so no
        # guard. Phase 2.0d W1 (M3): ``tx.staged_facts`` is appended
        # by surfaces like ``s.txn.fold_into`` that need to commit
        # opaque fact dicts atomically with the outer dosync; they
        # ride this single transact_batch so an outer raise rolls
        # them back along with the ref writes.
        facts = (
            write_facts + commute_facts + list(tx.staged_facts) + [commit_fact]
        )
        tx.db.transact_batch(facts, provenance=provenance)
    tx.commit_id = commit_id
    _replay_effect_intents(tx, commit_id)
    return True


def _run(
    db: Any,
    body: Callable[["Transaction"], Any],
    max_retries: int = DEFAULT_MAX_RETRIES,
    deadline: Optional[float] = None,
) -> str:
    """The retry loop that backs both ``with db.dosync()`` and
    ``@db.dosync``. Returns the commit_id on success.
    """
    if is_in_dosync():
        raise NestedDosyncNotSupported(
            "nested dosync is not supported in v0.5.0a1; restructure "
            "the calling code to do all transactional work in one "
            "outermost block (deferred to v0.5.4)."
        )

    deadline_at = time.monotonic() + deadline if deadline else None  # noqa: wall-clock
    attempt = 0
    while True:
        if deadline_at is not None and time.monotonic() >= deadline_at:  # noqa: wall-clock
            raise TxnDeadlineExceeded(
                f"dosync deadline of {deadline}s elapsed after {attempt} attempts"
            )
        if attempt > max_retries:
            raise TxnRetryExhausted(
                f"dosync exceeded max_retries={max_retries}"
            )

        t_start = db._clock()
        tx = Transaction(db=db, t_start=t_start, attempt=attempt)
        if deadline is not None:
            # Read by _build_commit_provenance; CM path leaves it absent.
            tx._deadline_set = True  # type: ignore[attr-defined]
        token = set_dosync_guard()
        try:
            try:
                body(tx)
            finally:
                clear_dosync_guard(token)
        except (TxnDeadlineExceeded, TxnRetryExhausted, NestedDosyncNotSupported):
            raise
        except Exception:
            # Body raised — propagate immediately, no commit, no retry.
            raise

        if _commit_attempt(tx):
            return tx.commit_id  # type: ignore[return-value]
        attempt += 1


__all__ = [
    "Transaction",
    "DEFAULT_MAX_RETRIES",
    "WRITE_ATTR",
    "_raise_spec_error",
    "_spec_validate_writes",
    "_build_write_facts",
    "_build_commit_fact",
    "_build_commute_facts",
    "_build_commit_provenance",
    "_replay_effect_intents",
    "_runtime_active",
    "_commit_attempt",
    "_run",
]
