"""Phase 2.4b — :sys/now handler tests.

G2 — replay-clock symmetry (LD-3 falsifier for LD-1): if the
``:sys/now`` handler bypasses ``:clock/now`` (e.g., calls
``time.time()`` directly), the replay-clock list never gets popped
→ ``test_sys_now_delegates_to_replay_clock`` fails. This is the
load-bearing falsifiability anchor for LD-1's "single source of
truth for replay sequencing" claim.

G4 — default-stack resolvability: ``:sys/now`` is callable from the
substrate's default canonical_audit_stack and returns a tz-aware UTC
:class:`datetime.datetime`.
"""
from __future__ import annotations

import datetime as dt

from persistence.effect.handlers.clock import make_replay_clock_handler
from persistence.effect.handlers.sys_now import make_sys_now_handler
from persistence.effect.runtime import Runtime, perform, with_runtime


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


def test_sys_now_delegates_to_replay_clock():
    """G2 / LD-3 — :sys/now nested-performs :clock/now so that
    ``make_replay_clock_handler`` substitution propagates.

    Falsifiability: if ``make_sys_now_handler``'s body calls
    ``time.time()`` or ``dt.datetime.now()`` directly (forgetting the
    nested perform), the replay list is never popped → ``t1`` and ``t2``
    are wall-clock now-ish values (not exactly 100.0 / 200.0 epoch),
    and the equality assertion fails sharply.
    """
    clock = make_replay_clock_handler([100.0, 200.0])
    sys_now = make_sys_now_handler()
    rt = Runtime(handlers=[clock, sys_now])

    # The module-level perform() reads the active-runtime ContextVar;
    # since make_sys_now_handler's clause itself calls module-level
    # perform(":clock/now") to nest down to the replay clock, the
    # runtime MUST be activated via with_runtime() — Runtime.perform()
    # alone does not set _active. Mirror the pattern at
    # tests/effect/test_code_exec.py:102-106.
    with with_runtime(rt):
        t1 = perform(":sys/now")
        t2 = perform(":sys/now")

    assert t1 == dt.datetime.fromtimestamp(100.0, dt.timezone.utc)
    assert t2 == dt.datetime.fromtimestamp(200.0, dt.timezone.utc)


def test_sys_now_returns_bare_datetime_not_dict():
    """LD-1 return-shape guard: :sys/now returns bare datetime, NOT dict.

    Guards against a future regression where someone wraps the return
    in ``{"now": dt.datetime}`` (the Option-A-rejected shape). The G3
    contract at ``tests/coder/test_steering_sys_now.py`` directly
    assigns ``sys_now_before = s.effect.perform(":sys/now", {})`` and
    asserts ``isinstance(..., dt.datetime)`` — a dict wrapper would
    silently break that contract here.
    """
    from persistence.sdk import Substrate

    with Substrate.open("memory") as s:
        result = s.effect.perform(":sys/now", {})

    assert isinstance(result, dt.datetime), (
        f"expected bare dt.datetime, got {type(result).__name__}: {result!r}. "
        "LD-1 contract: :sys/now returns BARE datetime, not {'now': datetime} "
        "or any other wrapper. See docs/plans/2026-05-11-phase-2.4b-sys-now-design.md §LD-1."
    )
    assert not isinstance(result, dict), (
        "regression: :sys/now wrapped in dict — LD-1 says bare datetime"
    )
