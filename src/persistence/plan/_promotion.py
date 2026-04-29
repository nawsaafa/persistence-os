"""Promotion gates G1+G2+G3+G4 + ``promote()`` orchestrator (v0.6.0a1).

A6 landed G1 (replay byte-identity) and G2 (audit-chain) as **internal
helpers**. A7 lands the remaining surface:

- :class:`PromotionRecord` — frozen+slots dataclass that carries the
  outcome of all four gates plus a content-addressed ``promotion_id``.
- :func:`gate_g3_score_delta` — score-delta gate (the value claim).
- :func:`gate_g4_stub` — operator-approval stub. Stream D's REPL (Module
  7) replaces the stub with a real operator-token path; Stream G's
  integration test asserts no stub markers before the v1.0.0 tag.
- :func:`promote` — orchestrates G1 → G2 → G3 → G4. On any gate failure
  raises :class:`persistence.plan.GateFailure` with a ``partial_record``
  attribute reflecting which gates ran (and what their results were)
  before the failure. Pure orchestration: no IO beyond what the gates
  themselves do (G1 calls the replay engine, G2 reads ``db.log()``).

Gate semantics — pinned by
``docs/plans/2026-04-28-v0.6.0a1-plan-execution-design.md`` §7 and
``docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md`` §2.A6 / §2.A7:

- **G1** — Replay byte-identity over held-out trajectories. The candidate
  Plan AST is replayed against ≥ ``min_count`` recorded trajectories;
  every replay must produce a counterfactual byte-identical to the
  factual. **One divergent byte = G1 fail.** Defends Prop 4.
- **G2** — Audit-chain unbroken over a tx_time window. Reconstructs
  ``AuditEntry`` instances from ``:audit/...`` datoms in the DB and
  feeds them to :func:`persistence.effect.verify_chain`. **Any chain
  break = G2 fail.** Defends Prop 2.
- **G3** — Score delta. ``optimized_score - baseline_score >= epsilon``
  → True. Sub-threshold deltas can't justify promotion; this is the
  value-claim gate.
- **G4** — Operator approval. Stream A ships a stub that always
  approves; Stream D wires it to a Module 7 REPL operator-token path.

``promote()`` runs **G1 → G2 → G3 → G4**. Cheap-to-expensive ordering
isn't the right axis here — G1 + G2 are the *correctness* gates (no
amount of value justifies promoting a candidate that can't replay or
sits on a broken audit chain), G3 is the value gate, G4 is the human-
in-the-loop gate that necessarily comes last. Either ordering works
under the contract (failures still surface a ``GateFailure`` with a
partial record); this one matches design-doc §7's enumeration order
and minimises late surprise (broken correctness should not be hidden
behind an early G3 win).

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
    docs/plans/2026-04-28-v0.6.0a1-plan-execution-impl.md §2.A6 / §2.A7
    src/persistence/effect/handlers/audit.py (verify_chain, datom_to_audit_entry)
    src/persistence/replay/engine.py (replay, compare)
"""
from __future__ import annotations

import hashlib
import json
import warnings
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol, runtime_checkable

from persistence.effect import datom_to_audit_entry, verify_chain
from persistence.effect.handlers.audit import AuditEntry
from persistence.fact.datom import Datom
from persistence.fact.db import DB
from persistence.plan._ast import Node
from persistence.plan._errors import GateFailure
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

#: Warning message emitted by G1 when called with an empty held-out
#: corpus. Pinned at module level so the test can match on a stable
#: substring without coupling to the full prose. Distinguishes the
#: empty-list branch from the (also-False) ``< min_count`` branch per
#: impl plan §2.A6 ("Empty trajectory list → False (and assert error
#: message)").
_G1_EMPTY_TRAJECTORIES_WARNING: str = (
    "G1 gate: empty held_out_trajectories — no coverage to claim Prop 4"
)


@runtime_checkable
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
    # Empty-list branch — distinguished signal so the caller can tell
    # "no corpus" apart from "below-threshold corpus". Both still return
    # False (Prop 4 cannot be claimed vacuously), but the empty case
    # almost always means the wiring is wrong rather than the candidate
    # is bad. Warning, not log, so callers can opt into ``pytest.warns``
    # / ``warnings.simplefilter("error")`` to escalate.
    if not held_out_trajectories:
        warnings.warn(_G1_EMPTY_TRAJECTORIES_WARNING, UserWarning, stacklevel=2)
        return False

    # Coverage gate next — reject below-threshold (but non-empty)
    # corpora before touching the replay engine. Prop 4's claim still
    # cannot be made under partial coverage.
    if len(held_out_trajectories) < min_count:
        return False

    for trajectory in held_out_trajectories:
        counterfactual = replay_engine.replay(candidate_plan, trajectory)
        diff = replay_engine.compare(trajectory, counterfactual)
        # Protocol contract: ``compare`` MUST return a dict containing
        # ``divergence_step``. A missing key is a wiring bug (not data
        # variation), so we raise rather than fall back to ``.get(...,
        # None)`` — silent fallback would let a malformed engine pass
        # G1 (R2 fix-pass W1.A).
        if _DIVERGENCE_KEY not in diff:
            raise TypeError(
                f"ReplayEngine.compare() returned dict without required "
                f"{_DIVERGENCE_KEY!r} key; got keys="
                f"{sorted(diff.keys())!r}"
            )
        if diff[_DIVERGENCE_KEY] is not None:
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

#: Warning message emitted by G2 when called over an empty audit
#: window. Symmetric with G1's empty-corpus warning: the gate returns
#: ``False`` (vacuous truth is not accepted at a correctness gate per
#: R2 fix-pass W1.C) and warns so callers can opt into ``pytest.warns``
#: to escalate. Pinned at module level so the test drift-pin and the
#: caller can match a stable substring.
_G2_EMPTY_WINDOW_WARNING: str = (
    "G2 gate: empty audit window — no audit entries to verify Prop 2"
)


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
    # the canonical pin. Missing ``:signature`` means the audit datom
    # was hand-rolled outside ``audit_entry_to_datom``; surface that as
    # a typed ValueError rather than producing a malformed wire dict
    # whose downstream G2 failure mode is opaque (R2 fix-pass W1.F-1).
    if ":signature" not in provenance:
        raise ValueError(
            "audit Datom missing required provenance[':signature'] — "
            "cannot reconstruct AuditEntry.id; check the audit handler "
            "wiring or the datom's provenance map"
        )
    audit_id = provenance[":signature"]
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
) -> Iterable[AuditEntry]:
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
    :func:`persistence.effect.verify_chain`. **An empty window
    returns False with a UserWarning** (R2 fix-pass W1.C) — vacuous
    truth is not accepted at a correctness gate. Symmetric with G1's
    empty-corpus handling: callers can ``pytest.warns(UserWarning)``
    to escalate the wiring bug, and a miscomputed window or a DB
    with zero audit datoms can no longer silently pass G2.

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
    ``True`` iff at least one audit entry falls in-window AND every
    entry's content hash matches its stored ``id`` AND every
    ``prev_hash`` references the previous entry's ``id``. ``False`` on
    any chain break, and ``False`` with a UserWarning when no audit
    entries fall in-window.
    """
    entries = list(
        _audit_entries_in_window(
            db, start_ms=audit_window_start, end_ms=audit_window_end
        )
    )
    if not entries:
        warnings.warn(_G2_EMPTY_WINDOW_WARNING, UserWarning, stacklevel=2)
        return False
    return verify_chain(entries)


# ---------------------------------------------------------------------------
# G3 — score delta
# ---------------------------------------------------------------------------


#: Default epsilon for the G3 score-delta gate. Pinned by impl plan
#: §2.A7 ("``optimized - baseline < epsilon`` → fail"). Exposed as a
#: module constant so the test drift-pin and the design doc reference
#: the same source.
_DEFAULT_G3_EPSILON: float = 0.05


def gate_g3_score_delta(
    optimized_score: float,
    baseline_score: float,
    *,
    epsilon: float = _DEFAULT_G3_EPSILON,
) -> bool:
    """G3 — ``optimized - baseline >= epsilon`` → True (value claim).

    The value-claim gate. Sub-threshold deltas (including negative
    deltas) can't justify promotion regardless of how cleanly G1 + G2
    pass. Boundary semantics: ``>=`` accepts exactly at ``epsilon`` —
    flipping to ``>`` would make the contract noisy at the threshold,
    and the design doc's "≥ epsilon" wording is canonical.

    Parameters
    ----------
    optimized_score
        Score of the candidate (post-optimization) plan on the
        validation set. Same metric as ``baseline_score``.
    baseline_score
        Score of the pre-optimization (or current production) plan on
        the same validation set.
    epsilon
        Minimum positive delta. Default ``0.05`` per impl plan §2.A7.

    Returns
    -------
    ``True`` iff ``optimized_score - baseline_score >= epsilon``;
    ``False`` otherwise.
    """
    return (optimized_score - baseline_score) >= epsilon


# ---------------------------------------------------------------------------
# G4 — operator approval (stub for Stream A; replaced by Stream D)
# ---------------------------------------------------------------------------


#: Stream A's G4 stub-approver name. Pinned at module level so the
#: Stream G integration test can scan persisted promotion records for
#: this exact string before the v1.0.0 tag and reject any that still
#: carry it.
_G4_STUB_APPROVER: str = "stub"

#: Stream A's G4 stub rationale. Pinned for the same reason as
#: ``_G4_STUB_APPROVER`` — Stream G's regression test compares
#: verbatim.
_G4_STUB_RATIONALE: str = "Stream A stub — Stream D replaces"


def gate_g4_stub() -> dict:
    """G4 stub — always approves. Stream D's REPL replaces.

    The substrate-level operator-approval gate. Real wiring lands in
    Stream D (Module 7 REPL): an operator-token capability check on
    ``persistence.repl`` that reads the operator's signed promote
    request, verifies the token, and returns
    ``{"approved": True, "approver": "<token-subject>", "rationale":
    "<operator note>"}`` (or False with a denial rationale).

    The stub returns a fixed-shape dict so callers can substitute the
    real implementation by passing ``g4_fn=<real-fn>`` to
    :func:`promote` once Stream D ships, with no contract drift.

    Returns
    -------
    Exactly:
    ``{"approved": True, "approver": "stub",
       "rationale": "Stream A stub — Stream D replaces"}``.

    Stream G integration test before the v1.0.0 tag MUST reject any
    persisted ``PromotionRecord`` whose ``g4_approver == "stub"`` —
    that's the marker.
    """
    return {
        "approved": True,
        "approver": _G4_STUB_APPROVER,
        "rationale": _G4_STUB_RATIONALE,
    }


# ---------------------------------------------------------------------------
# PromotionRecord + promote() orchestrator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PromotionRecord:
    """Outcome of a promotion attempt — all four gates' results.

    Frozen so the caller can hash / cache / log without worrying about
    silent mutation; ``slots=True`` saves the dataclass dict footprint
    (one record per promotion, but each record is small and hot in
    Module 7 REPL listings).

    Field-by-field semantics:

    - ``candidate_plan_id``: the Plan AST's content hash (``Node.id``).
      Together with the gate fields this is the input to the
      ``promotion_id`` canonical hash.
    - ``g1_*``: G1 outcome. ``g1_held_out_count`` is the size of the
      held-out corpus the gate ran against (NOT the count of replays
      that succeeded — G1 short-circuits on first divergence).
    - ``g2_audit_chain_verified``: G2 outcome.
    - ``g3_score_delta``: ``optimized_score - baseline_score`` as
      observed at gate time. Recorded even on G3 failure so the
      partial record carries the actual delta.
    - ``g3_score_threshold``: the ``epsilon`` G3 was run with.
    - ``g4_*``: G4 outcome. ``g4_approver`` is ``"stub"`` in Stream A;
      Stream D's REPL replaces with the operator token's subject.
    - ``promoted_at``: caller-supplied milliseconds-since-epoch (no
      ``time.time()`` fallback — repo discipline, Phase B precedent).
    - ``promotion_id``: sha256 hex of canonical-JSON over the dict
      pinned by impl plan §2.A7 lines 540–553. Deterministic: same
      gate outcomes + same plan + same timestamp → same id.

    Drift-pin to A5: this dataclass MUST satisfy
    :class:`persistence.plan._skill_library._PromotionRecordLike`
    structurally — A5 only reads ``promotion_id``, but the
    runtime_checkable protocol is what
    :class:`persistence.plan.SkillLibrary` types its
    ``register(promotion_record=...)`` argument against, and A7's
    drift-pin test asserts the isinstance relation.
    """

    candidate_plan_id: str
    g1_replay_byte_identity: bool
    g1_held_out_count: int
    g2_audit_chain_verified: bool
    g3_score_delta: float
    g3_score_threshold: float
    g4_approver: str
    g4_approved: bool
    g4_rationale: str
    promoted_at: int
    promotion_id: str


def _compute_promotion_id(
    *,
    candidate_plan_id: str,
    g1_replay_byte_identity: bool,
    g1_held_out_count: int,
    g2_audit_chain_verified: bool,
    g3_score_delta: float,
    g3_score_threshold: float,
    g4_approver: str,
    g4_approved: bool,
    g4_rationale: str,
    promoted_at: int,
) -> str:
    """SHA-256 hex of canonical-JSON over the impl-plan §2.A7 dict.

    Same canonical-JSON rule as :class:`Node.id` and
    :func:`persistence.plan._optimize._compute_optimizer_call_hash`
    (sort_keys, separators=(",",":"), allow_nan=False). The digest is
    stable across runs and machines — same gate outcomes + same plan +
    same timestamp → same ``promotion_id``.

    Returns
    -------
    64-char lowercase hex digest.
    """
    payload = {
        "candidate_plan_id": candidate_plan_id,
        "g1_replay_byte_identity": g1_replay_byte_identity,
        "g1_held_out_count": g1_held_out_count,
        "g2_audit_chain_verified": g2_audit_chain_verified,
        "g3_score_delta": g3_score_delta,
        "g3_score_threshold": g3_score_threshold,
        "g4_approver": g4_approver,
        "g4_approved": g4_approved,
        "g4_rationale": g4_rationale,
        "promoted_at": promoted_at,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _build_promotion_record(
    *,
    candidate_plan_id: str,
    g1_replay_byte_identity: bool,
    g1_held_out_count: int,
    g2_audit_chain_verified: bool,
    g3_score_delta: float,
    g3_score_threshold: float,
    g4_approver: str,
    g4_approved: bool,
    g4_rationale: str,
    promoted_at: int,
) -> PromotionRecord:
    """Build a :class:`PromotionRecord` with the canonical ``promotion_id``.

    Helper so :func:`promote` can build both successful and partial
    records through one site — keeps the hash-input dict in one place,
    matching :func:`_compute_promotion_id`.
    """
    promotion_id = _compute_promotion_id(
        candidate_plan_id=candidate_plan_id,
        g1_replay_byte_identity=g1_replay_byte_identity,
        g1_held_out_count=g1_held_out_count,
        g2_audit_chain_verified=g2_audit_chain_verified,
        g3_score_delta=g3_score_delta,
        g3_score_threshold=g3_score_threshold,
        g4_approver=g4_approver,
        g4_approved=g4_approved,
        g4_rationale=g4_rationale,
        promoted_at=promoted_at,
    )
    return PromotionRecord(
        candidate_plan_id=candidate_plan_id,
        g1_replay_byte_identity=g1_replay_byte_identity,
        g1_held_out_count=g1_held_out_count,
        g2_audit_chain_verified=g2_audit_chain_verified,
        g3_score_delta=g3_score_delta,
        g3_score_threshold=g3_score_threshold,
        g4_approver=g4_approver,
        g4_approved=g4_approved,
        g4_rationale=g4_rationale,
        promoted_at=promoted_at,
        promotion_id=promotion_id,
    )


def _raise_gate_failure(message: str, partial_record: PromotionRecord) -> None:
    """Raise :class:`GateFailure` carrying ``partial_record`` as an attr.

    :class:`GateFailure` accepts ``partial_record`` in its ``__init__``
    (R2 fix-pass W1.F-2: typed class-level attribute, no dynamic-attr
    type-ignore at the call site). Callers read it via
    ``exc.partial_record``.
    """
    raise GateFailure(message, partial_record)


def promote(
    candidate_plan: Node,
    *,
    db: DB,
    optimized_score: float,
    baseline_score: float,
    held_out_trajectories: list[Trajectory],
    replay_engine: ReplayEngine,
    audit_window: tuple[int, int],
    epsilon: float = _DEFAULT_G3_EPSILON,
    min_g1_count: int = _DEFAULT_MIN_COUNT,
    promoted_at_ms: int,
    g4_fn: Callable[[], dict] = gate_g4_stub,
) -> PromotionRecord:
    """Run all four promotion gates and return a :class:`PromotionRecord`.

    Order: **G1 → G2 → G3 → G4**. The first gate to fail aborts with a
    :class:`GateFailure` whose ``partial_record`` attribute carries the
    state of the gates that ran (the failing gate's outcome is recorded
    on the partial record; gates that did not run keep their default
    sentinel — ``False`` for bools, ``0.0`` / ``0`` for the score-delta
    fields).

    Pure orchestration. No IO beyond:

    - G1 calls ``replay_engine.replay`` / ``replay_engine.compare`` per
      held-out trajectory.
    - G2 walks ``db.log()`` once.

    No new datoms are written. Persisting the result is the caller's
    job (typically :meth:`persistence.plan.SkillLibrary.register`).

    Parameters
    ----------
    candidate_plan
        The Plan AST under evaluation.
    db
        Fact store for G2's audit-chain check. Read-only here.
    optimized_score
        Candidate's score on the validation set (G3 input).
    baseline_score
        Pre-optimization plan's score on the same set (G3 input).
    held_out_trajectories
        Recorded trajectories for G1's byte-identity check.
    replay_engine
        Anything satisfying :class:`ReplayEngine`. Production callers
        pass an adapter around :mod:`persistence.replay`; tests pass a
        stub.
    audit_window
        ``(start_ms, end_ms)`` inclusive — milliseconds since the Unix
        epoch — for G2's chain check.
    epsilon
        G3 threshold. Default ``0.05`` (impl plan §2.A7).
    min_g1_count
        G1 corpus minimum. Default ``10`` (design doc §7).
    promoted_at_ms
        Caller-supplied promotion timestamp. **Keyword-only, no
        default** — repo discipline forbids ``time.time()`` fallback.
        Source TBD by Module 7 REPL or a ``:clock/now`` effect handler.
    g4_fn
        G4 callable. Default :func:`gate_g4_stub` (Stream A); Stream D
        passes a real operator-token check. Must return a dict with
        keys ``approved`` (bool), ``approver`` (str), ``rationale`` (str).

    Returns
    -------
    PromotionRecord
        On all-gates-pass.

    Raises
    ------
    GateFailure
        On any gate failure. The exception's ``partial_record``
        attribute carries the state at failure-time.
    """
    candidate_plan_id = candidate_plan.id
    audit_window_start, audit_window_end = audit_window

    # G1 ---------------------------------------------------------------
    g1_count = len(held_out_trajectories)
    g1_pass = gate_g1_replay_byte_identity(
        candidate_plan,
        held_out_trajectories=held_out_trajectories,
        replay_engine=replay_engine,
        min_count=min_g1_count,
    )
    if not g1_pass:
        partial = _build_promotion_record(
            candidate_plan_id=candidate_plan_id,
            g1_replay_byte_identity=False,
            g1_held_out_count=g1_count,
            g2_audit_chain_verified=False,
            g3_score_delta=0.0,
            g3_score_threshold=epsilon,
            g4_approver="",
            g4_approved=False,
            g4_rationale="",
            promoted_at=promoted_at_ms,
        )
        _raise_gate_failure("G1 replay byte-identity failed", partial)

    # G2 ---------------------------------------------------------------
    g2_pass = gate_g2_audit_chain(
        db,
        audit_window_start=audit_window_start,
        audit_window_end=audit_window_end,
    )
    if not g2_pass:
        partial = _build_promotion_record(
            candidate_plan_id=candidate_plan_id,
            g1_replay_byte_identity=True,
            g1_held_out_count=g1_count,
            g2_audit_chain_verified=False,
            g3_score_delta=0.0,
            g3_score_threshold=epsilon,
            g4_approver="",
            g4_approved=False,
            g4_rationale="",
            promoted_at=promoted_at_ms,
        )
        _raise_gate_failure("G2 audit chain failed", partial)

    # G3 ---------------------------------------------------------------
    g3_delta = optimized_score - baseline_score
    g3_pass = gate_g3_score_delta(
        optimized_score, baseline_score, epsilon=epsilon
    )
    if not g3_pass:
        partial = _build_promotion_record(
            candidate_plan_id=candidate_plan_id,
            g1_replay_byte_identity=True,
            g1_held_out_count=g1_count,
            g2_audit_chain_verified=True,
            # Record the actual delta so the caller can see how far
            # below threshold the candidate landed — debugging signal.
            g3_score_delta=g3_delta,
            g3_score_threshold=epsilon,
            g4_approver="",
            g4_approved=False,
            g4_rationale="",
            promoted_at=promoted_at_ms,
        )
        _raise_gate_failure("G3 score delta below threshold", partial)

    # G4 ---------------------------------------------------------------
    # The g4_fn contract: dict with keys ``approved`` (bool),
    # ``approver`` (str), ``rationale`` (str). Stream A's stub is the
    # default; Stream D's REPL operator-token check substitutes here.
    g4_result = g4_fn()
    # R2 fix-pass W1.B: enforce strict bool. ``bool(...)`` coercion
    # would let truthy non-bools (e.g. the string "False", non-empty
    # dicts) silently approve. The g4_fn contract is bool, not bool-ish.
    g4_approved_raw = g4_result["approved"]
    if not isinstance(g4_approved_raw, bool):
        raise TypeError(
            f"g4_fn returned 'approved' of type "
            f"{type(g4_approved_raw).__name__}; expected bool"
        )
    g4_approved = g4_approved_raw
    g4_approver = str(g4_result["approver"])
    g4_rationale = str(g4_result["rationale"])
    if not g4_approved:
        partial = _build_promotion_record(
            candidate_plan_id=candidate_plan_id,
            g1_replay_byte_identity=True,
            g1_held_out_count=g1_count,
            g2_audit_chain_verified=True,
            g3_score_delta=g3_delta,
            g3_score_threshold=epsilon,
            g4_approver=g4_approver,
            g4_approved=False,
            g4_rationale=g4_rationale,
            promoted_at=promoted_at_ms,
        )
        _raise_gate_failure("G4 not approved", partial)

    # All four gates passed.
    return _build_promotion_record(
        candidate_plan_id=candidate_plan_id,
        g1_replay_byte_identity=True,
        g1_held_out_count=g1_count,
        g2_audit_chain_verified=True,
        g3_score_delta=g3_delta,
        g3_score_threshold=epsilon,
        g4_approver=g4_approver,
        g4_approved=True,
        g4_rationale=g4_rationale,
        promoted_at=promoted_at_ms,
    )
