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

import ast
import datetime as dt
import pathlib

import pytest

from persistence.coder import Coder, _CoderSteeringSession
from persistence.effect.handlers import make_callable_llm_handler
from persistence.effect.handlers.clock import make_fixed_clock_handler
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
    # G1 falsifiability sharpener (ARIS R0-fold B1, 2026-05-11):
    # The original sandwich `before <= recorded <= after` did NOT
    # falsify "_steering.branch() still uses wall-clock" because
    # :sys/now delegates to :clock/now which is backed by the SAME
    # time.time() source as dt.datetime.now() → both calls land in the
    # same microsecond → sandwich passes incidentally even if wiring
    # is forgotten. Replaced with FIXED-CLOCK EQUALITY: install a
    # make_fixed_clock_handler(ts=FIXED_EPOCH), which replaces the
    # canonical system clock (Handler.install_handler is idempotent on
    # name="clock"). After LD-1+LD-2 land, :sys/now delegates to the
    # fixed clock → recorded_fork_at must equal the FIXED datetime
    # EXACTLY. If wiring is forgotten, recorded_fork_at == real-now
    # (datetime.now() syscall path, unaffected by handler install) →
    # assertion fails sharply (delta of seconds-to-years between
    # 1970-01-01T00:00:42Z and 2026-now).
    FIXED_EPOCH = 42.0  # 1970-01-01T00:00:42Z — far from any plausible real-now
    expected_fork_at = dt.datetime.fromtimestamp(FIXED_EPOCH, dt.timezone.utc)

    with Substrate.open("memory") as s:
        s.effect.install_handler(make_callable_llm_handler(_done_call_fn()))
        # Replace the system clock with a fixed clock. install_handler
        # is idempotent on name="clock" per sdk/_facade.py:143-166, so
        # this REPLACES the canonical clock handler. :sys/now (LD-1)
        # delegates to :clock/now → returns fromtimestamp(FIXED_EPOCH).
        s.effect.install_handler(make_fixed_clock_handler(ts=FIXED_EPOCH))

        coder = Coder(substrate=s, task="t", model="m", max_iters=1)
        session = _CoderSteeringSession(coder=coder)

        # Sanity probe: :sys/now must resolve and return the FIXED
        # datetime. Today raises Unhandled (op missing) — xfail-strict
        # absorbs. After LD-1: returns expected_fork_at exactly.
        sys_now_probe = s.effect.perform(":sys/now", {})
        assert sys_now_probe == expected_fork_at, (
            f":sys/now returned {sys_now_probe!r}, expected "
            f"{expected_fork_at!r} (fixed-clock didn't propagate — "
            f"LD-1 handler isn't delegating to :clock/now)"
        )

        # Take a branch using the DEFAULT fork_at (no _fork_at_override).
        branch_id = session.branch({"directive": "test-sys-now-default"})

        # Recover the recorded fork_at. Stored on the directive
        # assertion under "valid_from" per _steering.py:317-322.
        branch_db = session.branches[branch_id]
        directive_entity = f"directive-{branch_id}"
        directive_datoms = [
            d for d in branch_db.log()
            if d.e == directive_entity and d.a == ":directive"
        ]
        assert directive_datoms, (
            "no :directive datom found on branch — branch() did not "
            "transact the directive assertion"
        )
        recorded_fork_at = directive_datoms[0].valid_from

        # SHARP: recorded_fork_at must equal the FIXED :sys/now
        # EXACTLY. Wall-clock fallback would produce real-now (year
        # 2026+), not 1970-01-01T00:00:42Z — falsification is sharp.
        assert isinstance(recorded_fork_at, dt.datetime)
        assert recorded_fork_at == expected_fork_at, (
            f"branch() default fork_at {recorded_fork_at!r} != fixed "
            f":sys/now {expected_fork_at!r} — wiring not sourcing from "
            f":sys/now (LD-2 wiring miss). If recorded_fork_at is a "
            f"2026+ datetime, _steering.py:299 still uses wall-clock; "
            f"swap to substrate.effect.perform(':sys/now', {{}})."
        )


@pytest.mark.xfail(
    strict=True,
    reason=(
        ":sys/now W3 rescope 2.4b — _steering.py:299 currently calls "
        "dt.datetime.now() pending LD-2 wiring. This DECORATOR MUST BE "
        "REMOVED in the same T-task that lands the perform(':sys/now') "
        "swap. XPASS-strict catches incomplete ship if LD-2 lands but "
        "removal is forgotten."
    ),
)
def test_no_wall_clock_in_steering_branch() -> None:
    """G1 Test B (ARIS R0-fold B1): AST ban for wall-clock in _steering.py.

    Belt-and-suspenders catch: static-code regression where a future
    PR re-introduces dt.datetime.now() or datetime.utcnow() in
    src/persistence/coder/_steering.py. Complementary to Test A's
    runtime fixed-clock equality.

    Scans for any X.now() or X.utcnow() call whose leftmost name is
    `dt` or `datetime`. Allows uuid.uuid4().hex (entity-id) and
    time.time_ns() (orthogonal). Does NOT scan for `:sys/now` perform
    calls (those use `substrate.effect.perform(":sys/now", ...)`).
    """
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    steering_path = repo_root / "src" / "persistence" / "coder" / "_steering.py"
    src = steering_path.read_text()
    tree = ast.parse(src)
    banned_attrs = {"now", "utcnow"}
    banned_leftmost = {"dt", "datetime"}

    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute)
                    and func.attr in banned_attrs):
                leftmost = func.value
                while isinstance(leftmost, ast.Attribute):
                    leftmost = leftmost.value
                leftmost_name = (
                    leftmost.id if isinstance(leftmost, ast.Name) else ""
                )
                if leftmost_name in banned_leftmost:
                    offenders.append(
                        f"  line {node.lineno}: {leftmost_name}.{func.attr}()"
                    )

    assert not offenders, (
        "wall-clock read found in src/persistence/coder/_steering.py — "
        "must route through `substrate.effect.perform(':sys/now', {})`:\n"
        + "\n".join(offenders)
    )
