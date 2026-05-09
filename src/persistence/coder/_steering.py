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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from persistence.fact.db import DB, DBView
    from persistence.coder._session import Coder


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
        """
        self._pause_event.clear()

    def resume(self) -> None:
        """Set the event so any _check_pause() currently blocked unblocks.

        Idempotent: calling resume() when not paused is a no-op.
        """
        self._pause_event.set()

    def _check_pause(self) -> None:
        """Called from Coder.run() at iteration head, BEFORE _observe().

        Blocks on ``wait()`` while the event is cleared (paused state);
        returns immediately while the event is set (default / resumed state).
        """
        self._pause_event.wait()

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
        """
        db = self._resolve_db(branch_id)
        all_datoms = list(db.log())
        return all_datoms[-n:]

    def context_at(self, t: dt.datetime, branch_id: str = "active") -> "DBView":
        """Return the ``DBView`` at transaction time ``t`` for the
        selected branch.

        R0-fold B3 invariant: read-only — ``DB.as_of(t)`` builds a fresh
        ``DBView`` from a filter over the store; the underlying store is
        unchanged.

        R0-fold I2: ``branch_id`` kwarg as in :meth:`snapshot`.
        """
        db = self._resolve_db(branch_id)
        return db.as_of(t)

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
        """
        from persistence.effect.canonical import canonical_dumps

        if _fork_at_override is not None:
            fork_at = _fork_at_override
        else:
            # R0-fold I3: source from agent-side wall-clock until :sys/now lands
            # (W3 rescope to 2.4a, joining existing :sys/now bundle).
            fork_at = dt.datetime.now(dt.timezone.utc)  # noqa: wall-clock — :sys/now W3 rescope to 2.4a

        branch_id = self._derive_branch_id(directive, fork_at)

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
        return branch_id
