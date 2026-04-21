"""DB + DBView — the functional query surface over the datom log.

See ``docs/agent1-fact-spec.md`` §2 for the intended API and §8 for the
reference 76-line prototype. This module is that prototype evolved to (a)
sit on top of a pluggable :class:`~persistence.fact.store.Store`, and (b)
enforce the invariants the paper §4.1 formalizes:

  - asOf(D, t)      = {d ∈ D | τ_sys(d) ≤ t}
  - validAsOf(D, t) = {d ∈ D | ω(d) = assert ∧ ν_from ≤ t < ν_to}
  - history(D, e)   = {d ∈ D | entity(d) = e}, sorted by τ
  - branch(D, t, Δ) = asOf(D, t) ∪ Δ
"""

from __future__ import annotations

import itertools

# Module-level monotonic transaction counter. A process-wide counter is the
# simplest thing that can work for a single-writer deployment; a production
# Postgres path uses a SEQUENCE object. The conftest resets this between
# tests so ids are predictable.
_tx_counter = itertools.count(1)


__all__ = []
