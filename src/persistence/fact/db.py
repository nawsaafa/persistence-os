"""DB + DBView — the functional query surface over the datom log.

See ``docs/agent1-fact-spec.md`` §2 for the intended API and §8 for the
reference 76-line prototype. This module is that prototype evolved to (a)
sit on top of a pluggable :class:`~persistence.fact.store.Store`, and (b)
enforce the invariants the paper §4.1 formalizes:

    asOf(D, t)      = {d ∈ D | τ_sys(d) ≤ t}
    validAsOf(D, t) = {d ∈ D | ω(d) = assert ∧ ν_from ≤ t < ν_to}
    history(D, e)   = {d ∈ D | entity(d) = e}, sorted by τ
    branch(D, t, Δ) = asOf(D, t) ∪ Δ

The DB object is itself a *value*: every mutating operation returns a new
DB wrapping the same (or a branched) store, so passing a DB around never
causes spooky action at a distance.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator, Literal, Optional, Sequence

from persistence.fact.datom import Datom
from persistence.fact.store import TX_PLACEHOLDER, InMemoryStore, Store

# Transaction ids are allocated by the Store (see Store.next_tx), not by a
# module-level counter. Two InMemoryStore instances each get their own id
# sequence starting at 1; a SQLiteStore reopened against an existing file
# resumes at ``max(tx) + 1`` instead of stomping on row 1 (ARIS R3 F10).


# Clock seam — paper §4.2 / ARIS R2 F5. Production code goes through the
# Effect runtime's ``:sys/now`` handler (once W-integration lands the
# leading-colon namespace). Until then, callers that need a deterministic
# tx_time may pass ``DB(store, clock=...)`` to inject a frozen clock.
ClockFn = Callable[[], datetime]


def _system_clock() -> datetime:
    """The single authorised ``datetime.now`` call in the fact module.

    This is the only place in ``persistence.fact`` that samples the wall
    clock; everywhere else takes a :data:`ClockFn` argument or calls
    ``self._clock()`` on a DB instance so replay can substitute a handler.
    """
    return datetime.now(timezone.utc)  # noqa: wall-clock -- authorised clock source


class RetroactiveCorrectionError(ValueError):
    """Raised when a ``transact`` asserts a ``valid_from`` strictly earlier
    than the prior open assert's ``valid_from`` without opting in via
    ``force_retroactive=True``.

    Motivating case (agent1-fact-spec §0): "the true value was lower from
    earlier than we thought". This is a legitimate operation but silently
    emitting a companion retract with ``valid_to < valid_from`` corrupts
    the bitemporal rectangle; callers must opt in explicitly.
    """


class FoldError(RuntimeError):
    """Raised by :meth:`DB.fold` when an item-level callable raises and
    ``on_error`` was set to ``"abort"`` or ``"checkpoint"``.

    The exception carries the partial state recovered from the last
    successful checkpoint plus the original cause as ``__cause__`` so
    callers can either swallow + inspect the partial state or re-raise
    with full traceback context.

    Attributes:
        acc:                  accumulator value as of the last
                              successful checkpoint, or the seed if no
                              checkpoint was reached. The same value
                              for both ``on_error="abort"`` and
                              ``on_error="checkpoint"`` — the
                              difference between the two is the
                              caller-side intent signalled by the
                              flag, not the recovered state. Under
                              ``checkpoint_every=0`` (per-item) the
                              checkpoint advances on every successful
                              ``fn``, so this is the accumulator after
                              the last successful item; under
                              ``checkpoint_every=N`` it is the
                              accumulator after the last *flushed*
                              batch (the in-progress buffer is
                              discarded).
        committed_count:      number of datoms committed up to the
                              last clean checkpoint. The failing
                              item's facts are not counted (they were
                              never flushed).
        last_committed_acc:   alias for :attr:`acc` — kept under a
                              longer name for explicit readability in
                              caller error handlers.
        item_index:           the 0-based index of the item that
                              triggered the failure (i.e.
                              ``items[item_index]`` is the item ``fn``
                              raised on).

    PG6 (Phase 1 stream #169) ships this as ``@experimental`` surface
    alongside :meth:`DB.fold`; the exception class is part of the
    documented experimental contract but its precise shape may evolve
    in v0.9.
    """

    def __init__(
        self,
        message: str,
        *,
        acc: Any = None,
        committed_count: int = 0,
        item_index: int,
    ) -> None:
        super().__init__(message)
        self.acc = acc
        self.committed_count = committed_count
        self.last_committed_acc = acc
        self.item_index = item_index


def _hash_fact(fact: dict) -> str:
    """Stable prompt-hash-style digest — first 16 hex chars of sha256 over
    the canonicalized fact. Matches the shape the paper §4.1 provenance
    record expects for ``prompt-hash``."""
    blob = json.dumps(fact, default=str, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class CausalDAG:
    """Result of DB.causal_history: seeds + immediate-parent map.

    seeds:    list of Datoms returned by history(e), the entry points.
    parents:  dict mapping a datom's substrate-internal canonical id
              (f"{d.e}|{d.a}|{d.tx}") to the list of parent hashes
              pulled from provenance.parent_provenance_hash (or its
              alias :prev-hash). Empty for seeds that have no parent.

    The parent hashes are opaque strings — typically AuditEntry ids
    from the effect handler's Merkle chain. Resolving them to actual
    AuditEntries (or further datoms) is the caller's responsibility:
    the substrate does NOT maintain a hash → entry index in v0.4.0a1.

    Mutability note: ``seeds`` and ``parents`` are exposed as their natural
    mutable types (``list``, ``dict``) for ergonomic consumption. The
    dataclass is ``frozen=True, slots=True`` for attribute-assignment
    protection but does NOT recursively freeze the containers — treat as
    read-only by convention. v0.5 may switch to ``tuple`` + ``Mapping`` if
    multi-level walking surfaces a need.
    """
    seeds: list[Datom]
    parents: dict[str, list[str]]


def _datom_canonical_id(d: Datom) -> str:
    """Stable identifier for a Datom in the causal DAG bookkeeping.

    Returns f"{e}|{a}|{tx}". This is a substrate-internal identity for
    walker bookkeeping ONLY — it is NOT the same as
    `audit.AuditEntry.id` (which is content-hashed) and is NOT the
    target of `provenance.parent_provenance_hash` (which references
    AuditEntry ids).
    """
    return f"{d.e}|{d.a}|{d.tx}"


@dataclass
class DB:
    """An append-only datom log, read through the bitemporal query API.

    A DB is a thin functional wrapper over a Store. Methods that "modify"
    the DB return a new DB bound to either the same store (transact) or a
    fresh branch store (branch). Callers can always hold a reference to an
    earlier DB value and re-query it.
    """

    store: Store

    # ---- Construction ----------------------------------------------------
    def __init__(
        self,
        store: Optional[Store] = None,
        *,
        clock: Optional[ClockFn] = None,
    ) -> None:
        # Default to in-memory so ``DB()`` is usable from a notebook.
        self.store = store if store is not None else InMemoryStore()
        # ARIS R2 F5: tx_time comes from an injectable clock so replay mode
        # can pin timestamps deterministically. Defaults to the system clock.
        self._clock: ClockFn = clock or _system_clock

    # ---- Writes ----------------------------------------------------------
    def transact(
        self,
        facts: list[dict],
        provenance: Optional[dict] = None,
        *,
        force_retroactive: bool = False,
    ) -> "DB":
        """Append a transaction. Cardinality-one auto-retraction built in.

        Each fact dict must have ``e``, ``a``, ``v``, ``valid_from`` and may
        have ``valid_to``, ``op`` (default ``"assert"``). The supplied
        ``provenance`` merges into each datom's provenance, with a per-fact
        ``prompt_hash`` added automatically.

        Returns a NEW DB bound to the same store, so callers can pipe
        ``db = db.transact(...)`` the way the spec prototype does.

        Retroactive corrections (ARIS Round 1 R1 F3): if a new assert's
        ``valid_from`` is strictly earlier than the prior open assert's
        ``valid_from``, the naive companion retract would have
        ``valid_to < valid_from`` — a negative interval. We REFUSE such a
        transact unless the caller passes ``force_retroactive=True``, in
        which case the companion retract's ``valid_to`` is clamped to
        ``new.valid_from`` (the retract closes the prior from the new
        effective date onward; agent1-fact-spec §0 motivates this).
        """
        if not facts:
            return self

        prov_base: dict = provenance or {}
        # tx is allocated atomically by ``store.allocate_and_append`` after
        # we've shaped all the datoms; see ARIS Round 3 P-concurrency and
        # the TX_PLACEHOLDER sentinel in fact.store. Until the atomic
        # append runs, every datom and every provenance reference sits at
        # the placeholder, which allocate_and_append rewrites in one pass.
        tx = TX_PLACEHOLDER
        now = self._clock()
        new_datoms: list[Datom] = []
        invalidations: list[int] = []  # old_tx values; new_tx patched post-alloc

        # Snapshot the log once so auto-retraction sees a stable view.
        current_log = list(self.store.all_datoms())

        for fact in facts:
            op = fact.get("op", "assert")
            vf = _coerce_dt(fact.get("valid_from", now))
            vt = _coerce_dt(fact.get("valid_to")) if fact.get("valid_to") else None

            if op == "assert":
                # Find the most recent open-interval, non-invalidated assert
                # for the same (e, a) and retract it.
                prior = _find_prior_assert(
                    current_log + new_datoms, fact["e"], fact["a"]
                )
                if prior is not None:
                    # ARIS R1 F3: guard against retroactive correction that
                    # would produce a negative interval on the companion
                    # retract. vf < prior.valid_from is the retroactive case.
                    if vf < prior.valid_from:
                        if not force_retroactive:
                            raise RetroactiveCorrectionError(
                                f"retroactive correction for ({fact['e']!r}, "
                                f"{fact['a']!r}): new valid_from={vf.isoformat()} "
                                f"is earlier than prior valid_from="
                                f"{prior.valid_from.isoformat()}. "
                                f"Pass force_retroactive=True to opt in; the "
                                f"companion retract's valid_to will be clamped "
                                f"to the new valid_from."
                            )
                        # Opt-in path: clamp valid_to so the retract spans
                        # [new.valid_from, prior.valid_from] (no negative
                        # interval). This invalidates the prior from the new
                        # effective date onward, which is what retroactive
                        # corrections like "the WACC was lower than we thought"
                        # semantically require.
                        retract_valid_to = prior.valid_from
                        retract_valid_from = vf
                    else:
                        # Normal case (vf >= prior.valid_from): retract the
                        # prior's open interval from its own valid_from to
                        # the new valid_from.
                        retract_valid_from = prior.valid_from
                        retract_valid_to = vf

                    companion = Datom(
                        e=prior.e,
                        a=prior.a,
                        v=prior.v,
                        tx=tx,
                        tx_time=now,
                        valid_from=retract_valid_from,
                        valid_to=retract_valid_to,
                        op="retract",
                        provenance={**prior.provenance, "superseded_by_tx": tx},
                        invalidated_by=None,
                    )
                    new_datoms.append(companion)
                    invalidations.append(prior.tx)

            prov = {
                **prov_base,
                "prompt_hash": _hash_fact(fact),
            }
            new_datoms.append(
                Datom(
                    e=fact["e"],
                    a=fact["a"],
                    v=fact["v"],
                    tx=tx,
                    tx_time=now,
                    valid_from=vf,
                    valid_to=vt,
                    op=op,
                    # Note: the `# type: ignore[arg-type]` directive is on the
                    # `provenance=prov` line below (pyright reads it from the
                    # last logical line of the statement). Rationale, parallel
                    # to the field-default ignore in datom.py: prov is a free-
                    # form dict by construction (caller-supplied prov_base +
                    # prompt_hash), structurally compatible with Provenance at
                    # runtime but not statically typed as such. D2's typed
                    # schema is a documentation/typecheck aid; transact() does
                    # not coerce via provenance_from_dict because that
                    # rearranges prompt_hash into extra and changes the
                    # persisted shape.
                    provenance=prov,  # type: ignore[arg-type]
                )
            )

        # Atomically allocate a tx id and append in a single transaction.
        # ``allocate_and_append`` returns the datoms with ``tx`` set to the
        # freshly-allocated id, and also rewrites any TX_PLACEHOLDER in
        # provenance (e.g. "superseded_by_tx") to the real id (ARIS Round 3
        # P-concurrency).
        stored = self.store.allocate_and_append(new_datoms)
        if stored:
            real_tx = stored[0].tx
            # Every datom in a single transact shares one tx — invariant
            # enforced by allocate_and_append.
            assert all(d.tx == real_tx for d in stored), (
                "allocate_and_append returned mixed tx ids"
            )
            for old_tx in invalidations:
                self.store.mark_invalidated(old_tx, real_tx)

        return DB(self.store, clock=self._clock)

    def transact_batch(
        self,
        facts: list[dict],
        provenance: Optional[dict] = None,
        *,
        force_retroactive: bool = False,
    ) -> "DB":
        """Like ``transact`` but folds all auto-retraction lookups into
        a single pass over the log.

        Equivalent to ``transact`` for correctness — the same companion
        retract datoms are emitted, the same RetroactiveCorrectionError
        is raised under the same conditions. The performance difference
        only matters for fact-counts in the tens or higher; for
        single-fact transactions, ``transact`` is fine.

        Used by ``persistence.txn`` to commit a dosync block's write_set
        as one atomic transaction without paying O(N²) auto-retraction
        cost. See design doc § 6, rev I.
        """
        if not facts:
            return self

        prov_base: dict = provenance or {}
        tx = TX_PLACEHOLDER
        now = self._clock()
        new_datoms: list[Datom] = []
        invalidations: list[int] = []

        # Single snapshot of the log for all auto-retraction lookups.
        current_log = list(self.store.all_datoms())

        # Build an index over the snapshot keyed by (e, a) so each fact's
        # prior-assert lookup is O(1) average instead of O(N).
        # Walk newest-to-oldest so the first hit per (e, a) is the most
        # recent open assert (matching _find_prior_assert semantics).
        prior_index: dict[tuple[str, str], Datom] = {}
        for d in reversed(current_log):
            if d.op != "assert":
                continue
            if d.invalidated_by is not None:
                continue
            if d.valid_to is not None:
                continue
            key = (d.e, d.a)
            if key not in prior_index:
                prior_index[key] = d

        # Index for new_datoms accumulated in THIS batch — order matters
        # because later facts in the batch may auto-retract earlier ones.
        in_batch_open: dict[tuple[str, str], Datom] = {}

        for fact in facts:
            op = fact.get("op", "assert")
            vf = _coerce_dt(fact.get("valid_from", now))
            vt = _coerce_dt(fact.get("valid_to")) if fact.get("valid_to") else None

            if op == "assert":
                key = (fact["e"], fact["a"])
                # Prefer in-batch open over snapshot.
                prior = in_batch_open.get(key) or prior_index.get(key)
                if prior is not None:
                    if vf < prior.valid_from:
                        if not force_retroactive:
                            raise RetroactiveCorrectionError(
                                f"retroactive correction for ({fact['e']!r}, "
                                f"{fact['a']!r}): new valid_from={vf.isoformat()} "
                                f"is earlier than prior valid_from="
                                f"{prior.valid_from.isoformat()}. "
                                f"Pass force_retroactive=True to opt in."
                            )
                        retract_valid_to = prior.valid_from
                        retract_valid_from = vf
                    else:
                        retract_valid_from = prior.valid_from
                        retract_valid_to = vf

                    companion = Datom(
                        e=prior.e,
                        a=prior.a,
                        v=prior.v,
                        tx=tx,
                        tx_time=now,
                        valid_from=retract_valid_from,
                        valid_to=retract_valid_to,
                        op="retract",
                        provenance={**prior.provenance, "superseded_by_tx": tx},
                        invalidated_by=None,
                    )
                    new_datoms.append(companion)
                    invalidations.append(prior.tx)
                    # Remove from in_batch_open since it's now closed.
                    in_batch_open.pop(key, None)

            prov = {
                **prov_base,
                "prompt_hash": _hash_fact(fact),
            }
            new_datoms.append(
                Datom(
                    e=fact["e"],
                    a=fact["a"],
                    v=fact["v"],
                    tx=tx,
                    tx_time=now,
                    valid_from=vf,
                    valid_to=vt,
                    op=op,
                    # Same rationale as transact() at db.py:250-260:
                    # prov is structurally Provenance-compatible at runtime
                    # but caller-supplied as a free-form dict.
                    provenance=prov,  # type: ignore[arg-type]
                )
            )
            if op == "assert" and vt is None:
                in_batch_open[(fact["e"], fact["a"])] = new_datoms[-1]

        stored = self.store.allocate_and_append(new_datoms)
        if stored:
            real_tx = stored[0].tx
            assert all(d.tx == real_tx for d in stored), (
                "allocate_and_append returned mixed tx ids"
            )
            for old_tx in invalidations:
                self.store.mark_invalidated(old_tx, real_tx)

        return DB(self.store, clock=self._clock)

    # ---- Reads -----------------------------------------------------------
    def log(self) -> Iterator[Datom]:
        """Yield every datom in insertion order. Debugging + replication."""
        return self.store.all_datoms()

    def as_of(self, t: datetime) -> "DBView":
        """Transaction-time slice: everything the DB learned on or before t."""
        t = _coerce_dt(t)
        return DBView([d for d in self.store.all_datoms() if d.tx_time <= t])

    def as_of_valid(self, vt: datetime) -> "DBView":
        """Valid-time slice: every assert whose interval contains ``vt``."""
        vt = _coerce_dt(vt)
        out: list[Datom] = []
        for d in self.store.all_datoms():
            if d.op != "assert":
                continue
            if d.valid_from > vt:
                continue
            if d.valid_to is not None and vt >= d.valid_to:
                continue
            out.append(d)
        return DBView(out)

    def history(self, e: str) -> list[Datom]:
        """Every datom touching entity ``e``, sorted by transaction id."""
        return sorted(
            (d for d in self.store.all_datoms() if d.e == e),
            key=lambda d: d.tx,
        )

    def causal_history(self, e: str, max_depth: int = 16) -> CausalDAG:
        """Return seeds + immediate parent hashes from provenance.

        For each datom in ``history(e)``, extract
        ``provenance.parent_provenance_hash`` (or its alias
        ``:prev-hash`` from the audit handler) and record it in the
        parents map keyed by the datom's substrate-internal canonical
        id (e|a|tx).

        The walk is **single-level** in v0.4.0a1: parent hashes
        reference AuditEntry ids from the effect handler's Merkle
        chain, which the substrate does not index. Multi-level walks
        require AuditEntry resolution and are out of substrate scope
        until v0.5 (or downstream callers like ai-box-vault's
        ``/vault/why`` endpoint).

        ``max_depth`` is accepted for forward-compatibility with
        future multi-level walking; in v0.4.0a1 any value >= 1 yields
        the same result. ``max_depth=0`` yields no parents at all.
        """
        seeds = self.history(e)
        parents: dict[str, list[str]] = {}
        if max_depth < 1:
            return CausalDAG(seeds=seeds, parents=parents)
        for d in seeds:
            cid = _datom_canonical_id(d)
            # Read both keys: the typed-Provenance schema (D2) uses
            # parent_provenance_hash; legacy raw provenance dicts emitted
            # before D2 coercion (e.g. by transact() and the audit handler
            # via D4 aliasing) carry the same value at top-level under
            # the colon-keyword form ":prev-hash". They are the same chain.
            parent_hash = d.provenance.get("parent_provenance_hash") \
                or d.provenance.get(":prev-hash")
            if parent_hash:
                parents.setdefault(cid, []).append(parent_hash)
        return CausalDAG(seeds=seeds, parents=parents)

    def since(self, t: datetime) -> "DBView":
        """Transaction-time delta — feeds incremental sync / replication."""
        t = _coerce_dt(t)
        return DBView(list(self.store.since(t)))

    def branch(self, t: datetime, assertions: list[dict]) -> "DB":
        """Fork a counterfactual DB at ``t`` with hypothetical assertions.

        The branch gets its own fresh :class:`InMemoryStore` seeded from the
        ``as_of(t)`` snapshot. Writes on the branch can never leak into the
        parent store — the paper §4.1 ``branch(D, t, Δ)`` definition.
        """
        seed = list(self.as_of(t).datoms)
        # Re-home the seed datoms into a new in-memory store. We copy the
        # dict fields because Datom itself is frozen and the provenance
        # dicts are shared references.
        branched_store = InMemoryStore()
        branched_store.append(
            [
                Datom(
                    e=d.e,
                    a=d.a,
                    v=d.v,
                    tx=d.tx,
                    tx_time=d.tx_time,
                    valid_from=d.valid_from,
                    valid_to=d.valid_to,
                    op=d.op,
                    provenance=copy.deepcopy(d.provenance),
                    invalidated_by=d.invalidated_by,
                )
                for d in seed
            ]
        )

        db = DB(branched_store, clock=self._clock)
        if assertions:
            db = db.transact(
                assertions,
                provenance={
                    "source": "branch",
                    "base_tx_time": _coerce_dt(t).isoformat(),
                },
            )
        return db

    # ---- Fold: speculation / rollback / checkpointing primitive --------
    def fold(
        self,
        seed: Any,
        items: Iterable[Any],
        fn: "Callable[[Any, Any, DB], tuple[Any, list[dict]]]",
        *,
        on_error: Literal["abort", "skip", "checkpoint"] = "abort",
        checkpoint_every: int = 0,
        provenance: Optional[dict] = None,
    ) -> tuple[Any, int]:
        """Fold ``items`` through ``fn``, accumulating facts transactionally.

        Closes Phase 1 stream #145 (R3-M1 — the fold() executor primitive
        from the v1.0 roadmap). The shape mirrors a classic ``reduce``
        / ``foldl`` but with three substrate-aware twists:

        1. ``fn`` is invoked as ``fn(acc, item, db)`` and must return a
           ``(new_acc, facts)`` tuple where ``facts`` is a list of fact
           dicts in the same shape :meth:`transact` accepts (``e``, ``a``,
           ``v``, ``valid_from`` etc.). An empty list is acceptable and
           commits no datoms — the accumulator still advances.
        2. Facts are committed in batches via :meth:`transact_batch`.
           The batch boundary depends on ``checkpoint_every``:

           - ``checkpoint_every == 0`` (default): every item's facts are
             committed in their own per-item :meth:`transact_batch` call
             AND the accumulator after that item is the new "last
             checkpoint" (so callers get fine-grained recovery without
             paying for buffering).
           - ``checkpoint_every > 0``: facts are buffered across N items
             and flushed once N items have been processed (cheaper for
             high-fanout folds). Recovery granularity is N items.
        3. ``on_error`` controls behaviour when ``fn`` raises:

           - ``"abort"`` — re-raise immediately wrapped in
             :class:`FoldError`; the in-progress (buffered, uncommitted)
             facts are discarded; previously-checkpointed facts remain.
             ``acc`` on the FoldError is the last *checkpointed*
             accumulator (so callers can resume from a known-good
             state).
           - ``"skip"`` — log nothing, swallow the exception, do NOT
             advance the accumulator, do NOT commit any in-flight facts
             from that item, and continue with the next item. Recovers
             from transient per-item failures without aborting the
             whole fold.
           - ``"checkpoint"`` — commits up to the last successful
             checkpoint and re-raises with the partial state attached
             on :class:`FoldError`. The semantic difference from
             ``"abort"`` is subtle: ``"abort"`` and ``"checkpoint"``
             both raise on first failure; ``"checkpoint"`` makes the
             intent explicit and emits the partial-state contract as
             a deliberate API gesture rather than an accidental
             consequence of per-item batching.

        Args:
            seed: initial accumulator value passed as ``fn``'s first
                argument on iteration 0.
            items: iterable of items to fold over. Materialised once at
                the top of the call (so a generator is fine).
            fn: ``(acc, item, db) -> (new_acc, facts)`` reducer.
            on_error: failure-handling discipline; see above.
            checkpoint_every: batch size for the buffered flush mode;
                default ``0`` means "checkpoint after every item".
                Negative values raise :class:`ValueError`.
            provenance: optional dict merged into the per-fact
                provenance (alongside the auto-stamped
                ``prompt_hash``). The ``"source": "fold"`` tag is
                added if the caller did not set ``"source"`` so audit
                consumers can identify fold-emitted datoms.

        Returns:
            ``(final_acc, total_datoms_committed)``. ``final_acc`` is
            the accumulator after the last successful iteration
            (which under ``"skip"`` may be the same as one before a
            skipped failure). ``total_datoms_committed`` counts the
            actual datoms inserted (companion retracts produced by
            cardinality-one auto-retraction count too — they are
            emitted as part of the same transaction batch). For
            ``on_error="abort"`` / ``"checkpoint"`` callers see the
            counts up to the last checkpoint via
            :class:`FoldError.committed_count` instead.

        Raises:
            FoldError: when ``fn`` raises and ``on_error`` is
                ``"abort"`` or ``"checkpoint"``. The original
                exception is attached as ``__cause__``.
            ValueError: on invalid ``on_error`` / ``checkpoint_every``.

        Stability:
            ``@experimental`` per Adapter SDK ADR-5 + R3-M1. The
            shape may evolve in v0.9 once the coding-agent MVP
            (Phase 2) tells us which sub-shape is load-bearing.
            Adapter authors who depend on this method should NOT
            pin against ``@stable("v0.8")`` semantics; the curated
            ``Substrate.txn.fold`` re-export (PG6) is also
            ``@experimental``.

        Example::

            def step(acc, item, db):
                # accumulator is "running sum"; emit one fact per item
                fact = {"e": f"item-{item.id}", "a": "value",
                        "v": item.value,
                        "valid_from": now}
                return acc + item.value, [fact]

            total, n = db.fold(seed=0, items=stream, fn=step,
                               on_error="checkpoint",
                               checkpoint_every=10)

        Notes:
            The current implementation routes through
            :meth:`transact_batch` per checkpoint flush. PG3+ may
            promote this to a single ``transact_serializable`` call
            on backends where it is available so the WHOLE fold
            becomes one cross-process atomic write — that's a
            backwards-compatible change because ``fold`` already
            commits transactionally per checkpoint, and a future
            single-call promotion only tightens the atomicity
            guarantee.
        """
        if on_error not in ("abort", "skip", "checkpoint"):
            raise ValueError(
                f"DB.fold: on_error must be one of "
                f"'abort'/'skip'/'checkpoint', got {on_error!r}"
            )
        if not isinstance(checkpoint_every, int) or checkpoint_every < 0:
            raise ValueError(
                f"DB.fold: checkpoint_every must be a non-negative int, "
                f"got {checkpoint_every!r}"
            )

        # Default provenance carries a ``"source": "fold"`` tag so
        # audit consumers can identify fold-emitted datoms — but only
        # when the caller hasn't already set their own ``source``.
        prov_for_batch: Optional[dict]
        if provenance is None:
            prov_for_batch = {"source": "fold"}
        else:
            prov_for_batch = dict(provenance)
            prov_for_batch.setdefault("source", "fold")

        materialised_items = list(items)
        # ``acc`` is the *live* accumulator (advances on every successful
        # ``fn``); ``checkpoint_acc`` is the last accumulator we committed
        # facts for. They diverge when checkpoint_every > 1 and we are
        # mid-buffer.
        acc: Any = seed
        checkpoint_acc: Any = seed
        committed_total = 0
        # Pending buffer for checkpoint_every > 0 mode.
        pending_facts: list[dict] = []
        pending_count_since_flush = 0

        def _flush() -> None:
            """Commit pending facts; advance the checkpoint accumulator."""
            nonlocal pending_facts, pending_count_since_flush, checkpoint_acc
            nonlocal committed_total
            if not pending_facts:
                pending_count_since_flush = 0
                return
            # ``transact_batch`` handles the auto-retraction lookups in
            # one pass over the log — important for high-fanout folds
            # where per-item ``transact`` would re-walk the snapshot N
            # times. Companion retracts produced by cardinality-one
            # auto-retraction are emitted as part of the same
            # transaction; we count them in ``committed_total`` by
            # diffing the store's tx-id allocation. The simpler
            # contract — count of input facts — undercounts for
            # cardinality-one workloads. We count emitted datoms
            # directly so the return value matches operator
            # expectations.
            facts_to_flush = pending_facts
            pending_facts = []
            pending_count_since_flush = 0
            pre_log_len = sum(1 for _ in self.store.all_datoms())
            # Update self in-place (DB is functional, so re-bind the
            # store via the result). The new DB shares the same store,
            # so ``self.store`` reflects the appended datoms either
            # way — but we re-bind ``self`` semantically by rebinding
            # the closure's view (we read self.store after).
            self.transact_batch(facts_to_flush, prov_for_batch)
            post_log_len = sum(1 for _ in self.store.all_datoms())
            committed_total += max(0, post_log_len - pre_log_len)
            checkpoint_acc = acc

        def _raise_fold_error(
            msg: str,
            cause: BaseException,
            item_idx: int,
        ) -> None:
            """Construct + raise a :class:`FoldError` with provenance."""
            err = FoldError(
                msg,
                acc=checkpoint_acc,
                committed_count=committed_total,
                item_index=item_idx,
            )
            raise err from cause

        for idx, item in enumerate(materialised_items):
            try:
                new_acc, facts = fn(acc, item, self)
            except BaseException as exc:  # noqa: BLE001 — caller policy decides
                if on_error == "skip":
                    # Discard any pending facts from THIS item only —
                    # under skip we still keep the previous
                    # checkpoint_acc and continue; the buffered facts
                    # from earlier items are kept for the next flush.
                    continue
                # abort or checkpoint: flush nothing further; raise
                # with whatever has been committed so far.
                _raise_fold_error(
                    f"DB.fold: fn raised on item index {idx}: "
                    f"{type(exc).__name__}: {exc}",
                    cause=exc,
                    item_idx=idx,
                )

            # Validate fn's return shape early so a buggy ``fn`` does
            # not silently drop facts.
            if not isinstance(facts, list):
                # Wrap into a clean fold-failure with the index, so
                # the caller sees the same FoldError shape regardless
                # of WHY fn misbehaved.
                _raise_fold_error(
                    f"DB.fold: fn must return (acc, list[dict]); item "
                    f"index {idx} returned {type(facts).__name__} for "
                    f"the second tuple element",
                    cause=TypeError(
                        f"expected list[dict], got {type(facts).__name__}"
                    ),
                    item_idx=idx,
                )

            acc = new_acc
            if facts:
                pending_facts.extend(facts)
            pending_count_since_flush += 1

            # Flush condition: per-item (checkpoint_every == 0) or
            # buffered (after every N items).
            if checkpoint_every == 0:
                _flush()
            elif pending_count_since_flush >= checkpoint_every:
                _flush()

        # Final flush for buffered mode (or any tail that never hit
        # the threshold).
        if pending_facts:
            _flush()

        return acc, committed_total

    # ---- Fork: speculate / score / pick / rollback primitive ---------
    def fork(
        self,
        items: "Sequence[Any]",
        fn: "Callable[[Any, Any], Any]",
        choose: "Callable[[list[Any]], int]",
        *,
        seed: Any = None,
        tx: Any = None,
        on_error: Literal["stop", "continue"] = "stop",
        provenance: Optional[dict] = None,
    ) -> Any:
        """Speculate over N candidate branches, score them, pick a winner.

        Phase 2.0c-extended (#145ext, folds in carryover #201). The
        sibling primitive to :meth:`fold` — where ``fold`` is a
        transactional foldl/reduce that commits every item's facts as
        it iterates, ``fork`` runs ``fn`` against N **isolated** child
        branches and queues the canonical 4-datom audit shape
        (``:fork/probe`` + ``:fork/branch`` x N + ``:fork/score`` x N
        + ``:fork/chosen``) under the enclosing dosync. Non-chosen
        branches' tentative state is discarded (rollback is trivial —
        ``fn`` operates on opaque Python state, not on the substrate,
        so nothing was ever written).

        See :mod:`persistence.fact._fork` for the contract, error
        types (:class:`ForkOutsideDosync`, :class:`ForkChooseError`),
        and the result types (:class:`ForkBranchResult`,
        :class:`ForkResult`).

        Args:
            items: branch candidates; one item produces one branch.
                Must be non-empty.
            fn: ``(branch_state, item) -> branch_state`` reducer.
                Called once per branch with ``seed`` as the initial
                state.
            choose: ``(branches) -> int`` picks the winning index.
            seed: initial branch state. Defaults to ``None``.
            tx: the active :class:`persistence.txn.Transaction`
                (from the enclosing dosync body). Required keyword-
                only.
            on_error: ``"stop"`` (default) or ``"continue"``.
            provenance: forwarded for adapter compatibility; not
                used at the fork-primitive layer (no facts committed
                here).

        Returns:
            :class:`persistence.fact._fork.ForkResult`.

        Raises:
            ForkOutsideDosync: not in an active dosync body or
                ``tx`` is None.
            ValueError: ``items`` is empty or ``on_error`` invalid.
            ForkChooseError: ``choose`` violated its contract.

        Stability:
            ``@experimental`` per ADR-7 + Phase 2.0c-extended. The
            curated SDK surface is :func:`persistence.sdk.Substrate.txn.fork`;
            adapter authors who need explicit speculate-rollback-pick
            semantics on the raw DB use this method.
        """
        from persistence.fact._fork import fork_impl

        return fork_impl(
            self,
            items,
            fn,
            choose,
            seed=seed,
            tx=tx,
            on_error=on_error,
            provenance=provenance,
        )


# ---------------------------------------------------------------------------
# DBView — immutable snapshot with entity projection.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DBView:
    """A filtered slice of the log. Not a separate store — just a list."""

    datoms: list[Datom]

    def entity(self, e: str) -> dict:
        """Materialize entity ``e`` from this slice.

        The projection rule (paper §5.1) is:

          - Only ``op == "assert"`` datoms are candidates.
          - An assert is *closed* iff there exists a ``retract`` datom in the
            same view with matching ``(e, a, v, valid_from)``. Closed asserts
            are excluded — this is how both explicit retracts and auto-
            retractions on cardinality-one overwrites take effect.
          - Among the remaining (open) asserts for the same (e, a), pick the
            one with the greatest ``valid_from``; ties broken by ``tx``.

        Note: ``invalidated_by`` is NOT consulted here. It is a tx-time
        optimization hint used by the covering indexes, not a semantic filter
        — respecting it would break ``as_of_valid`` for ranges in which the
        superseding assert is outside the view.
        """
        retracts: set[tuple[str, Any, datetime]] = set()
        candidates: list[Datom] = []
        for d in self.datoms:
            if d.e != e:
                continue
            if d.op == "retract":
                retracts.add((d.a, _freeze(d.v), d.valid_from))
            elif d.op == "assert":
                candidates.append(d)

        latest: dict[str, Datom] = {}
        for d in candidates:
            key = (d.a, _freeze(d.v), d.valid_from)
            if key in retracts:
                continue
            cur = latest.get(d.a)
            if cur is None or (d.valid_from, d.tx) > (cur.valid_from, cur.tx):
                latest[d.a] = d
        return {a: d.v for a, d in latest.items()}

    def __iter__(self) -> Iterator[Datom]:
        return iter(self.datoms)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _freeze(v: Any) -> Any:
    """Best-effort hashable representation of an EDN value for set membership.

    Primitives and tuples/frozensets pass through. Dicts and lists are
    serialized to a canonical JSON string — stable under key ordering —
    which is good enough for the equality-of-value check in ``entity()``.
    """
    try:
        hash(v)
        return v
    except TypeError:
        return json.dumps(v, default=str, sort_keys=True)


def _coerce_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"naive datetime not allowed: {value!r}")
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"cannot coerce {value!r} to datetime")


def _find_prior_assert(
    datoms: Iterable[Datom], e: str, a: str
) -> Optional[Datom]:
    """Return the most recent open-interval, non-invalidated, un-retracted
    assert for (e, a), scanning the list reversed. O(n) — fine for the
    reference implementation; production walks the EAVT index."""
    for d in reversed(list(datoms)):
        if d.e != e or d.a != a:
            continue
        if d.op != "assert":
            continue
        if d.invalidated_by is not None:
            continue
        if d.valid_to is not None:
            continue
        return d
    return None


__all__ = [
    "CausalDAG",
    "DB",
    "DBView",
    "FoldError",
    "RetroactiveCorrectionError",
]


# ---------------------------------------------------------------------------
# Txn module attaches db.ref / db.new_ref / db.dosync at import time.
# This is a one-way coupling: txn imports DB; DB does NOT import txn at
# the top of this file. The attach call lives at the bottom so DB is
# fully constructed when it runs.
# ---------------------------------------------------------------------------
from persistence.txn._db_extension import _attach_txn_methods  # noqa: E402

_attach_txn_methods(DB)
