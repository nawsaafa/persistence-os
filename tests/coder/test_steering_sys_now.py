"""Phase 2.4a G3 — :sys/now xfail-strict W3-marker (rescoped to 2.4b).

LD-3 was W3-rescoped to 2.4b after T0 receipts confirmed ``:sys/now`` is
a substrate-level addition (not CLI wiring). This test is the
falsifiability anchor: it ENCODES the contract that 2.4b must honor
when the substrate-time op lands.

Today the test fails because:
  (a) ``:sys/now`` substrate op does not exist —
      ``substrate.effect.perform(":sys/now", {})`` raises
      :class:`persistence.effect.runtime.Unhandled`.
  (b) ``_steering.branch()`` defaults its ``fork_at`` to
      ``dt.datetime.now(dt.timezone.utc)`` (see
      ``_steering.py:299``) — wall-clock, not substrate-time.

The strict-xfail decorator is the falsifiability mechanism:

  * If 2.4b lands BOTH (op + wiring change) AND removes the decorator
    → test PASSES. Suite stays green.
  * If 2.4b lands op + wiring but FORGETS the decorator removal →
    XPASS-strict triggers → suite FAILS with "[XPASS(strict)] Strict
    expected failure but it passed". Catches incomplete ship.
  * If 2.4b lands marker removal but only PARTIAL implementation
    (e.g. op landed but wiring not changed, or vice versa) → the
    spine action item ("REMOVE @pytest.mark.xfail") in the test
    docstring + the bare assertion (no skip) makes the test fail
    loudly with the actual missing piece.
  * If 2.4b doesn't land at all → marker stays, test stays xfail,
    suite green. The 2.4b spine carries the responsibility for
    flipping all four pieces simultaneously.

See ``docs/plans/2026-05-09-phase-2.4a-cli-wiring-design.md`` § LD-3
for the full 5-state falsifiability matrix.
"""
from __future__ import annotations

import datetime as dt

import pytest

from persistence.coder import Coder, _CoderSteeringSession
from persistence.effect.handlers import make_callable_llm_handler
from persistence.sdk import Substrate


def _done_call_fn():
    """Minimal :llm/call handler that lets Coder construction succeed."""
    def call_fn(*, model, messages, tools=None, temperature=None, max_tokens=None):
        return {
            "tool_calls": [{
                "input": {
                    "kind": "act",
                    "confidence": 0.9,
                    "payload": {"done": True},
                },
            }],
            "text": "",
        }
    return call_fn


@pytest.mark.xfail(
    strict=True,
    reason=(
        ":sys/now W3 rescope 2.4b — substrate-time op not yet landed; "
        "this DECORATOR MUST BE REMOVED as part of 2.4b's scope along "
        "with the :sys/now op landing + _steering.branch() wiring change. "
        "If 2.4b lands op + wiring but forgets this removal → strict "
        "triggers XPASS-strict → suite fails → catches incomplete ship."
    ),
)
def test_branch_default_fork_at_is_substrate_now() -> None:
    """G3 — :sys/now xfail-strict marker, W3-rescoped to 2.4b.

    Asserts that ``_steering.branch()``'s default ``fork_at`` (no
    override) equals ``substrate.effect.perform(":sys/now", {})``.
    Today this fails because:
      (a) ``:sys/now`` op doesn't exist (perform raises Unhandled).
      (b) ``_steering.branch()`` defaults to
          ``dt.datetime.now(dt.timezone.utc)`` (``_steering.py:299``).

    The strict-xfail decorator is the falsifiability mechanism. See
    ``docs/plans/2026-05-09-phase-2.4a-cli-wiring-design.md`` § LD-3
    for the 5-state falsifiability matrix.

    2.4b's REQUIRED actions (when this test moves out of xfail):

      1. Land ``:sys/now`` substrate op (handler returning
         replay-stable substrate-time, monotonic across re-runs).
      2. Wire ``_steering.branch()`` default ``fork_at`` to
         ``substrate.effect.perform(":sys/now", {})`` — replace the
         ``dt.datetime.now()`` fallback at ``_steering.py:299``.
      3. REMOVE the ``@pytest.mark.xfail`` decorator above.
      4. Update the W3 comments at ``_steering.py`` lines 271-272,
         297-298, 299, 305-306 (currently pointing to "2.4b") — drop
         the W3-rescope language entirely once ``:sys/now`` lands.
    """
    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)

        # 2.4b WIRING CONTRACT: branch()'s default fork_at must come from
        # the substrate-time op, not wall-clock. Today this raises
        # Unhandled (op missing) — the xfail-strict marker absorbs the
        # raise and reports XFAIL. After 2.4b: the perform returns a
        # dt.datetime that branch() should mirror exactly.
        sys_now_before = s.effect.perform(":sys/now", {})  # raises today

        # Take a branch using the DEFAULT fork_at (no _fork_at_override).
        branch_id = session.branch({"directive": "test-sys-now-default"})

        # Recover the recorded fork_at. It's stored on the directive
        # assertion under "valid_from" per _steering.py:317-322; query
        # the resulting branch DB for the directive entity.
        branch_db = session.branches[branch_id]
        directive_entity = f"directive-{branch_id}"
        # Find the :directive datom for this branch's directive entity.
        directive_datoms = [
            d for d in branch_db.log()
            if d.e == directive_entity and d.a == ":directive"
        ]
        assert directive_datoms, (
            "no :directive datom found on branch — branch() did not "
            "transact the directive assertion"
        )
        recorded_fork_at = directive_datoms[0].valid_from

        # Re-read substrate :sys/now AFTER the branch. Per the 2.4b
        # contract, :sys/now must be replay-stable and monotonic, so
        # before/after readings bracket the recorded fork_at.
        sys_now_after = s.effect.perform(":sys/now", {})

        assert isinstance(recorded_fork_at, dt.datetime)
        assert isinstance(sys_now_before, dt.datetime)
        assert isinstance(sys_now_after, dt.datetime)
        # Equality (or sandwich) — the exact contract is 2.4b's spine
        # to lock; today we only need the assertion to be SHARP enough
        # that wall-clock fallback would not satisfy it incidentally.
        assert sys_now_before <= recorded_fork_at <= sys_now_after, (
            f"branch() default fork_at {recorded_fork_at!r} not "
            f"sandwiched by :sys/now readings "
            f"[{sys_now_before!r}, {sys_now_after!r}] — wiring not "
            f"sourcing from :sys/now"
        )
