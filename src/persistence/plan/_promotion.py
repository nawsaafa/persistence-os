"""Promotion gates G1 (replay byte-identity) + G2 (audit-chain) (v0.6.0a1, A6).

A6 lands the first two of four promotion gates as **internal helpers**;
A7 will add G3, the G4 stub, and the ``promote()`` orchestrator that
calls all four and produces a ``PromotionRecord``. A6 does NOT export
anything publicly — :mod:`persistence.plan.__init__` is A7's surface.

Gate semantics — pinned by
``docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md`` §7 and
``docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md`` §2.A6:

- **G1** — Replay byte-identity over held-out trajectories. The candidate
  Plan AST is replayed against ≥ ``min_count`` recorded trajectories;
  every replay must produce a counterfactual byte-identical to the
  factual. **One divergent byte = G1 fail.** Defends Prop 4.
- **G2** — Audit-chain unbroken over a tx_time window. Reconstructs
  ``AuditEntry`` instances from ``:audit/...`` datoms in the DB and
  feeds them to :func:`persistence.effect.verify_chain`. **Any chain
  break = G2 fail.** Defends Prop 2.

Engine-wiring decision (impl plan calls G1's parameter ``replay_engine:
ReplayEngine``). :mod:`persistence.replay` exposes module-level functions
(``replay``, ``compare``), not a class. We define a structural
:class:`ReplayEngine` ``Protocol`` here so:

- callers can pass a thin shim around ``persistence.replay.replay`` /
  ``persistence.replay.compare`` (the production wiring; Stream F lands
  the corpus + adapter in ``bench/regulator_replay/``);
- tests inject a fabricated stub without importing the heavy replay
  engine (matches A5's ``_PromotionRecordLike`` decoupling pattern).

References:
    docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md §7
    docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md §2.A6
    src/persistence/effect/handlers/audit.py (verify_chain, datom_to_audit_entry)
    src/persistence/replay/engine.py (replay, compare)
"""
from __future__ import annotations

from typing import Any, Iterable, Protocol

from persistence.effect import datom_to_audit_entry, verify_chain
from persistence.fact.datom import Datom
from persistence.fact.db import DB
from persistence.plan._ast import Node
from persistence.replay.trajectory import Trajectory


# ---------------------------------------------------------------------------
# G1 — replay byte-identity
# ---------------------------------------------------------------------------


#: Default minimum trajectory count to claim G1 coverage. The design doc
#: pins this at 10; bumping it tightens the bar. Exposed as a module
#: constant so the test drift-pin and the impl-plan reference the same
#: source.
_DEFAULT_MIN_COUNT: int = 10

#: ``compare()`` convention: ``divergence_step is None`` means the two
#: trajectories agree on every step. The gate uses this as its
#: byte-identity predicate. See
#: :func:`persistence.replay.engine.compare`.
_DIVERGENCE_KEY: str = "divergence_step"


class ReplayEngine(Protocol):
    """Structural type for the engine G1 calls.

    The two operations needed by :func:`gate_g1_replay_byte_identity`
    map onto :func:`persistence.replay.replay` (re-execute under the
    candidate plan) and :func:`persistence.replay.compare` (diff factual
    vs counterfactual).

    Stream F's adapter wraps those module-level functions; tests pass
    fabricated stubs. Both implementations satisfy this protocol
    structurally — no import-order coupling.

    Positional-only parameter declaration. Pyright's protocol-conformance
    check requires parameter *name* equality unless the protocol
    declares its parameters positional-only with ``/``. We use ``/`` so
    callers can name their stub parameters whatever they like (``a``,
    ``b``, ``plan``, ``traj``…) and still satisfy the protocol — the
    body of the gate only ever calls these positionally.
    """

    def replay(self, plan: Node, trajectory: Trajectory, /) -> Trajectory:
        """Re-execute ``trajectory``'s recorded inputs under ``plan``.

        Returns a counterfactual ``Trajectory`` whose ``facts`` /
        ``outcome`` should byte-match the factual when the plan is a
        valid candidate. Wiring detail: Stream F's adapter constructs
        whatever per-trajectory dispatcher the plan needs (e.g.
        :class:`persistence.plan.Dispatcher` over the same handlers
        used at record time) before delegating to
        ``persistence.replay.replay``.
        """
        ...

    def compare(self, factual: Trajectory, counterfactual: Trajectory, /) -> dict:
        """Pair-diff per :func:`persistence.replay.engine.compare`.

        The gate reads ``result["divergence_step"]``; ``None`` is the
        byte-identity signal. Other keys (``pnl_delta``, ``kl_divergence``,
        ...) are ignored by G1 but preserved on the contract for
        symmetry with the underlying engine.
        """
        ...


def gate_g1_replay_byte_identity(
    candidate_plan: Node,
    *,
    held_out_trajectories: list[Trajectory],
    replay_engine: ReplayEngine,
    min_count: int = _DEFAULT_MIN_COUNT,
) -> bool:
    """G1 — every held-out trajectory replays byte-identical → True.

    Iterates ``held_out_trajectories`` in order. For each trajectory
    ``t``, asks the engine to replay the candidate plan against ``t``,
    then asks the engine to compare the two. A ``divergence_step``
    other than ``None`` aborts with ``False`` — the gate may
    short-circuit on the first divergence, by design.

    Coverage gate: fewer than ``min_count`` trajectories returns
    ``False`` regardless of byte-identity, because Prop 4's claim
    requires statistical coverage. Empty lists fall under the same
    rule (``0 < min_count``). Callers can lower ``min_count`` for
    integration testing — production promotions use the default.

    Parameters
    ----------
    candidate_plan
        The Plan AST under evaluation. Passed to ``engine.replay`` for
        each trajectory.
    held_out_trajectories
        Recorded trajectories the candidate plan has not seen during
        optimization. Real corpus comes from
        ``bench/regulator_replay/trajectories/`` (Stream F); A6 tests
        fabricate trivial fixtures.
    replay_engine
        Anything satisfying :class:`ReplayEngine`. Production callers
        pass an adapter around :mod:`persistence.replay`; tests pass a
        stub.
    min_count
        Minimum trajectory count to claim coverage. Default ``10``,
        per design doc §7.

    Returns
    -------
    ``True`` iff all of:

    - ``len(held_out_trajectories) >= min_count``,
    - every ``compare(factual, counterfactual)["divergence_step"]`` is
      ``None``.

    Otherwise ``False``.
    """
    # Coverage gate first — reject below-threshold corpora before
    # touching the replay engine. This is also the empty-list branch
    # (``0 < min_count``) — Prop 4 cannot be claimed vacuously.
    if len(held_out_trajectories) < min_count:
        return False

    for trajectory in held_out_trajectories:
        counterfactual = replay_engine.replay(candidate_plan, trajectory)
        diff = replay_engine.compare(trajectory, counterfactual)
        if diff.get(_DIVERGENCE_KEY) is not None:
            # First divergence is a hard fail — no need to continue.
            return False
    return True


# ---------------------------------------------------------------------------
# G2 — audit-chain unbroken
# ---------------------------------------------------------------------------


#: Bare-string attribute prefix for audit datoms in the fact store.
#: ``:datom/a`` arrives at the wire boundary as ``":audit/<op>"``;
#: :class:`Datom.a` strips the leading colon (per ``datom.py:114``), so
#: the in-DB form is bare ``"audit/<op>"``. The gate filters on this
#: prefix — datoms whose ``a`` does not start here are not audit
#: entries.
_AUDIT_ATTR_PREFIX: str = "audit/"


def _datom_to_wire_for_audit(datom: Datom) -> dict[str, Any]:
    """Reconstruct the wire-form dict ``datom_to_audit_entry`` expects.

    :class:`Datom` strips the leading ``:`` from ``a`` (canonical
    storage form), and :class:`Datom.tx` is ``int`` — but the canonical
    audit-emission shape produced by
    :func:`persistence.effect.audit_entry_to_datom` puts the audit
    entry's content hash (``sha256:...``) in ``:datom/tx``. The fact
    store's ``Datom`` slot can't carry that string, so the audit
    entry's hash is also pinned in
    ``provenance[":signature"]`` (the
    :func:`persistence.effect.audit_entry_to_datom` contract). The
    inverse reads ``id = datom[":datom/tx"]``, so we splice the
    signature back into that slot here.

    The Datom's ``provenance`` is already the wire-shape map produced
    by :func:`persistence.effect.audit_entry_to_datom` (with leading
    colons on its known keys and a bare-snake_case
    ``parent_provenance_hash`` alias). We pass it through unchanged —
    the inverse reads ``:prev-hash``, ``:policy-id``, ``:handler-chain``,
    ``:principal``, ``:episode``, ``:effect/txn-commit`` from that map.
    """
    provenance = dict(datom.provenance)
    # Splice the entry id back into ``:datom/tx``. ``audit_entry_to_datom``
    # writes it as ``provenance[":signature"]`` (and into ``:datom/tx``
    # itself in the wire form). Storing in the fact store costs us the
    # ``:datom/tx`` slot — Datom.tx must be int — but the signature is
    # the canonical fallback.
    audit_id = provenance.get(":signature", datom.tx)
    return {
        ":datom/e": datom.e,
        # Restore the leading colon dropped by Datom.a's bare-string
        # storage convention.
        ":datom/a": ":" + datom.a,
        ":datom/v": datom.v,
        ":datom/tx": audit_id,
        ":datom/tx-time": datom.tx_time,
        ":datom/valid-from": datom.valid_from,
        ":datom/valid-to": datom.valid_to,
        # ``Datom.op`` is bare ("assert" / "retract"); the wire form is
        # the EDN keyword. ``datom_to_audit_entry`` does not read this
        # field, so the value is informational; we keyword-form it for
        # contract symmetry with :func:`persistence.effect.audit_entry_to_datom`.
        ":datom/op": ":" + datom.op,
        ":datom/provenance": provenance,
        ":datom/invalidated-by": datom.invalidated_by,
    }


def _audit_entries_in_window(
    db: DB, *, start_ms: int, end_ms: int
) -> Iterable:
    """Yield ``AuditEntry`` reconstructions for audit datoms in the window.

    Walks ``db.log()`` once. For each datom whose attribute starts with
    ``audit/`` and whose ``tx_time`` falls in
    ``[start_ms, end_ms]`` (inclusive both ends), reconstructs the
    wire form and asks
    :func:`persistence.effect.datom_to_audit_entry` to invert it.

    Insertion order in the log matches the audit chain order (each
    audit entry is one transact's worth of one datom in production), so
    the yielded sequence is already in chain order — no extra sort.

    Parameters
    ----------
    db
        Fact store containing audit datoms.
    start_ms
        Lower bound on ``tx_time``, **milliseconds since the Unix epoch**.
    end_ms
        Upper bound on ``tx_time``, **milliseconds since the Unix epoch**.
    """
    for datom in db.log():
        if not (isinstance(datom.a, str) and datom.a.startswith(_AUDIT_ATTR_PREFIX)):
            continue
        # ``Datom.tx_time`` is a tz-aware datetime (stamped by the
        # audit handler's ``recorded_at`` instant on seed). Convert to
        # ms-since-epoch for window comparison — the gate's contract
        # is in ms (impl plan §2.A6).
        tx_time_ms = int(datom.tx_time.timestamp() * 1000)
        if tx_time_ms < start_ms or tx_time_ms > end_ms:
            continue
        wire = _datom_to_wire_for_audit(datom)
        yield datom_to_audit_entry(wire)


def gate_g2_audit_chain(
    db: DB,
    *,
    audit_window_start: int,
    audit_window_end: int,
) -> bool:
    """G2 — audit chain over ``[start, end]`` is unbroken → True.

    Pulls ``AuditEntry`` instances from ``db`` for the
    ``[audit_window_start, audit_window_end]`` window (inclusive,
    in milliseconds since the Unix epoch), then delegates to
    :func:`persistence.effect.verify_chain`. The empty window is
    vacuously consistent — ``verify_chain([])`` is ``True``.

    Module 2 owns the verification logic; this gate is a thin pull-and-
    delegate adapter so Module 3 has a uniform G1/G2/G3/G4 surface for
    A7's ``promote()`` orchestrator. Non-audit datoms in the same
    window are filtered out by the attribute prefix check.

    Parameters
    ----------
    db
        Fact store with audit datoms (typically seeded by
        :func:`persistence.effect.audit_entry_to_datom`).
    audit_window_start
        Lower bound on ``tx_time``, milliseconds since the Unix epoch.
    audit_window_end
        Upper bound on ``tx_time``, milliseconds since the Unix epoch.

    Returns
    -------
    ``True`` iff every entry's content hash matches its stored ``id``
    AND every ``prev_hash`` references the previous entry's ``id``;
    ``False`` on any break. Empty window → ``True`` (vacuously).
    """
    entries = list(
        _audit_entries_in_window(
            db, start_ms=audit_window_start, end_ms=audit_window_end
        )
    )
    return verify_chain(entries)
