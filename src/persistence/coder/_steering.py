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

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from persistence.fact.db import DB
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
