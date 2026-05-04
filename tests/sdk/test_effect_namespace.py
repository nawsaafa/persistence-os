"""Phase 2.1b — install_handler curated surface tests (design § 3.0.1)."""
from __future__ import annotations

import pytest

from persistence.effect.runtime import Handler
from persistence.sdk import Substrate


def _noop_handler(name: str = "test-noop") -> Handler:
    return Handler(
        name=name,
        wraps={":test/op"},
        clauses={":test/op": lambda args, k, ctx: {"ok": True}},
    )


def test_install_handler_position_bottom_inserts_at_index_zero():
    with Substrate.open("memory") as s:
        before = list(s._runtime.handlers)
        h = _noop_handler()
        s.effect.install_handler(h, position="bottom")
        assert s._runtime.handlers[0] is h
        # Existing handlers (canonical audit stack) shifted up
        assert s._runtime.handlers[1:] == before


def test_install_handler_position_top_appends():
    with Substrate.open("memory") as s:
        before = list(s._runtime.handlers)
        h = _noop_handler(name="top-noop")
        s.effect.install_handler(h, position="top")
        assert s._runtime.handlers[-1] is h
        assert s._runtime.handlers[:-1] == before


def test_install_handler_idempotent_replaces_by_name():
    with Substrate.open("memory") as s:
        h1 = _noop_handler(name="dup")
        h2 = _noop_handler(name="dup")
        s.effect.install_handler(h1, position="bottom")
        s.effect.install_handler(h2, position="bottom")
        # Only one handler with name="dup" should remain — h2 replaced h1
        dups = [h for h in s._runtime.handlers if h.name == "dup"]
        assert len(dups) == 1
        assert dups[0] is h2


def test_install_handler_invalid_position_raises():
    with Substrate.open("memory") as s:
        h = _noop_handler()
        with pytest.raises(ValueError, match="position must be"):
            s.effect.install_handler(h, position="middle")  # type: ignore[arg-type]


def test_install_handler_default_position_is_bottom():
    with Substrate.open("memory") as s:
        h = _noop_handler()
        s.effect.install_handler(h)  # no position kwarg
        assert s._runtime.handlers[0] is h
