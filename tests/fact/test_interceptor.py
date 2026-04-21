"""Tests for the mem0 interceptor adapter.

This adapter is the seam the Memory Palace integration PR will wire up. It
MUST emit a datom BEFORE the legacy write; if the datom write fails, the
legacy write must NOT happen. The adapter itself does not import mem0 — it
is duck-typed on ``add(...)`` / ``update(...)`` methods so the test can pass
a fake client and CI can run without mem0 installed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from persistence.fact import DB, InMemoryStore
from persistence.fact.interceptors.mem0_adapter import (
    InterceptorError,
    Mem0Interceptor,
)


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


class FakeMem0:
    """Mirrors the real ``mem0.Memory`` surface (mem0ai 2.x).

    Signatures (reproduced from ``inspect.signature(mem0.Memory.add / update)``,
    Apr 2026):

    - ``add(messages, *, user_id=None, agent_id=None, run_id=None,
           metadata=None, infer=True, memory_type=None, prompt=None)``
    - ``update(memory_id, data, metadata=None)``

    Earlier revisions of this fake accepted ``**kw`` unconditionally; that
    hid ARIS R3 F5 for months because every extra kwarg the interceptor
    was passing (``e=, a=, v=, valid_from=``) was silently swallowed.
    A strict fake reproduces the real-world TypeError.
    """

    def __init__(self, fail_on=None):
        self.added: list[dict] = []
        self.updated: list[tuple[str, str, dict | None]] = []
        self._fail_on = fail_on  # "add" | "update" | None

    def add(
        self,
        messages,
        *,
        user_id=None,
        agent_id=None,
        run_id=None,
        metadata=None,
        infer=True,
        memory_type=None,
        prompt=None,
    ):
        if self._fail_on == "add":
            raise RuntimeError("legacy mem0 write failed")
        self.added.append(
            {
                "messages": messages,
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "metadata": metadata,
                "infer": infer,
                "memory_type": memory_type,
                "prompt": prompt,
            }
        )
        return {"id": f"m-{len(self.added)}"}

    def update(self, memory_id, data, metadata=None):
        if self._fail_on == "update":
            raise RuntimeError("legacy mem0 update failed")
        self.updated.append((memory_id, data, metadata))
        return {"id": memory_id}


class TestDatomEmittedBeforeLegacyWrite:
    def test_add_emits_datom_then_calls_legacy(self):
        db = DB(InMemoryStore())
        fake = FakeMem0()
        interceptor = Mem0Interceptor(db, fake, principal={"tenant": "nawfal-egh"})
        interceptor.add(
            e="building/bankability",
            a="memory/text",
            v="WACC ceiling for MENA hydrogen = 8.7%",
            valid_from=_dt(2026, 4, 14),
            provenance={"source": "claude-code"},
        )
        # The datom lives in the log.
        log = list(interceptor.db.log())
        assert len(log) == 1
        assert log[0].e == "building/bankability"
        assert log[0].a == "memory/text"
        # And the legacy mem0 write happened after.
        assert len(fake.added) == 1

    def test_add_skips_legacy_when_datom_fails(self, monkeypatch):
        db = DB(InMemoryStore())
        fake = FakeMem0()
        interceptor = Mem0Interceptor(db, fake, principal={"tenant": "t"})

        def boom(*a, **kw):
            raise RuntimeError("fact store unavailable")

        monkeypatch.setattr(interceptor, "_emit_datom", boom)
        with pytest.raises(InterceptorError):
            interceptor.add(
                e="e",
                a="a",
                v="v",
                valid_from=_dt(2026, 1, 1),
                provenance={},
            )
        # Legacy never ran.
        assert fake.added == []

    def test_update_emits_retract_then_assert_then_calls_legacy(self):
        db = DB(InMemoryStore())
        fake = FakeMem0()
        interceptor = Mem0Interceptor(db, fake, principal={"tenant": "t"})

        # Seed an original value.
        interceptor.add(
            e="e", a="a", v="old",
            valid_from=_dt(2026, 1, 1),
            provenance={"source": "original"},
        )
        interceptor.update(
            memory_id="m-1",
            e="e", a="a", v="new",
            valid_from=_dt(2026, 1, 2),
            provenance={"source": "update"},
        )

        # The log should show auto-retraction: [assert old, retract old, assert new]
        ops = [(d.op, d.v) for d in interceptor.db.log()]
        assert ops == [
            ("assert", "old"),
            ("retract", "old"),
            ("assert", "new"),
        ]
        assert len(fake.added) == 1
        assert len(fake.updated) == 1

    def test_legacy_failure_does_not_rewind_datom(self):
        """Datom is the source of truth — legacy writes are derived caches.

        If the legacy cache write fails, the datom stays. An operator fixes
        the cache by rebuilding the projection from the log.
        """
        db = DB(InMemoryStore())
        fake = FakeMem0(fail_on="add")
        interceptor = Mem0Interceptor(db, fake, principal={"tenant": "t"})
        with pytest.raises(RuntimeError):
            interceptor.add(
                e="e", a="a", v="v",
                valid_from=_dt(2026, 1, 1),
                provenance={},
            )
        assert len(list(interceptor.db.log())) == 1, (
            "datom must persist even when legacy write fails"
        )
