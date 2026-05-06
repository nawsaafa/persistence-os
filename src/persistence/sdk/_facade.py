"""``Substrate`` — curated namespace + lifecycle facade (SDK2 body).

Per the design doc ``docs/plans/2026-04-29-adapter-sdk-contract-design.md``
ADR-1, :class:`Substrate` is a thin curated namespace over the seven
Persistence OS modules — NOT a god-object wrapper.

The body lands in SDK2 (this slice). SDK1 shipped an empty placeholder so
that ``from persistence.sdk import Substrate`` was importable from day
one; SDK2 fills in:

- ``Substrate.open(uri)`` classmethod that drives URI dispatch via
  :func:`persistence.sdk.uri.open_store`,
- idempotent ``close()``,
- ``__enter__`` / ``__exit__`` context-manager support,
- six **curated** subsurface namespaces (``fact`` / ``effect`` / ``txn``
  / ``repl`` / ``audit`` / ``replay``) that thin-pass-through to the
  underlying Module surfaces — the curated shape replaces the SDK1-design
  "raw Module reach-through" interpretation per ADR-1's "curated
  namespace, not raw module" line,
- one **escape-hatch** namespace ``escape`` that reaches into the raw
  Module instances (`s.escape.fact` etc.) and emits a
  ``:sdk/escape-hatch-access`` audit entry on first access per-session,
- promotion of the class stability marker from
  ``@experimental`` (SDK1 placeholder) to ``@stable("v0.8")``.

Adapter authors are documented to bind to the curated namespaces and to
treat any reach-through into ``s.escape.*`` as out-of-contract per the
ADR-1 escape-hatch boundary.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal, Optional

from persistence.claim import (
    CLAIM_KINDS,
    CallerIdentity,
    is_claim_kind,
    validate_attrs,
)
from persistence.sdk._audit import build_escape_hatch_payload
from persistence.sdk._stability import experimental, stable
from persistence.sdk.uri import open_store

if TYPE_CHECKING:  # pragma: no cover
    from persistence.effect.runtime import Handler


# ---------------------------------------------------------------------------
# Curated subsurface namespaces
# ---------------------------------------------------------------------------
# Each namespace is a small bound-method facade — adapter authors call
# ``s.fact.transact(...)`` etc., and each call thin-pass-throughs to the
# real impl on the underlying ``DB`` / ``Runtime`` / etc. Per ADR-1 these
# namespaces are the contract surface; the raw Module instances live
# behind ``s.escape.<module>`` so that reach-through is observable and
# audit-emitting.
#
# The namespaces are intentionally THIN — they expose the load-bearing
# methods listed in the design doc § 4 (the G1 gate exercises the
# canonical ones) without becoming god-objects. v0.9 may add curated
# methods that proved load-bearing during Phase-2 dogfooding; v0.8 keeps
# the surface deliberately small.


class _FactNamespace:
    """Curated ``s.fact.*`` surface.

    Thin pass-through to :class:`persistence.fact.DB`. The substrate's
    underlying ``DB`` instance is reachable via ``s.escape.fact`` for
    callers that need methods not yet folded into this curated namespace
    (per ADR-1 W2 escape-hatch telemetry).
    """

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate

    def transact(self, datoms, **kwargs):
        """Append datoms to the bitemporal log; thin pass-through to
        :meth:`persistence.fact.DB.transact`.
        """
        return self._substrate._db.transact(datoms, **kwargs)

    def transact_batch(self, batches, **kwargs):
        """Multi-batch append; thin pass-through to
        :meth:`persistence.fact.DB.transact_batch`.
        """
        return self._substrate._db.transact_batch(batches, **kwargs)

    def as_of(self, t):
        """Snapshot view at transaction time ``t``; thin pass-through to
        :meth:`persistence.fact.DB.as_of`.
        """
        return self._substrate._db.as_of(t)

    def as_of_valid(self, t):
        """Snapshot view at valid time ``t``; thin pass-through to
        :meth:`persistence.fact.DB.as_of_valid`.
        """
        return self._substrate._db.as_of_valid(t)

    def history(self, e, a):
        """Time-ordered history for entity-attribute pair; thin
        pass-through to :meth:`persistence.fact.DB.history`.
        """
        return self._substrate._db.history(e, a)

    def since(self, t):
        """Datoms transacted since ``t``; thin pass-through to
        :meth:`persistence.fact.DB.since`.
        """
        return self._substrate._db.since(t)


class _EffectNamespace:
    """Curated ``s.effect.*`` surface.

    Thin pass-through to :class:`persistence.effect.Runtime`, plus the
    curated :meth:`install_handler` for stack composition (Phase 2.1b).
    Callers needing the raw runtime for advanced/test-only use can
    reach via ``s.escape.effect``.
    """

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate

    def perform(self, op, args=None, **kwargs):
        """Dispatch an effect through the runtime stack; thin
        pass-through to :meth:`persistence.effect.Runtime.perform`.
        """
        if args is None:
            args = {}
        return self._substrate._runtime.perform(op, args, **kwargs)

    def is_well_formed(self, catalog) -> bool:
        """Check the runtime stack covers every op in ``catalog``;
        thin pass-through to
        :meth:`persistence.effect.Runtime.is_well_formed`.
        """
        return self._substrate._runtime.is_well_formed(catalog)

    def install_handler(self, handler: "Handler", *, position: Literal["bottom", "top"] = "bottom") -> None:
        """Install a handler into the substrate's runtime stack.

        ``position="bottom"`` inserts at ``handlers[0]`` (innermost — the
        raw-terminator slot per :class:`Runtime` docstring convention).
        ``position="top"`` appends to ``handlers[-1]`` (outermost — the
        middleware slot). Idempotent: re-installing a handler with the
        same ``name`` replaces the existing one in place.

        Phase 2.1b: ``coder/__main__.py`` uses ``position="bottom"`` to
        install the chosen ``:llm/call`` handler under the canonical
        audit middleware so audit wraps the LLM call. Library callers
        (Mode 3, ``make_callable_llm_handler``) use the same method.
        """
        if position not in ("bottom", "top"):
            raise ValueError(
                f"position must be 'bottom' or 'top', got {position!r}"
            )
        rt = self._substrate._runtime
        rt.handlers = [h for h in rt.handlers if h.name != handler.name]
        if position == "bottom":
            rt.handlers.insert(0, handler)
        else:  # "top"
            rt.handlers.append(handler)


class _TxnNamespace:
    """Curated ``s.txn.*`` surface.

    Thin pass-through to the dosync-attached ``DB`` from
    :mod:`persistence.txn`. The :func:`dosync` entry-point in this
    namespace mirrors the ``db.dosync`` attached method signature so
    adapter authors can write ``with s.txn.dosync() as tx: ...``.
    """

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate

    def dosync(self, *args, **kwargs):
        """Atomic dosync block; thin pass-through to
        :meth:`persistence.fact.DB.dosync` (attached by
        :mod:`persistence.txn._db_extension`).

        Both context-manager and decorator forms are forwarded:

        - ``with s.txn.dosync() as tx: ...`` — context manager
        - ``@s.txn.dosync`` / ``@s.txn.dosync(deadline=2.0)`` — decorator
        """
        return self._substrate._db.dosync(*args, **kwargs)

    def new_ref(self, value, **kwargs):
        """Allocate a fresh :class:`persistence.txn.Ref`; thin
        pass-through to :meth:`persistence.fact.DB.new_ref` (attached
        by :mod:`persistence.txn._db_extension`).
        """
        return self._substrate._db.new_ref(value, **kwargs)

    def ref(self, ref_id, **kwargs):
        """Resolve an existing :class:`persistence.txn.Ref` by id; thin
        pass-through to :meth:`persistence.fact.DB.ref`.
        """
        return self._substrate._db.ref(ref_id, **kwargs)

    @experimental(
        reason=(
            "PG6 R3-M1: speculation / rollback / checkpointing primitive. "
            "Surface may evolve in v0.9 once Phase-2 dogfooding identifies "
            "the load-bearing sub-shape; adapter authors who depend on it "
            "should not pin against @stable('v0.8') semantics."
        )
    )
    def fold(self, seed, items, fn, **kwargs):
        """Curated re-export of :meth:`persistence.fact.DB.fold`.

        Folds ``items`` through ``fn`` against the substrate's underlying
        :class:`DB`, accumulating fact-emission transactionally per
        checkpoint. See :meth:`persistence.fact.DB.fold` for the full
        signature, error-handling discipline (``on_error``), batch
        semantics (``checkpoint_every``), and stability promise.

        ``@experimental`` per Adapter SDK ADR-5 — this is a new surface
        that the v0.8 contract intentionally does NOT cover; the v0.9
        cycle decides whether to fold the shape into a curated stable
        namespace based on Phase-2 dogfooding signal.
        """
        return self._substrate._db.fold(seed, items, fn, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-extended #145ext: speculate-rollback-pick "
            "convenience rewired on top of DB.fork. Substrate-true "
            "rollback (only chosen branch's facts persist) + canonical "
            "4-datom audit shape (:fork/*). Phase 2.0d W1 (M3) staged "
            "the chosen facts on tx.staged_facts so they ride the outer "
            "dosync atomic commit. Surface may evolve in v0.9 once "
            "Phase-2 dogfooding identifies whether the 3-tuple `fn` "
            "shape (acc, facts, score) and the FoldBranchScore record "
            "are the load-bearing form. Adapter authors who depend on "
            "it should not pin against @stable('v0.8') semantics."
        )
    )
    def fold_into(self, seed, items, fn, choose, **kwargs):
        """Run a speculate-rollback-pick over N candidate branches in
        a single dosync, committing only the chosen branch's facts.

        Phase 2.0c-extended (#145ext) rewired ``s.txn.fold_into`` on
        top of :meth:`persistence.fact.DB.fork` for substrate-true
        rollback semantics. Per-branch isolation is structural
        (``fn`` operates on opaque Python state, not on the
        substrate); only the chosen branch's facts reach
        ``db.history()`` post-commit. Non-chosen branches' tentative
        datoms NEVER appear in the substrate — they are discarded
        Python objects.

        **Audit shape (Phase 2.0c-extended).** Each call emits the
        canonical 4-datom emission via ``tx.effect()`` riding the
        existing Merkle chain at ``persistence.effect.handlers.audit``
        (same chain as ``:plan/edit`` / ``:code/exec`` /
        ``:fork/*``):

        - ``:fork/probe``  — one
          ``{seed_hash, items_hash, fn_hash, choose_hash, branch_count}``
        - ``:fork/branch`` × N —
          ``{branch_index, branch_id, item_hash, branch_state_hash}``
        - ``:fork/score``  × N —
          ``{branch_index, score_value, score_hash}``
        - ``:fork/chosen`` — one
          ``{chosen_index, chosen_branch_id, chosen_state_hash}``

        The legacy ``:fold/chosen`` audit op (Path-A foldl-with-marker
        shape) is NO LONGER emitted by ``fold_into`` post-2.0c-ext —
        callers who depend on the chosen-marker shape should bind to
        the bare ``DB.fold`` instead.

        **Atomicity (Phase 2.0d W1 / M3).** The chosen branch's facts
        are staged on ``tx.staged_facts`` via ``tx.add_facts`` and
        ride the outer ``dosync``'s atomic ``transact_batch`` call,
        so an outer body raise after ``fold_into`` returns rolls them
        back along with the rest of the transaction. Pre-W1 the
        chosen facts were committed mid-dosync via a direct
        ``db.transact_batch`` call, which broke the rollback
        contract.

        See :func:`persistence.sdk._fold_into.fold_into` for the full
        signature, error-handling discipline (``on_error``,
        ``checkpoint_every``, ``provenance``), the dosync gate, and
        the ``FoldIntoResult`` shape. The 3-tuple ``fn`` shape
        ``(new_acc, facts, score)`` is the load-bearing surface; the
        chosen branch's ``facts`` list is what gets staged.

        Usage::

            with s.txn.dosync() as tx:
                result = s.txn.fold_into(
                    seed=initial,
                    items=branch_candidates,
                    fn=score_and_emit,
                    choose=argmax,
                    tx=tx,                  # required keyword-only
                )

        ``@experimental`` per Adapter SDK ADR-5 + Phase-2 ADR-7. The
        ``s.txn.fold_into`` shape may evolve in v0.9 once Phase-2
        dogfooding tells us whether the 3-tuple ``fn`` and the
        ``FoldBranchScore`` record are the load-bearing surface.
        Adapter authors should NOT pin against ``@stable("v0.8")``
        semantics; they get promoted to ``@stable("v0.9")`` after
        Phase 2 dogfood survives without API change.
        """
        from persistence.sdk._fold_into import fold_into as _fold_into

        return _fold_into(
            self._substrate._db, seed, items, fn, choose, **kwargs
        )

    @experimental(
        reason=(
            "Phase 2.0c-extended #145ext: substrate-true speculate / "
            "score / pick / rollback primitive. Sibling of s.txn.fold "
            "and s.txn.fold_into; lower-level than fold_into (operates "
            "on opaque Python state, NOT on substrate facts). Surface "
            "may evolve in v0.9 once Phase-2 dogfooding identifies "
            "whether the bare-fork API or the fold_into-on-top-of-fork "
            "wrapper is the load-bearing form."
        )
    )
    def fork(self, items, fn, choose, **kwargs):
        """Speculate over N candidate branches and pick a winner.

        Curated re-export of :meth:`persistence.fact.DB.fork`. Where
        :meth:`fold` is a transactional foldl/reduce that commits every
        item's facts as it iterates, ``fork`` runs ``fn`` against N
        **isolated** child branches and queues the canonical 4-datom
        audit shape (``:fork/probe`` + ``:fork/branch`` × N +
        ``:fork/score`` × N + ``:fork/chosen``) under the enclosing
        dosync. Non-chosen branches' tentative state is discarded —
        rollback is structural, since ``fn`` operates on opaque Python
        state, not on the substrate.

        The bare ``s.txn.fork`` API is for callers who want explicit
        speculate-rollback-pick semantics over Python state without
        the fact-level convenience layer. Adapter authors who want to
        emit substrate facts per branch should layer their own
        commit-on-winner step on top — ``s.txn.fold_into`` is the
        canonical example of that pattern.

        See :meth:`persistence.fact.DB.fork` for the full signature,
        error-handling discipline (``on_error``), the dosync gate
        (``ForkOutsideDosync``), and the ``ForkResult`` /
        ``ForkBranchResult`` return shapes.

        Usage::

            with s.txn.dosync() as tx:
                result = s.txn.fork(
                    items=branch_candidates,
                    fn=lambda state, item: build_state(state, item),
                    choose=lambda branches: argmax(branches),
                    seed=initial_state,
                    tx=tx,
                )
                # result.chosen_state is the winner's terminal state.

        ``@experimental`` per Adapter SDK ADR-5 + Phase 2.0c-extended
        ADR-7. Promotes to ``@stable("v0.9")`` after Phase 2 dogfood
        survives without API change.
        """
        return self._substrate._db.fork(items, fn, choose, **kwargs)


class _PlanNamespace:
    """Curated ``s.plan.*`` surface — Plan AST + execute + optimize +
    promote + MCTS + edit (Phase 2.0a) + registries.

    Thin pass-through to :mod:`persistence.plan`. Per Phase 2.0c-prime
    #147 the existing ``persistence.plan`` substrate (which adapters
    reach today via ``s.escape.plan`` + a ``:sdk/escape-hatch-access``
    audit entry) is filed as a versioned SDK addition under the
    ``@experimental("v0.8.5a1")`` ADR-7 surface-naming pattern (same
    pattern used for ``s.txn.fold`` / ``s.txn.fold_into`` / ``s.txn.fork``).

    The curated namespace surfaces FUNCTIONS only. Type vocabulary
    (``MCTSConfig`` / ``Action`` subclasses / ``Evaluator`` / ``Expander``
    / ``Dispatcher`` / ``Handler`` / ``MetricRef`` / ``Coercion`` /
    ``SkillLibrary`` / error classes) stays in :mod:`persistence.plan`;
    a small load-bearing subset of value-shape types (``Node`` /
    ``ExecutionResult`` / ``OptimizedPlan`` / ``PromotionRecord`` /
    ``TrainingExample`` / ``LeafResult`` / ``FailureInfo``) is also
    re-exported at :mod:`persistence.sdk` top-level for adapter ergonomics.

    No substrate behavior change. No audit chain change. No new
    primitives. Strictly a curated re-export layer.
    """

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate

    # ------------------------------------------------------------------
    # Plan AST + parse
    # ------------------------------------------------------------------
    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.parse. Surface may evolve in v0.9 once "
            "Phase-2 dogfooding identifies whether this is the load-"
            "bearing shape. Adapter authors who depend on it should "
            "not pin against @stable('v0.8') semantics."
        )
    )
    def parse(self, *args, **kwargs):
        """Parse EDN text to a :class:`persistence.plan.Node`; thin
        pass-through to :func:`persistence.plan.parse`.
        """
        from persistence.plan import parse as _parse

        return _parse(*args, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.unparse. Surface may evolve in v0.9 "
            "once Phase-2 dogfooding identifies whether this is the "
            "load-bearing shape. Adapter authors who depend on it "
            "should not pin against @stable('v0.8') semantics."
        )
    )
    def unparse(self, *args, **kwargs):
        """Emit canonical EDN text from a :class:`persistence.plan.Node`;
        thin pass-through to :func:`persistence.plan.unparse`.
        """
        from persistence.plan import unparse as _unparse

        return _unparse(*args, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.walk. Surface may evolve in v0.9 once "
            "Phase-2 dogfooding identifies whether this is the load-"
            "bearing shape. Adapter authors who depend on it should "
            "not pin against @stable('v0.8') semantics."
        )
    )
    def walk(self, *args, **kwargs):
        """Depth-first traversal of a Plan AST; thin pass-through to
        :func:`persistence.plan.walk`.
        """
        from persistence.plan import walk as _walk

        return _walk(*args, **kwargs)

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------
    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.execute. Surface may evolve in v0.9 "
            "once Phase-2 dogfooding identifies whether this is the "
            "load-bearing shape. Adapter authors who depend on it "
            "should not pin against @stable('v0.8') semantics."
        )
    )
    def execute(self, *args, **kwargs):
        """Execute a plan through a dispatcher; thin pass-through to
        :func:`persistence.plan.execute`.
        """
        from persistence.plan import execute as _execute

        return _execute(*args, **kwargs)

    # ------------------------------------------------------------------
    # Dispatcher factory (Phase 2.3a)
    # ------------------------------------------------------------------
    @experimental(
        reason=(
            "Phase 2.3a #147 extension: curated factory for "
            "persistence.plan.Dispatcher. Returns a fresh empty "
            "instance per call. Surface may evolve in v0.9 once "
            "Phase-2 dogfooding identifies whether this is the load-"
            "bearing shape. Adapter authors who depend on it should "
            "not pin against @stable('v0.8') semantics."
        )
    )
    def new_dispatcher(self):
        """Return a fresh :class:`persistence.plan.Dispatcher` instance.

        Used by adapters (e.g. persistence-coder's `_escalate_plan`) to
        construct a per-call handler registry without importing the
        `Dispatcher` type from `persistence.plan` directly. Type
        vocabulary stays in `persistence.plan` per the
        ``_PlanNamespace`` re-export rule (functions only).
        """
        from persistence.plan import Dispatcher

        return Dispatcher()

    # ------------------------------------------------------------------
    # Edit (Phase 2.0a) — re-export only
    # ------------------------------------------------------------------
    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.edit_step (Phase 2.0a). Surface may "
            "evolve in v0.9 once Phase-2 dogfooding identifies whether "
            "this is the load-bearing shape. Adapter authors who "
            "depend on it should not pin against @stable('v0.8') "
            "semantics."
        )
    )
    def edit_step(self, *args, **kwargs):
        """Replace the subtree rooted at ``step_id`` with a new op;
        thin pass-through to :func:`persistence.plan.edit_step`. Must
        be called inside a ``s.txn.dosync()`` body.
        """
        from persistence.plan import edit_step as _edit_step

        return _edit_step(*args, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.insert_step_after (Phase 2.0a). Surface "
            "may evolve in v0.9 once Phase-2 dogfooding identifies "
            "whether this is the load-bearing shape. Adapter authors "
            "who depend on it should not pin against @stable('v0.8') "
            "semantics."
        )
    )
    def insert_step_after(self, *args, **kwargs):
        """Insert a new step immediately after the matched step; thin
        pass-through to :func:`persistence.plan.insert_step_after`.
        Must be called inside a ``s.txn.dosync()`` body.
        """
        from persistence.plan import insert_step_after as _insert_after

        return _insert_after(*args, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.insert_step_before (Phase 2.0a). "
            "Surface may evolve in v0.9 once Phase-2 dogfooding "
            "identifies whether this is the load-bearing shape. "
            "Adapter authors who depend on it should not pin against "
            "@stable('v0.8') semantics."
        )
    )
    def insert_step_before(self, *args, **kwargs):
        """Insert a new step immediately before the matched step; thin
        pass-through to :func:`persistence.plan.insert_step_before`.
        Must be called inside a ``s.txn.dosync()`` body.
        """
        from persistence.plan import insert_step_before as _insert_before

        return _insert_before(*args, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.delete_step (Phase 2.0a). Surface may "
            "evolve in v0.9 once Phase-2 dogfooding identifies "
            "whether this is the load-bearing shape. Adapter authors "
            "who depend on it should not pin against @stable('v0.8') "
            "semantics."
        )
    )
    def delete_step(self, *args, **kwargs):
        """Delete the matched step (and its subtree); thin pass-through
        to :func:`persistence.plan.delete_step`. Must be called inside
        a ``s.txn.dosync()`` body.
        """
        from persistence.plan import delete_step as _delete_step

        return _delete_step(*args, **kwargs)

    # ------------------------------------------------------------------
    # Optimize
    # ------------------------------------------------------------------
    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.optimize. Surface may evolve in v0.9 "
            "once Phase-2 dogfooding identifies whether this is the "
            "load-bearing shape. Adapter authors who depend on it "
            "should not pin against @stable('v0.8') semantics."
        )
    )
    def optimize(self, *args, **kwargs):
        """Optimize a plan against a training set under a metric; thin
        pass-through to :func:`persistence.plan.optimize`.
        """
        from persistence.plan import optimize as _optimize

        return _optimize(*args, **kwargs)

    # ------------------------------------------------------------------
    # Promote
    # ------------------------------------------------------------------
    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.promote. Surface may evolve in v0.9 "
            "once Phase-2 dogfooding identifies whether this is the "
            "load-bearing shape. Adapter authors who depend on it "
            "should not pin against @stable('v0.8') semantics."
        )
    )
    def promote(self, *args, **kwargs):
        """Run all four promotion gates against a candidate plan; thin
        pass-through to :func:`persistence.plan.promote`.
        """
        from persistence.plan import promote as _promote

        return _promote(*args, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.gate_g1_replay_byte_identity. Exposed "
            "for adapter authors who want to compose their own gate "
            "stack. Surface may evolve in v0.9 once Phase-2 "
            "dogfooding identifies whether this is the load-bearing "
            "shape. Adapter authors who depend on it should not pin "
            "against @stable('v0.8') semantics."
        )
    )
    def gate_g1_replay_byte_identity(self, *args, **kwargs):
        """G1 (replay byte-identity) gate; thin pass-through to
        :func:`persistence.plan.gate_g1_replay_byte_identity`.
        """
        from persistence.plan import (
            gate_g1_replay_byte_identity as _g1,
        )

        return _g1(*args, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.gate_g2_audit_chain. Exposed for "
            "adapter authors who want to compose their own gate "
            "stack. Surface may evolve in v0.9 once Phase-2 "
            "dogfooding identifies whether this is the load-bearing "
            "shape. Adapter authors who depend on it should not pin "
            "against @stable('v0.8') semantics."
        )
    )
    def gate_g2_audit_chain(self, *args, **kwargs):
        """G2 (audit chain) gate; thin pass-through to
        :func:`persistence.plan.gate_g2_audit_chain`.
        """
        from persistence.plan import gate_g2_audit_chain as _g2

        return _g2(*args, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.gate_g3_score_delta. Exposed for "
            "adapter authors who want to compose their own gate "
            "stack. Surface may evolve in v0.9 once Phase-2 "
            "dogfooding identifies whether this is the load-bearing "
            "shape. Adapter authors who depend on it should not pin "
            "against @stable('v0.8') semantics."
        )
    )
    def gate_g3_score_delta(self, *args, **kwargs):
        """G3 (score-delta) gate; thin pass-through to
        :func:`persistence.plan.gate_g3_score_delta`.
        """
        from persistence.plan import gate_g3_score_delta as _g3

        return _g3(*args, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.gate_g4_stub. Exposed for adapter "
            "authors who want to compose their own gate stack. "
            "Surface may evolve in v0.9 once Phase-2 dogfooding "
            "identifies whether this is the load-bearing shape. "
            "Adapter authors who depend on it should not pin against "
            "@stable('v0.8') semantics."
        )
    )
    def gate_g4_stub(self, *args, **kwargs):
        """G4 (stub) gate; thin pass-through to
        :func:`persistence.plan.gate_g4_stub`.
        """
        from persistence.plan import gate_g4_stub as _g4

        return _g4(*args, **kwargs)

    # ------------------------------------------------------------------
    # MCTS
    # ------------------------------------------------------------------
    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.mcts_search. Surface may evolve in "
            "v0.9 once Phase-2 dogfooding identifies whether this is "
            "the load-bearing shape. Adapter authors who depend on "
            "it should not pin against @stable('v0.8') semantics."
        )
    )
    def mcts_search(self, *args, **kwargs):
        """PUCT tree search over a Plan AST; thin pass-through to
        :func:`persistence.plan.mcts_search`.
        """
        from persistence.plan import mcts_search as _mcts_search

        return _mcts_search(*args, **kwargs)


    @experimental(
        reason=(
            "Phase 2.0f curated judge surface — Bhatt principle 5 "
            "(multi-agent collaboration). Thin pass-through to "
            "persistence.plan.judge. Surface may evolve in v0.9 once "
            "Phase-2 dogfooding identifies whether this is the "
            "load-bearing shape. Adapter authors who depend on it "
            "should not pin against @stable('v0.8') semantics."
        )
    )
    def judge(self, *args, **kwargs):
        """Score a plan via the :class:`Evaluator` Protocol; thin
        pass-through to :func:`persistence.plan.judge`. Required
        keyword arg ``evaluator`` (any ``Evaluator``-protocol
        implementer). Returns the float score.
        """
        from persistence.plan import judge as _judge

        return _judge(*args, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.mcts_promote. Surface may evolve in "
            "v0.9 once Phase-2 dogfooding identifies whether this is "
            "the load-bearing shape. Adapter authors who depend on "
            "it should not pin against @stable('v0.8') semantics."
        )
    )
    def mcts_promote(self, *args, **kwargs):
        """MCTS-search composed with the 4-gate promote pipeline;
        thin pass-through to :func:`persistence.plan.mcts_promote`.
        """
        from persistence.plan import mcts_promote as _mcts_promote

        return _mcts_promote(*args, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.apply_action. Surface may evolve in "
            "v0.9 once Phase-2 dogfooding identifies whether this is "
            "the load-bearing shape. Adapter authors who depend on "
            "it should not pin against @stable('v0.8') semantics."
        )
    )
    def apply_action(self, *args, **kwargs):
        """Apply an MCTS Action to a plan; thin pass-through to
        :func:`persistence.plan.apply_action`.
        """
        from persistence.plan import apply_action as _apply_action

        return _apply_action(*args, **kwargs)

    # ------------------------------------------------------------------
    # Registries — global mutable state. Documented caveat applies.
    # ------------------------------------------------------------------
    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.register_metric. NOTE: this mutates "
            "the global metric registry. Surface may evolve in v0.9 "
            "once Phase-2 dogfooding identifies whether this is the "
            "load-bearing shape. Adapter authors who depend on it "
            "should not pin against @stable('v0.8') semantics."
        )
    )
    def register_metric(self, *args, **kwargs):
        """Register a metric callable into the global registry; thin
        pass-through to :func:`persistence.plan.register_metric`.
        **NOTE**: mutates global state. Adapter authors should use
        unique ``(id, version)`` refs to avoid collisions across
        adapters that share a process.
        """
        from persistence.plan import register_metric as _register_metric

        return _register_metric(*args, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.unregister_metric. NOTE: this mutates "
            "the global metric registry. Surface may evolve in v0.9 "
            "once Phase-2 dogfooding identifies whether this is the "
            "load-bearing shape. Adapter authors who depend on it "
            "should not pin against @stable('v0.8') semantics."
        )
    )
    def unregister_metric(self, *args, **kwargs):
        """Remove a metric from the global registry; thin pass-through
        to :func:`persistence.plan.unregister_metric`. **NOTE**:
        mutates global state.
        """
        from persistence.plan import unregister_metric as _unregister_metric

        return _unregister_metric(*args, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.lookup_metric. Surface may evolve in "
            "v0.9 once Phase-2 dogfooding identifies whether this is "
            "the load-bearing shape. Adapter authors who depend on "
            "it should not pin against @stable('v0.8') semantics."
        )
    )
    def lookup_metric(self, *args, **kwargs):
        """Resolve a :class:`persistence.plan.MetricRef` to its
        registered callable; thin pass-through to
        :func:`persistence.plan.lookup_metric`.
        """
        from persistence.plan import lookup_metric as _lookup_metric

        return _lookup_metric(*args, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.register_coercion. NOTE: this mutates "
            "the global coercion registry. Surface may evolve in "
            "v0.9 once Phase-2 dogfooding identifies whether this is "
            "the load-bearing shape. Adapter authors who depend on "
            "it should not pin against @stable('v0.8') semantics."
        )
    )
    def register_coercion(self, *args, **kwargs):
        """Register a coercion for a non-EDN value type; thin
        pass-through to :func:`persistence.plan.register_coercion`.
        **NOTE**: mutates global state.
        """
        from persistence.plan import register_coercion as _register_coercion

        return _register_coercion(*args, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.unregister_coercion. NOTE: this "
            "mutates the global coercion registry. Surface may "
            "evolve in v0.9 once Phase-2 dogfooding identifies "
            "whether this is the load-bearing shape. Adapter authors "
            "who depend on it should not pin against @stable('v0.8') "
            "semantics."
        )
    )
    def unregister_coercion(self, *args, **kwargs):
        """Remove a coercion from the global registry; thin
        pass-through to :func:`persistence.plan.unregister_coercion`.
        **NOTE**: mutates global state.
        """
        from persistence.plan import (
            unregister_coercion as _unregister_coercion,
        )

        return _unregister_coercion(*args, **kwargs)

    @experimental(
        reason=(
            "Phase 2.0c-prime #147: curated re-export of "
            "persistence.plan.lookup_coercion. Surface may evolve in "
            "v0.9 once Phase-2 dogfooding identifies whether this is "
            "the load-bearing shape. Adapter authors who depend on "
            "it should not pin against @stable('v0.8') semantics."
        )
    )
    def lookup_coercion(self, *args, **kwargs):
        """Resolve a coercion for a target type; thin pass-through to
        :func:`persistence.plan.lookup_coercion`.
        """
        from persistence.plan import lookup_coercion as _lookup_coercion

        return _lookup_coercion(*args, **kwargs)

    # ------------------------------------------------------------------
    # Skill library — factory
    # ------------------------------------------------------------------
    @experimental(
        reason=(
            "Phase 2.0c-prime #147: factory for "
            "persistence.plan.SkillLibrary instances. Surface may "
            "evolve in v0.9 once Phase-2 dogfooding identifies "
            "whether this is the load-bearing shape. Adapter authors "
            "who depend on it should not pin against @stable('v0.8') "
            "semantics."
        )
    )
    def skill_library(self, *args, **kwargs):
        """Construct a :class:`persistence.plan.SkillLibrary`; thin
        pass-through to the constructor.
        """
        from persistence.plan import SkillLibrary as _SkillLibrary

        return _SkillLibrary(*args, **kwargs)


class _ReplNamespace:
    """Curated ``s.repl.*`` surface — REPL server FACTORY.

    Per ADR-12 the SDK does NOT auto-start the REPL server. Adapter
    authors who want a network-listening REPL session call
    :meth:`serve` explicitly. The curated namespace exposes server
    construction + the token-lifecycle helpers from
    :mod:`persistence.repl`.
    """

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate

    def serve(self, **kwargs):
        """Construct (but do not run) a :class:`persistence.repl.WSServer`
        bound to this substrate's :class:`DB`. Adapter authors call
        ``server.serve(...)`` (the aiohttp run-loop) themselves; the
        SDK does not commit a port lifetime to the substrate.

        Per ADR-12 + design § 4: the substrate's ``s.close()`` is the
        teardown handle; if a REPL server was started, the adapter is
        responsible for its lifecycle.
        """
        from datetime import datetime, timezone

        from persistence.repl import WSServer

        # The default ``runtime_clock`` reaches the system clock as a
        # last resort — production deployments inject an effect-routed
        # clock via the kwarg. The fallback path is the deliberate
        # injection seam ``persistence.repl._ws.WSServer.__init__``
        # already exposes (its existing public-API behavior is
        # ``runtime_clock`` defaults to ``datetime.now`` in tests too).
        runtime_clock = kwargs.pop(
            "runtime_clock",
            lambda: datetime.now(tz=timezone.utc),  # noqa: wall-clock
        )
        return WSServer(
            self._substrate._db,
            runtime_clock=runtime_clock,
            **kwargs,
        )

    def mint_token(self, **kwargs):
        """Issue a fresh capability token; thin pass-through to
        :func:`persistence.repl.mint_token`.
        """
        from persistence.repl import mint_token

        return mint_token(**kwargs)

    def revoke_token(self, *args, **kwargs):
        """Revoke a token (idempotent); thin pass-through to
        :func:`persistence.repl.revoke_token`.
        """
        from persistence.repl import revoke_token

        return revoke_token(*args, **kwargs)

    def list_tokens(self, *args, **kwargs):
        """Enumerate active tokens; thin pass-through to
        :func:`persistence.repl.list_tokens`.
        """
        from persistence.repl import list_tokens

        return list_tokens(*args, **kwargs)


class _AuditNamespace:
    """Curated ``s.audit.*`` surface.

    Exposes the audit-chain integrity probe (:func:`verify_chain`) and
    read-only access to the substrate's session-local audit entry list.

    **Phase 2.0d W1 (R2 MAJOR M2 fix).** The substrate's
    ``_audit_entries`` list is the same in-memory ``entries`` parameter
    passed into :func:`persistence.effect.make_audit_handler` by the
    canonical audit stack installed by default at
    :meth:`Substrate.open` time
    (:func:`persistence.effect.canonical_audit_stack`). Under the W1
    default-on regime, every audit-emitting op committed inside the
    substrate (``:plan/edit`` / ``:fork/*`` / ``:code/exec`` /
    ``:fold/chosen``) appends an :class:`persistence.effect.AuditEntry`
    here at intent-replay time; :func:`verify_chain` reads back a valid
    Merkle chain across the substrate's lifetime.

    When ``Substrate.open(uri, audit=False)`` opted out, the entry list
    stays empty unless an adapter installs its own audit middleware via
    ``s.escape.effect.handlers``; ``s.escape``'s
    ``:sdk/escape-hatch-access`` records use a different shape (plain
    dicts with ``op`` / ``args``, not :class:`AuditEntry`) and are not
    expected to verify_chain (the wire form is documented as
    ``@experimental`` per ADR-1 W3 NIT-5). Calling
    :func:`verify_chain` on a list mixing both shapes is undefined.
    """

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate

    def verify_chain(self, entries=None) -> bool:
        """Re-verify the Merkle chain on a sequence of audit entries;
        defaults to the substrate's session-local AuditEntry chain
        (the canonical-handler output, dict-shaped escape-hatch
        entries excluded). Thin pass-through to
        :func:`persistence.effect.verify_chain`.

        Phase 2.0d W1 (m2): when no ``entries`` argument is passed,
        the default source is the substrate's
        ``_canonical_audit_entries`` list — the AuditEntry-only chain
        produced by the canonical audit stack installed at
        :meth:`Substrate.open(audit=True)`. Under
        ``Substrate.open(audit=False)`` the canonical chain is absent,
        so this method falls back to filtering ``_audit_entries`` for
        :class:`AuditEntry` instances; under that opt-out regime, an
        empty chain trivially verifies.
        """
        from persistence.effect import AuditEntry, verify_chain

        if entries is None:
            canonical = self._substrate._canonical_audit_entries
            if canonical is not None:
                # audit=True: read straight off the AuditEntry-only
                # mirror list. verify_chain iterates [-1].id without
                # tripping on dict-shaped union entries.
                entries = list(canonical)
            else:
                # audit=False fallback: filter the union list for
                # AuditEntry records. Adapter-installed audit
                # handlers (via s.escape.effect) land here.
                entries = [
                    e for e in self._substrate._audit_entries
                    if isinstance(e, AuditEntry)
                ]
        return verify_chain(entries)

    def entries(self):
        """Return a snapshot tuple of the substrate's audit-entry list.

        The caller gets a tuple (immutable) so adapter code that wants
        to filter / window the chain cannot accidentally mutate the
        substrate's ledger. Order is append-order.

        Phase 2.0d W1 (m2): the returned tuple may interleave
        :class:`persistence.effect.AuditEntry` records (canonical
        audit-stack output, default-on under
        :meth:`Substrate.open`) with plain dict envelopes
        (``s.escape.*`` first-access telemetry, shape:
        ``{"op": ":sdk/escape-hatch-access", "args": {...}}``). Callers
        that want only the chain-verifiable subset should filter on
        ``isinstance(e, AuditEntry)``.
        """
        return tuple(self._substrate._audit_entries)


class _ReplayNamespace:
    """Curated ``s.replay.*`` surface.

    Thin pass-through to :mod:`persistence.replay`. The three load-bearing
    primitives (record / replay / compare) are surfaced; adapter authors
    needing the lower-level ``EffectHandler`` / ``Trajectory`` classes
    reach via ``s.escape.replay``.
    """

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate

    def record(self, *args, **kwargs):
        """Record a factual trajectory; thin pass-through to
        :func:`persistence.replay.record`.
        """
        from persistence.replay import record

        return record(*args, **kwargs)

    def replay(self, *args, **kwargs):
        """Re-execute a trajectory under interventions; thin pass-through
        to :func:`persistence.replay.replay`.
        """
        from persistence.replay import replay as _replay

        return _replay(*args, **kwargs)

    def compare(self, *args, **kwargs):
        """Diff two trajectories; thin pass-through to
        :func:`persistence.replay.compare`.
        """
        from persistence.replay import compare

        return compare(*args, **kwargs)


class _EscapeNamespace:
    """``s.escape.*`` — the raw-module reach-through namespace.

    Per ADR-1 + ADR-1 W2 SHOULD-FIX 3 + W3 NIT-5: the seven attributes
    here (``fact`` / ``effect`` / ``plan`` / ``replay`` / ``txn`` /
    ``spec`` / ``repl``) return the **raw** Module instance (or module
    object) — bypassing the curated namespaces above. Each first
    access per-session emits exactly one ``:sdk/escape-hatch-access``
    audit entry; subsequent accesses to the same attribute in the same
    session are silent.

    The audit-entry shape is ``@experimental`` (NOT ``@stable("v0.8")``)
    per ADR-1 W3 NIT-5; see :func:`persistence.sdk._audit.build_escape_hatch_payload`.
    """

    # Mapping from escape-hatch attr name → callable that returns the raw
    # underlying surface for the substrate. Lazy module imports keep the
    # SDK import-time graph minimal.
    _RAW_RESOLVERS: dict[str, Any] = {}

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate
        # Per-instance: which attrs have already emitted their telemetry
        # entry this session. Lookup-by-attr keeps the dedupe scope
        # exactly per ADR-1 W2 ("one entry per session per attribute").
        self._emitted: set[str] = set()

    def __getattr__(self, name: str) -> Any:
        # __getattr__ is only invoked when a normal attribute lookup
        # fails, so it doesn't shadow ``_substrate`` / ``_emitted``.
        if name in _EscapeNamespace._RAW_RESOLVERS:
            self._record_first_access(name)
            return _EscapeNamespace._RAW_RESOLVERS[name](self._substrate)
        raise AttributeError(
            f"'escape' namespace has no attribute {name!r}; valid escape-"
            f"hatch attrs: {sorted(_EscapeNamespace._RAW_RESOLVERS)!r}"
        )

    def __dir__(self) -> list[str]:
        # Surface the seven escape-hatch names so introspection tools
        # (REPL completion, IDE) can find them.
        return sorted(_EscapeNamespace._RAW_RESOLVERS) + [
            "_substrate",
            "_emitted",
        ]

    def _record_first_access(self, name: str) -> None:
        """Emit the ``:sdk/escape-hatch-access`` audit entry on first
        access per attribute. The entry is appended to the substrate's
        ``_audit_entries`` list (the same list that backs
        ``s.audit.entries()``).

        Per W3 NIT-5 the entry shape itself is not part of the v0.8
        contract; downstream tooling that parses these entries does so
        at its own risk.
        """
        if name in self._emitted:
            return
        self._emitted.add(name)
        # Build the payload via the @experimental helper. ``caller_depth``
        # of 3 puts the resolved frame at the adapter source line that
        # accessed ``s.escape.<name>`` — depth 1 is __getattr__, depth 2
        # is the bound-method that made the call inside __getattr__,
        # depth 3 is the user's adapter source. Tests may pin a smaller
        # depth via direct calls into ``build_escape_hatch_payload``.
        payload = build_escape_hatch_payload(
            module=name,
            session_id=self._substrate._session_id,
            caller_depth=3,
        )
        # Append a lightweight dict envelope. v0.8 ships these as plain
        # dicts on ``Substrate._audit_entries`` (NOT ``AuditEntry``
        # records — those carry a hash chain, and SDK2 does not yet
        # boot the audit handler stack; SDK3 wires a real handler when
        # the MCP server's audit emission lands). The dict shape is
        # documented as ``@experimental`` so SDK3's swap to
        # ``AuditEntry`` is not a contract break.
        self._substrate._audit_entries.append(
            {"op": ":sdk/escape-hatch-access", "args": payload}
        )


class _ClaimIdentityNamespace:
    """`s.claim.identity.*` — caller identity attestation primitives.

    Stub in 2.1c; real in 2.1c.5.
    """

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate

    @staticmethod
    def attest(*, caller_id, payload, signature):
        """Verify a caller-signed payload; stub returns None in 2.1c."""
        return CallerIdentity.attest(
            caller_id=caller_id, payload=payload, signature=signature
        )


class _ClaimNamespace:
    """`s.claim.*` — claim kind namespace + schema validation.

    @experimental("v0.9.x")
    """

    def __init__(self, substrate: "Substrate") -> None:
        self._substrate = substrate
        self.identity = _ClaimIdentityNamespace(substrate)

    kinds: frozenset = CLAIM_KINDS

    @staticmethod
    def is_kind(kind: str) -> bool:
        """Return True iff *kind* is a registered ``:claim/*`` kind."""
        return is_claim_kind(kind)

    @staticmethod
    def validate(kind: str, attrs: dict) -> dict:
        """Validate *attrs* for the given claim *kind* and return canonical attrs."""
        return validate_attrs(kind, attrs)


def _resolve_raw_fact(s: "Substrate") -> Any:
    return s._db


def _resolve_raw_effect(s: "Substrate") -> Any:
    return s._runtime


def _resolve_raw_plan(s: "Substrate") -> Any:
    import persistence.plan as _plan_mod

    return _plan_mod


def _resolve_raw_replay(s: "Substrate") -> Any:
    import persistence.replay as _replay_mod

    return _replay_mod


def _resolve_raw_txn(s: "Substrate") -> Any:
    import persistence.txn as _txn_mod

    return _txn_mod


def _resolve_raw_spec(s: "Substrate") -> Any:
    import persistence.spec as _spec_mod

    return _spec_mod


def _resolve_raw_repl(s: "Substrate") -> Any:
    import persistence.repl as _repl_mod

    return _repl_mod


_EscapeNamespace._RAW_RESOLVERS = {
    "fact": _resolve_raw_fact,
    "effect": _resolve_raw_effect,
    "plan": _resolve_raw_plan,
    "replay": _resolve_raw_replay,
    "txn": _resolve_raw_txn,
    "spec": _resolve_raw_spec,
    "repl": _resolve_raw_repl,
}


# ---------------------------------------------------------------------------
# Substrate
# ---------------------------------------------------------------------------
# The closed list of names ``dir(s)`` returns at the contract level.
# Per the brief's frozen-surface test: "dir(s) returns exactly the curated
# 7 subsurface names + lifecycle methods (no raw module bleed-through)."
# NOTE: Python's ``dir()`` sorts the returned list lexically; underscore
# names sort before lowercase letters (ASCII 0x5F < 0x61). The literal
# below preserves that lexical order so the contract-surface comparison
# in the frozen-surface test reads naturally.
_SUBSTRATE_PUBLIC_DIR: tuple[str, ...] = (
    # stability marker (class attribute)
    "_stability_version",
    # 9 curated subsurfaces (Phase 2.0c-prime #147 added ``plan``; 2.1c adds ``claim``)
    "audit",
    "claim",
    "close",
    "effect",
    "escape",
    "fact",
    "open",
    "plan",
    "replay",
    "repl",
    "txn",
)


class _MirroringEntryList(list):
    """A list subclass that mirrors every ``append`` into a sibling list.

    Phase 2.0d W1 (M2): the canonical audit handler installed by
    :meth:`Substrate.open` writes :class:`persistence.effect.AuditEntry`
    records into a dedicated AuditEntry-only list (so
    ``s.audit.verify_chain()`` can iterate ``[-1].id`` cleanly without
    tripping on the dict-shaped ``:sdk/escape-hatch-access`` entries
    that ``s.escape.*`` appends per ADR-1 W3 NIT-5). To preserve the
    pre-W1 contract that ``s.audit.entries()`` returns the *union* of
    escape-hatch dicts + audit chain entries, this list wraps each
    canonical-handler append by also pushing into the substrate's
    public ``_audit_entries`` list.

    The mirror direction is one-way (canonical → public). Escape-hatch
    appends go directly to the public list and do NOT reflect back
    into the canonical-only list — they are not chain-verifiable.

    The class is intentionally minimal: only ``append`` is overridden
    because that is the only mutation the audit handler performs. If
    a future audit-handler revision uses ``extend`` or slice
    assignment, those paths must be added here too (otherwise the
    mirror desyncs).
    """

    __slots__ = ("_mirror",)

    def __init__(self, mirror: list) -> None:
        super().__init__()
        self._mirror = mirror

    def append(self, item: Any) -> None:  # type: ignore[override]
        super().append(item)
        self._mirror.append(item)


@stable("v0.8")
class Substrate:
    """Curated namespace + lifecycle facade for the v0.8 adapter contract.

    Open with :meth:`open`, close with :meth:`close`, or use as a
    context manager. The seven subsurface attributes (``fact`` /
    ``effect`` / ``txn`` / ``repl`` / ``audit`` / ``replay`` /
    ``escape``) are the contract surface for adapter authors.

    Lifecycle::

        s = Substrate.open("memory")
        # ... use s.fact.transact(...), s.txn.dosync(), etc.
        s.close()

        # Or as a context manager:
        with Substrate.open("sqlite:///path/to/db") as s:
            ...

    Per ADR-1 the curated namespaces (``fact`` / ``effect`` / ``txn``
    / ``repl`` / ``audit`` / ``replay``) are the v0.8 stable surface;
    ``escape`` is the explicit out-of-contract reach-through with
    audit-emission first-access telemetry.

    Per ADR-12 the SDK does NOT auto-start a REPL server; adapter
    authors call ``s.repl.serve(...)`` explicitly when they want one.

    **Audit-chain installation (Phase 2.0d W1 / R2 MAJOR M2 fix).**
    ``Substrate.open(uri)`` installs the canonical audit handler stack
    by default — the substrate's effect runtime is wired with
    :func:`persistence.effect.canonical_audit_stack` covering every
    audit-emitting op shipped through Phase 2.0a / 2.0b / 2.0c / 2.0c-ext
    (``:plan/edit`` / ``:fork/*`` / ``:code/exec`` / ``:fold/chosen``).
    The runtime is activated via ``persistence.effect.runtime._active``
    at substrate construction time and released in :meth:`close`, so
    audit coverage holds for the substrate's lifetime without requiring
    callers to wrap usage in a ``with with_runtime(...):`` block.

    Pass ``audit=False`` only for sandbox tests where Merkle-chain
    enforcement is undesirable; in that regime, do not queue
    audit-emitting intents on a transaction (or you will get
    :class:`persistence.txn.AuditStackMissing` at commit time per the
    W1 fail-fast guard in :func:`persistence.txn.transaction._replay_effect_intents`).
    Lower-level :class:`persistence.fact.DB` instances do NOT install
    the audit stack — only the SDK-level ``Substrate`` does.
    """

    # Class attribute pinning the stability profile — read by the spec
    # generator (G7 / SDK5) and by adapter authors who introspect
    # ``Substrate._stability_version``. Per the design doc § 4:
    # "Class attribute :attr:`_stability_version = "v0.8"`".
    _stability_version: str = "v0.8"

    def __init__(
        self,
        *,
        _db: Any,
        _runtime: Any,
        _audit_entries: Optional[list] = None,
        _audit_runtime_token: Any = None,
        _canonical_audit_entries: Optional[list] = None,
    ) -> None:
        # Private constructor; adapter authors use :meth:`open`.
        self._db = _db
        self._runtime = _runtime
        # Phase 2.0d W1 (M2): the public ``_audit_entries`` list IS the
        # union surface (dict-shaped escape-hatch entries from
        # ``s.escape.*`` + :class:`AuditEntry` records mirrored from
        # the canonical handler's chain). Backward-compatible with the
        # pre-W1 ``s.audit.entries()`` contract.
        if _audit_entries is None:
            self._audit_entries: list[Any] = []
        else:
            # Phase 2.0d W1 (M2): the open() path passes the same list
            # the canonical-handler mirror reflects into; retain
            # identity so ``s.audit.entries()`` sees post-commit
            # AuditEntry mirrors.
            self._audit_entries = _audit_entries
        # Phase 2.0d W1 (M2): the AuditEntry-only chain backing
        # ``s.audit.verify_chain()``. None when audit=False (or the
        # pre-W1 path passed nothing). The list IS the
        # :class:`_MirroringEntryList` instance that the canonical
        # audit handler writes into; verify_chain reads ``[-1].id``
        # cleanly here without tripping on dict-shaped union entries.
        self._canonical_audit_entries: Optional[list] = (
            _canonical_audit_entries
        )
        # Phase 2.0d W1 (M2): when ``Substrate.open(audit=True)`` activates
        # the canonical audit stack, the resulting ContextVar token is
        # threaded back here so :meth:`close` can release it. ``None``
        # means "no token to release" (audit=False or pre-W1 callers).
        self._audit_runtime_token: Any = _audit_runtime_token
        self._closed = False
        # Per-substrate session id used in ADR-1 W3 NIT-5 escape-hatch
        # audit-entry payloads. v0.8 uses the system uuid generator at
        # construction time; the value never escapes the audit-entry
        # shape (which is itself ``@experimental`` per ADR-1 W3 NIT-5)
        # so it is not a contract surface for adapter authors. Routing
        # through ``:sys/random`` would require booting a runtime stack
        # at substrate-open time — Phase 2.0d W1 added that runtime
        # boot for the audit-default install, but the session_id stays
        # on uuid4 because it is opaque to adapters and need not be
        # replay-deterministic across substrate constructions.
        self._session_id = uuid.uuid4().hex  # noqa: wall-clock
        # Subsurfaces are bound at construction time so each call site
        # gets the same namespace instance back (identity stability is
        # documented for adapter authors who cache references).
        self._fact = _FactNamespace(self)
        self._effect = _EffectNamespace(self)
        self._txn = _TxnNamespace(self)
        self._repl = _ReplNamespace(self)
        self._audit = _AuditNamespace(self)
        self._replay = _ReplayNamespace(self)
        self._plan = _PlanNamespace(self)
        self._escape = _EscapeNamespace(self)
        self._claim = _ClaimNamespace(self)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @classmethod
    def open(cls, uri: str = "memory", *, audit: bool = True) -> "Substrate":
        """Open a substrate against the given store URI.

        Per ADR-9 the URI may be:

        - ``"memory"``                  — in-process in-memory store
        - ``"sqlite:///<absolute-path>"`` — file-backed SQLite store
        - ``"postgres://..."``          — PG1 stream (raises
          :class:`BackendNotInstalled` until PG1 lands)

        URI parsing + backend dispatch are delegated to
        :func:`persistence.sdk.uri.open_store`; SDK2 wires the resulting
        ``Store`` into a fresh :class:`persistence.fact.DB`.

        **Audit-stack installation (Phase 2.0d W1 default).** When
        ``audit=True`` (default), the substrate's effect runtime is
        built via :func:`persistence.effect.canonical_audit_stack` and
        activated via
        ``persistence.effect.runtime._active.set(rt)`` for the
        substrate's lifetime; :meth:`close` releases the activation.
        The audit handler's :class:`AuditEntry` records are appended
        to ``Substrate._audit_entries`` (the same list backing
        ``s.audit.entries()`` and ``s.audit.verify_chain()``).

        When ``audit=False``, a bare empty :class:`persistence.effect.Runtime`
        is constructed and NOT activated — the substrate ships with no
        audit middleware. This is intended for sandbox tests where
        Merkle-chain enforcement is undesirable; do not queue
        audit-emitting intents on a transaction in that regime, or
        :class:`persistence.txn.AuditStackMissing` will fire at commit
        time (W1 fail-fast guard).

        Args:
            uri: store URI per ADR-9; default ``"memory"``.
            audit: install the canonical audit handler stack by
                default. Pass ``False`` only when Merkle-chain
                enforcement is undesirable. Default ``True``.

        Returns:
            a fresh :class:`Substrate` ready for use.

        Raises:
            UnknownStoreScheme:   per :func:`open_store`.
            BackendNotInstalled:  per :func:`open_store`.
            ValueError:           per :func:`open_store`.
        """
        from persistence.effect import Runtime, canonical_audit_stack
        from persistence.effect.runtime import _active as _effect_active
        from persistence.fact import DB

        store = open_store(uri)
        db = DB(store)
        # Phase 2.0d W1 (M2): the public ``_audit_entries`` list is the
        # union surface (escape-hatch dicts + AuditEntry records) per
        # the pre-W1 ``s.audit.entries()`` contract. The canonical
        # audit handler writes AuditEntry records into a sibling
        # mirror list whose ``append`` reflects into the public union;
        # the handler iterates that mirror's ``[-1].id`` cleanly
        # without tripping on dict-shaped escape-hatch entries.
        audit_entries: list[Any] = []
        if audit:
            canonical_entries = _MirroringEntryList(mirror=audit_entries)
            runtime = canonical_audit_stack(canonical_entries)
            # Activate for the substrate's lifetime. The token is
            # released in :meth:`close`. We do NOT use the
            # ``with_runtime`` context manager here — the substrate is
            # not necessarily used as a context manager (callers may
            # ``Substrate.open()`` and ``s.close()`` explicitly), so we
            # manage the ContextVar manually. The async/threading
            # caveat in :mod:`persistence.txn.intents` (ContextVars
            # don't propagate to raw threads) applies here too: a
            # substrate opened in thread A will not auto-extend its
            # audit runtime into a child thread spawned via
            # ``threading.Thread`` from thread A. Adapters running
            # multi-threaded substrates must re-activate per thread
            # (or use ``with_runtime`` inline at the boundary).
            audit_runtime_token = _effect_active.set(runtime)
        else:
            runtime = Runtime()
            audit_runtime_token = None
            canonical_entries = None
        return cls(
            _db=db,
            _runtime=runtime,
            _audit_entries=audit_entries,
            _audit_runtime_token=audit_runtime_token,
            _canonical_audit_entries=canonical_entries,
        )

    def close(self) -> None:
        """Release substrate resources. Idempotent.

        Per the design doc § 4 lifecycle pin: ``close()`` must be safe to
        call multiple times. v0.8 closes the underlying store (when the
        store exposes a ``close``) and marks the substrate as closed so
        subsequent subsurface access raises :class:`RuntimeError`.

        Phase 2.0d W1 (M2): if the substrate was opened with
        ``audit=True`` (default), the canonical audit runtime token
        captured at construction is released here so the ContextVar
        does not leak across substrate lifetimes. The token release
        leaves the prior context unchanged — if a caller manually
        wrapped the substrate in ``with_runtime(other_rt)``, the outer
        runtime is preserved.

        REPL server lifetimes are NOT auto-shut down by ``close()`` per
        ADR-12 — adapter authors who started a server via
        ``s.repl.serve(...)`` are responsible for its teardown. (The
        design doc explicitly notes this.)
        """
        if self._closed:
            return
        self._closed = True
        # Phase 2.0d W1 (M2): release the audit runtime token captured
        # at open() time. Best-effort — if the ContextVar was already
        # reset out of band (e.g. caller exited a nested with_runtime
        # block in the wrong order), swallow the error rather than
        # double-failing on close.
        if self._audit_runtime_token is not None:
            from persistence.effect.runtime import _active as _effect_active

            try:
                _effect_active.reset(self._audit_runtime_token)
            except (ValueError, LookupError):  # pragma: no cover
                pass
            self._audit_runtime_token = None
        # Best-effort store close; not every Store impl exposes one.
        store = getattr(self._db, "store", None)
        store_close = getattr(store, "close", None)
        if callable(store_close):
            try:
                store_close()
            except Exception:  # noqa: BLE001
                # Idempotent close must not raise on second teardown
                # paths; the store may already be closed by the
                # caller. Swallowing matches stdlib file-close
                # idempotency expectations for adapter ergonomics.
                pass

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------
    def __enter__(self) -> "Substrate":
        if self._closed:
            raise RuntimeError(
                "Substrate.__enter__: this substrate is already closed"
            )
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Curated subsurface properties
    # ------------------------------------------------------------------
    @property
    def fact(self) -> _FactNamespace:
        """Curated ``s.fact.*`` namespace; see :class:`_FactNamespace`."""
        self._check_open("fact")
        return self._fact

    @property
    def effect(self) -> _EffectNamespace:
        """Curated ``s.effect.*`` namespace; see
        :class:`_EffectNamespace`."""
        self._check_open("effect")
        return self._effect

    @property
    def txn(self) -> _TxnNamespace:
        """Curated ``s.txn.*`` namespace; see :class:`_TxnNamespace`."""
        self._check_open("txn")
        return self._txn

    @property
    def repl(self) -> _ReplNamespace:
        """Curated ``s.repl.*`` namespace; see :class:`_ReplNamespace`."""
        self._check_open("repl")
        return self._repl

    @property
    def audit(self) -> _AuditNamespace:
        """Curated ``s.audit.*`` namespace; see :class:`_AuditNamespace`."""
        self._check_open("audit")
        return self._audit

    @property
    def replay(self) -> _ReplayNamespace:
        """Curated ``s.replay.*`` namespace; see
        :class:`_ReplayNamespace`."""
        self._check_open("replay")
        return self._replay

    @property
    def plan(self) -> _PlanNamespace:
        """Curated ``s.plan.*`` namespace; see :class:`_PlanNamespace`.

        Phase 2.0c-prime #147 SDK-gap closure: surfaces the existing
        :mod:`persistence.plan` substrate as a curated, ``@experimental
        ('v0.8.5a1')`` re-export layer. Adapter authors who previously
        reached via ``s.escape.plan`` (which fires a
        ``:sdk/escape-hatch-access`` audit entry) can now bind to the
        curated surface instead.
        """
        self._check_open("plan")
        return self._plan

    @property
    def escape(self) -> _EscapeNamespace:
        """Out-of-contract escape-hatch namespace; see
        :class:`_EscapeNamespace`. Each first access per-session
        emits one ``:sdk/escape-hatch-access`` audit entry."""
        self._check_open("escape")
        return self._escape

    @property
    def claim(self) -> _ClaimNamespace:
        """Curated ``s.claim.*`` namespace; see :class:`_ClaimNamespace`.

        @experimental("v0.9.x")
        """
        self._check_open("claim")
        return self._claim

    # ------------------------------------------------------------------
    # Frozen surface
    # ------------------------------------------------------------------
    def __dir__(self) -> list[str]:
        """Return the closed contract-surface name list.

        Per the design doc + the brief's frozen-surface test: ``dir(s)``
        is the load-bearing way for adapter authors (and the spec
        generator) to enumerate the contract surface. Returning a fixed
        tuple — instead of falling through to ``object.__dir__`` —
        prevents raw Module attributes from bleeding through.
        """
        return list(_SUBSTRATE_PUBLIC_DIR)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _check_open(self, attr: str) -> None:
        """Raise :class:`RuntimeError` if the substrate has been closed.

        Adapter authors who reach into a closed substrate get a clear
        error instead of a silent UnboundLocalError-on-_db.
        """
        if self._closed:
            raise RuntimeError(
                f"Substrate is closed; cannot access {attr!r} subsurface"
            )


__all__ = [
    "Substrate",
]
