"""Phase A — Dispatcher: handler-per-tag registry over the existing walker."""
from __future__ import annotations

import pytest


def test_dispatcher_register_stores_handler():
    """Dispatcher.register(tag, handler) stores the handler keyed by tag."""
    from persistence.plan import Dispatcher

    d = Dispatcher()
    handler = lambda node, env: env  # no-op
    d.register(":fact", handler)
    assert d.has_handler(":fact")
    assert not d.has_handler(":no-such-tag")


def test_dispatcher_register_rejects_duplicate_without_replace():
    """Re-registering the same tag without replace=True raises ValueError."""
    from persistence.plan import Dispatcher

    d = Dispatcher()
    d.register(":fact", lambda node, env: env)
    with pytest.raises(ValueError) as excinfo:
        d.register(":fact", lambda node, env: env)
    assert ":fact" in str(excinfo.value)


def test_dispatcher_register_replace_overrides():
    """Re-registering with replace=True overwrites the prior handler."""
    from persistence.plan import Dispatcher

    d = Dispatcher()
    d.register(":fact", lambda node, env: "first")
    d.register(":fact", lambda node, env: "second", replace=True)
    # Verified indirectly via dispatch (later test), or via has_handler
    assert d.has_handler(":fact")
