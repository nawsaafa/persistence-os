"""CLI demo — the agent1-fact-spec §8 BankabilityAI WACC counterfactual.

Run with::

    python -m persistence.fact.demo

Expected output::

    Now:      {'project/wacc': 0.091}
    April 15: {'project/wacc': 0.087}
    Branch:   {'project/wacc': 0.095}

This mirrors the 76-line prototype from the architectural spec, run against
the full bitemporal query API we ship — not a standalone toy. The scenario:

  - DFI agent asserts WACC = 0.087 for project P-042 on April 14.
  - A rerun asserts WACC = 0.091 on April 19 (auto-retract of the prior).
  - We query three views:
      Now       — the current head of the log.
      April 15  — valid-time view on a historical date.
      Branch    — counterfactual: what if WACC had been 0.095?

The branch query is the point of the whole module. Because the DB is a
value, forking the state to ask "what if?" is O(|Δ|·log|D|) (paper
Proposition 1) and never mutates the authoritative log.
"""

from __future__ import annotations

from datetime import datetime, timezone

from persistence.fact import DB, InMemoryStore


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


def main() -> int:
    db = DB(InMemoryStore())

    # DFI agent's initial WACC assumption for project P-042, effective Apr 14.
    db = db.transact(
        [
            {
                "e": "p-042",
                "a": "project/wacc",
                "v": 0.087,
                "valid_from": _dt(2026, 4, 14),
            }
        ],
        provenance={
            "source": "dfi-agent",
            "model": "claude-opus-4.7",
            "confidence": 0.82,
        },
    )

    # Rerun on Apr 19 revises the ceiling up. Auto-retraction handles the
    # "the old value was true until the new one took over" bookkeeping.
    db = db.transact(
        [
            {
                "e": "p-042",
                "a": "project/wacc",
                "v": 0.091,
                "valid_from": _dt(2026, 4, 19),
            }
        ],
        provenance={
            "source": "dfi-agent-rerun",
            "model": "claude-opus-4.7",
            "confidence": 0.88,
        },
    )

    now = datetime.now(timezone.utc)
    print("Now:     ", db.as_of(now).entity("p-042"))
    print("April 15:", db.as_of_valid(_dt(2026, 4, 15)).entity("p-042"))

    # Counterfactual: what if WACC were 0.095 from *now* forward? The
    # hypothetical assert's valid_from defaults to the branch time, which
    # is after the most recent real assert — so it wins under the "max
    # valid_from then max tx" resolution rule.
    cf = db.branch(
        now,
        [{"e": "p-042", "a": "project/wacc", "v": 0.095}],
    )
    # Re-read "now" so the tx_time of the branch's write is included in
    # the as_of window — otherwise the 0.095 tx would be filtered out.
    print("Branch:  ", cf.as_of(datetime.now(timezone.utc)).entity("p-042"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
