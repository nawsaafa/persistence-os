"""Effect-intent atomicity — fire on commit, drop on conflict."""
import pytest

from persistence.effect.runtime import Handler, Runtime, with_runtime
from persistence.fact.db import DB


def _capture_handler(captured: list) -> Handler:
    """A handler that appends every dispatch to ``captured``."""
    return Handler(
        name="capture",
        wraps={":log/write"},
        clauses={
            ":log/write": lambda args, *_: captured.append(args) or None,
        },
    )


def test_intent_fires_after_commit():
    db = DB()
    captured: list = []
    rt = Runtime(handlers=[_capture_handler(captured)])
    with with_runtime(rt):
        @db.dosync
        def write(tx):
            tx.effect(":log/write", message="hello")
            tx.assoc(db.ref("v"), 1)

        write()
    # Intent should have fired exactly once.
    assert len(captured) == 1
    assert captured[0]["message"] == "hello"
    assert "_txn_commit" in captured[0]  # commit_id pinned


def test_intent_does_not_fire_during_body():
    db = DB()
    captured: list = []
    rt = Runtime(handlers=[_capture_handler(captured)])
    with with_runtime(rt):
        @db.dosync
        def write(tx):
            tx.effect(":log/write", message="hello")
            # During body, captured should still be empty.
            assert captured == []
        write()
    assert len(captured) == 1


def test_intent_does_not_fire_on_body_exception():
    db = DB()
    captured: list = []
    rt = Runtime(handlers=[_capture_handler(captured)])
    with with_runtime(rt):
        @db.dosync
        def write(tx):
            tx.effect(":log/write", message="hello")
            raise ValueError("boom")
        with pytest.raises(ValueError):
            write()
    assert captured == []  # no intent fired


def test_multiple_intents_fire_in_queue_order():
    db = DB()
    captured: list = []
    rt = Runtime(handlers=[_capture_handler(captured)])
    with with_runtime(rt):
        @db.dosync
        def write(tx):
            tx.effect(":log/write", message="first")
            tx.effect(":log/write", message="second")
            tx.effect(":log/write", message="third")
        write()
    assert [c["message"] for c in captured] == ["first", "second", "third"]
