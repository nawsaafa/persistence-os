"""Phase 2.4b — :sys/now handler tests.

G4 — default-stack resolvability: ``:sys/now`` is callable from the
substrate's default canonical_audit_stack and returns a tz-aware UTC
:class:`datetime.datetime`.

LD-3 replay-clock symmetry tests (G2) land in T3.
"""
from __future__ import annotations

import datetime as dt


def test_sys_now_resolvable_from_canonical_stack():
    """G4 — :sys/now is callable from the default Substrate.open stack
    and returns a tz-aware UTC datetime.

    Falsifiability: if ``canonical_audit_stack`` doesn't install the
    sys-now handler, ``perform(":sys/now", {})`` raises ``Unhandled``.
    The ``tzinfo.utcoffset(result) == timedelta(0)`` assertion catches
    any accidental local-tz path (would surface as a
    ``timezone(timedelta(hours=N))`` not UTC).
    """
    from persistence.sdk import Substrate

    with Substrate.open("memory") as s:
        result = s.effect.perform(":sys/now", {})

    assert isinstance(result, dt.datetime)
    assert result.tzinfo is not None
    assert result.tzinfo.utcoffset(result) == dt.timedelta(0)
