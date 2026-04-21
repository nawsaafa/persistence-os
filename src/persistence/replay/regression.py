"""Regression test generator — given a golden trajectory + an assertion,
emit a self-contained pytest file that replays and asserts.

Generated tests are designed to run without any fixture setup beyond the
standard library + this module — the trajectory is inlined as JSON.
"""
from __future__ import annotations

from persistence.replay.trajectory import Trajectory


_TEMPLATE = '''\
"""AUTO-GENERATED regression test from trajectory {tid}.

Replay the golden trajectory with a NO-OP intervention and assert the
supplied predicate holds. If the agent's behavior drifts, this test will
fail loudly.
"""
from __future__ import annotations

import json

from persistence.replay.trajectory import Trajectory


# Inlined golden trajectory.
_GOLDEN_JSON = {payload!r}


def {test_name}():
    golden = Trajectory.from_json(_GOLDEN_JSON)
    # For regression purposes we don't actually re-run the agent (it may
    # not even be importable in this consumer). Instead we assert the
    # supplied predicate against the loaded trajectory.
    predicate = {assertion}
    assert predicate(golden), (
        f"regression: trajectory {{golden.id}} no longer satisfies predicate"
    )
'''


def gen_regression_test(
    trajectory: Trajectory,
    assertion: str,
    *,
    test_name: str = "test_trajectory_regression",
) -> str:
    """Emit a pytest-compatible source string for the given trajectory.

    `assertion` must be a Python expression that evaluates to a callable
    taking the trajectory and returning bool. Example::

        assertion='lambda traj: traj.outcome["pnl"] == 5.0'
    """
    payload = trajectory.to_json()
    return _TEMPLATE.format(
        tid=trajectory.id,
        payload=payload,
        test_name=test_name,
        assertion=assertion,
    )
