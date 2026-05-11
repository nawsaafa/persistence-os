"""Phase 2.4b G1 — :sys/now wiring contract for ``_steering.branch()``.

W3-rescoped from 2.4a LD-3 and landed in Phase 2.4b (LD-1 substrate op
+ LD-2 ``_steering.py`` wiring). Both tests below were strict-xfail
markers in 2.4a; they FLIP TO PASS once the 2.4b spine ships:

  * **Test A** (``test_branch_default_fork_at_is_substrate_now``)
    runtime fixed-clock equality: install a
    :func:`make_fixed_clock_handler` and assert ``branch()``'s default
    ``fork_at`` equals ``substrate.effect.perform(":sys/now", {})``
    exactly. Falsifies a wall-clock fallback in ``_steering.py``
    sharply (delta of seconds-to-years between 1970-01-01T00:00:42Z
    and 2026-now).
  * **Test B** (``test_no_wall_clock_in_steering_branch``)
    static-code AST ban: scans ``src/persistence/coder/_steering.py``
    and asserts zero ``X.now(...)`` / ``X.utcnow(...)`` calls whose
    leftmost name is ``dt`` or ``datetime``. Belt-and-suspenders
    catch for future-regression where someone re-introduces a
    wall-clock read in this file.

See ``docs/plans/2026-05-11-phase-2.4b-sys-now-design.md`` § G1 for
the falsifiability matrix.
"""
from __future__ import annotations

import ast
import datetime as dt
import pathlib

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


def test_branch_default_fork_at_is_substrate_now() -> None:
    """G1 Test A — :sys/now wiring runtime contract (LD-1 + LD-2).

    Asserts that ``_steering.branch()``'s default ``fork_at`` (no
    override) sources from ``substrate.effect.perform(":sys/now", {})``
    by installing a :func:`make_fixed_clock_handler` and checking the
    recorded ``valid_from`` on the branch's ``:directive`` datom is
    EXACTLY the fixed datetime.

    Falsifiability (ARIS R0-fold B1, 2026-05-11): the original sandwich
    ``before <= recorded <= after`` did NOT catch "wiring forgotten"
    because ``:sys/now`` delegates to ``:clock/now`` which is backed
    by the same ``time.time()`` source as ``dt.datetime.now()`` — both
    calls land in the same microsecond and a sandwich passes
    incidentally. The fixed-clock-equality test below produces a
    seconds-to-years delta (1970 vs 2026+) whenever wiring is missing.
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
        # NOTE: _steering.py writes ``"a": ":directive"`` but
        # ``Datom.__post_init__`` strips leading colons per the
        # ARIS R4 W4-wire-identity / R5 W5-datom-idempotent invariant
        # (datom.py:159-177). The canonical in-memory form is bare
        # (``"directive"``); the wire form re-prepends ``":"`` in
        # ``datom_to_wire``. Filter on the bare in-memory form here.
        branch_db = session.branches[branch_id]
        directive_entity = f"directive-{branch_id}"
        directive_datoms = [
            d for d in branch_db.log()
            if d.e == directive_entity and d.a == "directive"
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
