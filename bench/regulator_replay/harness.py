"""Regulator-replay reconstruction harness.

STATUS: STUB. Implementation lands with Phase 4 of the memory-palace
bitemporal retrofit (~2026-05).

Planned shape::

    from persistence.fact import DB
    from persistence.effect.handlers.audit import verify_chain

    def reconstruct(trajectory_path: Path) -> ReconstructionReport:
        '''Re-run every :vault/recall entry against db.as_of(tx_time) and
        assert the recomputed result_hash matches the recorded one.
        Returns pass/fail per entry and aggregate stats.'''
        raise NotImplementedError("scheduled for Phase 4 of retrofit")


The harness is what the paper §6.3 walkthrough invokes. Output format is a
markdown report written to ``reports/<seed>.md`` summarising:

- Total audit entries replayed.
- Per-entry reconstruction pass/fail.
- Aggregate reconstruction rate (target: 100% for untampered logs).
- Tamper-detection matrix: for each entry, flip one byte and assert
  ``verify_chain`` catches it.
- Latency per reconstruction (so the paper can cite a number).

Implementation blocker: none. Ships alongside ``generator.py``.
"""

from __future__ import annotations


def reconstruct(trajectory_path) -> dict:
    """Placeholder — implementation scheduled for Phase 4."""
    _ = trajectory_path  # reserved for Phase 4 implementation
    raise NotImplementedError(
        "bench/regulator_replay/harness.py is a scaffold; "
        "implementation lands with Phase 4 of memory-palace-bitemporal-retrofit"
    )


if __name__ == "__main__":
    raise SystemExit(
        "harness.py is not yet implemented. See README.md for scope and Phase 4 schedule."
    )
