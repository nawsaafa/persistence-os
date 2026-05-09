"""Phase 2.3d — REPL Steering Integration.

The `_CoderSteeringSession` class turns a persistence-coder Coder loop into a
live debugger. Seven ops expose the agent's decision boundary to a REPL
operator: pause / resume / snapshot / context_at / branch / fold / commit.

Design doc: docs/plans/2026-05-09-phase-2.3d-repl-steering-design.md (FROZEN
at edcb61d, ARIS R1 PASS 8.31/8.0).

LD-1 (codex consensus-locked): branch durability via db.branch(fork_at, [Δ]).
LD-2 (R0-fold B1): threading.Event semantics — set=go, clear=block.
LD-3 (R0-fold B5): Capability(op="coder", qualifier="read|write|any") extended.
LD-4: :repl/request + :repl/response per op; :coder/branch agent-side for branch.
LD-5 (FD-LD5): coder.fold is session-side dict iteration, NOT s.txn.fold.
"""
from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from persistence.fact.db import DB, DBView
    from persistence.coder._session import Coder
    from persistence.repl._caps import CapabilitySet


@dataclass
class _CoderSteeringSession:
    """Operator-side session class for live coder steering.

    NOT frozen (mutable state: pause event, branches dict, active branch_id).
    Distinct from REPL's ``Session`` (frozen, transport-level). Composes a
    Coder reference for capability gating + active_db management.

    LD-2 / R0-fold B1 — threading.Event semantics:
    - ``_pause_event.set()`` = NOT paused (DEFAULT STATE). ``wait()``
      returns immediately.
    - ``_pause_event.clear()`` = paused. ``wait()`` blocks.
    - ``pause()`` calls ``clear()``, ``resume()`` calls ``set()``,
      ``_check_pause()`` calls ``wait()``.
    """

    coder: "Coder"
    branches: dict[str, "DB"] = field(default_factory=dict)
    active_branch_id: str = "parent"  # default: agent runs in parent branch
    cap_set: "CapabilitySet | None" = None  # LD-3 / R0-fold B5: None = trusted in-process
    _pause_event: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        # LD-2 / R0-fold B1 — default state is SET (not paused). Event SET
        # means _pause_event.wait() returns immediately; CLEARED blocks.
        self._pause_event.set()

        # Bind self onto the coder so Coder._check_pause() can find it.
        self.coder._steering_session = self

    # ---- LD-2: pause / resume / _check_pause ------------------------------

    def pause(self) -> None:
        """Clear the event so the next _check_pause() call blocks at iter head.

        Idempotent: calling pause() when already paused is a no-op.
        Requires (coder, read). Emits :repl/request BEFORE the clear
        (boundary open) and :repl/response AFTER (boundary close) per LD-4.
        """
        self._check_cap("read")
        self._emit_repl_request("pause", {})
        self._pause_event.clear()
        self._emit_repl_response("pause", None)

    def resume(self) -> None:
        """Set the event so any _check_pause() currently blocked unblocks.

        Idempotent: calling resume() when not paused is a no-op.
        Requires (coder, read). Emits :repl/request BEFORE / :repl/response
        AFTER, bracketing the actual ``set()`` per LD-4.
        """
        self._check_cap("read")
        self._emit_repl_request("resume", {})
        self._pause_event.set()
        self._emit_repl_response("resume", None)

    def _check_pause(self) -> None:
        """Called from Coder.run() at iteration head, BEFORE _observe().

        Blocks on ``wait()`` while the event is cleared (paused state);
        returns immediately while the event is set (default / resumed state).
        """
        self._pause_event.wait()

    # ---- LD-3 (R0-fold B5): capability gating ----------------------------

    def _check_cap(self, qualifier: str) -> None:
        """Verify the session's ``cap_set`` permits this op qualifier.

        Raises ``_OpError(ERR_CAPABILITY_DENIED)`` if missing.
        ``cap_set=None`` is the trusted in-process caller default — no
        check (production REPL adapters wire a real CapabilitySet at
        session construction; in-process unit tests + 2.3d-local code
        paths construct without one).

        R0-fold B5: closed-set ``Capability(op="coder", qualifier=...)``
        on top of ADR-3's ``QUALIFIERS_BY_OP``. The union ``"any"`` grants
        both ``"read"`` and ``"write"``. Lazy-imports the REPL types so
        ``_steering.py`` does NOT pull aiohttp / repl-stack into the
        coder package's import-graph at module load.

        IMPORTANT: cap denial fires BEFORE audit emission — denied ops
        never happened on the audit chain.
        """
        if self.cap_set is None:
            return
        from persistence.repl._caps import Capability
        from persistence.repl._protocol import ERR_CAPABILITY_DENIED
        from persistence.repl._ws import _OpError

        cap = Capability(op="coder", qualifier=qualifier)
        cap_any = Capability(op="coder", qualifier="any")
        if cap not in self.cap_set.caps and cap_any not in self.cap_set.caps:
            raise _OpError(
                ERR_CAPABILITY_DENIED,
                f"missing coder/{qualifier} capability",
            )

    # ---- LD-4: :repl/request + :repl/response audit emission -------------

    def _emit_repl_request(
        self,
        op_name: str,
        payload: "dict[str, Any]",
    ) -> None:
        """Emit ``:repl/request`` BEFORE the op body executes.

        LD-4 / R0-fold I3: ``:repl/request`` opens the boundary; the action
        body runs between request and response so the pair brackets the
        action (T9.1-fold B3 + I1: split request/response around the action,
        not adjacent post-action). Recorded payload is canonical-hashed
        into ``args_hash`` for byte-identity replay.
        """
        self.coder.substrate.effect.perform(
            ":repl/request", {"op": op_name, "payload": payload},
        )

    def _emit_repl_response(
        self,
        op_name: str,
        result: "Any | None" = None,
    ) -> None:
        """Emit ``:repl/response`` AFTER the op body executes.

        LD-4: closes the boundary opened by :meth:`_emit_repl_request`.
        ``result`` is a small summary dict (e.g. ``{"branch_id": ...}``,
        ``{"n_returned": ...}``) — never the full datom list.

        FD-T6.1 (pathway choice): Option A — :repl/response is in
        CANONICAL_AUDIT_WRAPPED_OPS + CANONICAL_AUDIT_RAW_OPS. The
        audit middleware automatically wraps these ops on
        substrate.effect.perform; the raw terminator (audit-only no-op
        clause) provides the bottom-of-stack handler so perform doesn't
        raise Unhandled. This is the established 2.1c.6 :claim/emit /
        :blob/put pattern.
        """
        self.coder.substrate.effect.perform(
            ":repl/response", {"op": op_name, "result": result},
        )

    # ---- LD-0 LD-2: snapshot + context_at (read-side, branch_id kwarg) ----

    def _resolve_db(self, branch_id: str) -> "DB":
        """Resolve ``branch_id`` to the DB.

        - ``"active"`` → resolves to ``self.active_branch_id`` (the
          currently-selected branch; defaults to ``"parent"``).
        - ``"parent"`` → reserved for the original substrate DB
          (``self.coder.substrate._db``).
        - any other string → key in ``self.branches`` registered by
          ``branch()`` (LD-1; T4).

        Raises ``KeyError`` on unknown branch ids.
        """
        if branch_id == "active":
            branch_id = self.active_branch_id
        if branch_id == "parent":
            return self.coder.substrate._db
        if branch_id not in self.branches:
            raise KeyError(f"unknown branch_id: {branch_id!r}")
        return self.branches[branch_id]

    def snapshot(self, n: int = 50, branch_id: str = "active") -> list[Any]:
        """Return the last ``n`` datoms emitted on the selected branch.

        R0-fold B3 invariant: read-only — does NOT mutate any DB. The
        underlying ``DB.log()`` returns an iterator over the immutable
        store; we materialize it and slice the tail.

        R0-fold I2: ``branch_id`` selects ``"active"`` (default),
        ``"parent"``, or a registered child key in ``self.branches``.

        Requires (coder, read).
        """
        self._check_cap("read")
        self._emit_repl_request("snapshot", {"n": n, "branch_id": branch_id})
        db = self._resolve_db(branch_id)
        all_datoms = list(db.log())
        result = all_datoms[-n:]
        self._emit_repl_response("snapshot", {"n_returned": len(result)})
        return result

    def context_at(self, t: dt.datetime, branch_id: str = "active") -> "DBView":
        """Return the ``DBView`` at transaction time ``t`` for the
        selected branch.

        R0-fold B3 invariant: read-only — ``DB.as_of(t)`` builds a fresh
        ``DBView`` from a filter over the store; the underlying store is
        unchanged.

        R0-fold I2: ``branch_id`` kwarg as in :meth:`snapshot`.

        Requires (coder, read).
        """
        self._check_cap("read")
        self._emit_repl_request(
            "context_at", {"t": t.isoformat(), "branch_id": branch_id},
        )
        db = self._resolve_db(branch_id)
        view = db.as_of(t)
        self._emit_repl_response("context_at", {"n_datoms": len(view.datoms)})
        return view

    # ---- LD-1: branch via db.branch() ------------------------------------

    def _derive_branch_id(
        self,
        directive: dict,
        fork_at: dt.datetime,
    ) -> str:
        """Deterministic branch_id from (parent_branch_id, fork_at, directive).

        Replay-pinable: same (parent, fork_at, directive) always derives the
        same branch_id. 16-hex-char prefix of SHA-256.
        """
        import hashlib

        from persistence.effect.canonical import canonical_dumps as _cd

        directive_canon = _cd(directive)
        directive_str = (
            directive_canon if isinstance(directive_canon, str)
            else directive_canon.decode("utf-8")
        )
        seed = f"{self.active_branch_id}:{fork_at.isoformat()}:{directive_str}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]

    def branch(
        self,
        directive: dict,
        *,
        _fork_at_override: "dt.datetime | None" = None,
    ) -> str:
        """Open a counterfactual branch via ``db.branch(fork_at,
        [directive_assertion])``.

        LD-1 (codex consensus): substrate-true branch using the existing
        ``db.branch()`` primitive at ``db.py:543-582``. Branched DB is
        registered in ``self.branches`` by a deterministic ``branch_id``
        derived from ``(parent_branch_id, fork_at, directive)``.

        R0-fold I3: ``fork_at`` is read from agent-side wall-clock at THIS
        entry point (transition to substrate ``:sys/now`` W3-rescoped to
        2.4a, joining the existing ``:sys/now`` bundle from 2.3b). Recorded
        in ``:repl/request`` for byte-identity replay (T6).

        Args:
            directive: dict payload describing what the operator wants the
                agent to consider in the branch. Inserted as a fact via
                ``db.branch(fork_at, [{"e": "directive-<id>",
                "a": ":directive", "v": canonical_dumps(directive),
                "valid_from": fork_at}])``.
            _fork_at_override: TEST-ONLY hook for determinism tests.
                Production callers must NOT pass this; it's part of the
                test surface only.

        Returns:
            branch_id: 16-hex-char deterministic id; key into
                ``self.branches``.

        Requires (coder, write).
        """
        self._check_cap("write")
        from persistence.effect.canonical import canonical_dumps

        if _fork_at_override is not None:
            fork_at = _fork_at_override
        else:
            # R0-fold I3: source from agent-side wall-clock until :sys/now lands
            # (W3 rescope to 2.4a, joining existing :sys/now bundle).
            fork_at = dt.datetime.now(dt.timezone.utc)  # noqa: wall-clock — :sys/now W3 rescope to 2.4a

        branch_id = self._derive_branch_id(directive, fork_at)

        # T9.1-fold B3: emit :repl/request FIRST so the audit stream reads
        # request → action (db.branch + :coder/branch) → response. R0-fold I3:
        # :repl/request payload records fork_at explicitly for byte-identity
        # replay (full byte-identity W3-rescoped to 2.4a per :sys/now).
        self._emit_repl_request(
            "branch",
            {"directive": directive, "fork_at": fork_at.isoformat()},
        )

        directive_canon = canonical_dumps(directive)
        directive_str = (
            directive_canon if isinstance(directive_canon, str)
            else directive_canon.decode("utf-8")
        )
        directive_assertion = {
            "e": f"directive-{branch_id}",
            "a": ":directive",
            "v": directive_str,
            "valid_from": fork_at,
        }

        # Source DB: the currently-active branch (defaults to parent).
        source_db = self._resolve_db(self.active_branch_id)
        new_db = source_db.branch(fork_at, [directive_assertion])

        self.branches[branch_id] = new_db

        # LD-4: agent-side commit datom on parent's audit chain. Recorded as
        # :coder/branch fact so a downstream replayer can reconstruct the
        # branch graph from the parent's log alone (the branch DBs themselves
        # are session-scoped). Performed via substrate.effect.perform so the
        # audit middleware emits an AuditEntry with op=":coder/branch"; there
        # is no separate handler, so we add :coder/branch to the canonical
        # audit ops alongside :repl/*. If the audit pathway fails, the
        # branch() call fails — the steerability contract.
        self.coder.substrate.effect.perform(
            ":coder/branch",
            {
                "branch_id": branch_id,
                "fork_at": fork_at.isoformat(),
                "directive": directive_str,
            },
        )

        # T9.1-fold B3: :repl/response closes the boundary AFTER the action.
        self._emit_repl_response("branch", {"branch_id": branch_id})
        return branch_id

    # ---- LD-5 (FD-LD5): fold(probe) — session-side, NOT s.txn.fold ------

    def fold(self, probe: Callable[["DB"], Any]) -> dict[str, Any]:
        """Run ``probe(db)`` over parent + all child branches; return a dict
        of scores keyed by branch_id.

        FD-LD5: deviates from § 3.6 line 241 ("uses #145 surface
        ``s.txn.fold``"). Session-side dict iteration. Rationale:
        ``s.txn.fold`` emits facts on every iteration; ``coder.fold`` is
        read-only scoring, so we cannot use the substrate fold primitive
        here. v0.10.x rescope: substrate primitive change to allow
        ``s.txn.fold`` with NULL fact-list path.

        R0-fold B4: the parent IS included via the reserved ``"parent"``
        key. Returns a deterministic dict — Python 3.7+ dict iteration
        order is insertion order, and ``"parent"`` is inserted FIRST.

        Read-only: probe is expected to inspect each DB without mutating
        it. The session does not enforce immutability — that contract is
        the caller's responsibility.

        Requires (coder, write) — even though probes are read-only, fold
        is a search/scoring primitive that operators classify as write
        per the LD-3 mapping table.
        """
        self._check_cap("write")
        # T9.1-fold I1: emit :repl/request BEFORE the fold body. The probe
        # is a Python callable that doesn't survive serialization; we
        # record the branch_ids that WILL be covered (parent + current
        # children) so the request payload is complete. Replay must
        # re-derive the probe at the boundary (out-of-scope for 2.3d).
        scheduled_branch_ids = ["parent"] + list(self.branches.keys())
        self._emit_repl_request("fold", {"branch_ids": scheduled_branch_ids})
        scores: dict[str, Any] = {}
        # Parent first (reserved key).
        scores["parent"] = probe(self.coder.substrate._db)
        # Children in self.branches insertion order.
        for branch_id, branch_db in self.branches.items():
            scores[branch_id] = probe(branch_db)
        self._emit_repl_response("fold", {"n_scores": len(scores)})
        return scores

    # ---- LD-1: commit(branch_id) — session pointer swap ------------------

    def commit(self, branch_id: str) -> None:
        """Promote a branch (or ``"parent"``) as the new active branch.

        LD-1 substrate-true: session-level pointer swap, NOT a substrate
        merge. The previous active branch's DB stays alive in
        ``self.branches`` (or as parent), so it remains addressable via
        ``context_at(branch_id=...)`` for the rest of the session.

        Raises ``KeyError`` if ``branch_id`` is unknown (not ``"parent"``
        and not a registered key in ``self.branches``).

        Requires (coder, write).
        """
        self._check_cap("write")
        # T9.1-fold I1: validate FIRST (KeyError must precede any audit
        # emission — denied/invalid ops never happened). Then emit
        # :repl/request, swap the pointer, emit :repl/response.
        if branch_id != "parent" and branch_id not in self.branches:
            raise KeyError(f"unknown branch_id: {branch_id!r}")
        self._emit_repl_request("commit", {"branch_id": branch_id})
        self.active_branch_id = branch_id
        self._emit_repl_response("commit", None)
