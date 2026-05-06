"""T3/G3 — `_register_substrate_handlers` registers 10 adapter callables
on a fresh Dispatcher; each adapter delegates to substrate.effect.perform
with the leaf's keyword-form tag. (T2 FD1: Node.tag is keyword-form.)"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from persistence.coder._planner import (
    REGISTERED_LEAF_TAGS,
    _register_substrate_handlers,
)
from persistence.plan import Node
from persistence.sdk import Substrate


@pytest.fixture
def s():
    with Substrate.open("memory") as substrate:
        yield substrate


def test_register_handlers_covers_all_10_registered_leaf_tags(s):
    d = s.plan.new_dispatcher()
    _register_substrate_handlers(d, s)
    for tag in REGISTERED_LEAF_TAGS:
        assert d.has_handler(tag), f"missing handler for {tag!r}"


def test_register_handlers_adapter_calls_effect_perform_with_keyword_tag(s):
    """Adapter takes (node, env) and calls substrate.effect.perform with
    the keyword-form tag and node.attrs as args dict."""
    fake_perform = MagicMock(return_value={"stub": True})
    # Step 3.3 calibration confirmed direct assignment works.
    s.effect.perform = fake_perform  # type: ignore[method-assign]
    d = s.plan.new_dispatcher()
    _register_substrate_handlers(d, s)
    node = Node(tag=":fs/read", attrs={"path": "x.txt"}, children=())
    handler = d.get_handler(":fs/read")
    assert handler is not None
    result = handler(node, {})
    fake_perform.assert_called_once_with(":fs/read", {"path": "x.txt"})
    assert result == {"stub": True}


def test_register_handlers_does_not_register_branch_or_code():
    """Banned tags MUST NOT be registered (defense in depth)."""
    from persistence.plan import Dispatcher
    d = Dispatcher()
    s = MagicMock()
    _register_substrate_handlers(d, s)
    assert not d.has_handler(":branch")
    assert not d.has_handler(":code")


def test_register_handlers_does_not_register_llm_call():
    """LD5: `:llm/call` is NOT a plan leaf — queued to 2.3c."""
    from persistence.plan import Dispatcher
    d = Dispatcher()
    s = MagicMock()
    _register_substrate_handlers(d, s)
    assert not d.has_handler(":llm/call")
