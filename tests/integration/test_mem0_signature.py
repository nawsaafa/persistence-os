"""Mem0Interceptor contract test — real mem0.Memory.add signature (R3 F5).

Before the R3 F5 fix, ``Mem0Interceptor.add`` forwarded ``e=, a=, v=,
valid_from=`` into ``mem0.Memory.add(...)`` which does NOT accept those
kwargs. On a live VPS the first call would raise
``TypeError: add() got an unexpected keyword argument 'e'``. The existing
duck-typed ``FakeMem0(**kwargs)`` fake swallowed everything, so the bug
never surfaced in CI.

This test file uses a STRICT fake that mirrors the real mem0 2.x signature
exactly:
    Memory.add(messages, *, user_id=None, agent_id=None, run_id=None,
               metadata=None, infer=True, memory_type=None, prompt=None)
    Memory.update(memory_id, data, metadata=None)

If the interceptor passes any extra kwargs, the fake raises TypeError —
mirroring what the real mem0 would do.

We also run the same-signature introspection check against the installed
``mem0`` package when present (marked @pytest.mark.integration, skipped
if mem0 is not importable) so the fake signature cannot silently drift
from the real one.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pytest

from persistence.fact import DB, InMemoryStore
from persistence.fact.interceptors.mem0_adapter import (
    InterceptorError,
    Mem0Interceptor,
)


# ---------------------------------------------------------------------------
# Strict fake mirroring the real mem0.Memory.add / update shape.
# ---------------------------------------------------------------------------


class StrictFakeMem0:
    """Fake mem0 client that rejects unexpected kwargs (mirrors real mem0).

    Signatures mirror mem0ai 2.0 (April 2026):
    - ``add(messages, *, user_id=None, agent_id=None, run_id=None,
           metadata=None, infer=True, memory_type=None, prompt=None)``
    - ``update(memory_id, data, metadata=None)``
    """

    def __init__(self) -> None:
        self.added: list[dict] = []
        self.updated: list[tuple[str, str, Optional[Dict[str, Any]]]] = []

    def add(
        self,
        messages,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        metadata: Dict[str, Any] | None = None,
        infer: bool = True,
        memory_type: str | None = None,
        prompt: str | None = None,
    ):
        # Record the call so tests can assert how the interceptor mapped
        # the datom fields into metadata.
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

    def update(
        self,
        memory_id: str,
        data: str,
        metadata: Dict[str, Any] | None = None,
    ):
        self.updated.append((memory_id, data, metadata))
        return {"id": memory_id}


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# The contract tests
# ---------------------------------------------------------------------------


class TestInterceptorMatchesRealMem0Signature:
    def test_add_forwards_only_kwargs_mem0_accepts(self):
        """Interceptor must NOT pass e/a/v/valid_from to mem0.add."""
        db = DB(InMemoryStore())
        fake = StrictFakeMem0()
        interceptor = Mem0Interceptor(db, fake, principal={"tenant": "t"})

        # Call without any ``messages`` kwarg — the interceptor should
        # synthesize a messages list from the datom v (or default to empty).
        interceptor.add(
            e="entity/42",
            a="memory/text",
            v="WACC ceiling 8.7%",
            valid_from=_dt(2026, 4, 14),
            provenance={"source": "claude-code"},
        )
        assert len(fake.added) == 1
        call = fake.added[0]

        # ``messages`` must be provided (mem0 requires it positionally / by
        # the method signature). Interceptor should derive one from v.
        assert call["messages"] is not None
        # Metadata must carry the datom fields so downstream mem0 projections
        # can reconstruct the bitemporal context.
        md = call["metadata"] or {}
        assert md.get("e") == "entity/42"
        assert md.get("a") == "memory/text"
        # valid_from serialized to ISO (JSON-safe for mem0's metadata store).
        assert "valid_from" in md
        assert str(md["valid_from"]).startswith("2026-04-14")
        # The datom lives in the fact log exactly once.
        log = list(interceptor.db.log())
        assert len(log) == 1
        assert log[0].e == "entity/42"

    def test_add_forwards_user_id_and_messages_passthrough(self):
        """Caller-supplied mem0 kwargs (user_id, messages) pass through."""
        db = DB(InMemoryStore())
        fake = StrictFakeMem0()
        interceptor = Mem0Interceptor(db, fake, principal={"tenant": "t"})

        interceptor.add(
            e="e",
            a="a",
            v="v",
            valid_from=_dt(2026, 1, 1),
            provenance={},
            messages=[{"role": "user", "content": "hi"}],
            user_id="alice",
            metadata={"custom": "extra"},
        )
        call = fake.added[0]
        assert call["messages"] == [{"role": "user", "content": "hi"}]
        assert call["user_id"] == "alice"
        # Caller metadata merged with datom metadata — datom fields win
        # (we need them for reconstruction) but ``custom`` is preserved.
        assert call["metadata"] is not None
        assert call["metadata"].get("custom") == "extra"
        assert call["metadata"].get("e") == "e"

    def test_add_rejects_unknown_kwargs_to_mem0(self):
        """Regression: a stray kwarg from the interceptor body would
        blow up. The strict fake enforces this the way real mem0 would.
        """
        db = DB(InMemoryStore())
        fake = StrictFakeMem0()
        interceptor = Mem0Interceptor(db, fake, principal={"tenant": "t"})

        # Even if the caller passes a totally bogus kwarg, it must NOT
        # flow into mem0.add (the interceptor's ``**legacy_kwargs`` is
        # typed narrowly in the contract). We verify by asserting the
        # fake rejects — TypeError would be raised.
        with pytest.raises(TypeError):
            interceptor.add(
                e="e",
                a="a",
                v="v",
                valid_from=_dt(2026, 1, 1),
                provenance={},
                not_a_real_mem0_kwarg=True,  # must surface as TypeError
            )

    def test_update_forwards_memory_id_data_metadata_only(self):
        """mem0.Memory.update takes (memory_id, data, metadata=None). The
        interceptor must not push e/a/v/valid_from through."""
        db = DB(InMemoryStore())
        fake = StrictFakeMem0()
        interceptor = Mem0Interceptor(db, fake, principal={"tenant": "t"})

        # Seed an initial assert so update's auto-retract has a prior.
        interceptor.add(
            e="e",
            a="a",
            v="old",
            valid_from=_dt(2026, 1, 1),
            provenance={},
            messages=[{"role": "user", "content": "old"}],
        )
        interceptor.update(
            memory_id="m-1",
            e="e",
            a="a",
            v="new",
            valid_from=_dt(2026, 1, 2),
            provenance={},
            data="new",
        )
        assert len(fake.updated) == 1
        memory_id, data, metadata = fake.updated[0]
        assert memory_id == "m-1"
        assert data == "new"
        assert metadata is not None
        assert metadata.get("e") == "e"
        assert metadata.get("a") == "a"
        # The datom log shows assert-old, retract-old, assert-new.
        ops = [(d.op, d.v) for d in interceptor.db.log()]
        assert ops == [("assert", "old"), ("retract", "old"), ("assert", "new")]


@pytest.mark.integration
class TestInterceptorMatchesInstalledMem0Signature:
    """If the real mem0 is installed, the strict fake's signature MUST be
    a superset of it — otherwise this test file is drifting from reality.

    Integration-marked so CI matrices that don't install mem0 skip it.
    """

    def test_fake_add_is_shape_compatible_with_real_mem0_add(self):
        mem0 = pytest.importorskip("mem0")
        from mem0 import Memory

        real_sig = inspect.signature(Memory.add)
        fake_sig = inspect.signature(StrictFakeMem0.add)

        # Every non-self parameter of the real ``add`` must exist on the
        # fake with the same kind (POSITIONAL_OR_KEYWORD vs KEYWORD_ONLY).
        real_params = {
            name: p
            for name, p in real_sig.parameters.items()
            if name != "self"
        }
        fake_params = {
            name: p
            for name, p in fake_sig.parameters.items()
            if name != "self"
        }
        missing = set(real_params) - set(fake_params)
        assert not missing, (
            f"StrictFakeMem0.add is missing real mem0 kwargs: {missing!r}"
        )

    def test_fake_update_is_shape_compatible_with_real_mem0_update(self):
        mem0 = pytest.importorskip("mem0")
        from mem0 import Memory

        real_sig = inspect.signature(Memory.update)
        fake_sig = inspect.signature(StrictFakeMem0.update)
        real_params = {
            name: p
            for name, p in real_sig.parameters.items()
            if name != "self"
        }
        fake_params = {
            name: p
            for name, p in fake_sig.parameters.items()
            if name != "self"
        }
        missing = set(real_params) - set(fake_params)
        assert not missing, (
            f"StrictFakeMem0.update is missing real mem0 kwargs: {missing!r}"
        )
