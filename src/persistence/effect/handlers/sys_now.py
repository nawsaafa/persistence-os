"""Sys-now handler — substrate-time view over ``:clock/now``.

``:sys/now`` is a thin "view" op that nested-performs ``:clock/now`` (the
single primitive wall-clock source per ``clock.py``) and returns a BARE
UTC-aware ``datetime.datetime``. Keeping the actual ``time.time()`` call
confined to the clock handler preserves replay determinism: installing
``make_replay_clock_handler`` (or ``make_fixed_clock_handler``) under
this handler propagates substitution automatically, so no parallel
``:sys/now`` replay family is required.

Phase 2.4b LD-1 (codex consensus). See
``docs/plans/2026-05-11-phase-2.4b-sys-now-design.md`` §LD-1.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable

from persistence.effect.runtime import Handler, perform


def make_sys_now_handler(wraps: Iterable[str] = (":sys/now",)) -> Handler:
    """Return a handler that answers ``:sys/now`` with a UTC-aware datetime.

    Delegates to ``:clock/now`` (the single primitive wall-clock op) so
    that ``make_replay_clock_handler`` substitution propagates without
    needing a parallel ``:sys/now`` replay family. Returns BARE
    ``dt.datetime`` per the G1/G3 contract (NOT ``{"now": ...}``).

    Phase 2.4b LD-1, codex-consensus-locked (REJECT-FOR-NEW-OPTION-Z
    over Option A "extend clock handler with a second clause"). See
    ``docs/plans/2026-05-11-phase-2.4b-sys-now-design.md`` §LD-1.
    """

    def sys_now(args, k, ctx):
        ts = perform(":clock/now")["ts"]
        return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc)

    return Handler(
        name="sys_now",
        wraps=set(wraps),
        clauses={":sys/now": sys_now},
    )
