"""Synthetic agent-trajectory generator for the regulator-replay benchmark.

STATUS: STUB. Implementation lands with Phase 4 of the memory-palace
bitemporal retrofit (~2026-05). The scaffold exists so ``paper §6.3`` has
a real citation path; see ``README.md``.

Planned shape::

    from persistence.fact import DB
    from persistence.effect import Runtime, make_audit_handler

    def generate_trajectory(seed: int, length: int = 40) -> Trajectory:
        '''Deterministic synthetic agent trajectory.

        Generates ``length`` operations (remember / recall / llm_call /
        retract) over a bounded entity set, seeded by ``seed``. Returns a
        ``Trajectory`` bundle of (db, audit_entries, expected_result_hashes).
        '''
        raise NotImplementedError("scheduled for Phase 4 of retrofit")


Design notes for the implementor:

- 10 trajectories, seeds 0..9. Each trajectory is a separate ``DB`` instance
  backed by :class:`persistence.fact.InMemoryStore`.
- Ops include at least one retraction per trajectory, at least one
  tier-changing correction (L2 → L1, say), and at least one retroactive
  correction with ``force_retroactive=True`` (so the benchmark exercises
  the ``RetroactiveCorrectionError`` opt-in path).
- Every op is wrapped in a ``persistence.effect.Runtime`` with the audit
  handler; the resulting ``AuditEntry`` list is serialized to
  ``trajectories/<seed>.audit.jsonl``.
- The final ``DB.log()`` snapshot is serialized to
  ``trajectories/<seed>.log.jsonl`` for reconstruction replay.

Implementation blocker: none — all primitives shipped in
``persistence.fact`` v0.1.0a1 and ``persistence.effect`` v0.1.0a1.
"""

from __future__ import annotations


def generate_trajectory(seed: int, length: int = 40) -> dict:
    """Placeholder — implementation scheduled for Phase 4.

    Raises ``NotImplementedError``. Present so import paths in the harness
    are valid and tests can ``pytest.importorskip`` on it until populated.
    """
    _ = (seed, length)  # reserved for Phase 4 implementation
    raise NotImplementedError(
        "bench/regulator_replay/generator.py is a scaffold; "
        "implementation lands with Phase 4 of memory-palace-bitemporal-retrofit"
    )


if __name__ == "__main__":
    raise SystemExit(
        "generator.py is not yet implemented. See README.md for scope and Phase 4 schedule."
    )
