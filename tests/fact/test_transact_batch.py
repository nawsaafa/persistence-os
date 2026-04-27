"""DB.transact_batch — equivalent to transact for correctness, faster
under many-fact transactions because auto-retraction lookups are batched
into a single log pass.
"""
from datetime import datetime, timezone, timedelta

from persistence.fact.db import DB


def test_transact_batch_auto_retracts_prior_open_assert():
    db = DB()
    db = db.transact([{
        "e": "user-1", "a": "name", "v": "Alice",
        "valid_from": datetime.now(timezone.utc),
    }])
    db = db.transact_batch([{
        "e": "user-1", "a": "name", "v": "Bob",
        "valid_from": datetime.now(timezone.utc) + timedelta(seconds=1),
    }])
    view = db.as_of(db._clock())
    assert view.entity("user-1").get("name") == "Bob"


def test_transact_batch_handles_50_facts_without_quadratic_blowup():
    """Stress test — confirms single-pass auto-retraction works on bulk."""
    import time

    db = DB()
    seed_facts = [
        {"e": f"e-{i}", "a": "v", "v": i,
         "valid_from": datetime.now(timezone.utc)}
        for i in range(50)
    ]
    db = db.transact_batch(seed_facts)

    update_facts = [
        {"e": f"e-{i}", "a": "v", "v": i * 2,
         "valid_from": datetime.now(timezone.utc) + timedelta(seconds=1)}
        for i in range(50)
    ]
    start = time.monotonic()  # noqa: wall-clock
    db = db.transact_batch(update_facts)
    elapsed = time.monotonic() - start  # noqa: wall-clock

    view = db.as_of(db._clock())
    for i in range(50):
        assert view.entity(f"e-{i}").get("v") == i * 2

    assert elapsed < 1.0, f"transact_batch quadratic? took {elapsed:.3f}s"


def test_transact_batch_equivalent_to_transact_per_fact():
    """For correctness: results should be identical to one-fact-at-a-time."""
    db1 = DB()
    db2 = DB()
    facts = [
        {"e": f"e-{i}", "a": "v", "v": i,
         "valid_from": datetime.now(timezone.utc)}
        for i in range(5)
    ]
    for f in facts:
        db1 = db1.transact([f])
    db2 = db2.transact_batch(facts)
    v1 = {e: db1.as_of(db1._clock()).entity(e) for e in [f["e"] for f in facts]}
    v2 = {e: db2.as_of(db2._clock()).entity(e) for e in [f["e"] for f in facts]}
    assert v1 == v2
