"""No-GIL forward-compat marker — rev N of design doc.

Marked with custom marker @pytest.mark.no_gil_safe so the conflict
suite can be optionally re-run under `python -X gil=0` (Python 3.13
experimental, 3.14 supported) when available. On GIL builds this is
a regular test that exercises real-thread concurrency.
"""
import threading

import pytest

from persistence.fact.db import DB


@pytest.mark.no_gil_safe
def test_concurrent_increments_under_real_threads():
    """Same as test_concurrent_threads_increment_counter_via_alter but
    with the explicit no-gil marker for documentation and CI matrix
    selection.
    """
    db = DB()
    r = db.ref("counter")
    with db.dosync() as tx:
        tx.assoc(r, 0)

    @db.dosync
    def increment(tx):
        tx.alter(r, lambda v: (v if v is not None else 0) + 1)

    threads = [threading.Thread(target=increment) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    view = db.as_of(db._clock())
    assert view.entity("counter").get("value") == 20
